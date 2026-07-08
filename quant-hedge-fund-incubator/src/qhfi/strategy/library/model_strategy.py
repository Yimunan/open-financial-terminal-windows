"""ModelStrategy — trade a cross-sectional ML forecaster's predictions.

The model-world analogue of :class:`~qhfi.strategy.library.factor_strategy.FactorStrategy`:
instead of blending factor *scores*, it feeds the standardized factors into a trained sklearn
estimator, treats the predicted forward returns as the composite score, and maps that to
long/short weights with the shared :func:`long_short_weights` constructor.

Two modes, by ``refit``:

* **served** (``refit=False``) — use a pre-fitted ``estimator`` (e.g. loaded from a
  :class:`~qhfi.models.repository.ModelRepository`); ``generate_weights`` only predicts. Use
  for in-sample backtests and live/production.
* **walk-forward** (``refit=True``) — refit a fresh estimator from ``spec`` inside every
  ``generate_weights`` call, training only on rows older than the trailing ``embargo`` days.
  This is exactly the per-fold refit hook ``backtest.validation.walk_forward`` describes:
  pass it the causal fold view and set ``embargo = test_days + purge_days`` so the model never
  sees the fold's test window. Honest OOS, no leakage.
"""

from __future__ import annotations

from typing import Any

from qhfi.core.types import Panel, TargetWeights, Universe
from qhfi.factors.base import Factor
from qhfi.models import features as feat
from qhfi.models.predictive import ModelSpec, build_estimator
from qhfi.strategy.base import Strategy, StrategyParams
from qhfi.strategy.library.factor_strategy import long_short_weights
from qhfi.strategy.registry import register


class ModelStrategyParams(StrategyParams):
    quantile: float = 0.2     # long top / short bottom fraction
    gross: float = 1.0        # target gross exposure
    long_only: bool = False
    winsor: float = 0.02      # symmetric winsorization tail
    horizon: int = 5          # forward-return horizon the model predicts
    refit: bool = False       # refit per call (walk-forward) vs use the pre-fitted estimator
    embargo: int = 0          # rows held out from the tail when refitting (== test+purge days)


@register
class ModelStrategy(Strategy):
    """Construct with the feature ``factors`` and either a fitted ``estimator`` (served mode)
    or a ``spec`` to refit each fold (walk-forward mode, ``params.refit=True``)."""

    name = "model"
    params_model = ModelStrategyParams

    def __init__(
        self,
        factors: list[Factor],
        estimator: Any | None = None,
        spec: ModelSpec | None = None,
        sectors: dict[str, str] | None = None,
        params: ModelStrategyParams | None = None,
    ) -> None:
        super().__init__(params)
        p: ModelStrategyParams = self.params  # type: ignore[assignment]
        if p.refit and spec is None:
            raise ValueError("refit=True requires a `spec` to rebuild the estimator each fold")
        if not p.refit and estimator is None:
            raise ValueError("refit=False requires a pre-fitted `estimator`")
        self.factors = factors
        self.estimator = estimator
        self.spec = spec
        self.sectors = sectors

    def _score(self, prices: Panel, universe: Universe) -> Panel:
        """Predict the forward-return score panel; refit on a causal, embargoed window if asked."""
        p: ModelStrategyParams = self.params  # type: ignore[assignment]
        panels = feat.feature_panels(self.factors, prices, universe, self.sectors, p.winsor)

        if p.refit:
            assert self.spec is not None  # guarded in __init__
            # Hold out the trailing `embargo` dates so the test window is never trained on.
            train_prices = prices.iloc[: len(prices) - p.embargo] if p.embargo else prices
            train_panels = {n: pl.reindex(index=train_prices.index) for n, pl in panels.items()}
            X, y, _ = feat.to_training_frame(train_panels, train_prices, p.horizon)
            estimator = build_estimator(self.spec)
            estimator.fit(X, y)
        else:
            estimator = self.estimator

        X_all, idx = feat.to_feature_matrix(panels)
        return feat.predictions_to_panel(estimator.predict(X_all), idx, like=prices)

    def generate_weights(self, prices: Panel, universe: Universe) -> TargetWeights:
        """Predicted forward returns → top/bottom quantile selection → gross-scaled weights."""
        p: ModelStrategyParams = self.params  # type: ignore[assignment]
        score = self._score(prices, universe)
        return long_short_weights(
            score, quantile=p.quantile, gross=p.gross, long_only=p.long_only
        )
