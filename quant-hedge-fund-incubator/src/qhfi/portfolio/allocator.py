"""Fund-level capital allocation across multiple incubated strategies.

Each strategy emits its own ``TargetWeights``; the allocator blends them into a single
book-level weight schedule. This is where the "incubator" becomes a "fund": promoted
strategies get capital by rule, not by hand.
"""

from __future__ import annotations

from typing import Protocol

import pandas as pd

from qhfi.core.types import TargetWeights


class Allocator(Protocol):
    def allocate(self, strategy_weights: dict[str, TargetWeights]) -> TargetWeights:
        """Combine per-strategy weights into one book-level weight schedule."""
        ...


class EqualWeightAllocator:
    """Equal capital per strategy; sum the scaled weights into a book."""

    def allocate(self, strategy_weights: dict[str, TargetWeights]) -> TargetWeights:
        raise NotImplementedError("TODO: scale each by 1/N and sum (align on union index)")


class VolTargetAllocator:
    """Inverse-vol weight each strategy and scale the book to a target annual volatility."""

    def __init__(self, target_vol: float = 0.10, lookback: int = 90) -> None:
        self.target_vol, self.lookback = target_vol, lookback

    def allocate(self, strategy_weights: dict[str, TargetWeights]) -> TargetWeights:
        raise NotImplementedError("TODO: inverse-vol blend + book-level vol scaling")
