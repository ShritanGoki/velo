# End-to-End Loop on Synthetic Data — Design Doc (Milestone 3: Python Strategy Client)

## 1. Purpose

Prove the full pipeline works as a loop — not just "the server accepts
orders" (Milestone 2) but "a strategy reacts to prices, sends real orders
over the socket, and processes real fills coming back" — before any real
market data enters the picture. Real/live data (Milestones 5–6) is a data
source swap; this milestone is about proving the *loop* itself is sound
first, against a source we fully control (a synthetic random-walk feed),
so a bug can't be blamed on flaky external data.

This milestone adds the Python side of the wire protocol defined in
`docs/socket-service-design-doc.md` §4, plus a synthetic price feed and a
minimal strategy loop. It does not add real data, risk management (that's
the Rust engine, Milestone 4), or backtest analytics beyond a raw trade
log — those come later once the loop itself is proven.

## 2. Scope

**In scope:**
- A synthetic ES price generator (seeded random walk, tick-aligned).
- A Python `OrderBookClient` that speaks the Milestone 2 binary protocol
  directly (no JSON, no third-party client library — same "no
  translation layer" reasoning as the wire format itself).
- A background reader thread so the client can receive Acks/Fills
  asynchronously while the main thread keeps feeding price ticks to the
  strategy — the same problem `test_client.py` sidestepped by only ever
  doing one blocking round-trip at a time.
- A deliberately simple strategy (see §5) whose only job is to prove
  signal → order → fill → log works, not to make money.
- A flat trade log (every order sent + every fill received) as a plain
  list of dicts / CSV — the minimum needed for Milestone 5 to build a
  real backtest report on top of.

**Out of scope for this milestone** (see §9):
- Real historical or live data (Milestones 5–6).
- Risk/position limits (Milestone 4 — this milestone has no P&L or
  exposure tracking at all, on purpose, so it's obvious later which
  bugs belong to the risk engine vs. the loop).
- Sharpe/drawdown/trade statistics (Milestone 5's "first real backtest
  report" — this milestone only needs a raw log, not analysis).
- Reconnection/retry logic if the server drops the connection — assume
  a stable local process pair for now.

## 3. Components

```
SyntheticPriceFeed  ──ticks──►  Strategy  ──orders──►  OrderBookClient  ──wire──►  OrderBookServer (C++)
                                    ▲                        │
                                    └────────fills/acks───────┘
                                        (via a queue, fed by
                                         a background reader thread)
```

All four pieces live in `strategy/` (per plan.md's repository structure)
and run as a single Python process talking to the already-running
`orderbook_server` binary from Milestone 2 over a plain TCP socket.

## 4. Synthetic Price Feed

```python
class SyntheticPriceFeed:
    def __init__(self, start_price_ticks: int, tick_std_dev: float, seed: int):
        ...
    def next_price(self) -> int:
        """Returns the next tick-aligned price via a seeded random walk."""
```

- Prices are generated directly in integer ticks (never floats) — the
  feed exists specifically to drive the socket protocol, which is
  tick-only per the order book's design (§6 of the order book doc), so
  there's no float-to-tick conversion step to get subtly wrong.
- Seeded (`random.Random(seed)`), so a broken run is reproducible.
- Deliberately dumb: a bounded random walk (each step ±0, ±1, or ±2
  ticks), not a realistic price process — realism isn't the point until
  Milestone 5's real data arrives.

## 5. Strategy Interface + Example Strategy

```python
class Strategy(Protocol):
    def on_price(self, price_ticks: int) -> list[OrderRequest]:
        """Called on every synthetic tick. Returns zero or more orders
        to submit (empty list is the common case)."""
```

For this milestone, the concrete strategy is intentionally trivial —
a **fixed-offset quoter**: on every price tick, if it has no resting
orders, place a buy 2 ticks below and a sell 2 ticks above the current
price, each for a fixed size. This is enough to reliably generate both
resting orders *and* crosses (since the synthetic feed will eventually
walk into one of the two quotes), which is exactly the two code paths
Milestone 2's server needs exercised under real usage. A "good" strategy
(mean-reversion, momentum, the eventual delta-hedger) is explicitly a
later concern — Milestones 5 and 8 respectively.

## 6. `OrderBookClient` Design

```python
class OrderBookClient:
    def __init__(self, host: str, port: int): ...

    def submit_limit(self, order_id: int, side: Side, price_ticks: int, qty: int) -> None: ...
    def submit_market(self, order_id: int, side: Side, qty: int) -> None: ...
    def cancel(self, order_id: int) -> None: ...

    def poll_events(self, timeout: float | None = None) -> list[AckEvent | FillEvent]:
        """Drains whatever Ack/Fill events have arrived since the last call."""

    def close(self) -> None: ...
```

- Encodes/decodes the exact byte layouts from
  `docs/socket-service-design-doc.md` §4.2 — this is the one place the
  protocol gets implemented a second time (the first being the C++
  server), so `tests/test_protocol_client.py` (§7) round-trips every
  message type against fixed byte strings to catch the two
  implementations drifting apart.
- Internally: a background thread owns the socket's read side, decodes
  complete messages off it exactly like the C++ server does (read 1 tag
  byte, then that tag's fixed payload size, no partial-message handling
  needed for the same reason it wasn't needed server-side), and pushes
  decoded events onto a thread-safe queue. The main thread's `poll_events`
  just drains that queue — this keeps the strategy loop's main thread
  simple and synchronous even though the socket itself is async from its
  perspective.
- Writes (`submit_limit`/`submit_market`/`cancel`) happen directly on the
  calling thread via a blocking `socket.sendall` — matching the server's
  own assumption that messages are small enough this never meaningfully
  blocks.

## 7. Test Cases to Write First

1. `OrderBookClient` protocol encode/decode round-trip for each message
   type, independent of any real socket — mirrors
   `engine/tests/test_protocol.cpp` on the Python side, and exists
   specifically to catch the two protocol implementations drifting.
2. `SyntheticPriceFeed` with a fixed seed produces the exact same
   sequence of prices on repeated runs (reproducibility check).
3. Fixed-offset quoter: given a scripted sequence of prices fed directly
   to `on_price` (no real feed/socket), verify it quotes once and then
   stays flat (no duplicate quoting) until a fill clears a side.
4. Full loop, real server: start a real `orderbook_server` subprocess,
   run the synthetic feed + strategy + client end-to-end for N ticks,
   and assert the trade log contains at least one fill and that every
   order in the log received a matching Ack.
5. Client survives an out-of-order arrival: submit two orders back to
   back before draining events, verify `poll_events` returns both
   Acks (and any Fills) in the order the server sent them.
6. Clean shutdown: closing the client while the reader thread is
   blocked on `recv` doesn't hang the process (verifies the background
   thread is a daemon thread / responds to socket close).

## 8. Trade Log Format

A flat, append-only list of dicts (also dumpable to CSV), one row per
event:

```python
{"event": "order_sent", "order_id": ..., "side": ..., "price_ticks": ..., "qty": ..., "ts": ...}
{"event": "ack",        "order_id": ..., "status": ...,                            "ts": ...}
{"event": "fill",       "resting_id": ..., "incoming_id": ..., "price_ticks": ..., "qty": ..., "ts": ...}
```

No derived statistics here (no P&L, no Sharpe) — Milestone 5 builds the
actual backtest report on top of this log once real data replaces the
synthetic feed, so the log format needs to be the same regardless of
which feed produced it.

## 9. Explicitly Deferred to Later Milestones

- Real historical (`yfinance`) or live (IBKR) data feeds (Milestones
  5–6) — `SyntheticPriceFeed` and any future real feed should be
  swappable behind the same `next_price()`-shaped interface, but
  building that adapter now would be speculative.
- Risk/position tracking and the Rust risk engine entirely (Milestone 4).
- Backtest statistics: Sharpe, drawdown, per-trade P&L (Milestone 5).
- Reconnection, multiple simultaneous strategies, or multi-instrument
  support.

## 10. Definition of Done for Milestone 3

- All 6 test cases in §7 pass.
- A `run_synthetic_session.py` script runs the full loop for a
  configurable number of ticks against a locally running
  `orderbook_server`, and prints a summary (orders sent, fills received,
  final trade log length) at the end.
- The Milestone 1 and Milestone 2 test suites still pass unmodified —
  this milestone is a new client against the existing server, not a
  change to engine behavior.
