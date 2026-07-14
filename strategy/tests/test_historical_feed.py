"""Milestone 5 test cases #2 and #3: HistoricalPriceFeed against an
injected fake downloader (never a real network call) and its caching
behavior. See docs/historical-backtest-design-doc.md §9.
"""
import shutil
import tempfile
import unittest

import _pathfix  # noqa: F401

from orderbook_client.historical_feed import HistoricalPriceFeed
from orderbook_client.ticks import price_to_ticks

FAKE_CLOSES = [4787.25, 4746.50, 4729.50, 4734.75, 4801.25]


class _CountingDownloader:
    def __init__(self, closes):
        self._closes = closes
        self.call_count = 0

    def __call__(self, ticker, start, end, interval):
        self.call_count += 1
        return list(self._closes)


class TestHistoricalPriceFeed(unittest.TestCase):
    def setUp(self):
        self.cache_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    def test_exact_tick_sequence_from_fixture_data(self):
        downloader = _CountingDownloader(FAKE_CLOSES)
        feed = HistoricalPriceFeed("ES=F", "2024-01-01", "2024-01-10", "1d", self.cache_dir, downloader)

        prices = [feed.next_price() for _ in range(len(FAKE_CLOSES))]
        self.assertEqual(prices, [price_to_ticks(c) for c in FAKE_CLOSES])

    def test_stop_iteration_once_exhausted(self):
        downloader = _CountingDownloader(FAKE_CLOSES)
        feed = HistoricalPriceFeed("ES=F", "2024-01-01", "2024-01-10", "1d", self.cache_dir, downloader)

        for _ in range(len(FAKE_CLOSES)):
            feed.next_price()

        with self.assertRaises(StopIteration):
            feed.next_price()

    def test_second_construction_hits_the_cache_not_the_downloader(self):
        downloader = _CountingDownloader(FAKE_CLOSES)
        HistoricalPriceFeed("ES=F", "2024-01-01", "2024-01-10", "1d", self.cache_dir, downloader)
        self.assertEqual(downloader.call_count, 1)

        feed_two = HistoricalPriceFeed("ES=F", "2024-01-01", "2024-01-10", "1d", self.cache_dir, downloader)
        self.assertEqual(downloader.call_count, 1, "second construction should read the cache, not re-download")
        self.assertEqual(len(feed_two), len(FAKE_CLOSES))

    def test_same_cache_produces_identical_sequence_on_repeat_runs(self):
        downloader = _CountingDownloader(FAKE_CLOSES)
        first_run = HistoricalPriceFeed("ES=F", "2024-01-01", "2024-01-10", "1d", self.cache_dir, downloader)
        first_prices = [first_run.next_price() for _ in range(len(FAKE_CLOSES))]

        second_run = HistoricalPriceFeed("ES=F", "2024-01-01", "2024-01-10", "1d", self.cache_dir, downloader)
        second_prices = [second_run.next_price() for _ in range(len(FAKE_CLOSES))]

        self.assertEqual(first_prices, second_prices)


if __name__ == "__main__":
    unittest.main()
