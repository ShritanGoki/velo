"""The (simulated) options position model. See
docs/delta-hedging-design-doc.md §4.

OptionBook is a fixed, assumed set of option legs - as if acquired
through some other desk or process this project doesn't model.
OrderBook has been single-instrument (ES futures) since Milestone 1, so
these legs are never themselves traded here; they're the input the
hedging problem is defined against. ES options are physically settled
into ES futures and share the same contract multiplier, so an option's
delta already *is* its ES-futures-equivalent exposure per contract - no
unit conversion beyond summing delta * quantity across legs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .pricing_client import PricingClient


@dataclass(frozen=True)
class OptionLeg:
    strike_ticks: int
    time_to_expiry_years: float
    vol: float
    is_call: bool
    quantity: int  # + long, - short, in ES-option contracts

    def delta(self, spot_ticks: int, rate: float, pricing_client: PricingClient) -> float:
        greeks = pricing_client.price(spot_ticks, self.strike_ticks, rate, self.vol,
                                       self.time_to_expiry_years, self.is_call)
        return greeks.delta

    def value(self, spot_ticks: int, rate: float, pricing_client: PricingClient) -> float:
        greeks = pricing_client.price(spot_ticks, self.strike_ticks, rate, self.vol,
                                       self.time_to_expiry_years, self.is_call)
        return greeks.price


class OptionBook:
    def __init__(self, legs: List[OptionLeg], rate: float):
        self._legs = legs
        self._rate = rate

    def aggregate_delta(self, spot_ticks: int, pricing_client: PricingClient) -> float:
        return sum(leg.delta(spot_ticks, self._rate, pricing_client) * leg.quantity for leg in self._legs)

    def aggregate_value(self, spot_ticks: int, pricing_client: PricingClient) -> float:
        """Mark-to-market value of the whole book (in index points), for
        the unhedged-vs-hedged P&L comparison in
        docs/delta-hedging-design-doc.md §7."""
        return sum(leg.value(spot_ticks, self._rate, pricing_client) * leg.quantity for leg in self._legs)
