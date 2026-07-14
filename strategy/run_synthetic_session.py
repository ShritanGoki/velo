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
from orderbook_client.protocol import AckEvent, FillEvent
from orderbook_client.strategy import CancelRequest, FixedOffsetQuoter, OrderRequest
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

    def drain(events) -> None:
        for event in events:
            if isinstance(event, AckEvent):
                log.ack(event.client_order_id, event.status)
            elif isinstance(event, FillEvent):
                log.fill(event.resting_order_id, event.incoming_order_id,
                          event.price_ticks, event.quantity)
                strategy.on_fill(event.resting_order_id, event.incoming_order_id)

    try:
        for _ in range(ticks):
            price = feed.next_price()
            for action in strategy.on_price(price):
                if isinstance(action, OrderRequest):
                    client.submit_limit(action.order_id, action.side, action.price_ticks, action.quantity)
                    log.order_sent(action.order_id, action.side, action.price_ticks, action.quantity)
                elif isinstance(action, CancelRequest):
                    client.cancel(action.order_id)
                    log.cancel_sent(action.order_id)

            drain(client.poll_events(timeout=0.05))

        # A final drain until the connection goes quiet, so acks/fills for
        # the last few ticks' orders aren't cut off by close() before the
        # server's replies arrive. poll_events only waits for its *first*
        # event per call, so looping until an empty call is what actually
        # waits out stragglers rather than a single fixed-timeout call.
        while True:
            events = client.poll_events(timeout=0.2)
            if not events:
                break
            drain(events)
    finally:
        client.close()

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
