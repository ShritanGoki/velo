"""The float-to-tick conversion boundary. See docs/historical-backtest-design-doc.md §4.

This is the only place in the whole pipeline a float is allowed to touch
a price - everything downstream (Strategy, OrderBookClient, the wire
protocol, the matching engine) only ever sees plain ints, exactly as it
did with SyntheticPriceFeed's ticks-from-the-start prices.
"""
from __future__ import annotations

TICK_SIZE = 0.25  # ES: 0.25 index points = 1 tick ($12.50/tick)


def price_to_ticks(price: float) -> int:
    # round(), not int()/truncation: a real feed's prices won't land
    # exactly on tick boundaries the way synthetic ticks always did by
    # construction, and truncating would introduce a systematic downward
    # bias on every single price.
    return round(price / TICK_SIZE)


def ticks_to_price(ticks: int) -> float:
    return ticks * TICK_SIZE
