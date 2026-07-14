# Real Historical Data & First Backtest Report — Design Doc (Milestone 5)

## 1. Purpose

Replace `SyntheticPriceFeed` (`strategy/orderbook_client/feed.py`,
Milestone 3) with real ES futures history pulled via `yfinance`, and
produce the project's first real backtest report — Sharpe ratio, max
drawdown, and trade-level statistics — instead of the raw
order/ack/fill log Milestone 3 stopped at. This is the pipeline's first
contact with real market data (plan.md's Data Sources section:
"historical replay... used to prove the pipeline works against real (if
delayed/lower resolution) market history before adding live
complexity"), and the first time real floating-point prices enter a
system that has, up to now, only ever generated ticks directly.

## 2. Scope

**In scope:**
- `HistoricalPriceFeed`: loads cached ES history and exposes the exact
  same `next_price() -> int` shape as `SyntheticPriceFeed`, so
  `run_synthetic_session.py`'s strategy-loop plumbing needs zero changes
  to run against it — swapping the feed was the explicit design goal
  `docs/synthetic-loop-design-doc.md` §9 left for this milestone.
- A price-to-ticks conversion boundary, and tests proving it's the
  *only* place a float touches this pipeline (§4).
- A local on-disk cache of the downloaded history, so backtests are
  reproducible and the test suite never depends on network access (§5).
- `BacktestReport`: Sharpe ratio, max drawdown, win rate, and per-trade
  P&L computed from a completed `TradeLog` (§6).
- `run_historical_backtest.py`, mirroring `run_synthetic_session.py`,
  ending in a printed (and optionally CSV-dumped) report instead of just
  raw counts.

**Out of scope for this milestone** (see §9):
- Live IBKR data (Milestone 6).
- Any strategy sophistication beyond what Milestone 3 already built —
  this milestone is about the data and the report, not a better
  strategy.
- Session-boundary handling (overnight gaps, holiday closures) in the
  historical series — treated as a documented simplification (§4).

## 3. Data Acquisition

`yfinance`'s continuous front-month ES futures ticker (`ES=F`) is
downloaded once per requested date range and window, then cached to
`strategy/data/<ticker>_<interval>_<start>_<end>.csv` — a plain OHLCV
CSV, not a database, matching the project's "no infrastructure beyond
what's needed" bias. Every subsequent run (and the entire test suite)
reads the cache; a network call only happens on a genuine cache miss.
This isn't just a performance nicety — it's what makes backtests
reproducible and keeps `strategy/tests/` from depending on Yahoo
Finance being reachable or returning identical data twice.

`yfinance` returns whatever bar resolution it has for the requested
range (typically daily bars for older history, hourly for recent data)
— matching plan.md's own acknowledged trade-off of "delayed/lower
resolution" history in exchange for real prices. No attempt is made to
synthesize finer intraday structure than what's actually returned.

## 4. The Float-to-Tick Boundary

`docs/orderbook-design-doc.md` §6 established the rule this project has
followed ever since: price is an integer count of ticks internally,
floats only at a display/logging boundary. `SyntheticPriceFeed`
satisfied this for free — it generated ticks directly and never saw a
float. Real market data is exactly the opposite: `yfinance` returns
decimal prices, so this milestone is the first place the boundary
actually has to be implemented, not just assumed.

```python
TICK_SIZE = 0.25  # ES: 0.25 index points = 1 tick ($12.50/tick)

def price_to_ticks(price: float) -> int:
    return round(price / TICK_SIZE)

def ticks_to_price(ticks: int) -> float:
    return ticks * TICK_SIZE
```

`HistoricalPriceFeed.next_price()` calls `price_to_ticks` exactly once,
at the moment a bar's price is pulled from the cached series, and
returns a plain `int` from then on — identical to what
`SyntheticPriceFeed` always returned. Every downstream consumer
(`FixedOffsetQuoter`, `OrderBookClient`, the wire protocol, the matching
engine) is therefore unaware anything changed; this is exactly the
payoff of having kept ticks as the lingua franca since Milestone 1.
`round()` rather than truncation matters here specifically because ES
prices from a real feed won't land exactly on tick boundaries the way
synthetic ticks always did by construction — rounding to the nearest
tick is the correct, standard behavior (truncating would introduce a
small systematic downward bias on every single price).

**Documented simplification:** the historical series is fed as one
continuous stream of ticks regardless of session boundaries (overnight
gaps, holiday closures). A real gap might look like an enormous
single-tick jump to `FixedOffsetQuoter`, which doesn't know or care —
it'll just reprice, possibly generating an unrealistic cross. This is
an accepted rough edge for "first backtest report," not something this
milestone tries to solve.

## 5. `HistoricalPriceFeed`

```python
class HistoricalPriceFeed:
    def __init__(self, ticker: str, start: str, end: str, interval: str, cache_dir: str):
        self._prices: list[int] = self._load_or_download(ticker, start, end, interval, cache_dir)
        self._index = 0

    def next_price(self) -> int:
        """Same contract as SyntheticPriceFeed.next_price(): returns the
        next tick-aligned price. Raises StopIteration once the cached
        series is exhausted (a real feed is finite, unlike the synthetic
        one's unbounded random walk)."""
        if self._index >= len(self._prices):
            raise StopIteration("historical feed exhausted")
        price = self._prices[self._index]
        self._index += 1
        return price
```

The one real interface difference from `SyntheticPriceFeed` — a finite
feed can run out — is deliberately surfaced as `StopIteration` rather
than silently repeating or returning a sentinel, so
`run_historical_backtest.py`'s loop has to handle it explicitly (it just
stops the session early and still produces a report over whatever ticks
were actually processed).

