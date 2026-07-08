"""Built-in factor library.

Price/return-derived factors (momentum, volatility, short-term reversal) are fully
implemented — they need only the close panel the data layer already provides. Factors that
require fundamentals or carry data (value, quality, carry) are typed stubs that declare the
extra input they need; wire them once a fundamentals provider exists.
"""

from __future__ import annotations

from qhfi.core.types import Panel, Universe
from qhfi.factors.base import Factor, FactorParams
from qhfi.factors.registry import register


class MomentumParams(FactorParams):
    lookback: int = 90
    gap: int = 5


@register
class MomentumFactor(Factor):
    """Trailing total return over ``lookback`` days, skipping the most recent ``gap`` days
    to sidestep short-term reversal. Higher = stronger momentum = long."""

    name = "momentum"
    direction = 1
    params_model = MomentumParams

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        p: MomentumParams = self.params  # type: ignore[assignment]
        return prices.shift(p.gap) / prices.shift(p.gap + p.lookback) - 1.0


class VolatilityParams(FactorParams):
    window: int = 60


@register
class VolatilityFactor(Factor):
    """Trailing realized volatility of daily returns. ``direction = -1`` encodes the
    low-volatility anomaly: lower vol → more long."""

    name = "volatility"
    direction = -1
    params_model = VolatilityParams

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        p: VolatilityParams = self.params  # type: ignore[assignment]
        return prices.pct_change().rolling(p.window).std()


class ReversalParams(FactorParams):
    window: int = 5


@register
class ShortTermReversalFactor(Factor):
    """Recent short-window return; ``direction = -1`` bets on mean reversion (recent losers
    bounce). Complements momentum at a different horizon."""

    name = "reversal"
    direction = -1
    params_model = ReversalParams

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        p: ReversalParams = self.params  # type: ignore[assignment]
        return prices.pct_change(p.window)


class FundamentalFactor(Factor):
    """Wraps a point-in-time fundamentals panel (from data.fundamentals.FundamentalsStore)
    as a factor. Constructed with the metric panel rather than instantiated zero-arg, since
    it carries data — so these are built explicitly, not pulled from the string registry.

    compute() reindexes the (sparse, report-date-stamped) panel onto the daily price grid
    and forward-fills, so the factor value on any date is the latest *publicly known* figure
    — never a future restatement (the PIT look-ahead guard).
    """

    def __init__(self, metric_panel: Panel, params: FactorParams | None = None) -> None:
        super().__init__(params)
        self._panel = metric_panel

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        return self._panel.reindex(index=prices.index, columns=prices.columns).ffill()


@register
class ValueFactor(FundamentalFactor):
    """Cheapness from a fundamental ratio (earnings_yield = E/P, book_yield = B/P, …).
    Higher yield = cheaper = long. Build with the corresponding fundamentals panel:
    ``ValueFactor(store.panel(universe.instruments, "earnings_yield"))``."""

    name = "value"
    direction = 1


@register
class QualityFactor(FundamentalFactor):
    """Profitability/soundness (ROE, gross margin, low leverage). Higher = better = long.
    Build with e.g. ``QualityFactor(store.panel(universe.instruments, "roe"))``."""

    name = "quality"
    direction = 1


@register
class CarryFactor(Factor):
    """Yield earned for holding (funding rate for crypto perps, roll yield for futures,
    dividend yield for equities). Requires asset-class carry data."""

    name = "carry"
    direction = 1

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        raise NotImplementedError("TODO: needs carry/funding/roll-yield panel per asset class")
