"""Shared strategy-loop driver: feed -> strategy -> OrderBookClient -> a
real orderbook_server. Used by both run_synthetic_session.py (Milestone 3)
and run_historical_backtest.py (Milestone 5) - the only thing that differs
between them is which feed is plugged in, which is exactly the point of
giving both feeds the same next_price()-shaped interface.
"""
from __future__ import annotations

from typing import Optional

from .client import OrderBookClient
from .protocol import AckEvent, FillEvent
from .strategy import CancelRequest, OrderRequest, Strategy
from .trade_log import TradeLog


def run_strategy_loop(feed, strategy: Strategy, client: OrderBookClient, log: TradeLog,
                       max_ticks: Optional[int] = None,
                       tick_poll_timeout: float = 0.05, final_drain_timeout: float = 0.2) -> int:
    """Runs the loop until max_ticks is reached (if given) or the feed
    raises StopIteration (a finite feed, e.g. HistoricalPriceFeed).
    Returns the number of ticks actually processed."""

    def drain(events) -> None:
        for event in events:
            if isinstance(event, AckEvent):
                log.ack(event.client_order_id, event.status)
            elif isinstance(event, FillEvent):
                log.fill(event.resting_order_id, event.incoming_order_id,
                          event.incoming_side, event.price_ticks, event.quantity)
                strategy.on_fill(event.resting_order_id, event.incoming_order_id)

    ticks_done = 0
    try:
        while max_ticks is None or ticks_done < max_ticks:
            try:
                price = feed.next_price()
            except StopIteration:
                break
            ticks_done += 1

            for action in strategy.on_price(price):
                if isinstance(action, OrderRequest):
                    client.submit_limit(action.order_id, action.side, action.price_ticks, action.quantity)
                    log.order_sent(action.order_id, action.side, action.price_ticks, action.quantity)
                elif isinstance(action, CancelRequest):
                    client.cancel(action.order_id)
                    log.cancel_sent(action.order_id)

            drain(client.poll_events(timeout=tick_poll_timeout))

        # A final drain until the connection goes quiet, so acks/fills for
        # the last few ticks' orders aren't cut off by close() before the
        # server's replies arrive. poll_events only waits for its *first*
        # event per call, so looping until an empty call is what actually
        # waits out stragglers rather than a single fixed-timeout call.
        while True:
            events = client.poll_events(timeout=final_drain_timeout)
            if not events:
                break
            drain(events)
    finally:
        client.close()

    return ticks_done
