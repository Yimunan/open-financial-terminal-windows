"""FactorStrategy — turns a (blended) factor into a tradable strategy.

This is the bridge from the ``factors`` layer to the ``Strategy`` contract: take one or
more factors, apply standard hygiene (winsorize → rank/zscore → optional neutralize →
combine), then map the composite score to long/short ``TargetWeights``. Most cross-sectional
strategies are an instance of this with different factor choices.
"""

from __future__ import annotations

import pandas as pd

from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.factors import transforms
from qhfi.factors.base import Factor
from qhfi.strategy.base import Strategy, StrategyParams


class FactorStrategyParams(StrategyParams):
    quantile: float = 0.2     # long top / short bottom fraction
    gross: float = 1.0        # target gross exposure
    long_only: bool = False
    winsor: float = 0.02      # symmetric winsorization tail


def long_short_weights(
    score: Panel, *, quantile: float = 0.2, gross: float = 1.0, long_only: bool = False
) -> TargetWeights:
    """Map a composite score panel to top/bottom-quantile, gross-scaled target weights.

    Per date: rank the score cross-sectionally, long the top ``quantile`` and (unless
    ``long_only``) short the bottom; equal-weight within each leg; scale so the book's gross
    exposure is ``gross`` (split evenly long/short when both legs exist). Pure and look-ahead
    free — the backtest engine applies the one-bar execution lag. Shared by every cross-
    sectional strategy that turns a score into weights (factor- or model-driven)."""
    weights = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    for date, row in score.iterrows():
        row = row.dropna()
        n = len(row)
        if n < 2:
            continue
        k = max(1, round(n * quantile))
        ranked = row.sort_values()
        if long_only:
            longs = ranked.index[-k:]
            weights.loc[date, longs] = gross / len(longs)
        else:
            k = min(k, n // 2)  # keep long/short legs disjoint
            longs, shorts = ranked.index[-k:], ranked.index[:k]
            weights.loc[date, longs] = (gross / 2) / k
            weights.loc[date, shorts] = -(gross / 2) / k
    return weights


class FactorStrategy(Strategy):
    """Construct with one or more factors and optional per-factor blend weights."""

    name = "factor"
    params_model = FactorStrategyParams

    def __init__(
        self,
        factors: list[Factor],
        blend: dict[str, float] | None = None,
        sectors: dict[str, str] | None = None,
        params: FactorStrategyParams | None = None,
    ) -> None:
        super().__init__(params)
        self.factors = factors
        self.blend = blend
        self.sectors = sectors

    def composite_score(self, prices: Panel, universe: Universe) -> Panel:
        """Standardize each factor and blend into one neutral, comparable score panel."""
        standardized: dict[str, Panel] = {}
        for f in self.factors:
            raw = f.signed(prices, universe)
            raw = transforms.winsorize(raw, self.params.winsor, 1 - self.params.winsor)  # type: ignore[attr-defined]
            score = transforms.zscore(raw)
            if self.sectors:
                score = transforms.neutralize(score, self.sectors)
            standardized[f.name] = score
        return transforms.combine(standardized, self.blend)

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        """Composite score → top/bottom quantile selection → gross-scaled weights.

        Per date: rank the composite cross-sectionally, long the top ``quantile`` and
        (unless ``long_only``) short the bottom; equal-weight within each leg; scale so the
        book's gross exposure is ``gross`` (split evenly long/short when both legs exist).
        The backtest engine applies the one-bar execution lag.
        """
        p: FactorStrategyParams = self.params  # type: ignore[assignment]
        score = self.composite_score(prices, universe)
        return long_short_weights(
            score, quantile=p.quantile, gross=p.gross, long_only=p.long_only
        )
