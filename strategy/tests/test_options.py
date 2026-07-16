"""Milestone 8 test case #2: OptionBook aggregate delta computation.
See docs/delta-hedging-design-doc.md §8.
"""
import unittest

import _pathfix  # noqa: F401

from orderbook_client.options import OptionBook, OptionLeg
from orderbook_client.pricing_client import Greeks


class FakePricingClient:
    """A simple, deterministic stand-in for a real pricing_server
    connection - enough structure to verify aggregation math without
    needing real Black-Scholes or a live socket."""

    def price(self, spot_ticks, strike_ticks, rate, vol, time_to_expiry, is_call):
        del rate, vol, time_to_expiry  # unused by this fake
        delta = max(0.0, min(1.0, 0.5 + 0.0001 * (spot_ticks - strike_ticks)))
        if not is_call:
            delta -= 1.0
        return Greeks(price=0.0, delta=delta, gamma=0.0, vega=0.0, theta=0.0, rho=0.0)


class TestOptionBookAggregateDelta(unittest.TestCase):
    def test_aggregates_signed_quantity_weighted_deltas(self):
        client = FakePricingClient()
        legs = [
            OptionLeg(strike_ticks=30000, time_to_expiry_years=0.25, vol=0.2, is_call=True, quantity=10),
            OptionLeg(strike_ticks=30000, time_to_expiry_years=0.25, vol=0.2, is_call=False, quantity=-5),
        ]
        book = OptionBook(legs, rate=0.03)

        spot_ticks = 30000
        expected = (
            client.price(spot_ticks, 30000, 0.03, 0.2, 0.25, True).delta * 10
            + client.price(spot_ticks, 30000, 0.03, 0.2, 0.25, False).delta * -5
        )
        self.assertAlmostEqual(book.aggregate_delta(spot_ticks, client), expected)

    def test_empty_book_has_zero_delta(self):
        book = OptionBook([], rate=0.03)
        self.assertEqual(book.aggregate_delta(30000, FakePricingClient()), 0.0)


if __name__ == "__main__":
    unittest.main()
