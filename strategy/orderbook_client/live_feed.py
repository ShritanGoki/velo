"""Live IBKR ES futures feed. See docs/live-ibkr-design-doc.md §4.

Same next_price()-shaped interface as SyntheticPriceFeed
(orderbook_client/feed.py) and HistoricalPriceFeed
(orderbook_client/historical_feed.py) - the strategy loop in
orderbook_client/session_runner.py needs zero changes to run against this
feed instead of either of those.
"""
from __future__ import annotations

import queue
import time
from typing import Any, Callable, Protocol

from .ticks import price_to_ticks


class IBClient(Protocol):
    """The thin seam LiveIBKRFeed depends on, so tests can inject a fake
    implementation instead of a real ib_async connection - see
    tests/test_live_feed.py. The real implementation is IBAsyncClient in
    orderbook_client/live_ib_client.py."""

    def subscribe(self, contract: Any, on_tick: Callable[[float], None]) -> None:
        ...


class LiveIBKRFeed:
    def __init__(self, contract: Any, ib_client: IBClient, duration_seconds: float):
        self._queue: "queue.Queue[float]" = queue.Queue()
        self._deadline = time.monotonic() + duration_seconds
        ib_client.subscribe(contract, self._queue.put)

    def next_price(self) -> int:
        """Same contract as SyntheticPriceFeed/HistoricalPriceFeed's
        next_price(), except this feed ends via StopIteration either once
        its configured duration elapses or the IBKR connection drops (the
        latter simply looks like "no more ticks arrive before the
        deadline" from here - see docs/live-ibkr-design-doc.md §4)."""
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise StopIteration("live session duration elapsed")
        try:
            price = self._queue.get(timeout=remaining)
        except queue.Empty:
            raise StopIteration("live session duration elapsed") from None
        return price_to_ticks(price)
