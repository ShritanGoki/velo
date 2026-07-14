# Risk Engine Joins the Pipeline — Design Doc (Milestone 4: Rust P&L/Position/Halts)

## 1. Purpose

Add the Rust risk engine described in plan.md's Component Responsibilities:
it observes every fill the matching engine produces, maintains the live
position and P&L for the strategy's account, and enforces a configurable
exposure limit — halting and automatically flattening the position if
that limit is breached. This is the first of three Rust milestones
(pricing/Greeks and the FIX parser come later, Milestones 7 and 9); this
one is deliberately just "watch fills, track risk, act on breach" — no
options, no Greeks, no message parsing yet.

Unlike Milestones 2–3, this milestone requires two small, explicitly
justified extensions to the C++ engine and its wire protocol (§3) — the
existing design doesn't give a third-party observer enough information to
do its job, and it's better to fix that gap deliberately than to work
around it in Rust.

## 2. Scope

**In scope:**
- Two minimal protocol/engine extensions (§3) needed for *any* observer
  (not just the order's own counterparties) to reconstruct trade
  direction and receive a copy of the fill stream.
- A Rust `risk_engine` binary: connects to `orderbook_server` as a
  read-mostly client, tracks net position and realized/unrealized P&L,
  and enforces one configurable limit (max absolute position).
- On breach: log a halt event, then submit a single flattening order
  (opposite side, sized to bring position back to zero) — not an attempt
  to enumerate and cancel every possibly-related resting order (§5
  explains why that's the right scope boundary here, not a shortcut).
- A structured, append-only risk state log for Milestone 10's dashboard
  to eventually read.

**Out of scope for this milestone** (see §11):
- Options pricing, Greeks, SIMD (Milestone 7).
- FIX-style message parsing (Milestone 9).
- Cancelling specific resting orders by id — the engine doesn't have
  full order-book visibility (only fill visibility), and flattening via
  a corrective trade is the practical equivalent for exposure control
  without that visibility (§5).
- Multiple simultaneously-monitored strategy accounts — one risk engine
  watches one book, one account, for now.

## 3. Required Engine/Protocol Extensions

Milestones 2–3 only ever needed a fill's two direct counterparties to
understand it, and each already knew their own side (they'd just
submitted the order). A risk engine is a third party watching *someone
else's* trades — it needs enough on the wire to reconstruct what
happened without that context.

### 3.1 `Fill` gains a `resting_side` field

```cpp
// engine/include/orderbook/order.hpp
struct Fill {
    uint64_t resting_order_id;
    uint64_t incoming_order_id;
    Side     resting_side;     // NEW: which side the resting order was on
    int64_t  price_ticks;
    uint32_t quantity;
    uint64_t timestamp;
};
```

Without this, a `Fill` is symmetric on the wire — `resting_order_id` and
`incoming_order_id` alone don't say whether the resting order was a buy
or a sell, only the two counterparties (who already knew their own side
when they submitted) could tell. `matchBuy`/`matchSell` in
`engine/src/order_book.cpp` already know this for free (a `Fill`
generated inside `matchBuy` always has a resting side of `Sell`, and vice
versa) — it's a one-line addition at each fill-construction site, not new
logic. The wire `FillWireMsg` (`engine/include/orderbook/protocol.hpp`)
gains one byte accordingly (37 bytes instead of 36); this is a wire
format change, so the Milestone 2 C++ tests and the Milestone 3 Python
protocol module both need their fixed-size expectations updated —
expected and mechanical, not a design risk.

### 3.2 A `SubscribeFills` message and fan-out broadcast

```
0x03  SubscribeFills   client -> server, empty payload
```

A connection that sends this is marked as a **fill subscriber**: from
then on, every `Fill` generated anywhere in the book is also sent to it,
in addition to the existing owner-routing from
`docs/socket-service-design-doc.md` §5 (deduplicated — a subscriber that
also happens to be a counterparty on a given fill only receives it
once). Subscribers never submit `NewOrder`; they can still send `Cancel`
(useful for the flattening/halt path in §5, and already unauthenticated
by order ownership — see that doc's error-handling table — so no server
change is needed there).

This is the smallest change that gives a third party the same
information both counterparties already had. It does *not* give
subscribers visibility into resting orders that haven't traded yet
(§5 explains why that's an acceptable boundary, not an oversight).

## 4. Position & P&L Model

```rust
struct PositionTracker {
    net_position: i64,       // + long, - short, in contract units
    realized_pnl_ticks: i64, // cumulative, in ticks * $-per-tick terms
    last_trade_price_ticks: i64,
}

impl PositionTracker {
    fn on_fill(&mut self, resting_side: Side, quantity: u32, price_ticks: i64) {
        // The monitored account's own fills are exactly the ones where
        // one of the two order ids falls in its known id range (§8).
        // The trade's side, from the monitored account's perspective, is
        // whichever of {resting_side, opposite(resting_side)} its own
        // order id was on.
        ...
    }
}
```

- **Realized P&L**: updated on every fill that closes or reduces
  exposure (standard running weighted-average-cost accounting);
  increasing a position doesn't realize anything yet, it just moves
  `net_position` and updates the average entry price used later.
- **Unrealized P&L**: `net_position * (last_trade_price_ticks -
  average_entry_price_ticks)`. Using the last observed trade price as
  the mark is a deliberate simplification — there's no independent quote
  feed to mark against until Milestones 5–6 bring in real data, and
  marking against your own last fill is the honest approximation
  available right now, not a hidden inaccuracy the doc is glossing over.
- Everything here operates in integer ticks, same reasoning as
  `docs/orderbook-design-doc.md` §6 — no floats until a display/logging
  boundary.

## 5. Risk Limits and Halt Behavior

One configurable limit for this milestone: `max_abs_position`. On every
fill, after updating `PositionTracker`, check
`abs(net_position) > max_abs_position`.

**On breach:**
1. Log a halt event (§6) — this always happens, unconditionally, so a
   breach is never silent even if the flatten step below fails.
2. Submit a single `Market` order in the opposite direction, sized to
   bring `net_position` back to exactly zero.

**Why flattening instead of "cancel every open order":** a resting order
contributes zero to realized or unrealized exposure until it fills — the
risk engine's fill-only visibility (§3.2 gives it fills, not the full
order book) means it doesn't reliably know which ids are still resting
vs. already gone, so trying to cancel-by-id from partial information
would be guessing. A market order that immediately flattens the
*current, real* exposure is both simpler to implement correctly and, for
pure exposure control, the more directly effective action — it
addresses the risk that already exists rather than the risk that might
exist if some unknown resting order eventually fills too. Cancelling
specific stale resting orders is a real gap (documented in §11), but the
right fix for it is giving the risk engine full order-flow visibility
(an `OrderAccepted` broadcast alongside fills), which is a bigger change
than this milestone's fill-only extension — better done deliberately
later than half-built now.

The risk engine's own flattening orders flow back through the same
subscribed fill stream, so its own corrective trade updates
`net_position` exactly like any other fill — no special-cased feedback
loop needed.

## 6. Risk State Log

Append-only, one line per event, written as JSON Lines (simple to parse
from Python in Milestone 10, no schema migration concerns since every
line is independent):

```json
{"event": "position_update", "net_position": 30, "realized_pnl_ticks": 120, "unrealized_pnl_ticks": -15, "ts": 1730000000.123}
{"event": "halt", "net_position": 105, "max_abs_position": 100, "action": "flatten_market_order", "order_id": 1000000001, "ts": 1730000012.456}
```

No Sharpe/drawdown here either (same reasoning as the Milestone 3 trade
log) — this is the risk engine's own record of what it saw and did, not
backtest analytics.

## 7. Rust Crate Structure

```
risk/
├── Cargo.toml
└── src/
    ├── main.rs        # connects, subscribes, runs the read loop
    ├── protocol.rs     # wire format — mirrors engine/include/orderbook/protocol.hpp
    ├── position.rs     # PositionTracker (§4)
    ├── limits.rs        # breach check + flatten-order construction (§5)
    └── risk_log.rs      # JSON Lines writer (§6)
```

`protocol.rs` is the third from-scratch implementation of the same wire
format (C++, Python, now Rust). Each implementation gets its own
round-trip unit tests, same pattern as
`engine/tests/test_protocol.cpp` and the Python equivalent — this is the
one place, across the whole project, where the same byte layout is
implemented three times, so it's also the one place a shared, checked-in
set of fixed test byte vectors (`docs/wire-protocol-test-vectors.json` or
similar) pays for itself: each language's test suite decodes the same
vectors and asserts identical field values, catching any of the three
implementations drifting from the other two directly instead of only
indirectly (via integration tests failing later, with a less obvious
cause).

## 8. Order ID Namespacing (a documented simplification)

The matching engine has one global id space per `docs/orderbook-design-
doc.md` §3 (`OrderBook` rejects duplicate ids outright). With two
independent clients (strategy, risk engine) both submitting orders, id
collision is a real hazard. For this milestone: the strategy uses ids
starting from 1 (per `strategy/orderbook_client/strategy.py`'s
`itertools.count(1)`), and the risk engine's flattening orders use ids
starting from `1_000_000_000`. This is a coordination convention, not an
enforced invariant — a real system would use exchange-assigned or UUID-
derived ids specifically to make this class of bug structurally
impossible. Documented here as a known simplification appropriate for a
single-strategy, single-risk-engine demo system, not a gap to silently
carry forward if a second strategy client is ever added.

## 9. Test Cases to Write First

1. `Fill` round-trip in all three protocol implementations includes
   `resting_side` correctly, and the fixed test vectors (§7) match
   across all three.
2. `SubscribeFills`: a subscriber connection receives a fill it wasn't a
   counterparty to; a non-subscriber connection does not.
3. `PositionTracker`: a scripted sequence of fills (opening long,
   partial close, re-opening short) produces the expected
   `net_position` and `realized_pnl_ticks` at each step.
4. Breach triggers exactly one flattening market order sized to return
   `net_position` to zero, and exactly one halt log line.
5. The flattening order's own resulting fill is correctly folded back
   into `net_position` (verifying the no-special-case feedback loop in
   §5 actually holds).
6. Full loop: run `strategy/run_synthetic_session.py` against a real
   `orderbook_server` with `risk_engine` subscribed and a deliberately
   low `max_abs_position`; assert at least one halt event appears in the
   risk log and `net_position` returns to (or near) zero afterward.

## 10. Explicitly Deferred to Later Milestones

- Full order-flow visibility (`OrderAccepted`/`CancelConfirmed`
  broadcasts) and true force-cancel-by-id — noted in §5 as the real fix
  for a real gap, deferred because it's a bigger protocol change than
  this milestone's scope.
- Options pricing, Greeks, SIMD (Milestone 7).
- FIX-style parsing (Milestone 9).
- Multiple risk limit types beyond max absolute position (e.g.
  notional, per-tick loss velocity) — one limit, proven correct, before
  more.

## 11. Definition of Done for Milestone 4

- All 6 test cases in §9 pass.
- `Fill`'s new field and the `SubscribeFills` message are implemented in
  the C++ engine, and the Milestone 1–2 test suites still pass with
  their expectations updated for the one-byte wire size change (not
  otherwise modified).
- `risk_engine` runs standalone against a live `orderbook_server`,
  correctly tracks position/P&L for a full synthetic session, and
  demonstrably halts + flattens when a low limit is deliberately
  configured to be breached.
- The risk state log is inspectable (plain JSON Lines) after a session
  ends.
