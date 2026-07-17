# FIX-Style Message Parser — Design Doc (Milestone 9: Rust Depth)

## 1. Purpose

Per plan.md's Component Responsibilities, a "FIX-style message parser as
a throughput benchmark" — a standalone Rust depth exercise, deliberately
unrelated to Milestone 8's delta-hedging strategy despite both sitting
under "Rust depth" in plan.md's roadmap (Milestone 7 and 8's own design
docs already flagged this explicitly). This milestone exists to
demonstrate parsing a real, industry-standard wire format — FIX
(Financial Information eXchange), the actual protocol real trading
venues and OMS/EMS systems use — rather than this project's own
custom binary protocol, and to prove parsing throughput with the same
honest, measured discipline Milestone 7 established: a number, not an
adjective (Design Principle 3).

This is not wired into `orderbook_server` or `pricing_server` — those
keep using the binary protocol from `docs/socket-service-design-doc.md`
project-wide. FIX parsing lives here as its own, self-contained
capability, on purpose (§9).

## 2. Scope

**In scope:**
- A parser for a deliberately narrow subset of FIX 4.2: tag=value pairs
  delimited by SOH (`0x01`), covering exactly the fields needed to
  represent a `NewOrderSingle` (`35=D`) carrying the same economic
  content this project's own `NewOrder` message does (symbol, side,
  quantity, price, order type, client order id) — grounded in a message
  shape this project already understands, not an arbitrary FIX sample.
- Checksum (tag `10`) and `BodyLength` (tag `9`) validation — FIX's own
  built-in error detection, which this project's own binary protocol
  doesn't have (§4).
- Two parser implementations, benchmarked against each other the same
  honest way Milestone 7 benchmarked scalar vs. SIMD: a naive,
  allocating parser and a zero-copy parser, both living in the same
  `risk` crate as a new `fix` module (§3).
- A minimal FIX message *builder*, but only as benchmark/test fixture
  tooling — not a first-class encoding API (§9).

**Out of scope for this milestone** (see §9):
- FIX session-layer messages (`Logon`, `Heartbeat`, `ResendRequest`,
  `SequenceReset`) — this is a parser for one application-level message
  shape, not a FIX engine.
- Repeating groups, multiple FIX versions, or any tag beyond the
  `NewOrderSingle` subset listed above.
- Wiring this parser into the live pipeline — `orderbook_server` and
  `pricing_server` keep their existing binary protocols unchanged.

## 3. Why the Same `risk` Crate, Not a New One

Rust already hosts three focused concerns in one crate:
`risk_engine`'s position/limit tracking, `pricing`'s Black-Scholes
module, and `pricing_protocol`/`pricing_server`'s socket service. Adding
`risk/src/fix.rs` as a fourth, equally self-contained module continues
that pattern rather than starting a new crate for a capability that has
zero runtime dependency on anything else in this project — matching
plan.md's own framing of Milestones 7–9 as three independent "Rust
depth" exercises sharing a language, not a codebase-wide feature.

## 4. Why FIX's Checksum Matters Here

This project's own binary protocol (`docs/socket-service-design-doc.md`
§4) has no checksum — it doesn't need one, since it runs exclusively
over trusted localhost connections between processes this project
controls. Real FIX messages travel over real networks between
independent counterparties, and the checksum (tag `10`: the sum of every
byte before it, mod 256, formatted as a zero-padded 3-digit string) is
FIX's own defense against exactly the kind of corruption a trusted
local socket never has to worry about. Implementing and testing this
validation isn't busywork — it's the one piece of this format that
reflects a real, different set of assumptions about the transport
underneath it, worth learning by building rather than skipping because
"this project doesn't need it elsewhere."

## 5. Two Parsers, Benchmarked Honestly

```rust
pub struct FixField<'a> {
    pub tag: u32,
    pub value: &'a str,
}

pub fn parse_naive(raw: &[u8]) -> Result<HashMap<u32, String>, FixParseError>;
pub fn parse_zero_copy(raw: &[u8]) -> Result<Vec<FixField<'_>>, FixParseError>;
```

- **`parse_naive`**: splits on SOH, splits each field on `=`, and
  allocates an owned `String` per field value into a `HashMap` — the
  straightforward, "just get it working" implementation.
- **`parse_zero_copy`**: identical validation logic, but every field's
  value is a `&str` slice borrowing directly from the input buffer `raw`
  — no per-field allocation at all, only the one `Vec` holding the
  slices themselves.

