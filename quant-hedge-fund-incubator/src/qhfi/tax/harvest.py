"""Tax-loss harvesting — surface positions with unrealized losses worth realizing.

Selling an underwater lot realizes a loss that offsets taxable gains. This proposes harvest
candidates: instruments whose *losing* lots sum to at least ``min_loss``, skipping any name in
``recent_buys`` (selling it now would trigger a wash sale). Only underwater lots are counted —
a name with both winners and losers contributes only its losers.

The estimated benefit assumes harvested losses offset the highest-taxed gains first (the
short-term rate), the realistic marginal value of a fresh loss.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from qhfi.tax.lots import LotBook
from qhfi.tax.rates import TaxRates


@dataclass
class HarvestCandidate:
    instrument_id: str
    quantity: float        # shares in underwater lots, eligible to sell for the loss
    est_loss: float        # positive loss amount that would be realized
    est_tax_benefit: float # est_loss × short-term rate


def harvest_candidates(
    book: LotBook,
    prices: dict[str, float],
    rates: TaxRates | None = None,
    *,
    min_loss: float = 100.0,
    recent_buys: Iterable[str] = (),
) -> list[HarvestCandidate]:
    rates = rates or TaxRates()
    blocked = set(recent_buys)
    out: list[HarvestCandidate] = []
    for iid in book.instruments():
        price = prices.get(iid)
        if price is None or iid in blocked:
            continue
        loss_lots = [(l.quantity, l.price - price) for l in book.lots(iid) if l.price > price]
        est_loss = sum(q * d for q, d in loss_lots)
        if est_loss >= min_loss:
            qty = sum(q for q, _ in loss_lots)
            out.append(HarvestCandidate(iid, qty, est_loss, est_loss * rates.short_term))
    return sorted(out, key=lambda c: c.est_tax_benefit, reverse=True)
