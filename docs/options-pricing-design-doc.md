# SIMD Options Pricing — Design Doc (Milestone 7: Rust Depth)

## 1. Purpose

Add a vectorized Black-Scholes + Greeks pricing module to the Rust side
of this project, per plan.md's Component Responsibilities: "a vectorized
(SIMD) options pricing module... to support a delta-hedging strategy."
This milestone builds and proves the pricer in isolation; Milestone 8
is what actually wires its Greeks into a trading decision. Design
Principle 3 from plan.md — "every hero feature has a number attached...
not just fast" — is the whole point of this milestone existing as its
own step: "SIMD" is not a checkbox, it's a claim that needs a real,
reproducible benchmark backing it, against both a naive Rust baseline
and a naive Python baseline.

## 2. Scope

**In scope:**
- A scalar (naive) Black-Scholes pricer for European calls/puts:
  price + all five standard Greeks (delta, gamma, vega, theta, rho).
- A hand-rolled, polynomial-approximation normal CDF — not `libm`'s
  `erf` — because that choice is what makes the vectorized version
  possible at all (§4).
- A SIMD-vectorized batch pricer processing many options per call, using
  the `wide` crate (§5) for portable lanes across x86_64 and Apple
  Silicon's NEON, rather than hand-written architecture-specific
  intrinsics.
- A `criterion`-based benchmark comparing naive Python, naive Rust
  (scalar), and SIMD Rust (batched) pricing throughput — a new,
  deliberate dev-dependency addition, justified the same way `yfinance`
  and `ib_async` were: this milestone's entire point requires it.

**Out of scope for this milestone** (see §9):
- The delta-hedging strategy itself that consumes these Greeks
  (Milestone 8).
- Wiring pricing into `risk_engine`'s live fill-processing loop — this
  milestone is a self-contained, independently-tested pricing library;
  Milestone 8 decides how (or whether) `risk_engine` calls into it live.
- American-style early exercise, dividend yields, or exotic Greeks
  (vanna, volga) — plain vanilla European options only, matching what
  plan.md actually names.

## 3. Black-Scholes Model

Standard European option pricing:

```
d1 = (ln(S/K) + (r + σ²/2)T) / (σ√T)
d2 = d1 - σ√T

Call price = S·N(d1) - K·e^(-rT)·N(d2)
Put price  = K·e^(-rT)·N(-d2) - S·N(-d1)
```

with the five Greeks (delta, gamma, vega, theta, rho) as their standard
closed-form partial derivatives of this same price formula — no
numerical differentiation, since closed forms exist and are exactly
what a real pricer would use.

```rust
pub struct OptionParams {
    pub spot: f64,
    pub strike: f64,
    pub rate: f64,
    pub vol: f64,
    pub time_to_expiry: f64,
    pub is_call: bool,
}

pub struct Greeks {
    pub price: f64,
    pub delta: f64,
    pub gamma: f64,
    pub vega: f64,
    pub theta: f64,
    pub rho: f64,
}

pub fn price_scalar(p: &OptionParams) -> Greeks;
```

## 4. Why a Polynomial Normal CDF, Not `libm`'s `erf`

Every one of these formulas needs the standard normal CDF, `N(x)`. The
obvious approach — call `libm`'s `erf` (`N(x) = 0.5·(1 + erf(x/√2))`) —
is exactly the thing that makes a function impossible to vectorize
cleanly: `erf` is a transcendental special function implemented as a
branchy, iterative, scalar-only routine in every math library, with no
portable SIMD form. A batch pricer that calls scalar `erf` once per
option in a loop isn't meaningfully vectorized at all — the actual
expensive part still runs one lane at a time.

The fix is the standard one real numerical libraries use for exactly
this reason: the Abramowitz & Stegun 7.1.26 rational polynomial
approximation of the normal CDF, with a documented maximum error of
about `1.5e-7` — negligible for pricing purposes, and, critically, *just
arithmetic* (multiplies, adds, one `exp`, no branches on the hot path).
Plain arithmetic vectorizes trivially across SIMD lanes. This is the one
piece of "real" numerical-methods judgment in this milestone, and it's
worth stating plainly: the SIMD version isn't fast because of clever
intrinsics, it's fast because the underlying math was chosen to be
vectorizable in the first place.

## 5. Why `wide`, Not Hand-Written Intrinsics or `std::arch`

