"""Synthetic, tick-aligned price feed. See docs/synthetic-loop-design-doc.md §4.

Deliberately dumb (a bounded random walk, not a realistic price process) —
realism isn't the point until Milestone 5's real data arrives. Prices are
generated directly in integer ticks so there's no float-to-tick conversion
step for the wire protocol to get subtly wrong.
"""
from __future__ import annotations

import random

_STEPS = (-2, -1, 0, 1, 2)


class SyntheticPriceFeed:
    def __init__(self, start_price_ticks: int, seed: int):
        self._price = start_price_ticks
        self._rng = random.Random(seed)

    def next_price(self) -> int:
        self._price += self._rng.choice(_STEPS)
        return self._price
