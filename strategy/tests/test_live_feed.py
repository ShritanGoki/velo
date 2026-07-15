"""Milestone 6 test cases #1-3, #5: LiveIBKRFeed against a fake IBClient -
no real IBKR connection required. See docs/live-ibkr-design-doc.md §7, §8.
"""
import unittest

import _pathfix  # noqa: F401

from orderbook_client.live_feed import LiveIBKRFeed
from orderbook_client.ticks import price_to_ticks


class FakeIBClient:
    """The injectable seam from docs/live-ibkr-design-doc.md §4/§7 - fires
    ticks on demand instead of connecting to a real TWS/IB Gateway."""

    def __init__(self):
        self._on_tick = None
        self.subscribed_contract = None

    def subscribe(self, contract, on_tick):
        self.subscribed_contract = contract
        self._on_tick = on_tick

    def fire(self, price: float) -> None:
        self._on_tick(price)


class TestLiveIBKRFeed(unittest.TestCase):
    def test_returns_tick_converted_prices_in_order(self):
        fake = FakeIBClient()
        feed = LiveIBKRFeed(contract="ES", ib_client=fake, duration_seconds=5.0)

        # 4787.60 is deliberately not tick-aligned (real live quotes won't
        # always land on a 0.25 boundary the way historical closes often
        # do) - exercises the same price_to_ticks boundary from Milestone
        # 5 with a genuinely live-shaped value (test case #5).
        prices = [4787.25, 4787.10, 4787.60]
        for p in prices:
            fake.fire(p)

        got = [feed.next_price() for _ in prices]
        self.assertEqual(got, [price_to_ticks(p) for p in prices])

    def test_subscribes_to_the_given_contract(self):
        fake = FakeIBClient()
        LiveIBKRFeed(contract="ES-FRONT-MONTH", ib_client=fake, duration_seconds=5.0)
        self.assertEqual(fake.subscribed_contract, "ES-FRONT-MONTH")

    def test_duration_cutoff_raises_stop_iteration(self):
        fake = FakeIBClient()
        feed = LiveIBKRFeed(contract="ES", ib_client=fake, duration_seconds=0.05)

        with self.assertRaises(StopIteration):
            feed.next_price()  # no ticks ever arrive; the deadline just passes

    def test_disconnect_surfaces_as_stop_iteration_not_a_hang(self):
        # A "disconnect" looks identical to the duration cutoff from the
        # feed's perspective: no more ticks arrive. Confirmed here with one
        # real tick first, so we know the feed was genuinely live before
        # going quiet - not just untested.
        fake = FakeIBClient()
        feed = LiveIBKRFeed(contract="ES", ib_client=fake, duration_seconds=0.1)
        fake.fire(4787.25)

        self.assertEqual(feed.next_price(), price_to_ticks(4787.25))
        with self.assertRaises(StopIteration):
            feed.next_price()


if __name__ == "__main__":
    unittest.main()
