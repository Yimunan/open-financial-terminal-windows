"""Marginal tax rates for realized gains.

A deliberately simple model — flat short- and long-term rates — sufficient for a research /
paper-trading incubator. ``tax_on`` returns the tax owed on a gain (negative for a loss, i.e.
a tax *benefit* that offsets other gains). This is not tax advice and omits brackets, NIIT,
state tax, and the §1211 capital-loss limitation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxRates:
    short_term: float = 0.37   # ordinary-income rate (held ≤ 1 year)
    long_term: float = 0.20    # preferential rate (held > 1 year)

    def rate(self, long_term: bool) -> float:
        return self.long_term if long_term else self.short_term

    def tax_on(self, gain: float, long_term: bool) -> float:
        """Tax owed on ``gain`` (negative = benefit from a loss)."""
        return gain * self.rate(long_term)
