"""Per-lot cost-basis accounting — the foundation of tax optimization.

The execution ``Position`` carries only a scalar ``avg_price``, which is enough to mark a book
but not to reason about tax: realized gains, holding periods, and wash sales all depend on
*which* lots are sold. ``LotBook`` tracks individual purchase lots and, on a sale, selects lots
by a chosen ``LotMethod`` and emits ``RealizedGain``s classified short- vs long-term.

Lot selection IS the tax lever: HIFO (highest cost first) minimizes the realized gain; MIN_TAX
orders lots by their actual tax (losses first, then long-term, then short-term gains) using the
supplied ``TaxRates``. Equities/cash only — derivatives have different tax regimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from qhfi.tax.rates import TaxRates

LONG_TERM_DAYS = 365  # held strictly more than one year ( > 365 days) is long-term


class LotMethod(str, Enum):
    FIFO = "fifo"        # oldest lots first
    LIFO = "lifo"        # newest lots first
    HIFO = "hifo"        # highest-cost first → smallest realized gain
    MIN_TAX = "min_tax"  # lowest tax first (losses, then long-term, then short-term gains)


@dataclass
class TaxLot:
    instrument_id: str
    quantity: float      # remaining shares in this lot (> 0)
    price: float         # cost basis per share
    acquired: date


@dataclass
class RealizedGain:
    instrument_id: str
    quantity: float
    proceeds: float
    cost_basis: float
    gain: float          # proceeds - cost_basis (negative = loss)
    long_term: bool
    acquired: date
    sold: date
    wash: bool = False
    disallowed_loss: float = 0.0   # loss amount (positive) deferred by a wash sale


def _is_long_term(acquired: date, sold: date, long_term_days: int) -> bool:
    return (sold - acquired).days > long_term_days


class LotBook:
    """Per-instrument list of open tax lots, FIFO-ordered by insertion."""

    def __init__(self) -> None:
        self._book: dict[str, list[TaxLot]] = {}

    # ── inspection ────────────────────────────────────────────────────────────
    def instruments(self) -> list[str]:
        return [k for k, v in self._book.items() if v]

    def lots(self, instrument_id: str) -> list[TaxLot]:
        return list(self._book.get(instrument_id, []))

    def quantity(self, instrument_id: str) -> float:
        return sum(l.quantity for l in self._book.get(instrument_id, []))

    def unrealized(self, instrument_id: str, price: float) -> float:
        """Mark-to-market gain across all open lots of ``instrument_id`` at ``price``."""
        return sum(l.quantity * (price - l.price) for l in self._book.get(instrument_id, []))

    # ── mutation ──────────────────────────────────────────────────────────────
    def buy(self, instrument_id: str, quantity: float, price: float, when: date) -> None:
        if quantity <= 0:
            return
        self._book.setdefault(instrument_id, []).append(
            TaxLot(instrument_id, quantity, price, when))

    def sell(
        self,
        instrument_id: str,
        quantity: float,
        price: float,
        when: date,
        method: LotMethod = LotMethod.FIFO,
        *,
        long_term_days: int = LONG_TERM_DAYS,
        rates: TaxRates | None = None,
    ) -> list[RealizedGain]:
        """Consume lots (clamped to holdings) per ``method``; return realized gains.

        Sells more than held are clamped to the held quantity (no short-lot tracking)."""
        lots = self._book.get(instrument_id, [])
        remaining = min(quantity, sum(l.quantity for l in lots))
        if remaining <= 0:
            return []
        ordered = self._order(lots, method, price, when, long_term_days, rates or TaxRates())

        realized: list[RealizedGain] = []
        for lot in ordered:
            if remaining <= 1e-12:
                break
            take = min(lot.quantity, remaining)
            basis = take * lot.price
            proceeds = take * price
            realized.append(RealizedGain(
                instrument_id=instrument_id, quantity=take, proceeds=proceeds,
                cost_basis=basis, gain=proceeds - basis,
                long_term=_is_long_term(lot.acquired, when, long_term_days),
                acquired=lot.acquired, sold=when,
            ))
            lot.quantity -= take
            remaining -= take

        self._book[instrument_id] = [l for l in lots if l.quantity > 1e-12]
        return realized

    @staticmethod
    def _order(
        lots: list[TaxLot], method: LotMethod, price: float, when: date,
        long_term_days: int, rates: TaxRates,
    ) -> list[TaxLot]:
        if method is LotMethod.FIFO:
            return sorted(lots, key=lambda l: l.acquired)
        if method is LotMethod.LIFO:
            return sorted(lots, key=lambda l: l.acquired, reverse=True)
        if method is LotMethod.HIFO:
            return sorted(lots, key=lambda l: l.price, reverse=True)
        # MIN_TAX: lowest per-share tax first → losses, then long-term gains, then short-term.
        def tax_key(l: TaxLot) -> float:
            lt = _is_long_term(l.acquired, when, long_term_days)
            return rates.tax_on(price - l.price, lt)
        return sorted(lots, key=tax_key)
