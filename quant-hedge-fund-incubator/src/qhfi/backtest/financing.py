"""Financing / carry — the daily cost of *holding* a book, distinct from trading it.

Real strategies bleed (or earn) money every day they hold positions, independent of
turnover:

* **short borrow** — a fee on the notional you are short (hard-to-borrow names cost more),
* **leverage financing** — interest on cash you borrowed to run gross exposure > equity,
* **cash interest** — what idle long cash earns (a credit, hence subtracted).

Ignoring these flatters levered or short-heavy strategies. Rates are annualized bps;
``days_per_year`` is 360 by market convention for money-market accrual.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FinancingModel:
    short_borrow_bps: float = 50.0   # annualized, charged on short notional
    leverage_bps: float = 100.0      # annualized, charged on borrowed cash (gross > equity)
    cash_bps: float = 0.0            # annualized, earned on positive cash
    days_per_year: int = 360

    def daily_carry(
        self, cash: float, equity: float, long_notional: float, short_notional: float
    ) -> float:
        """Net cost (positive = drag) of carrying the current book for one day."""
        d = self.days_per_year
        borrow = short_notional * self.short_borrow_bps / 1e4 / d
        gross = long_notional + short_notional
        financing = max(0.0, gross - equity) * self.leverage_bps / 1e4 / d
        interest = max(0.0, cash) * self.cash_bps / 1e4 / d
        return borrow + financing - interest
