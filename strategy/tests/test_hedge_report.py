"""Milestone 8 test case #5: HedgeReport against a scripted scenario
with a hand-computed variance reduction. See docs/delta-hedging-design-doc.md §8.
"""
import statistics
import unittest

import _pathfix  # noqa: F401

from orderbook_client.hedge_report import build_hedge_report


class TestHedgeReport(unittest.TestCase):
    def test_variance_reduction_matches_hand_computed_value(self):
        # A hedge that exactly offsets the options book's P&L swings:
        # unhedged P&L moves a lot; hedged P&L stays flat (perfect hedge).
        unhedged_pnl = [0.0, 10.0, -8.0, 15.0, -12.0]
        hedged_pnl = [0.0, 0.5, -0.3, 0.4, -0.2]  # small residual, imperfect but effective hedge

        hedge_errors = [0, 2, 1, 3, 0]
        rehedge_count = 3

        report = build_hedge_report(hedge_errors, rehedge_count, hedged_pnl, unhedged_pnl)

        expected_hedged_var = statistics.pvariance(hedged_pnl)
        expected_unhedged_var = statistics.pvariance(unhedged_pnl)
        expected_reduction = (1.0 - expected_hedged_var / expected_unhedged_var) * 100.0

        self.assertAlmostEqual(report.hedged_pnl_variance, expected_hedged_var)
        self.assertAlmostEqual(report.unhedged_pnl_variance, expected_unhedged_var)
        self.assertAlmostEqual(report.variance_reduction_pct, expected_reduction)
        self.assertGreater(report.variance_reduction_pct, 90.0, "a near-perfect hedge should show >90% variance reduction")

        self.assertEqual(report.rehedge_count, 3)
        self.assertAlmostEqual(report.mean_abs_hedge_error_contracts, statistics.mean([0, 2, 1, 3, 0]))

    def test_a_hedge_that_does_not_reduce_variance_shows_it(self):
        # An unhelpful "hedge" with the same variance as doing nothing -
        # variance_reduction_pct should be ~0, not a misleadingly positive number.
        pnl = [0.0, 5.0, -5.0, 5.0, -5.0]
        report = build_hedge_report([0, 0, 0, 0, 0], 0, pnl, pnl)
        self.assertAlmostEqual(report.variance_reduction_pct, 0.0)

    def test_zero_unhedged_variance_does_not_divide_by_zero(self):
        report = build_hedge_report([0], 0, [1.0, 2.0], [5.0, 5.0])
        self.assertEqual(report.variance_reduction_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
