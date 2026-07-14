#!/usr/bin/env python3
"""Milestone 5 driver: real ES history -> strategy -> OrderBookClient -> a
real orderbook_server -> a backtest report. See
docs/historical-backtest-design-doc.md.

Usage:
    engine/build/orderbook_server 9999 &
    python3 strategy/run_historical_backtest.py --ticker ES=F \\
        --start 2024-01-01 --end 2024-06-01 --interval 1d --port 9999
"""
from __future__ import annotations

import argparse
import os

from orderbook_client.backtest_report import BacktestReport, build_report
from orderbook_client.client import OrderBookClient
from orderbook_client.historical_feed import HistoricalPriceFeed
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter
from orderbook_client.ticks import ticks_to_price
from orderbook_client.trade_log import TradeLog

# Bars/year used to annualize the Sharpe ratio, keyed by yfinance interval.
# Passed explicitly rather than hardcoded once, since a daily-bar backtest
# and an hourly-bar backtest need different annualization factors (see
# docs/historical-backtest-design-doc.md §6).
_PERIODS_PER_YEAR = {
    "1d": 252,
    "1h": 252 * 7,   # ~7 trading hours/session for ES's regular session
}

_DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), "data")


def run_backtest(host: str, port: int, ticker: str, start: str, end: str, interval: str,
                  cache_dir: str = _DEFAULT_CACHE_DIR) -> tuple[TradeLog, BacktestReport]:
    feed = HistoricalPriceFeed(ticker, start, end, interval, cache_dir)
    strategy = FixedOffsetQuoter(offset_ticks=1, size=10)
    log = TradeLog()
    client = OrderBookClient(host, port)

    run_strategy_loop(feed, strategy, client, log)

    report = build_report(log.rows, periods_per_year=_PERIODS_PER_YEAR.get(interval, 252))
    return log, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--ticker", default="ES=F")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--interval", default="1d")
    parser.add_argument("--cache-dir", default=_DEFAULT_CACHE_DIR)
    parser.add_argument("--csv", default=None, help="optional path to dump the trade log to")
    args = parser.parse_args()

    log, report = run_backtest(args.host, args.port, args.ticker, args.start, args.end,
                                args.interval, args.cache_dir)

    rows = log.rows
    orders_sent = sum(1 for r in rows if r["event"] == "order_sent")
    fills = sum(1 for r in rows if r["event"] == "fill")
    print(f"orders sent: {orders_sent}")
    print(f"fills received: {fills}")
    print(f"Sharpe ratio: {report.sharpe_ratio:.2f}")
    dollars_per_tick = ticks_to_price(1) * 50  # ES: $12.50/tick = 0.25 index pts * $50/pt
    print(f"Max drawdown: {report.max_drawdown_ticks} ticks "
          f"(${report.max_drawdown_ticks * dollars_per_tick:.2f})")
    print(f"Win rate: {report.win_rate:.1%}")

    if args.csv:
        log.to_csv(args.csv)
        print(f"trade log written to {args.csv}")


if __name__ == "__main__":
    main()
