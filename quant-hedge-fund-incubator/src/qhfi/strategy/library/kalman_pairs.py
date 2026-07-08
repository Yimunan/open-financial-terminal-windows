"""KalmanPairsStrategy — statistical-arbitrage pairs trading on a Kalman-filtered spread.

Picks one pair ``(y_id, x_id)`` and runs a dynamic linear regression (``kalman.kalman_hedge``)
to track a *time-varying* hedge ratio ``beta_t``. The filter's forecast error is the spread and
its predicted std gives a self-normalizing z-score; we trade its mean reversion with hysteresis:

* enter **long the spread** (long y, short ``beta_t`` of x) when ``z < -entry_z`` (y cheap vs x),
* enter **short the spread** (short y, long ``beta_t`` of x) when ``z > +entry_z`` (y rich),
* **exit to flat** when z reverts back through ``exit_z``.

Weights are dollar-scaled so the two legs hold the live hedge ratio ``n_y : n_x = 1 : -beta_t``
(price-neutral) at gross exposure ``gross``; everything else in the universe is flat. The book
re-hedges daily as ``beta_t`` and prices drift. The engine applies the one-bar execution lag.

Like :class:`~qhfi.strategy.library.factor_strategy.FactorStrategy`, this carries required inputs
(the pair) so it is constructed explicitly rather than pulled zero-arg from the string registry.

Honest-OOS note: the filter is causal, but choosing the pair is itself a modeling decision —
select it on a train window (or by prior knowledge), not by scanning the test period.
"""

from __future__ import annotations

import pandas as pd

from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.kalman.filter import kalman_hedge
from qhfi.strategy.base import Strategy, StrategyParams
from qhfi.strategy.library.spread_common import hysteresis_positions, scale_to_gross


class KalmanPairsParams(StrategyParams):
    delta: float = 1e-4       # Kalman state-drift knob (Vw = delta/(1-delta) I); smaller = stiffer
    obs_var: float = 1e-3     # observation noise variance
    entry_z: float = 1.0      # |z| to open a position
    exit_z: float = 0.0       # z level (toward the mean) at which to close
    gross: float = 1.0        # target gross exposure of the two legs combined
    warmup: int = 20          # bars to let the filter converge before trading


class KalmanPairsStrategy(Strategy):
    """Construct with the dependent leg ``y_id`` and the hedge leg ``x_id`` (both instrument ids
    present in the price panel)."""

    name = "kalman_pairs"
    params_model = KalmanPairsParams

    def __init__(self, y_id: str, x_id: str, params: KalmanPairsParams | None = None) -> None:
        super().__init__(params)
        if y_id == x_id:
            raise ValueError("y_id and x_id must be different instruments")
        self.y_id = y_id
        self.x_id = x_id

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        p: KalmanPairsParams = self.params  # type: ignore[assignment]
        for leg in (self.y_id, self.x_id):
            if leg not in prices.columns:
                raise KeyError(f"pair leg {leg!r} not in the price panel")

        hedge = kalman_hedge(prices[self.y_id], prices[self.x_id], p.delta, p.obs_var)
        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        if hedge.empty:
            return weights

        signal = hysteresis_positions(hedge["z"], p.entry_z, p.exit_z, p.warmup)
        beta = hedge["beta"]
        yp = prices[self.y_id].reindex(hedge.index)
        xp = prices[self.x_id].reindex(hedge.index)

        # Dollar-weighted legs in the live hedge ratio (n_y : n_x = 1 : -beta), gross-scaled.
        raw = pd.DataFrame({self.y_id: signal * yp, self.x_id: -signal * beta * xp})
        legs = scale_to_gross(raw, p.gross)
        weights[[self.y_id, self.x_id]] = legs.reindex(prices.index).fillna(0.0)
        return weights
