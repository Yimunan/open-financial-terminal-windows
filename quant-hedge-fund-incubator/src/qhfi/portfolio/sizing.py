"""Position sizing — converts a strategy's target *weight* into target *units*, routed by
the instrument's risk basis.

This is the layer FICC forces into existence. For most assets a "weight" is a fraction of
equity (notional sizing). For rates/credit, $1m of a 2Y note and $1m of a 30Y bond carry
wildly different risk, so they are sized by **DV01** (dollar value of a 1bp move) against a
risk budget instead. ``CompositeSizing`` dispatches on ``Instrument.risk_basis`` so a single
mixed-asset ``TargetWeights`` frame means the right thing per column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from qhfi.core.types import Instrument, RiskBasis


@dataclass
class SizingConfig:
    # A DV01-sized weight of 1.0 targets a portfolio DV01 of this fraction of equity, i.e.
    # a 1bp rate move moves the book by (weight · dv01_budget_per_equity · equity).
    dv01_budget_per_equity: float = 0.0005


class SizingModel(Protocol):
    def target_units(self, instrument: Instrument, weight: float, equity: float, price: float) -> float:
        ...


class NotionalSizing:
    """weight = fraction of equity. units = weight·equity / (price·multiplier)."""

    def target_units(self, instrument: Instrument, weight: float, equity: float, price: float) -> float:
        denom = price * instrument.contract_multiplier
        return weight * equity / denom if denom else 0.0


class DV01Sizing:
    """weight = fraction of the DV01 risk budget. Requires ``modified_duration``.

    DV01 per unit = modified_duration · price · multiplier / 10_000.
    target DV01     = weight · equity · dv01_budget_per_equity.
    units           = target DV01 / DV01-per-unit.
    """

    def __init__(self, cfg: SizingConfig | None = None) -> None:
        self.cfg = cfg or SizingConfig()

    def target_units(self, instrument: Instrument, weight: float, equity: float, price: float) -> float:
        md = instrument.modified_duration
        if not md or price <= 0:
            return 0.0
        dv01_per_unit = md * price * instrument.contract_multiplier / 10_000.0
        if dv01_per_unit == 0:
            return 0.0
        target_dv01 = weight * equity * self.cfg.dv01_budget_per_equity
        return target_dv01 / dv01_per_unit


class CompositeSizing:
    """Routes by ``Instrument.risk_basis``: DV01 for rates/credit, notional otherwise."""

    def __init__(self, dv01_cfg: SizingConfig | None = None) -> None:
        self.notional = NotionalSizing()
        self.dv01 = DV01Sizing(dv01_cfg)

    def target_units(self, instrument: Instrument, weight: float, equity: float, price: float) -> float:
        model = self.dv01 if instrument.risk_basis is RiskBasis.DV01 else self.notional
        return model.target_units(instrument, weight, equity, price)
