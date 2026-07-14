"""Milestone 5 test case #1: the float-to-tick boundary.
See docs/historical-backtest-design-doc.md §4, §9.
"""
import unittest

import _pathfix  # noqa: F401

from orderbook_client.ticks import price_to_ticks, ticks_to_price


class TestTickConversion(unittest.TestCase):
    def test_exact_tick_aligned_round_trip(self):
        self.assertEqual(price_to_ticks(4787.25), 19149)
        self.assertEqual(ticks_to_price(19149), 4787.25)

    def test_rounds_to_nearest_tick_not_truncates(self):
        # 4787.10 is between ticks 19148 (4787.00) and 19149 (4787.25);
        # it's closer to 19148. Truncation (int(4787.10 / 0.25) = int(19148.4)
        # = 19148) happens to agree here, so also check a case where
        # truncating and rounding actually disagree.
        self.assertEqual(price_to_ticks(4787.10), 19148)

        # 4787.20 / 0.25 = 19148.8 -> truncation gives 19148 (wrong: that's
        # 4787.00, off by a whole tick from the nearest real tick, 4787.25).
        # Rounding correctly gives 19149.
        self.assertEqual(price_to_ticks(4787.20), 19149)

    def test_round_trip_is_lossless_for_tick_aligned_prices(self):
        for ticks in (-100, 0, 1, 19149, 1_000_000):
            self.assertEqual(price_to_ticks(ticks_to_price(ticks)), ticks)


if __name__ == "__main__":
    unittest.main()
