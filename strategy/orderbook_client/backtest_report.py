"""Offline backtest analytics. See docs/historical-backtest-design-doc.md §6, §8.

A second, independent implementation of the same simple "aggressor-side"
position accounting risk/src/position.rs uses (Milestone 4) - deliberately
duplicated rather than shared, since this is offline batch analysis in
Python over a completed trade log, not the live Rust risk engine acting
in real time. See the design doc §8 for why sharing code across that
boundary would be worse, not better.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence


@dataclass(frozen=True)
class BacktestReport:
    sharpe_ratio: float
    max_drawdown_ticks: int
    win_rate: float
    num_trades: int
    total_realized_pnl_ticks: int


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


class _PositionReplay:
    """Same math as risk/src/position.rs's PositionTracker - see that
    module's docstring for why every fill's incoming side is treated as
    the monitored account's trade direction."""

    def __init__(self):
        self.net_position = 0
        self.realized_pnl_ticks = 0
        self._avg_entry_price_ticks = 0.0

    def on_fill(self, incoming_side: int, quantity: int, price_ticks: int) -> int:
        """Applies one fill; returns this fill's realized P&L delta (0 for
        a fill that only opens/adds to a position)."""
        signed_qty = quantity if incoming_side == 0 else -quantity  # Side: 0 = Buy, 1 = Sell
        same_direction = self.net_position == 0 or _sign(self.net_position) == _sign(signed_qty)

        if same_direction:
            old_abs = abs(self.net_position)
            add_abs = abs(signed_qty)
            self._avg_entry_price_ticks = (
                (self._avg_entry_price_ticks * old_abs + price_ticks * add_abs) / (old_abs + add_abs)
            )
            self.net_position += signed_qty
            return 0

        direction = _sign(self.net_position)
        closing_qty = min(abs(signed_qty), abs(self.net_position))
        delta = int(round(direction * closing_qty * (price_ticks - self._avg_entry_price_ticks)))
        self.realized_pnl_ticks += delta
        self.net_position += signed_qty

        if self.net_position != 0 and _sign(self.net_position) != direction:
            self._avg_entry_price_ticks = float(price_ticks)

        return delta


def build_report(trade_log_rows: Sequence[Dict[str, Any]], periods_per_year: float) -> BacktestReport:
    replay = _PositionReplay()
    trade_pnls: List[int] = []
    equity_curve: List[int] = [0]

    for row in trade_log_rows:
        if row["event"] != "fill":
            continue
        delta = replay.on_fill(row["incoming_side"], row["qty"], row["price_ticks"])
        if delta != 0:
            trade_pnls.append(delta)
        equity_curve.append(equity_curve[-1] + delta)

    return BacktestReport(
        sharpe_ratio=_sharpe_ratio(trade_pnls, periods_per_year),
        max_drawdown_ticks=_max_drawdown(equity_curve),
        win_rate=_win_rate(trade_pnls),
        num_trades=len(trade_pnls),
        total_realized_pnl_ticks=replay.realized_pnl_ticks,
    )


def _sharpe_ratio(trade_pnls: Sequence[int], periods_per_year: float) -> float:
    if len(trade_pnls) < 2:
        return 0.0
    mean = statistics.mean(trade_pnls)
    stdev = statistics.stdev(trade_pnls)
    if stdev == 0:
        return 0.0
    return (mean / stdev) * (periods_per_year ** 0.5)


def _max_drawdown(equity_curve: Sequence[int]) -> int:
    peak = equity_curve[0]
    max_dd = 0
    for value in equity_curve:
        peak = max(peak, value)
        max_dd = min(max_dd, value - peak)
    return max_dd


def _win_rate(trade_pnls: Sequence[int]) -> float:
    if not trade_pnls:
        return 0.0
    wins = sum(1 for p in trade_pnls if p > 0)
    return wins / len(trade_pnls)
