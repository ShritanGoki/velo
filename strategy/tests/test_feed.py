"""Milestone 3 test case #2: a fixed seed reproduces the same price path."""
import unittest

import _pathfix  # noqa: F401

from orderbook_client.feed import SyntheticPriceFeed


class TestSyntheticPriceFeed(unittest.TestCase):
    def test_same_seed_reproduces_same_path(self):
        feed_a = SyntheticPriceFeed(start_price_ticks=10000, seed=7)
        feed_b = SyntheticPriceFeed(start_price_ticks=10000, seed=7)

        path_a = [feed_a.next_price() for _ in range(100)]
        path_b = [feed_b.next_price() for _ in range(100)]

        self.assertEqual(path_a, path_b)

    def test_different_seeds_diverge(self):
        feed_a = SyntheticPriceFeed(start_price_ticks=10000, seed=1)
        feed_b = SyntheticPriceFeed(start_price_ticks=10000, seed=2)

        path_a = [feed_a.next_price() for _ in range(50)]
        path_b = [feed_b.next_price() for _ in range(50)]

        self.assertNotEqual(path_a, path_b)

    def test_steps_are_bounded(self):
        feed = SyntheticPriceFeed(start_price_ticks=10000, seed=3)
        price = 10000
        for _ in range(500):
            next_price = feed.next_price()
            self.assertLessEqual(abs(next_price - price), 2)
            price = next_price


if __name__ == "__main__":
    unittest.main()
