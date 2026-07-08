"""Wash-sale detection.

A wash sale disallows the loss on a security sold at a loss if a "substantially identical"
security is bought within 30 days before or after the sale; the disallowed loss is deferred
into the replacement lot's basis. This module flags realized losses whose ``instrument_id`` was
(re)purchased inside the ±window. Simplification: identity is exact ``instrument_id`` match (no
"substantially identical" judgement across tickers/options), and we flag rather than re-base.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from qhfi.tax.lots import RealizedGain


def flag_wash_sales(
    realized: list[RealizedGain],
    buys: Iterable[tuple[str, date]],
    window_days: int = 30,
) -> list[RealizedGain]:
    """Mark each loss in ``realized`` as a wash sale if a buy of the same instrument falls
    within ±``window_days`` of the sale. Mutates and returns ``realized``."""
    buys = list(buys)
    for r in realized:
        if r.gain >= 0:
            continue
        if any(bid == r.instrument_id and abs((r.sold - bdate).days) <= window_days
               for bid, bdate in buys):
            r.wash = True
            r.disallowed_loss = -r.gain   # positive loss amount deferred
    return realized
