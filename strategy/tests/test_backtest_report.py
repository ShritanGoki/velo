"""Milestone 5 test cases #4 and #5: BacktestReport against scripted trade
logs with hand-computed expected values. See
docs/historical-backtest-design-doc.md §9.
"""
import statistics
import unittest

import _pathfix  # noqa: F401

from orderbook_client.backtest_report import build_report

BUY = 0
SELL = 1


def fill_row(incoming_side, qty, price_ticks):
    return {"event": "fill", "incoming_side": incoming_side, "qty": qty, "price_ticks": price_ticks}


class TestBacktestReportKnownValues(unittest.TestCase):
    def test_sharpe_win_rate_and_realized_pnl(self):
        rows = [
            fill_row(BUY, 10, 100),   # opens long 10 @ 100, no realized P&L yet
            fill_row(SELL, 4, 105),   # closes 4 @ 105: +20 (a win)
            fill_row(SELL, 6, 98),    # closes remaining 6 @ 98: -12 (a loss)
        ]

        report = build_report(rows, periods_per_year=1)

        expected_pnls = [20, -12]
        expected_mean = statistics.mean(expected_pnls)
        expected_stdev = statistics.stdev(expected_pnls)
        expected_sharpe = expected_mean / expected_stdev  # periods_per_year=1

        self.assertEqual(report.num_trades, 2)
        self.assertAlmostEqual(report.win_rate, 0.5)
        self.assertEqual(report.total_realized_pnl_ticks, 8)
        self.assertAlmostEqual(report.sharpe_ratio, expected_sharpe)

    def test_max_drawdown_is_not_just_first_to_last_difference(self):
        rows = [
            fill_row(BUY, 100, 1000),
            fill_row(SELL, 100, 1001),  # +100: equity 0 -> 100 (a peak)
            fill_row(BUY, 150, 1000),
            fill_row(SELL, 150, 999),   # -150: equity 100 -> -50 (the trough)
            fill_row(BUY, 200, 1000),
            fill_row(SELL, 200, 1001),  # +200: equity -50 -> 150 (a new peak, above the first)
        ]

        report = build_report(rows, periods_per_year=1)

        # A buggy "just diff the endpoints" implementation would see
        # 150 - 0 = +150 (no drawdown at all). The real max drawdown is the
        # decline from the first peak (100) to the trough (-50).
        self.assertEqual(report.max_drawdown_ticks, -150)
        self.assertEqual(report.total_realized_pnl_ticks, 150)
        self.assertEqual(report.num_trades, 3)


if __name__ == "__main__":
    unittest.main()
