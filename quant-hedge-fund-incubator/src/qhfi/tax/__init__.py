"""Tax optimization: per-lot accounting, lot selection, wash sales, and loss harvesting.

Faithful-but-simplified (flat ST/LT rates, exact-ticker wash-sale matching, no §1211 limit or
straddle rules) — built for a research / paper-trading incubator, not tax filing. Equities only.
"""

from qhfi.tax.apply import TaxReport, apply_orders
from qhfi.tax.harvest import HarvestCandidate, harvest_candidates
from qhfi.tax.lots import LotBook, LotMethod, RealizedGain, TaxLot
from qhfi.tax.rates import TaxRates
from qhfi.tax.wash_sale import flag_wash_sales

__all__ = [
    "TaxLot", "LotBook", "LotMethod", "RealizedGain", "TaxRates",
    "flag_wash_sales", "HarvestCandidate", "harvest_candidates",
    "TaxReport", "apply_orders",
]
