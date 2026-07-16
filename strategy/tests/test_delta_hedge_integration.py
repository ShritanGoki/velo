"""Milestone 8 test case #4: full loop against a real orderbook_server
and a real pricing_server. A companion FixedOffsetQuoter runs
concurrently on its own connection to provide counterparty liquidity for
DeltaHedger's market orders - this project's simulated sessions are
otherwise single-participant (see docs/synthetic-loop-design-doc.md §5),
and DeltaHedger, unlike FixedOffsetQuoter, never rests its own
liquidity, only ever crosses. See docs/delta-hedging-design-doc.md §8.
"""
import os
import socket
import subprocess
import threading
import time
import unittest

import _pathfix  # noqa: F401
from _server_process import ServerProcess

from orderbook_client.client import OrderBookClient
from orderbook_client.delta_hedger import DeltaHedger
from orderbook_client.feed import SyntheticPriceFeed
from orderbook_client.options import OptionBook, OptionLeg
from orderbook_client.pricing_client import PricingClient
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter
from orderbook_client.trade_log import TradeLog

ORDERBOOK_PORT = 19501
PRICING_PORT = 19502

_PRICING_SERVER_BIN = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "risk", "target", "debug", "pricing_server"))


class PricingServerProcess:
    def __init__(self, port: int):
        if not os.path.exists(_PRICING_SERVER_BIN):
            raise unittest.SkipTest(f"{_PRICING_SERVER_BIN} not built - run `cargo build --bins` in risk/ first")
        self.port = port
        self._proc = subprocess.Popen([_PRICING_SERVER_BIN, str(port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._wait_until_listening()

    def _wait_until_listening(self):
        for _ in range(200):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.01)
        raise RuntimeError("pricing_server never started listening")

    def stop(self):
        self._proc.terminate()
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()


class TestDeltaHedgeFullLoop(unittest.TestCase):
    def test_full_loop_keeps_hedge_error_bounded(self):
        server = ServerProcess(ORDERBOOK_PORT)
        pricing_server = PricingServerProcess(PRICING_PORT)

        def run_quoter_loop():
            feed = SyntheticPriceFeed(start_price_ticks=30000, seed=5)
            quoter = FixedOffsetQuoter(offset_ticks=1, size=50)
            log = TradeLog()
            client = OrderBookClient("127.0.0.1", ORDERBOOK_PORT)
            run_strategy_loop(feed, quoter, client, log, max_ticks=4000,
                               tick_poll_timeout=0.01, final_drain_timeout=0.05)

        quoter_thread = threading.Thread(target=run_quoter_loop, daemon=True)
        quoter_thread.start()
        time.sleep(0.2)  # let the quoter seed some resting liquidity first

        pricing_client = PricingClient("127.0.0.1", PRICING_PORT)
        try:
            options_book = OptionBook(
                [OptionLeg(strike_ticks=30000, time_to_expiry_years=0.25, vol=0.2, is_call=True, quantity=100)],
                rate=0.03,
            )
            hedger = DeltaHedger(options_book, pricing_client, hedge_band_contracts=5)
            hedger_log = TradeLog()
            hedger_client = OrderBookClient("127.0.0.1", ORDERBOOK_PORT)
            hedger_feed = SyntheticPriceFeed(start_price_ticks=30000, seed=7)

            run_strategy_loop(hedger_feed, hedger, hedger_client, hedger_log, max_ticks=200)
        finally:
            pricing_client.close()
            quoter_thread.join(timeout=5)
            pricing_server.stop()
            server.stop()

        self.assertGreater(hedger.rehedge_count, 0,
                            "a 100-lot options book should require at least one rehedge over 200 ticks")
        self.assertLess(max(abs(e) for e in hedger.hedge_error_history), 200,
                         "hedge error should stay bounded by repeated rehedging, not grow unboundedly")

        rows = hedger_log.rows
        sent_ids = {r["order_id"] for r in rows if r["event"] == "order_sent"}
        acked_ids = {r["order_id"] for r in rows if r["event"] == "ack"}
        self.assertTrue(sent_ids.issubset(acked_ids), "every rehedge order should have received an ack")


if __name__ == "__main__":
    unittest.main()
