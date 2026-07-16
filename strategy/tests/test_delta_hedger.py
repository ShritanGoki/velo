"""Milestone 8 test case #3: DeltaHedger's hedge-band behavior, scripted
against a fake options book so the band logic is tested in isolation
from real option pricing. See docs/delta-hedging-design-doc.md §8.
"""
import unittest

import _pathfix  # noqa: F401

from orderbook_client.delta_hedger import DeltaHedger
from orderbook_client.protocol import OrderType, Side
from orderbook_client.strategy import OrderRequest


class FixedDeltaOptionBook:
    """Stands in for OptionBook: returns a fixed aggregate delta
    regardless of spot/pricing_client, so DeltaHedger's own logic can be
    tested without a real pricing_server."""

    def __init__(self, delta: float):
        self._delta = delta

    def aggregate_delta(self, spot_ticks, pricing_client):
        del spot_ticks, pricing_client
        return self._delta


class TestDeltaHedgerBand(unittest.TestCase):
    def test_stays_flat_within_the_hedge_band(self):
        # aggregate_delta=-5 -> target_futures_position=5; starting
        # futures position is 0 -> hedge_error = 0 - 5 = -5, exactly at
        # the band boundary (5) -> should NOT rehedge (inclusive band).
        hedger = DeltaHedger(FixedDeltaOptionBook(-5.0), pricing_client=None, hedge_band_contracts=5)
        self.assertEqual(hedger.on_price(10000), [])
        self.assertEqual(hedger.rehedge_count, 0)

    def test_emits_a_correctly_sized_and_sided_order_outside_the_band(self):
        # aggregate_delta=-20 -> target_futures_position=20; current
        # futures position starts at 0 -> hedge_error = 0 - 20 = -20,
        # outside the band (5) -> must BUY 20 to move toward +20.
        hedger = DeltaHedger(FixedDeltaOptionBook(-20.0), pricing_client=None, hedge_band_contracts=5)
        actions = hedger.on_price(10000)

        self.assertEqual(len(actions), 1)
        order = actions[0]
        self.assertIsInstance(order, OrderRequest)
        self.assertEqual(order.side, Side.BUY)
        self.assertEqual(order.order_type, OrderType.MARKET)
        self.assertEqual(order.quantity, 20)
        self.assertEqual(hedger.rehedge_count, 1)

    def test_short_delta_book_requires_a_sell_to_rehedge(self):
        # aggregate_delta=+20 -> target_futures_position=-20; hedge_error
        # = 0 - (-20) = 20, outside the band -> must SELL 20.
        hedger = DeltaHedger(FixedDeltaOptionBook(20.0), pricing_client=None, hedge_band_contracts=5)
        actions = hedger.on_price(10000)

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].side, Side.SELL)
        self.assertEqual(actions[0].quantity, 20)

    def test_records_hedge_error_history_every_tick_even_when_flat(self):
        hedger = DeltaHedger(FixedDeltaOptionBook(-5.0), pricing_client=None, hedge_band_contracts=5)
        hedger.on_price(10000)
        hedger.on_price(10001)
        self.assertEqual(hedger.hedge_error_history, [-5, -5])


if __name__ == "__main__":
    unittest.main()
