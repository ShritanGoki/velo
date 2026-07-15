# Live IBKR Data — Design Doc (Milestone 6)

## 1. Purpose

Replace `HistoricalPriceFeed` (Milestone 5) with a feed backed by real,
live ES futures quotes from an Interactive Brokers **paper trading**
account via `ib_insync`, per plan.md's Data Sources section: "real live
ES futures quotes, zero capital risk." Real order routing with real
capital stays explicitly out of scope for this entire project (plan.md,
"Data Sources") — this milestone only changes where prices come from,
not what happens with capital.

If Milestone 5 did its job, this should be a small milestone: `feed →
strategy → OrderBookClient → orderbook_server` doesn't care where ticks
come from, as long as whatever's on the left implements
`next_price() -> int`. The interesting design work here isn't the
strategy loop (unchanged) — it's bridging IBKR's async, event-driven,
effectively-unbounded quote stream into that same simple synchronous
contract, and figuring out how to test any of this without a live
brokerage connection.

## 2. Scope

**In scope:**
- `LiveIBKRFeed`: same `next_price() -> int` contract as
  `SyntheticPriceFeed` and `HistoricalPriceFeed`, backed by a live
  `ib_insync` tick subscription.
- An injectable IB client seam (mirroring `HistoricalPriceFeed`'s
  injectable `Downloader` from Milestone 5), so the entire test suite
  runs against a fake client and never needs a real TWS/IB Gateway
  connection.
- A `duration_seconds` cutoff, encapsulated entirely inside the feed
  (not leaked into `session_runner.py`), so a live session ends the same
  way a historical one does: `next_price()` raises `StopIteration`, and
  the already-existing loop in `orderbook_client/session_runner.py`
  (Milestone 5) handles it with zero changes.
- Explicit handling of an IBKR-side disconnect as another
  `StopIteration` case, not a crash.

**Out of scope for this milestone** (see §9):
- Contract rollover (front-month ES expiring mid-session) — one fixed,
  explicitly-specified contract per session.
- Reconnection/retry after a dropped connection.
- Real-time data entitlement management beyond documenting the default
  (§5).
- Anything resembling real order routing — this milestone only ever
  reads quotes from IBKR; `OrderBookClient` keeps sending orders to the
  same local `orderbook_server` it always has.

## 3. Why a Feed, Not a New Pipeline Stage

It would be easy to over-scope this milestone into "integrate IBKR" as
a new architectural component. It isn't one: `LiveIBKRFeed` is a fourth
implementation of the exact interface `SyntheticPriceFeed` and
`HistoricalPriceFeed` already satisfy, living in
`strategy/orderbook_client/` next to them. Nothing else in the pipeline
— the strategy, the wire protocol, the matching engine, the risk engine
— needs to know or care that prices are now real and live instead of
historical. That's the payoff of having treated the feed as a narrow,
swappable seam since Milestone 3 explicitly called this out as future
work.

## 4. Bridging Async IBKR Events into Synchronous `next_price()`

`ib_insync` is built on `asyncio` and delivers quotes via callbacks
(`ib.pendingTickersEvent`), not a pull-based API — the opposite shape of
`next_price()`. The bridge:

```python
class LiveIBKRFeed:
    def __init__(self, contract, ib_client, duration_seconds: float):
        self._queue: queue.Queue[float] = queue.Queue()
        self._deadline = time.monotonic() + duration_seconds
        self._ib = ib_client
        self._ib.subscribe(contract, on_tick=self._queue.put)

    def next_price(self) -> int:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise StopIteration("live session duration elapsed")
        try:
            price = self._queue.get(timeout=remaining)
        except queue.Empty:
            raise StopIteration("live session duration elapsed") from None
        return price_to_ticks(price)
```

This is the same shape as `OrderBookClient`'s background-reader-thread
pattern from Milestone 3 (`orderbook_client/client.py`) — a callback-driven
source feeding a thread-safe queue, with the consumer-facing method
doing a bounded blocking read. Reusing a pattern already proven correct
here is deliberate, not incidental.

`ib_client` is the injectable seam mentioned in §2: a thin protocol
(`subscribe(contract, on_tick)`) that the real `ib_insync`-backed
implementation satisfies, and that tests satisfy with a fake that fires
scripted ticks synchronously — no real connection, no `asyncio` event
loop, no TWS/IB Gateway required to run the test suite.

