"""ButterflyStrategy — three-leg price-butterfly statistical arbitrage.

A butterfly is a basket of three related instruments — a **belly** and two **wings** — traded
on the mean reversion of the belly's value *relative to its wings* (the discrete second
difference / "convexity" of the three prices). The 3-leg generalization of
:class:`~qhfi.strategy.library.kalman_pairs.KalmanPairsStrategy`.

Two ways to form the spread (``weighting``):

* **kalman** (default) — regress the belly on its two wings with a multivariate Kalman filter
  (``kalman.kalman_regression``): ``belly ≈ a + b1·w1 + b2·w2``. The forecast error is a
  stationary spread, z-scored on a rolling ``z_window``; legs are held in the *fitted* hedge
  ratios ``n_belly : n_w1 : n_w2 = 1 : -b1 : -b2`` (a proper data-driven, ~dollar-neutral hedge).
* **fixed** — the textbook structural butterfly ``B = w1 - 2·belly + w2`` in equal shares
  (units ratio ``1 : -2 : 1``), z-scored on a rolling window. Unit-neutral by construction;
  dollar-neutral only when the leg prices are comparable.

Either way we open when ``|z| > entry_z`` and close once z reverts through ``exit_z`` (shared
hysteresis), and emit gross-scaled 3-leg weights. The sign convention is the same in both modes:
**long the belly when it is cheap vs. its wings, short it when rich.** The engine applies the
one-bar execution lag.

Like FactorStrategy/KalmanPairsStrategy it carries required inputs (the three legs), so it is
constructed explicitly rather than pulled zero-arg from the string registry. Pick the triplet on
a train window or by economic kinship (same sector / curve / index) — not by scanning the test.
"""

from __future__ import annotations

import pandas as pd

from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.kalman.filter import kalman_regression
from qhfi.strategy.base import Strategy, StrategyParams
from qhfi.strategy.library.spread_common import hysteresis_positions, scale_to_gross


class ButterflyParams(StrategyParams):
    weighting: str = "kalman"   # "kalman" (regress belly on wings) | "fixed" (1:-2:1 on prices)
    delta: float = 1e-4         # Kalman state-drift (kalman mode)
    obs_var: float = 1e-3       # Kalman observation noise (kalman mode)
    z_window: int = 60          # rolling z-score window (fixed mode)
    entry_z: float = 1.0        # |z| to open
    exit_z: float = 0.0         # z toward the mean at which to close
    gross: float = 1.0          # target gross exposure of the three legs combined
    warmup: int = 20            # bars to let the kalman filter converge before trading


class ButterflyStrategy(Strategy):
    """Construct with the ``belly_id`` and the two ``wing_ids`` (all distinct instrument ids in
    the price panel)."""

    name = "butterfly"
    params_model = ButterflyParams

    def __init__(
        self, belly_id: str, wing_ids: tuple[str, str], params: ButterflyParams | None = None
    ) -> None:
        super().__init__(params)
        legs = [belly_id, *wing_ids]
        if len(set(legs)) != 3:
            raise ValueError("a butterfly needs three distinct legs (belly + two wings)")
        self.belly_id = belly_id
        self.wing_ids = tuple(wing_ids)

    def _raw_legs(self, prices: Panel) -> pd.DataFrame:
        """Per-date raw (unnormalized) weights for the three legs, sign = held position.
        Empty frame if the spread can't be formed."""
        p: ButterflyParams = self.params  # type: ignore[assignment]
        b, w1, w2 = self.belly_id, *self.wing_ids

        if p.weighting == "kalman":
            reg = kalman_regression(
                prices[b], {w1: prices[w1], w2: prices[w2]}, p.delta, p.obs_var)
            if reg.empty:
                return pd.DataFrame()
            # Kalman gives the dynamic hedge (beta_w1, beta_w2) and a stationary residual; z-score
            # that residual on a rolling window (robust to leg scale, unlike the filter's own
            # forecast variance, which 2 price-level regressors inflate).
            resid = reg["spread"]
            sd = resid.rolling(p.z_window).std(ddof=0)
            z = (resid - resid.rolling(p.z_window).mean()) / sd.where(sd > 0)
            signal = hysteresis_positions(z, p.entry_z, p.exit_z, max(p.warmup, p.z_window))
            bp, w1p, w2p = (prices[c].reindex(reg.index) for c in (b, w1, w2))
            # long spread (s>0) = long belly, short the fitted wings: 1 : -b1 : -b2
            return pd.DataFrame({
                b: signal * bp,
                w1: -signal * reg[f"beta_{w1}"] * w1p,
                w2: -signal * reg[f"beta_{w2}"] * w2p,
            })

        if p.weighting == "fixed":
            spread = prices[w1] - 2.0 * prices[b] + prices[w2]          # B = w1 - 2·belly + w2
            mu = spread.rolling(p.z_window).mean()
            sd = spread.rolling(p.z_window).std(ddof=0)
            z = (spread - mu) / sd.where(sd > 0)
            signal = hysteresis_positions(z, p.entry_z, p.exit_z, p.z_window)
            # position in B (s>0 = long B = long wings, short 2·belly → long belly when cheap)
            return pd.DataFrame({
                b: -2.0 * signal * prices[b],
                w1: signal * prices[w1],
                w2: signal * prices[w2],
            })

        raise ValueError(f"weighting must be 'kalman' or 'fixed', got {p.weighting!r}")

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        p: ButterflyParams = self.params  # type: ignore[assignment]
        legs = [self.belly_id, *self.wing_ids]
        for leg in legs:
            if leg not in prices.columns:
                raise KeyError(f"butterfly leg {leg!r} not in the price panel")

        weights = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        raw = self._raw_legs(prices)
        if raw.empty:
            return weights
        legs_w = scale_to_gross(raw, p.gross)
        weights[legs] = legs_w.reindex(prices.index).fillna(0.0)
        return weights
