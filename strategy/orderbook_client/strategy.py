"""Strategy interface + the Milestone 3 example strategy.

See docs/synthetic-loop-design-doc.md §5. FixedOffsetQuoter's only job is
to reliably exercise both the resting-order and crossing-order paths
against a real server; it is not meant to be a good trading strategy —
that's Milestones 5 and 8's concern.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple, Union

from .protocol import OrderType, Side


@dataclass(frozen=True)
class OrderRequest:
    order_id: int
    side: Side
    order_type: OrderType
    price_ticks: int
    quantity: int


@dataclass(frozen=True)
class CancelRequest:
    order_id: int


StrategyAction = Union[OrderRequest, CancelRequest]


class Strategy(Protocol):
    def on_price(self, price_ticks: int) -> List[StrategyAction]:
        ...

    def on_fill(self, resting_order_id: int, incoming_order_id: int) -> None:
        ...


class FixedOffsetQuoter:
    """Keeps a resting buy `offset_ticks` below and a resting sell
    `offset_ticks` above the current price, cancelling and replacing
    whichever side has drifted from the live price on each tick.

    This repricing is what actually generates trade flow in the
    synthetic-only setting: since this strategy is the only participant,
    a quote that never moves would never get crossed by anything. Each
    tick's fresh order is submitted *before* the same side's stale one is
    cancelled, so a big enough price move lets the new order cross the
    previous tick's still-resting opposite-side quote before it's pulled.
    """

    def __init__(self, offset_ticks: int, size: int):
        self._offset = offset_ticks
        self._size = size
        self._ids = itertools.count(1)
        self._resting_buy: Optional[Tuple[int, int]] = None   # (order_id, price_ticks)
        self._resting_sell: Optional[Tuple[int, int]] = None

    def on_price(self, price_ticks: int) -> List[StrategyAction]:
        actions: List[StrategyAction] = []
        actions += self._requote(Side.BUY, price_ticks - self._offset)
        actions += self._requote(Side.SELL, price_ticks + self._offset)
        return actions

    def _requote(self, side: Side, target_price: int) -> List[StrategyAction]:
        current = self._resting_buy if side == Side.BUY else self._resting_sell
        if current is not None and current[1] == target_price:
            return []  # already quoting at the right price, leave it resting

        order_id = next(self._ids)
        actions: List[StrategyAction] = [OrderRequest(order_id, side, OrderType.LIMIT, target_price, self._size)]
        if current is not None:
            actions.append(CancelRequest(current[0]))

        new_state = (order_id, target_price)
        if side == Side.BUY:
            self._resting_buy = new_state
        else:
            self._resting_sell = new_state
        return actions

    def on_fill(self, resting_order_id: int, incoming_order_id: int) -> None:
        del incoming_order_id  # unused: only which side cleared matters here
        if self._resting_buy is not None and resting_order_id == self._resting_buy[0]:
            self._resting_buy = None
        elif self._resting_sell is not None and resting_order_id == self._resting_sell[0]:
            self._resting_sell = None