## 6. `BacktestReport`

Consumes a completed `TradeLog` (`strategy/orderbook_client/trade_log.py`,
unchanged since Milestone 3 — this is exactly the payoff of that log
having no derived statistics baked in already: Milestone 5 is free to
compute them however it wants without touching the log format).

```python
class BacktestReport:
    sharpe_ratio: float
    max_drawdown_ticks: int
    win_rate: float
    num_trades: int
    total_realized_pnl_ticks: int

def build_report(trade_log_rows: list[dict], periods_per_year: float) -> BacktestReport:
    ...
```

- **Equity curve**: replayed from the log's `fill` rows using the same
  aggressor-side accounting `risk/src/position.rs` uses (§4 of
  `docs/risk-engine-design-doc.md`) — the incoming side of every fill is
  treated as the monitored account's trade. This is a second,
  independent implementation of the same simple position math, in
  Python instead of Rust; that's intentional, not an oversight (§8).
- **Sharpe ratio**: mean/stdev of per-trade realized P&L, annualized by
  `periods_per_year` (a parameter, not a constant — a daily-bar backtest
  and an hourly-bar backtest need different annualization factors, and
  guessing one and hardcoding it would silently produce a meaningless
  number for the other).
- **Max drawdown**: largest peak-to-trough decline in the cumulative
  equity curve, in ticks (converted to dollars only at a display
  boundary, same rule as everywhere else in this project).
- **Win rate**: fraction of closing trades with positive realized P&L.

## 7. `run_historical_backtest.py`

Mirrors `run_synthetic_session.py`'s structure exactly (feed → strategy
→ `OrderBookClient` → real `orderbook_server`, same trailing-drain fix
from Milestone 3 §7's driver), swapping in `HistoricalPriceFeed` and
ending with `build_report()` instead of raw counts:

```
$ python3 strategy/run_historical_backtest.py --ticker ES=F --start 2024-01-01 --end 2024-06-01 --interval 1d --port 9999
orders sent: 142
fills received: 38
Sharpe ratio: 0.41
Max drawdown: -120 ticks ($1,500.00)
Win rate: 52.6%
```

## 8. On Duplicating the Position/P&L Math

This is the second implementation of "net position and realized P&L
from a stream of fills, treating the incoming side as the trade
direction" — the first being `risk/src/position.rs` (Milestone 4). This
is a deliberate acceptance of duplication, not a missed reuse
opportunity: the Rust version is an *online* component enforcing limits
in real time inside the live pipeline, while this one is *offline*
batch analysis over an already-completed log, in a different language,
for a different consumer (a human reading a backtest report, not a
risk system that has to react within the same process tick). Sharing
code between them would mean either the risk engine depending on Python
or the analytics layer depending on a Rust binary being invoked as a
subprocess for pure arithmetic — both worse than two small, independently
testable implementations of genuinely simple math. If the accounting
model ever grows more sophisticated (fees, multi-lot lot-matching
methods other than average cost), that complexity should be added to
both deliberately, not hidden behind a shared dependency that couples
two components this project has otherwise kept cleanly separated by
concern.

## 9. Test Cases to Write First

1. `price_to_ticks`/`ticks_to_price` round-trip for exact tick-aligned
   prices, and correct nearest-tick rounding for prices that fall
   between ticks (e.g. confirm rounding, not truncation, and confirm
   the systematic-bias case truncation would have gotten wrong).
2. `HistoricalPriceFeed` against a small fixture CSV checked into the
   repo (not a live network call): confirms exact tick sequence,
   confirms `StopIteration` once exhausted, confirms a second run
   against the same cache file produces an identical sequence.
3. Cache behavior: a fixture-backed "download" function is called at
   most once for a given ticker/range/interval; a second
   `HistoricalPriceFeed` construction with the same arguments reads the
   cache file, not the fake network call again.
4. `BacktestReport` against a scripted trade log with known, hand-computed
   Sharpe/drawdown/win-rate values (not just "it runs without crashing" —
   an actual expected-value assertion).
5. Max drawdown correctly identifies a peak-to-trough decline that
   isn't simply the first-to-last difference (a scripted equity curve
   that goes up, down, and back up higher than the original peak, to
   catch an implementation that just diffs the endpoints).
6. Full loop: run `run_historical_backtest.py` against a real
   `orderbook_server` using the fixture CSV, assert a `BacktestReport`
   is produced with no crash and internally consistent numbers (e.g.
   `num_trades` matches the fill count in the trade log).

## 10. Explicitly Deferred to Later Milestones

- Live IBKR data (Milestone 6) — `HistoricalPriceFeed` and any future
  live feed should both satisfy the same `next_price()` contract, same
  reasoning as Milestone 3 §9's original deferral for this feed.
- Session-boundary-aware gap handling (§4).
- Options pricing/Greeks/SIMD, delta-hedging, FIX parsing, dashboard
  (Milestones 7–10, unchanged).

## 11. Definition of Done for Milestone 5

- All 6 test cases in §9 pass, none of them requiring network access.
- `run_historical_backtest.py` runs an end-to-end backtest against real
  cached ES history and a live `orderbook_server`, printing a
  `BacktestReport` with Sharpe ratio, max drawdown, and win rate.
- The Milestone 1–4 test suites (C++, Python, Rust) still pass
  unmodified — this milestone adds a new feed and a new analytics
  module, it doesn't change the engine, protocol, or risk engine.
