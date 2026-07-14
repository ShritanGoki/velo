"""Milestone 5 test case #6: run_historical_backtest against a real
orderbook_server, using a fixture downloader (no network access).
See docs/historical-backtest-design-doc.md §9.
"""
import random
import shutil
import tempfile
import unittest

import _pathfix  # noqa: F401
from _server_process import ServerProcess

from orderbook_client.backtest_report import build_report
from orderbook_client.client import OrderBookClient
from orderbook_client.historical_feed import HistoricalPriceFeed
from orderbook_client.session_runner import run_strategy_loop
from orderbook_client.strategy import FixedOffsetQuoter
from orderbook_client.trade_log import TradeLog

TEST_PORT = 19301


def _build_fixture_closes(count: int) -> list:
    # A bounded random walk in ticks (same -2..+2 step shape as
    # SyntheticPriceFeed, Milestone 3), converted to a float price series -
    # standing in for real ES history so this test never touches the
    # network. The point is proving the wiring (feed -> strategy -> client
    # -> server -> report), not validating yfinance itself. Occasional
    # +-2-tick single-step moves are what let FixedOffsetQuoter's
    # offset_ticks=1 quotes actually cross (see its docstring) - a smooth
    # or purely 1-tick-per-step path would never generate a single fill.
    rng = random.Random(7)
    price_ticks = 19149  # 4787.25
    closes = []
    for _ in range(count):
        price_ticks += rng.choice((-2, -1, 0, 1, 2))
        closes.append(price_ticks * 0.25)
    return closes


FIXTURE_CLOSES = _build_fixture_closes(150)


def _fixture_downloader(ticker, start, end, interval):
    return list(FIXTURE_CLOSES)


class TestHistoricalBacktestFullLoop(unittest.TestCase):
    def test_full_loop_produces_a_consistent_report(self):
        server = ServerProcess(TEST_PORT)
        cache_dir = tempfile.mkdtemp()
        try:
            feed = HistoricalPriceFeed("ES=F", "2024-01-01", "2024-06-01", "1d",
                                        cache_dir, _fixture_downloader)
            strategy = FixedOffsetQuoter(offset_ticks=1, size=10)
            log = TradeLog()
            client = OrderBookClient("127.0.0.1", TEST_PORT)

            ticks_done = run_strategy_loop(feed, strategy, client, log)

            report = build_report(log.rows, periods_per_year=252)
        finally:
            server.stop()
            shutil.rmtree(cache_dir, ignore_errors=True)

        self.assertEqual(ticks_done, len(FIXTURE_CLOSES), "a finite feed should run out, not loop forever")

        rows = log.rows
        fill_count = sum(1 for r in rows if r["event"] == "fill")
        self.assertGreater(fill_count, 0, "this price path should cross the quoted spread at least once")

        # num_trades counts only *closing* fills (see build_report), so it
        # should never exceed the total fill count, and the report should
        # not have crashed producing a value for every field.
        self.assertLessEqual(report.num_trades, fill_count)
        self.assertIsInstance(report.sharpe_ratio, float)
        self.assertIsInstance(report.max_drawdown_ticks, int)


if __name__ == "__main__":
    unittest.main()
