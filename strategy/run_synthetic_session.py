#!/usr/bin/env python3
"""Milestone 3 driver: synthetic feed -> strategy -> OrderBookClient -> a
real orderbook_server, end to end. See docs/synthetic-loop-design-doc.md.

Usage:
    engine/build/orderbook_server 9999 &
    python3 strategy/run_synthetic_session.py --port 9999 --ticks 200
"""
from __future__ import annotations

import argparse

from orderbook_client.client import OrderBookClient
from orderbook_client.feed import SyntheticPriceFeed
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter
from orderbook_client.trade_log import TradeLog


def run_session(host: str, port: int, ticks: int, seed: int) -> TradeLog:
    feed = SyntheticPriceFeed(start_price_ticks=10000, seed=seed)
    # offset_ticks=1: with the feed's max single-tick step of 2 ticks, a
    # move of exactly +2 or -2 is what actually crosses the previous
    # tick's still-resting opposite quote (see FixedOffsetQuoter's
    # docstring) - offset_ticks=2 would need an impossible 4-tick jump.
    strategy = FixedOffsetQuoter(offset_ticks=1, size=10)
    log = TradeLog()
    client = OrderBookClient(host, port)

    run_strategy_loop(feed, strategy, client, log, max_ticks=ticks)

    return log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--ticks", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--csv", default=None, help="optional path to dump the trade log to")
    args = parser.parse_args()

    log = run_session(args.host, args.port, args.ticks, args.seed)

    rows = log.rows
    orders_sent = sum(1 for r in rows if r["event"] == "order_sent")
    fills = sum(1 for r in rows if r["event"] == "fill")
    print(f"orders sent: {orders_sent}")
    print(f"fills received: {fills}")
    print(f"trade log length: {len(rows)}")

    if args.csv:
        log.to_csv(args.csv)
        print(f"trade log written to {args.csv}")


if __name__ == "__main__":
    main()
