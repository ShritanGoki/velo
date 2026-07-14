"""Flat, append-only trade log. See docs/synthetic-loop-design-doc.md §8.

No derived statistics here on purpose (no P&L, no Sharpe) - Milestone 5
builds the actual backtest report on top of this log once real data
replaces the synthetic feed, so the format needs to stay the same
regardless of which feed produced it.
"""
from __future__ import annotations

import csv
import time
from typing import Any, Dict, List


class TradeLog:
    def __init__(self):
        self._rows: List[Dict[str, Any]] = []

    def order_sent(self, order_id: int, side: int, price_ticks: int, qty: int) -> None:
        self._rows.append({"event": "order_sent", "order_id": order_id, "side": int(side),
                            "price_ticks": price_ticks, "qty": qty, "ts": time.time()})

    def cancel_sent(self, order_id: int) -> None:
        self._rows.append({"event": "cancel_sent", "order_id": order_id, "ts": time.time()})

    def ack(self, order_id: int, status: int) -> None:
        self._rows.append({"event": "ack", "order_id": order_id, "status": status,
                            "ts": time.time()})

    def fill(self, resting_id: int, incoming_id: int, price_ticks: int, qty: int) -> None:
        self._rows.append({"event": "fill", "resting_id": resting_id, "incoming_id": incoming_id,
                            "price_ticks": price_ticks, "qty": qty, "ts": time.time()})

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def to_csv(self, path: str) -> None:
        fieldnames = sorted({key for row in self._rows for key in row})
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self._rows)
