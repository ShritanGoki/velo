# Delta-Hedging Strategy — Design Doc (Milestone 8: Depth)

## 1. Purpose

This is, per plan.md's Component Responsibilities, "the piece that ties
all three languages together through an actual quant concept rather than
just data-passing": a Python strategy holds a (simulated) ES options
position, uses the Rust pricing engine's live Greeks (Milestone 7) to
compute that position's delta as the underlying price moves, and trades
ES futures through the existing C++ matching engine to keep the combined
position delta-neutral. Every previous milestone either passed data
through unchanged (feeds, fills) or kept the three languages' concerns
cleanly separate (risk tracking, pricing). This is the first milestone
where a trading decision genuinely depends on a cross-language call —
Python asking Rust "what's this options book's delta right now" and
acting on the answer.

## 2. Scope

**In scope:**
- `pricing_server`: a small, new Rust binary (`risk/src/bin/pricing_server.rs`)
  exposing Milestone 7's `price_scalar` over a synchronous
  request/response socket protocol.
- A `DeltaHedger` strategy (Python) holding a fixed, configured options
  book, computing its aggregate delta via `pricing_server` on every
  price tick, and rebalancing an ES futures position toward
  delta-neutral through the existing `orderbook_server` whenever the
  hedge error exceeds a configured band.
- Hedge-effectiveness backtest metrics layered on top of Milestone 5's
  `BacktestReport`: mean absolute hedge error, rehedge turnover, and
  hedged-vs-unhedged P&L variance reduction.

**Out of scope for this milestone** (see §9):
- Actually trading options through the matching engine — `OrderBook`
  is single-instrument (ES futures) by design since Milestone 1; the
  options position is a held-fixed input to the hedging problem, not
  something this system trades. Real desks acquire options positions
  through other means; this project only hedges one.
- Gamma-hedging, vega-hedging, or any Greek beyond delta driving the
  rehedge decision.
- Dynamic changes to the options book mid-session (adding/removing
  option legs while a session runs).

## 3. Why a Separate `pricing_server`, Not FFI or Embedding in `risk_engine`

Three ways Python could reach Milestone 7's pricing code, and why the
chosen one won:

- **PyO3/maturin FFI bindings** would be the fastest option and the
  most direct "Python calling into Rust," but it's a new build toolchain
  this project has never needed (every other cross-language boundary so
  far is a socket, not a compiled extension module) — a real cost for a
  milestone whose actual point is the hedging logic, not build-system
  novelty.
- **Extending `risk_engine` itself** to also serve price requests would
  conflate two genuinely separate concerns: `risk_engine` is a
  fill-subscribing *client* of `orderbook_server` (Milestone 4); making
  it also *listen* for pricing requests turns one small, single-purpose
  process into two unrelated services sharing a binary, for no benefit.
- **A new, narrowly-scoped `pricing_server` binary**, reusing
  `risk_engine::pricing` as a library (already exposed via
  `risk/src/lib.rs`) behind a second `[[bin]]` target in the same crate,
  keeps one process doing one job — consistent with every other
  component in this project (`orderbook_server` matches orders,
  `risk_engine` tracks risk, `pricing_server` prices options) — and
  reuses the plain-socket-plus-binary-protocol IPC pattern plan.md's
  Tech Stack already commits to project-wide, rather than introducing a
  second IPC mechanism alongside it.

**`price_scalar`, not `price_batch_simd`:** each request here is exactly
one option (or a handful, for a small options book), never a large
batch — there's no batching opportunity for `price_batch_simd` to
exploit even if it wanted one. And Milestone 7's own honest benchmark
result (§6.1 of `docs/options-pricing-design-doc.md`) already found
scalar faster than SIMD on this hardware — so using `price_scalar` here
isn't a shortcut, it's applying that milestone's finding directly rather
than reaching for the "advanced-sounding" option out of habit.

## 4. The Options Position Model

`DeltaHedger` is configured with a fixed list of option legs — a
simulated book the strategy is assumed to already hold, as if acquired
through some other desk or process this project doesn't model:

```python
@dataclass(frozen=True)
class OptionLeg:
    strike_ticks: int
    time_to_expiry_years: float
    vol: float
    is_call: bool
    quantity: int  # + long, - short; in ES-option contracts

    def delta(self, spot_ticks: int, rate: float, pricing_client: "PricingClient") -> float:
        ...
```

ES options are physically settled into ES futures and share the same
contract multiplier, so an option's delta already *is* its ES-futures
equivalent exposure per contract — no unit conversion needed beyond
`sum(leg.delta(...) * leg.quantity for leg in book)`. This clean 1:1
relationship is a genuine, non-simplified fact about ES options, not a
convenient assumption this design is quietly leaning on.

## 5. `pricing_server` Wire Protocol

A new, deliberately smaller protocol than `orderbook_server`'s (no Ack,
no Cancel — pure synchronous request/response, one option per call):