Both get benchmarked with `criterion` (already a dev-dependency,
`risk/benches/pricing_bench.rs` established the pattern in Milestone 7),
reporting messages/sec and MB/sec for a batch of generated
`NewOrderSingle` messages. Unlike Milestone 7's SIMD result, this
comparison is not expected to be a coin flip — allocation avoidance is a
well-established, not hardware-dependent, Rust performance pattern —
but it still gets measured and reported honestly rather than assumed,
exactly as Design Principle 3 requires. If it turns out *not* to be a
clear win on measurement, that gets reported plainly too, the same way
Milestone 7's surprising result did.

### 5.1 Measured Results (Apple Silicon, arm64)

4096 generated `NewOrderSingle` messages, ~123 bytes each:

| Implementation | Throughput | MB/sec |
|---|---|---|
| Naive (`HashMap<u32, String>`) | ~654K messages/sec | ~76.7 MB/s |
| Zero-copy (`Vec<FixField>`) | ~1.40M messages/sec | ~164.1 MB/s |

Zero-copy measured **~2.1x faster** than the naive allocating parser —
the expected, unsurprising win predicted above, unlike Milestone 7's
SIMD result. Both numbers are reported regardless, per the same standard:
a real measurement, not an assumption, even when the outcome matches
what was predicted going in.

## 6. Message Shape

```
8=FIX.4.2␁9=<bodylen>␁35=D␁49=<sender>␁56=<target>␁34=<seqnum>␁
52=<sendingtime>␁11=<clordid>␁55=<symbol>␁54=<side>␁38=<orderqty>␁
40=<ordtype>␁44=<price>␁59=<timeinforce>␁10=<checksum>␁
```

(`␁` denoting SOH, `0x01`, shown here for readability.) Tags parsed and
validated: `8` (BeginString), `9` (BodyLength), `35` (MsgType, must be
`D`), `49`/`56` (sender/target — present, not semantically validated),
`34` (sequence number), `52` (sending time, treated as an opaque
string), `11` (client order id), `55` (symbol), `54` (side: `1`=buy,
`2`=sell), `38` (order quantity), `40` (order type: `1`=market,
`2`=limit), `44` (price, present only for limit orders — mirroring this
project's own `ClientOrderMsg.price_ticks` being "ignored but present"
for market orders, per `docs/socket-service-design-doc.md` §4.2), `59`
(time in force, accepted but not interpreted), `10` (checksum).

## 7. Test Cases to Write First

1. A well-formed `NewOrderSingle` (built via the benchmark fixture
   builder from §2) round-trips through both `parse_naive` and
   `parse_zero_copy`, producing the expected tag/value pairs.
2. Checksum validation: flipping a single byte in an otherwise
   well-formed message causes both parsers to reject it
   (`FixParseError`, not a panic); the unmodified message is accepted.
3. `BodyLength` mismatch (a tampered length that doesn't match the
   actual encoded body) is rejected the same way.
4. Malformed input — missing SOH delimiter, a non-numeric tag, a
   required tag absent entirely — each produces a specific, correctly
   distinguishing error variant, not a generic failure or a panic.
5. `parse_naive` and `parse_zero_copy` produce identical field values
   across a range of generated messages — the same "two independent
   implementations must agree" check Milestone 7 used for scalar vs.
   SIMD pricing.
6. The benchmark harness runs end to end and reports real messages/sec
   and MB/sec numbers for both implementations — not a pass/fail
   assertion, but part of proving the benchmark itself is wired up,
   same as Milestone 7's Definition of Done.

## 8. Explicitly Deferred to Later Milestones

- FIX session-layer messages and repeating groups (§2).
- Multiple FIX protocol versions — this milestone commits to one
  `NewOrderSingle`-shaped subset of FIX 4.2, not a general FIX library.
- Wiring FIX parsing into the live pipeline in any way.
- A first-class FIX message *encoder* — the builder used for benchmark
  fixtures stays test/bench-only tooling, not a public API this project
  commits to maintaining.
- Dashboard/polish work (Milestone 10) — unrelated to this milestone.

## 9. Definition of Done for Milestone 9

- All 6 test cases in §7 pass.
- A benchmark report (real numbers, via `criterion`) comparing
  `parse_naive` and `parse_zero_copy` throughput in messages/sec and
  MB/sec, with the actual measured factor stated plainly regardless of
  which direction it points — same standard Milestone 7 held itself to.
- The Milestone 1–8 test suites (C++, Python, Rust) still pass
  unmodified — this milestone adds one new, self-contained module to
  the `risk` crate with zero dependency on the order book, wire
  protocol, risk engine, pricing service, or any strategy code.
