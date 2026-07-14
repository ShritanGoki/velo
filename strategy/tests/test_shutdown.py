"""Milestone 3 test case #6: closing the client while its reader thread is
blocked on recv() must not hang the process.
"""
import threading
import time
import unittest

import _pathfix  # noqa: F401
from _server_process import ServerProcess

from orderbook_client.client import OrderBookClient

TEST_PORT = 19291


class TestCleanShutdown(unittest.TestCase):
    def test_close_returns_promptly_with_no_pending_data(self):
        server = ServerProcess(TEST_PORT)
        try:
            client = OrderBookClient("127.0.0.1", TEST_PORT)
            # Give the reader thread a moment to reach its blocking recv().
            time.sleep(0.05)

            start = time.monotonic()
            client.close()
            elapsed = time.monotonic() - start

            self.assertLess(elapsed, 2.0, "close() should not hang waiting on the reader thread")

            # The reader thread should be the daemon thread OrderBookClient
            # started, and it must have actually exited, not just detached.
            reader_threads = [t for t in threading.enumerate() if t is client._reader]
            self.assertEqual(reader_threads, [], "reader thread should have exited after close()")
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