## 5. Contract and Market Data Type

- **Contract**: a single, explicitly-specified ES future (e.g.
  `Future('ES', '202506', 'CME')`), qualified once via `ib_client` at
  feed construction. No auto-roll logic — if the session runs past
  expiry, that's a documented limitation (§9), not a hidden bug.
- **Market data type**: IBKR paper trading accounts default to delayed
  (15-20 minute) data unless linked to a funded account with live data
  entitlements. This milestone defaults to delayed data
  (`reqMarketDataType(3)` in `ib_insync` terms) specifically so the
  system runs for anyone with just a paper account, and documents this
  explicitly rather than silently behaving differently depending on the
  operator's account setup. Real-time entitlements are an opt-in
  configuration switch, not a requirement.

## 6. Session Runner: No Changes Needed

This is worth stating plainly because it's the actual validation that
Milestone 5's design held up: `orderbook_client/session_runner.py`'s
`run_strategy_loop` already handles a feed that raises `StopIteration`
mid-session (built for `HistoricalPriceFeed`'s finite series). A live
feed hitting its `duration_seconds` cutoff, or losing its IBKR
connection, is the same code path. If this milestone had needed to
touch `session_runner.py`, that would mean the feed abstraction wasn't
as clean as Milestone 3/5 claimed — it doesn't, so it wasn't.

## 7. Testing Strategy (No Live Account Required)

Every previous milestone's "test cases to write first" assumed a
process this project fully controls (a local `orderbook_server`, a
local `risk_engine`). IBKR is the first dependency this project cannot
spin up programmatically — there's no way to launch a TWS/IB Gateway
instance in a test run the way `ServerProcess` launches
`orderbook_server`. The entire automated test suite therefore runs
against the fake `ib_client` from §4, and a real paper-account run is
a separate, manual verification step (§10) — a deliberate, documented
exception to this project's usual "test cases to write first" pattern,
not a gap.

## 8. Test Cases to Write First

1. Fake `ib_client` fires a scripted sequence of tick prices;
   `next_price()` returns the tick-converted values in order (reusing
   `price_to_ticks` from Milestone 5 unchanged — this is the second real
   float source in the project, and it goes through the exact same,
   already-tested conversion boundary).
2. Duration cutoff: with a short `duration_seconds` and no ticks
   arriving, `next_price()` raises `StopIteration` once the deadline
   passes, rather than blocking forever.
3. Simulated disconnect: the fake client signals a disconnect;
   `next_price()` raises `StopIteration` rather than raising some other
   exception or hanging.
4. A full `run_strategy_loop` pass with `LiveIBKRFeed` wired to the fake
   client and a real `orderbook_server`, confirming the unmodified
   Milestone 5 session runner handles this feed exactly like
   `HistoricalPriceFeed` (same assertion style as
   `test_historical_integration.py`).
5. `price_to_ticks` is exercised with realistic live-tick-style floats
   (e.g. a bid/ask midpoint that isn't tick-aligned) to catch any
   assumption that crept in from historical data happening to be
   tick-aligned more often than live quotes are.

## 9. Explicitly Deferred to Later Milestones

- Contract rollover across expiries.
- Reconnection/retry after a dropped IBKR connection — for now, a drop
  just ends the session cleanly via `StopIteration`.
- Real-time (vs. delayed) market data as a first-class configuration
  path beyond documenting the switch exists.
- Anything resembling live order routing with real capital — explicitly
  and permanently out of this project's scope (plan.md, Data Sources).

## 10. Definition of Done for Milestone 6

- All 5 automated test cases in §8 pass against the fake IB client, no
  real IBKR connection required.
- A manual verification run against a real IBKR paper account (TWS or
  IB Gateway running locally, API access enabled) successfully streams
  live ES quotes through the full pipeline — strategy, `orderbook_server`,
  and `risk_engine` all running unmodified against real data — and this
  run (with actual output) is what gets reported as this milestone's
  live demo, since it can't be part of the automated suite.
- The Milestone 1–5 test suites (C++, Python, Rust) still pass
  unmodified — this milestone adds a new feed, it doesn't change the
  engine, protocol, risk engine, or session runner.
