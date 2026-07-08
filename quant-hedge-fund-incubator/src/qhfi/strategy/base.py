"""The Strategy contract.

A strategy is a **pure function of data**: it maps a price/feature panel to target weights.
No I/O, no broker, no clock. This is what makes strategies trivially backtestable,
composable into a book, and safe to generate with an LLM.

Critical invariant — **no look-ahead**: weights for date *t* may only use information
available at the close of *t* (they are applied to the *t+1* return inside the engine).
Concretely: shift any signal you derive from prices by at least one bar, or rely on the
engine's built-in one-bar execution lag.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

from qhfi.core.types import Panel, TargetWeights, Universe


class StrategyParams(BaseModel):
    """Base for per-strategy hyperparameters. Subclass with concrete fields so params are
    typed, serializable, and recordable in the registry (and tunable by the codegen agent)."""


class Strategy(ABC):
    """Subclass to add a strategy. The class name doubles as the registry key unless
    ``name`` is overridden."""

    name: str = ""
    params_model: type[StrategyParams] = StrategyParams

    def __init__(self, params: StrategyParams | None = None) -> None:
        self.params = params or self.params_model()
        if not self.name:
            self.name = type(self).__name__

    @abstractmethod
    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        """Return target weights (dates × instrument_id) given a wide close-price panel.

        Implementations MUST avoid look-ahead. The engine applies row *t* to the return
        from *t* → *t+1*, so weights computed from data through *t* are correct.
        """
        ...
