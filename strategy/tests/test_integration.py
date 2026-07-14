"""Milestone 3 test cases #4 and #5: the full loop against a real,
running orderbook_server, and event ordering from poll_events().
"""
import unittest

import _pathfix  # noqa: F401
from _server_process import ServerProcess

from orderbook_client.client import OrderBookClient
from orderbook_client.protocol import AckEvent, FillEvent, OrderType, Side
from run_synthetic_session import run_session

TEST_PORT = 19281


class TestFullLoopAgainstRealServer(unittest.TestCase):
    def test_synthetic_session_produces_fills(self):
        server = ServerProcess(TEST_PORT)
        try:
            log = run_session(host="127.0.0.1", port=TEST_PORT, ticks=200, seed=42)
        finally:
            server.stop()

        rows = log.rows
        orders_sent = [r for r in rows if r["event"] == "order_sent"]
        acks = [r for r in rows if r["event"] == "ack"]
        fills = [r for r in rows if r["event"] == "fill"]

        self.assertGreater(len(orders_sent), 0, "strategy should have sent at least one order")
        self.assertGreater(len(fills), 0,
                            "200 ticks of a bounded random walk should cross the quoted spread at least once")

        sent_ids = {r["order_id"] for r in orders_sent}
        acked_ids = {r["order_id"] for r in acks}
        self.assertTrue(sent_ids.issubset(acked_ids), "every sent order should have received an ack")


class TestEventOrdering(unittest.TestCase):
    def test_poll_events_preserves_arrival_order(self):
        server = ServerProcess(TEST_PORT + 1)
        try:
            client = OrderBookClient("127.0.0.1", TEST_PORT + 1)
            try:
                client.submit_limit(1, Side.SELL, 100, 50)
                client.submit_limit(2, Side.BUY, 100, 50)  # crosses immediately

                events = client.poll_events(timeout=2.0)
                while len(events) < 3:  # 2 acks + 1 fill
                    events += client.poll_events(timeout=2.0)

                acks = [e for e in events if isinstance(e, AckEvent)]
                fills = [e for e in events if isinstance(e, FillEvent)]

                self.assertEqual([a.client_order_id for a in acks], [1, 2])
                self.assertEqual(len(fills), 1)
                self.assertEqual(fills[0].resting_order_id, 1)
                self.assertEqual(fills[0].incoming_order_id, 2)
            finally:
                client.close()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
