"""Cross-sectional momentum — reference strategy showing the contract end-to-end.

Each day: rank instruments by trailing total return over `lookback` days (skipping the most
recent `gap` days to avoid short-term reversal), go long the top quantile / short the
bottom, equal-weighted, scaled to `gross` exposure. This is the template the codegen agent
emulates.
"""

from __future__ import annotations

from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.strategy.base import Strategy, StrategyParams
from qhfi.strategy.registry import register


class MomentumParams(StrategyParams):
    lookback: int = 90
    gap: int = 5
    quantile: float = 0.2
    gross: float = 1.0
    long_only: bool = False


@register
class Momentum(Strategy):
    name = "momentum"
    params_model = MomentumParams

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        p: MomentumParams = self.params  # type: ignore[assignment]
        # Signal available at close of t (uses prices through t-gap .. t-gap-lookback):
        #   momentum = prices.shift(p.gap) / prices.shift(p.gap + p.lookback) - 1
        # Cross-sectionally rank each row, select top/bottom `quantile`, equal-weight,
        # normalize to `gross`. Engine applies the one-bar execution lag.
        raise NotImplementedError(
            "TODO: implement ranked cross-sectional momentum on the close panel"
        )
