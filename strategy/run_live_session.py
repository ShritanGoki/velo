#!/usr/bin/env python3
"""Milestone 6 driver: live IBKR ES quotes -> strategy -> OrderBookClient ->
a real orderbook_server. See docs/live-ibkr-design-doc.md.

Requires TWS or IB Gateway running locally, logged into a paper trading
account, with API access enabled (Configure -> API -> Settings ->
Enable ActiveX and Socket Clients).

Usage:
    engine/build/orderbook_server 9999 &
    python3 strategy/run_live_session.py --expiry 202512 --duration 60 --port 9999
"""
from __future__ import annotations

import argparse

from orderbook_client.client import OrderBookClient
from orderbook_client.live_feed import LiveIBKRFeed
from orderbook_client.live_ib_client import IBAsyncClient, default_es_contract
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter
from orderbook_client.trade_log import TradeLog


def run_live_session(host: str, port: int, ib_host: str, ib_port: int, ib_client_id: int,
                      expiry: str, duration_seconds: float) -> TradeLog:
    ib_client = IBAsyncClient(ib_host, ib_port, ib_client_id)
    contract = default_es_contract(expiry)
    feed = LiveIBKRFeed(contract, ib_client, duration_seconds)

    strategy = FixedOffsetQuoter(offset_ticks=1, size=10)
    log = TradeLog()
    client = OrderBookClient(host, port)

    try:
        run_strategy_loop(feed, strategy, client, log)
    finally:
        ib_client.disconnect()

    return log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=7497)
    parser.add_argument("--ib-client-id", type=int, default=1)
    parser.add_argument("--expiry", required=True, help="ES futures expiry, e.g. 202512")
    parser.add_argument("--duration", type=float, default=60.0, help="session length in seconds")
    parser.add_argument("--csv", default=None, help="optional path to dump the trade log to")
    args = parser.parse_args()

    log = run_live_session(args.host, args.port, args.ib_host, args.ib_port, args.ib_client_id,
                            args.expiry, args.duration)

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
