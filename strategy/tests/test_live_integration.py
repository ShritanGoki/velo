"""Milestone 6 test case #4: full loop with LiveIBKRFeed against a real
orderbook_server, using a fake IBClient that fires a continuous stream of
ticks on a background thread - standing in for a real, always-updating
live quote feed. See docs/live-ibkr-design-doc.md §8.
"""
import random
import threading
import time
import unittest

import _pathfix  # noqa: F401
from _server_process import ServerProcess

from orderbook_client.client import OrderBookClient
from orderbook_client.live_feed import LiveIBKRFeed
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter
from orderbook_client.ticks import ticks_to_price
from orderbook_client.trade_log import TradeLog

TEST_PORT = 19401


class _TickingFakeIBClient:
    """Fires a bounded random walk of ticks (same -2..+2 step shape used
    in tests/test_historical_integration.py to prove crossing behavior)
    on a background thread, until stop() is called."""

    def __init__(self, tick_interval: float = 0.01, seed: int = 3):
        self._on_tick = None
        self._stop = threading.Event()
        self._rng = random.Random(seed)
        self._price_ticks = 19149
        self._tick_interval = tick_interval

    def subscribe(self, contract, on_tick):
        self._on_tick = on_tick
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop.is_set():
            self._price_ticks += self._rng.choice((-2, -1, 0, 1, 2))
            self._on_tick(ticks_to_price(self._price_ticks))
            time.sleep(self._tick_interval)

    def stop(self):
        self._stop.set()


class TestLiveFeedFullLoop(unittest.TestCase):
    def test_full_loop_against_real_server(self):
        server = ServerProcess(TEST_PORT)
        fake_ib = _TickingFakeIBClient()
        try:
            feed = LiveIBKRFeed(contract="ES", ib_client=fake_ib, duration_seconds=1.0)
            strategy = FixedOffsetQuoter(offset_ticks=1, size=10)
            log = TradeLog()
            client = OrderBookClient("127.0.0.1", TEST_PORT)

            run_strategy_loop(feed, strategy, client, log)
        finally:
            fake_ib.stop()
            server.stop()

        rows = log.rows
        fill_count = sum(1 for r in rows if r["event"] == "fill")
        self.assertGreater(fill_count, 0,
                            "a live-shaped tick stream should cross the quoted spread at least once")

        sent_ids = {r["order_id"] for r in rows if r["event"] == "order_sent"}
        acked_ids = {r["order_id"] for r in rows if r["event"] == "ack"}
        self.assertTrue(sent_ids.issubset(acked_ids), "every sent order should have received an ack")


if __name__ == "__main__":
    unittest.main()
