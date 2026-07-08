"""The Factor contract.

A factor is a **pure function of data** that maps a price/feature panel to a *raw score*
panel (dates × instrument_id). Factors are the reusable building blocks beneath strategies:
a strategy typically standardizes/neutralizes one or more factors and converts the blend
into ``TargetWeights``.

Like ``Strategy``, factors must avoid look-ahead — a score at date *t* may only use data
through *t*. They are evaluated against *forward* returns in ``factors.evaluation``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from qhfi.core.types import Panel, Universe


class FactorParams(BaseModel):
    """Base for per-factor hyperparameters (typed, serializable, registry-recordable)."""


class Factor(ABC):
    """Subclass to add a factor. ``direction`` states which sign is 'good': +1 means a
    higher raw score should be long, -1 means lower is better (e.g. low-volatility)."""

    name: str = ""
    direction: int = 1
    params_model: type[FactorParams] = FactorParams

    def __init__(self, params: FactorParams | None = None) -> None:
        self.params = params or self.params_model()
        if not self.name:
            self.name = type(self).__name__

    @abstractmethod
    def compute(self, prices: Panel, universe: Universe) -> Panel:
        """Return a raw factor-score panel (dates × instrument_id). No look-ahead."""
        ...

    def signed(self, prices: Panel, universe: Universe) -> Panel:
        """Raw scores multiplied by ``direction`` so that higher always means 'more long'."""
        return self.compute(prices, universe) * self.direction
