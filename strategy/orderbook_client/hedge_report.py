"""Hedge-effectiveness backtest metrics. See docs/delta-hedging-design-doc.md §7.

Extends orderbook_client/backtest_report.py's BacktestReport rather than
replacing it - Sharpe/drawdown/win-rate still apply to the futures P&L;
these are additional, hedging-specific numbers computed from a session's
recorded hedge-error samples and two parallel mark-to-market P&L series
(the actual hedged portfolio vs. a hypothetical unhedged one holding
only the naked options book).

A pure function over pre-computed sequences, deliberately decoupled from
how those sequences get built (a live session, a backtest replay, or a
hand-scripted test scenario) - that's what makes variance_reduction_pct
testable against hand-computed expected values rather than only
checkable end-to-end.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class HedgeReport:
    mean_abs_hedge_error_contracts: float
    rehedge_count: int
    hedged_pnl_variance: float
    unhedged_pnl_variance: float
    variance_reduction_pct: float  # 1 - hedged/unhedged, the "did this help" number


def build_hedge_report(hedge_error_history: Sequence[int], rehedge_count: int,
                        hedged_pnl_series: Sequence[float],
                        unhedged_pnl_series: Sequence[float]) -> HedgeReport:
    mean_abs_hedge_error = statistics.mean(abs(e) for e in hedge_error_history) if hedge_error_history else 0.0

    hedged_var = statistics.pvariance(hedged_pnl_series) if len(hedged_pnl_series) > 1 else 0.0
    unhedged_var = statistics.pvariance(unhedged_pnl_series) if len(unhedged_pnl_series) > 1 else 0.0

    variance_reduction_pct = (1.0 - hedged_var / unhedged_var) * 100.0 if unhedged_var > 0 else 0.0

    return HedgeReport(
        mean_abs_hedge_error_contracts=mean_abs_hedge_error,
        rehedge_count=rehedge_count,
        hedged_pnl_variance=hedged_var,
        unhedged_pnl_variance=unhedged_var,
        variance_reduction_pct=variance_reduction_pct,
    )