This project has generally preferred hand-rolled, dependency-free code
(the C++ order book uses no external libraries, the Python layers stuck
to stdlib until `yfinance`/`ib_async` were genuinely necessary). SIMD is
the exception, for a concrete portability reason: this project's own
dev machine is Apple Silicon (NEON), while a typical deployment target
might be x86_64 (AVX2/AVX512) — hand-written `core::arch` intrinsics
would mean maintaining two separate, architecture-specific
implementations of the same pricing math, or picking one architecture
and quietly not working on the other. `wide` provides portable,
fixed-width lane types (`f64x4`, etc.) that compile to real native SIMD
instructions on each supported architecture (falling back to a
sequential loop only where a platform genuinely lacks a wide-enough
native lane) — one implementation, correct and reasonably fast
everywhere, which matters more here than squeezing out the last cycle
on one specific chip.

```rust
pub fn price_batch_simd(params: &[OptionParams]) -> Vec<Greeks>;
```

Internally: parameters are transposed into lane-major form (all spots
in one `f64x4`, all strikes in another, etc. — "structure of arrays,"
not "array of structures," which is what actually lets the arithmetic
vectorize) and the same closed-form formulas from §3 run once across
four options' worth of lanes at a time instead of once per option.

## 6. Benchmarking

`criterion` (a dev-dependency only — it never ships in `risk_engine`'s
binary) benchmarks three implementations pricing the same batch of
options:

1. **Naive Python** (`risk/bench/naive_black_scholes.py`): a plain,
   loop-based, no-NumPy Python implementation — the honest "what would
   this cost without any of this project's C++/Rust work" baseline.
2. **Naive Rust scalar** (`price_scalar` in a loop): isolates "just
   using a compiled language" from "and also vectorizing it."
3. **SIMD Rust batch** (`price_batch_simd`): the actual deliverable.

The benchmark reports options priced per second (and the derived
per-option latency) for a fixed batch size, with `criterion`'s
statistical confidence intervals — reproducible, not a single noisy
timing run. The specific numbers are hardware-dependent (this is run on
whatever machine builds the project, most likely the same Apple Silicon
laptop the rest of this project was built on) and are reported as
"measured on this machine," not as a universal claim — consistent with
Design Principle 3's "specific... benchmarked and reproducible," not
"fast" as an unfalsifiable adjective.

## 7. Test Cases to Write First

1. Scalar pricer matches known textbook Black-Scholes values (e.g.
   S=100, K=100, r=0.05, σ=0.2, T=1 → call ≈ 10.4506) within a tight,
   documented tolerance.
2. Put-call parity holds for the scalar pricer — `C - P = S - K·e^(-rT)`
   — across a range of random parameter sets. This is a strong,
   reference-table-independent correctness check: it has to hold
   exactly regardless of which specific numbers are plugged in.
3. Greeks sanity bounds: call delta ∈ [0, 1], put delta ∈ [-1, 0], gamma
   and vega non-negative for both, and delta strictly increasing in
   spot price for a call — catches a sign error or swapped formula that
   a pure price-matching test might miss.
4. `price_batch_simd` produces results matching `price_scalar`
   (elementwise, within float tolerance) for the same batch of randomly
   generated option parameters — the core "vectorizing didn't change the
   math" test, and the one that actually matters most here.
5. The polynomial normal CDF approximation matches a trusted exact
   reference (`libm`'s `erf`-based computation, used *only* in this one
   test, never in the pricer itself) within its documented `~1.5e-7` max
   error bound.
6. The benchmark harness runs end to end and produces a report with all
   three implementations' throughput numbers — not a strict pass/fail
   assertion, but part of proving the benchmark itself is wired up and
   reproducible, which the Definition of Done depends on.

## 8. Explicitly Deferred to Later Milestones

- The delta-hedging strategy that actually uses these Greeks to
  rebalance a position (Milestone 8).
- Wiring live pricing calls into `risk_engine`'s fill-processing loop —
  that's a design decision for Milestone 8 to make once it knows what
  the hedging strategy actually needs from the pricer.
- FIX-style message parsing (Milestone 9) — unrelated to this milestone
  despite both living under "Rust depth" in plan.md's roadmap.
- American exercise, dividends, exotic Greeks (§2).

## 9. Definition of Done for Milestone 7

- All 6 test cases in §7 pass.
- A benchmark report (numbers, not just "it runs") comparing naive
  Python, naive Rust scalar, and SIMD Rust batch pricing throughput,
  generated via `criterion`, with the actual measured speedup factors
  stated plainly (e.g. "Nx faster than naive Python, Mx faster than
  naive Rust scalar, measured on [this machine's architecture]").
- The Milestone 1–6 test suites (C++, Python, Rust) still pass
  unmodified — this milestone adds a new, self-contained pricing module
  to the `risk` crate, it doesn't touch the order book, wire protocol,
  risk engine's live loop, or any existing feed.
