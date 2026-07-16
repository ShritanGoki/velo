"""DeltaHedger: the Milestone 8 strategy. See docs/delta-hedging-design-doc.md §6.

Holds a fixed OptionBook, prices it live via pricing_server on every
tick, and rebalances an ES futures position toward delta-neutral
whenever the hedge error exceeds a configured tolerance band. This is
the piece that ties Python, Rust (options.py -> pricing_client.py ->
pricing_server), and the C++ matching engine together through an actual
trading decision, not just data-passing.
"""
from __future__ import annotations

import itertools
from typing import List

from .options import OptionBook
from .pricing_client import PricingClient
from .protocol import FillEvent, Side
from .strategy import OrderRequest, OrderType, StrategyAction


class DeltaHedger:
    def __init__(self, options_book: OptionBook, pricing_client: PricingClient,
                 hedge_band_contracts: int):
        self._options_book = options_book
        self._pricing_client = pricing_client
        self._hedge_band = hedge_band_contracts
        self._ids = itertools.count(1)
        self._current_futures_position = 0
        self._my_order_ids: set = set()
        self._hedge_error_history: List[int] = []
        self._rehedge_count = 0

    def on_price(self, price_ticks: int) -> List[StrategyAction]:
        target_futures_position = -self._options_book.aggregate_delta(price_ticks, self._pricing_client)
        hedge_error = self._current_futures_position - round(target_futures_position)
        self._hedge_error_history.append(hedge_error)

        if abs(hedge_error) <= self._hedge_band:
            return []  # within tolerance - don't rehedge every single tick

        self._rehedge_count += 1
        order_id = next(self._ids)
        self._my_order_ids.add(order_id)
        side = Side.SELL if hedge_error > 0 else Side.BUY
        return [OrderRequest(order_id, side, OrderType.MARKET, 0, abs(hedge_error))]

    def on_fill(self, event: FillEvent) -> None:
        # DeltaHedger only ever submits market orders, so it's always
        # the incoming side of any fill it's party to - a single market
        # order can still generate multiple partial fills (walking
        # several price levels, per docs/orderbook-design-doc.md §5), so
        # this accumulates rather than assuming exactly one fill per order.
        if event.incoming_order_id not in self._my_order_ids:
            return
        signed_qty = event.quantity if event.incoming_side == Side.BUY else -event.quantity
        self._current_futures_position += signed_qty

    @property
    def current_futures_position(self) -> int:
        return self._current_futures_position

    @property
    def hedge_error_history(self) -> List[int]:
        return list(self._hedge_error_history)

    @property
    def rehedge_count(self) -> int:
        return self._rehedge_count
