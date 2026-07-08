"""Run a batch of orders through a LotBook and summarize the tax consequences.

This is where lot-selection tax optimization actually bites: ``reconcile.diff_to_orders`` decides
*how many* shares to trade to track the target; ``apply_orders`` decides *which lots* a sale
consumes (via ``method``) and computes realized short/long-term gains, wash-sale disallowances,
and estimated tax. BUYs add lots and also count as wash-sale replacement purchases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from qhfi.execution.base import Order, OrderSide
from qhfi.tax.lots import LotBook, LotMethod, RealizedGain
from qhfi.tax.rates import TaxRates
from qhfi.tax.wash_sale import flag_wash_sales


@dataclass
class TaxReport:
    realized: list[RealizedGain] = field(default_factory=list)
    st_gain: float = 0.0           # realized short-term gain (incl. losses)
    lt_gain: float = 0.0           # realized long-term gain (incl. losses)
    wash_disallowed: float = 0.0   # loss deferred by wash sales
    est_tax: float = 0.0           # tax on non-wash gains at ST/LT rates


def apply_orders(
    orders: list[Order],
    book: LotBook,
    prices: dict[str, float],
    when: date,
    method: LotMethod = LotMethod.FIFO,
    rates: TaxRates | None = None,
    *,
    recent_buys: list[tuple[str, date]] | None = None,
) -> TaxReport:
    """Process ``orders`` at ``prices`` on ``when``; mutate ``book``; return a TaxReport."""
    rates = rates or TaxRates()
    realized: list[RealizedGain] = []
    buys: list[tuple[str, date]] = list(recent_buys or [])

    for o in orders:
        price = prices[o.instrument_id]
        if o.side is OrderSide.BUY:
            book.buy(o.instrument_id, o.quantity, price, when)
            buys.append((o.instrument_id, when))
        else:
            realized += book.sell(o.instrument_id, o.quantity, price, when, method, rates=rates)

    flag_wash_sales(realized, buys)

    st = sum(r.gain for r in realized if not r.long_term)
    lt = sum(r.gain for r in realized if r.long_term)
    wash = sum(r.disallowed_loss for r in realized)
    # Wash-disallowed losses cannot offset gains this period.
    taxable_st = sum(r.gain for r in realized if not r.long_term and not r.wash)
    taxable_lt = sum(r.gain for r in realized if r.long_term and not r.wash)
    est_tax = rates.tax_on(taxable_st, False) + rates.tax_on(taxable_lt, True)
    return TaxReport(realized=realized, st_gain=st, lt_gain=lt, wash_disallowed=wash, est_tax=est_tax)
