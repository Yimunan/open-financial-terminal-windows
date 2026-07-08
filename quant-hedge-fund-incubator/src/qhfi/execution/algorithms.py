"""Execution algorithms — schedule a large parent order to limit market impact.

The project trades on **daily bars**, so there is no intraday volume curve to slice against.
The realistic lever at daily frequency is *how many days to spread a large order over*: trade
too much of a name's average daily volume (ADV) in one day and you pay outsized impact. These
algorithms split a parent ``Order`` into per-day ``Slice``s:

  * ``TWAP`` — even quantity across a fixed number of days.
  * ``POV``  — participation-of-volume: cap each day at ``rate`` × ADV, so the horizon grows
    with order size.

``MarketImpactModel`` is a square-root impact law (cost ∝ √participation) — the standard
stylized model — and is reusable as a size-aware complement to the flat
``backtest.fills.SlippageModel``. ADV is available from ``factors.market.MarketPanels.adv``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from qhfi.execution.base import Order, OrderSide


@dataclass
class MarketImpactModel:
    """Square-root market impact: ``impact_bps = eta * sqrt(participation)`` where
    ``participation = |quantity| / adv`` (a day's trade as a fraction of average daily volume).
    ``eta`` is the impact at 100% participation, in basis points of notional."""

    eta: float = 10.0

    def participation(self, quantity: float, adv: float) -> float:
        return abs(quantity) / adv if adv > 0 else 0.0

    def impact_bps(self, quantity: float, adv: float) -> float:
        return self.eta * math.sqrt(self.participation(quantity, adv))

    def impact_cost(self, quantity: float, adv: float, price: float) -> float:
        """Impact cost in quote currency for trading ``quantity`` units at ``price``."""
        return abs(quantity) * price * self.impact_bps(quantity, adv) / 1e4


@dataclass
class Slice:
    """One day's child order within a schedule (quantity is positive; ``side`` carries sign)."""

    day: int
    side: OrderSide
    quantity: float
    participation: float
    impact_bps: float


class ExecutionAlgorithm(Protocol):
    def schedule(self, order: Order, adv: float, price: float) -> list[Slice]:
        """Split ``order`` into per-day slices given the name's ADV (shares/day)."""
        ...


def _slice(day: int, order: Order, qty: float, adv: float, impact: MarketImpactModel) -> Slice:
    return Slice(day=day, side=order.side, quantity=qty,
                 participation=impact.participation(qty, adv),
                 impact_bps=impact.impact_bps(qty, adv))


@dataclass
class TWAP:
    """Even split of the parent quantity across ``horizon_days`` (time-weighted)."""

    horizon_days: int = 5
    impact: MarketImpactModel = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.impact = self.impact or MarketImpactModel()
        self.horizon_days = max(1, int(self.horizon_days))

    def schedule(self, order: Order, adv: float, price: float) -> list[Slice]:
        per_day = abs(order.quantity) / self.horizon_days
        if per_day <= 0:
            return []
        return [_slice(d, order, per_day, adv, self.impact) for d in range(self.horizon_days)]


@dataclass
class POV:
    """Participation-of-volume: each day trades at most ``rate`` × ADV. The horizon is derived
    from order size — ``ceil(|qty| / (rate*adv))`` days — capped at ``max_days`` (the cap forces
    the final day(s) above the participation target; that overshoot is reported on the slice)."""

    rate: float = 0.10
    max_days: int = 20
    impact: MarketImpactModel = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.impact = self.impact or MarketImpactModel()

    def schedule(self, order: Order, adv: float, price: float) -> list[Slice]:
        qty = abs(order.quantity)
        if qty <= 0:
            return []
        per_day = self.rate * adv
        if per_day <= 0:                       # no volume info → execute in one slice
            return [_slice(0, order, qty, adv, self.impact)]
        days = min(self.max_days, math.ceil(qty / per_day))
        base = qty / days if days >= self.max_days else per_day  # spread evenly if capped
        slices, remaining = [], qty
        for d in range(days):
            take = remaining if d == days - 1 else min(base, remaining)
            slices.append(_slice(d, order, take, adv, self.impact))
            remaining -= take
        return slices


def expected_cost_bps(slices: list[Slice]) -> float:
    """Quantity-weighted average impact (bps) of a schedule."""
    total = sum(s.quantity for s in slices)
    if total <= 0:
        return 0.0
    return sum(s.impact_bps * s.quantity for s in slices) / total
