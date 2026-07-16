"""Milestone 3 test case #3: FixedOffsetQuoter quotes once per side and
only reprices (cancel + replace) when the target price actually moves -
scripted prices, no real socket involved.
"""
import unittest

import _pathfix  # noqa: F401

from orderbook_client.protocol import FillEvent, Side
from orderbook_client.strategy import CancelRequest, FixedOffsetQuoter, OrderRequest


def fill_event(resting_order_id: int, incoming_order_id: int) -> FillEvent:
    return FillEvent(resting_order_id=resting_order_id, incoming_order_id=incoming_order_id,
                      resting_side=Side.SELL, price_ticks=10000, quantity=10, timestamp=0)


class TestFixedOffsetQuoter(unittest.TestCase):
    def test_quotes_once_then_stays_flat_at_the_same_price(self):
        quoter = FixedOffsetQuoter(offset_ticks=2, size=10)

        first = quoter.on_price(10000)
        self.assertEqual(len(first), 2)
        self.assertTrue(all(isinstance(a, OrderRequest) for a in first))
        sides = {req.side for req in first}
        self.assertEqual(sides, {Side.BUY, Side.SELL})

        # Same price again: target quote prices haven't changed, no new actions.
        self.assertEqual(quoter.on_price(10000), [])

    def test_reprices_on_price_move(self):
        quoter = FixedOffsetQuoter(offset_ticks=2, size=10)
        first = quoter.on_price(10000)
        old_buy_id = next(a.order_id for a in first if isinstance(a, OrderRequest) and a.side == Side.BUY)
        old_sell_id = next(a.order_id for a in first if isinstance(a, OrderRequest) and a.side == Side.SELL)

        actions = quoter.on_price(10010)  # moved well beyond the offset

        new_orders = [a for a in actions if isinstance(a, OrderRequest)]
        cancels = [a for a in actions if isinstance(a, CancelRequest)]
        self.assertEqual(len(new_orders), 2, "both sides should reprice when price moves")
        self.assertEqual({c.order_id for c in cancels}, {old_buy_id, old_sell_id},
                          "the stale quotes should be cancelled")
        self.assertEqual({a.price_ticks for a in new_orders}, {10008, 10012})

        # The new order for a side is submitted before that side's old one is
        # cancelled, so a crossing counterparty order sees it still resting.
        buy_new_index = next(i for i, a in enumerate(actions)
                              if isinstance(a, OrderRequest) and a.side == Side.BUY)
        buy_cancel_index = next(i for i, a in enumerate(actions)
                                 if isinstance(a, CancelRequest) and a.order_id == old_buy_id)
        self.assertLess(buy_new_index, buy_cancel_index)

    def test_on_fill_clears_state_for_the_filled_side(self):
        quoter = FixedOffsetQuoter(offset_ticks=2, size=10)
        first = quoter.on_price(10000)
        buy_id = next(a.order_id for a in first if isinstance(a, OrderRequest) and a.side == Side.BUY)

        quoter.on_fill(fill_event(resting_order_id=buy_id, incoming_order_id=999))
        # An unrelated id shouldn't clear anything.
        quoter.on_fill(fill_event(resting_order_id=424242, incoming_order_id=999))

        # The buy side no longer has anything resting (it was just filled),
        # so even at the same price it must be freshly re-quoted; the sell
        # side is untouched and shouldn't requote.
        actions = quoter.on_price(10000)
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], OrderRequest)
        self.assertEqual(actions[0].side, Side.BUY)
        self.assertNotEqual(actions[0].order_id, buy_id)


if __name__ == "__main__":
    unittest.main()