```
0x20  PriceRequest   client -> server (41 bytes: spot_ticks:i64,
                      strike_ticks:i64, rate:f64, vol:f64,
                      time_to_expiry:f64, is_call:u8)
0x21  PriceResponse  server -> client (48 bytes: price:f64, delta:f64,
                      gamma:f64, vega:f64, theta:f64, rho:f64)
```

Same framing rule as `orderbook_server` (1-byte tag, fixed payload,
big-endian) for consistency, but its own tag namespace and its own port
— this is a genuinely different service, not an extension of the order
matching protocol.

## 6. `DeltaHedger` Strategy

```python
class DeltaHedger:
    def __init__(self, options_book: list[OptionLeg], rate: float,
                 hedge_band_contracts: int, pricing_client: PricingClient):
        ...

    def on_price(self, price_ticks: int) -> list[StrategyAction]:
        target_futures_position = -sum(
            leg.delta(price_ticks, self._rate, self._pricing_client) * leg.quantity
            for leg in self._options_book
        )
        hedge_error = self._current_futures_position - round(target_futures_position)

        if abs(hedge_error) <= self._hedge_band_contracts:
            return []  # within tolerance - don't rehedge every single tick

        side = Side.SELL if hedge_error > 0 else Side.BUY
        return [OrderRequest(next(self._ids), side, OrderType.MARKET, 0, abs(hedge_error))]
```

`hedge_band_contracts` matters for the same reason `FixedOffsetQuoter`'s
repricing wasn't unconditional either: continuously rehedging to exactly
zero on every tick would generate unrealistic order flow and mask the
actual metric of interest (§7's hedge error) behind pure noise. A real
desk hedges within a tolerance band, not to the theoretical exact
target, and that's modeled explicitly here rather than glossed over.

`_current_futures_position` is tracked the same aggressor-side way
`risk/src/position.rs` and `orderbook_client/backtest_report.py` already
do (Milestones 4 and 5) — the third implementation of that same simple
accounting, for the same reason the second one was: a different
consumer (a live rehedging decision, not offline analytics or online
risk enforcement) in a different part of the pipeline.

## 7. Hedge-Effectiveness Backtest Metrics

Extends `orderbook_client/backtest_report.py`'s `BacktestReport`
(Milestone 5) rather than replacing it — Sharpe/drawdown/win-rate still
apply to the futures P&L; these are additional, hedging-specific numbers:

```python
@dataclass(frozen=True)
class HedgeReport:
    mean_abs_hedge_error_contracts: float
    rehedge_count: int
    hedged_pnl_variance_ticks2: float
    unhedged_pnl_variance_ticks2: float  # options book alone, no futures hedge at all
    variance_reduction_pct: float         # 1 - hedged/unhedged, the actual "did this help" number
```

`variance_reduction_pct` is the metric that actually answers "did
hedging work" — a hedge that trades constantly but doesn't reduce P&L
variance relative to holding the naked options book would be worse than
useless (all the transaction/order flow, none of the risk reduction),
and this is the number that would catch that.

## 8. Test Cases to Write First

1. `pricing_server` round-trip: a `PriceRequest` for known option
   parameters gets back a `PriceResponse` matching `price_scalar`
   exactly (same values, same process, just over a socket).
2. `OptionLeg.delta()` aggregation: a book of multiple legs (mixed
   calls/puts, mixed long/short) produces the correct signed sum,
   verified against manually computed expected deltas for each leg.
3. `DeltaHedger` stays flat (no orders) when hedge error is within the
   configured band, and emits exactly one correctly-sized, correctly-
   sided order when it isn't — scripted price sequence, known expected
   target position at each step.
4. Full loop: `DeltaHedger` against a real `orderbook_server` and a real
   `pricing_server`, over a scripted price path with a large synthetic
   options book, confirming futures fills actually move
   `_current_futures_position` toward the target and hedge error stays
   bounded by the configured band over the session.
5. `HedgeReport`: a scripted scenario with a known, hand-computed
   variance reduction (an options book with a clearly-offsetting futures
   hedge vs. the same book with no hedge at all) confirms
   `variance_reduction_pct` lands where hand computation says it should.

## 9. Explicitly Deferred to Later Milestones

- Trading options directly (permanently out of scope — single-instrument
  `OrderBook`, Milestone 1).
- Gamma/vega-aware rehedging, or any Greek beyond delta driving the
  rehedge decision.
- Dynamic/changing options books mid-session.
- FIX-style message parsing (Milestone 9) and the dashboard/polish work
  (Milestone 10) — unrelated to this milestone.

## 10. Definition of Done for Milestone 8

- All 5 test cases in §8 pass.
- A backtest run (against real or fixture historical ES data, per
  Milestone 5's existing infrastructure) produces both a
  `BacktestReport` and a `HedgeReport`, with a positive, plausible
  `variance_reduction_pct` demonstrating the hedge actually reduced P&L
  variance relative to the unhedged options book.
- The Milestone 1–7 test suites (C++, Python, Rust) still pass
  unmodified — this milestone adds a new Rust binary and a new Python
  strategy, it doesn't change the order book, wire protocol, risk
  engine, existing feeds, or the pricing module's own logic.
