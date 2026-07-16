#!/usr/bin/env python3
"""Milestone 8 driver: DeltaHedger against a real orderbook_server and a
real pricing_server, producing both a BacktestReport (futures P&L) and a
HedgeReport (hedge effectiveness). See docs/delta-hedging-design-doc.md.

A companion FixedOffsetQuoter runs concurrently to provide counterparty
liquidity - see tests/test_delta_hedge_integration.py's docstring for why
that's necessary in this project's otherwise single-participant sessions.

Usage:
    engine/build/orderbook_server 9999 &
    risk/target/debug/pricing_server 9998 &
    python3 strategy/run_delta_hedge_backtest.py --ticks 300
"""
from __future__ import annotations

import argparse
import threading
import time

from orderbook_client.backtest_report import build_report
from orderbook_client.client import OrderBookClient
from orderbook_client.delta_hedger import DeltaHedger
from orderbook_client.feed import SyntheticPriceFeed
from orderbook_client.hedge_report import build_hedge_report
from orderbook_client.options import OptionBook, OptionLeg
from orderbook_client.pricing_client import PricingClient
from orderbook_client.protocol import AckEvent, FillEvent
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter, OrderType
from orderbook_client.trade_log import TradeLog

RATE = 0.03


def run_quoter(host: str, port: int, ticks: int, seed: int) -> None:
    """Provides continuous two-sided liquidity so DeltaHedger's market
    orders have something to trade against."""
    feed = SyntheticPriceFeed(start_price_ticks=30000, seed=seed)
    quoter = FixedOffsetQuoter(offset_ticks=1, size=50)
    log = TradeLog()
    client = OrderBookClient(host, port)
    run_strategy_loop(feed, quoter, client, log, max_ticks=ticks,
                       tick_poll_timeout=0.01, final_drain_timeout=0.1)


def run_hedge_backtest(host: str, port: int, pricing_host: str, pricing_port: int,
                        ticks: int, seed: int, hedge_band: int):
    quoter_thread = threading.Thread(
        target=run_quoter, args=(host, port, ticks + 500, seed + 1), daemon=True)
    quoter_thread.start()
    time.sleep(0.2)  # let the quoter seed some resting liquidity first

    pricing_client = PricingClient(pricing_host, pricing_port)
    options_book = OptionBook(
        [OptionLeg(strike_ticks=30000, time_to_expiry_years=0.25, vol=0.2, is_call=True, quantity=100)],
        rate=RATE,
    )
    hedger = DeltaHedger(options_book, pricing_client, hedge_band_contracts=hedge_band)
    log = TradeLog()
    client = OrderBookClient(host, port)
    feed = SyntheticPriceFeed(start_price_ticks=30000, seed=seed)

    unhedged_pnl = []
    hedged_pnl = []
    baseline_value = None
    cumulative_futures_pnl = 0.0
    prev_price_ticks = None

    def drain(events) -> None:
        for event in events:
            if isinstance(event, AckEvent):
                log.ack(event.client_order_id, event.status)
            elif isinstance(event, FillEvent):
                log.fill(event.resting_order_id, event.incoming_order_id,
                          event.incoming_side, event.price_ticks, event.quantity)
                hedger.on_fill(event)

    try:
        for _ in range(ticks):
            price_ticks = feed.next_price()

            if prev_price_ticks is not None:
                # Mark-to-market P&L attribution: the position held
                # *before* this tick's rehedge decision earns P&L on this
                # tick's price move - the standard way to decompose a
                # hedge's P&L contribution.
                price_move_points = (price_ticks - prev_price_ticks) * 0.25
                cumulative_futures_pnl += hedger.current_futures_position * price_move_points

            book_value = options_book.aggregate_value(price_ticks, pricing_client)
            if baseline_value is None:
                baseline_value = book_value
            unhedged_pnl.append(book_value - baseline_value)
            hedged_pnl.append((book_value - baseline_value) + cumulative_futures_pnl)

            for action in hedger.on_price(price_ticks):
                if action.order_type == OrderType.MARKET:
                    client.submit_market(action.order_id, action.side, action.quantity)
                else:
                    client.submit_limit(action.order_id, action.side, action.price_ticks, action.quantity)
                log.order_sent(action.order_id, action.side, action.price_ticks, action.quantity)

            drain(client.poll_events(timeout=0.05))
            prev_price_ticks = price_ticks

        while True:
            events = client.poll_events(timeout=0.2)
            if not events:
                break
            drain(events)
    finally:
        client.close()
        pricing_client.close()
        quoter_thread.join(timeout=5)

    backtest_report = build_report(log.rows, periods_per_year=252)
    hedge_report = build_hedge_report(hedger.hedge_error_history, hedger.rehedge_count,
                                       hedged_pnl, unhedged_pnl)
    return log, backtest_report, hedge_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--pricing-host", default="127.0.0.1")
    parser.add_argument("--pricing-port", type=int, default=9998)
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--hedge-band", type=int, default=5)
    args = parser.parse_args()

    log, backtest_report, hedge_report = run_hedge_backtest(
        args.host, args.port, args.pricing_host, args.pricing_port,
        args.ticks, args.seed, args.hedge_band)

    fills = sum(1 for r in log.rows if r["event"] == "fill")
    print(f"rehedge orders sent: {hedge_report.rehedge_count}")
    print(f"fills received:      {fills}")
    print(f"mean |hedge error|:  {hedge_report.mean_abs_hedge_error_contracts:.2f} contracts")
    print(f"hedged P&L variance:   {hedge_report.hedged_pnl_variance:.4f}")
    print(f"unhedged P&L variance: {hedge_report.unhedged_pnl_variance:.4f}")
    print(f"variance reduction:    {hedge_report.variance_reduction_pct:.1f}%")
    print(f"(futures-only Sharpe: {backtest_report.sharpe_ratio:.2f})")


if __name__ == "__main__":
    main()
