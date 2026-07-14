"""Real ES futures history feed. See docs/historical-backtest-design-doc.md §3, §5.

Same next_price()-shaped interface as SyntheticPriceFeed
(orderbook_client/feed.py), so the strategy-loop plumbing in
run_synthetic_session.py needed zero changes to be reused for
run_historical_backtest.py - swapping the feed was the whole point.
"""
from __future__ import annotations

import csv
import os
from typing import Callable, List

from .ticks import price_to_ticks

# (ticker, start, end, interval) -> list of raw close prices, oldest first.
Downloader = Callable[[str, str, str, str], List[float]]


def yfinance_downloader(ticker: str, start: str, end: str, interval: str) -> List[float]:
    """The real, network-backed downloader. Only imported/invoked on an
    actual cache miss, so tests never need yfinance or network access -
    they inject a fake Downloader instead (see tests/test_historical_feed.py)."""
    import yfinance as yf

    df = yf.download(ticker, start=start, end=end, interval=interval, progress=False)
    close = df["Close"]
    if hasattr(close, "columns"):  # newer yfinance: MultiIndex (Price, Ticker) columns
        close = close.iloc[:, 0]
    return [float(p) for p in close.tolist()]


class HistoricalPriceFeed:
    def __init__(self, ticker: str, start: str, end: str, interval: str,
                 cache_dir: str, downloader: Downloader = yfinance_downloader):
        self._prices: List[int] = self._load_or_download(ticker, start, end, interval, cache_dir, downloader)
        self._index = 0

    def next_price(self) -> int:
        """Same contract as SyntheticPriceFeed.next_price(), except this
        feed is finite: raises StopIteration once exhausted rather than
        continuing forever."""
        if self._index >= len(self._prices):
            raise StopIteration("historical feed exhausted")
        price = self._prices[self._index]
        self._index += 1
        return price

    def __len__(self) -> int:
        return len(self._prices)

    @staticmethod
    def _cache_path(cache_dir: str, ticker: str, start: str, end: str, interval: str) -> str:
        safe_ticker = ticker.replace("=", "_").replace("/", "_")
        return os.path.join(cache_dir, f"{safe_ticker}_{interval}_{start}_{end}.csv")

    @classmethod
    def _load_or_download(cls, ticker: str, start: str, end: str, interval: str,
                           cache_dir: str, downloader: Downloader) -> List[int]:
        os.makedirs(cache_dir, exist_ok=True)
        path = cls._cache_path(cache_dir, ticker, start, end, interval)

        if not os.path.exists(path):
            closes = downloader(ticker, start, end, interval)
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["close"])
                writer.writerows([[c] for c in closes])

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            return [price_to_ticks(float(row["close"])) for row in reader]
