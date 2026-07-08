"""Tests for the predictive-model layer: feature stacking, training/IC recovery, the
ModelStrategy weight map (served + refit/walk-forward), and ModelRepository round-trip.

Synthetic, offline, seeded — following the project's convention of building data from NumPy
rather than fixtures on disk. The core trick: construct prices whose realized forward returns
are a *known* linear combination of two custom feature panels, so a fitted model must recover
a positive information coefficient.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
from qhfi.core.types import AssetClass, Instrument, Panel, Universe
from qhfi.factors.base import Factor
from qhfi.factors.evaluation import forward_returns
from qhfi.models import features as feat
from qhfi.models import train
from qhfi.models.predictive import ModelSpec, build_estimator
from qhfi.models.repository import ModelRepository
from qhfi.strategy.library.model_strategy import ModelStrategy, ModelStrategyParams

N = 14          # instruments
T = 400         # days
HORIZON = 1


class _PanelFactor(Factor):
    """A factor that just serves a pre-built panel (lets us inject a known feature)."""

    direction = 1

    def __init__(self, name: str, panel: Panel) -> None:
        self.name = name
        super().__init__()
        self._panel = panel

    def compute(self, prices: Panel, universe: Universe) -> Panel:
        return self._panel.reindex(index=prices.index, columns=prices.columns)


@pytest.fixture
def synthetic() -> tuple[Panel, Universe, list[Factor]]:
    """Prices whose 1-day forward return = 0.02*f1 + 0.01*f2 + small noise, with f1/f2 known."""
    rng = np.random.default_rng(7)
    dates = pd.date_range("2022-01-01", periods=T, freq="D", tz="UTC")
    cols = [f"A{i}" for i in range(N)]
    f1 = pd.DataFrame(rng.standard_normal((T, N)), index=dates, columns=cols)
    f2 = pd.DataFrame(rng.standard_normal((T, N)), index=dates, columns=cols)

    g = 0.02 * f1 + 0.01 * f2 + 0.001 * rng.standard_normal((T, N))  # realized fwd return
    # Build a price path so price_{t+1}/price_t - 1 == g_t (i.e. forward_returns == g).
    prices = pd.DataFrame(index=dates, columns=cols, dtype=float)
    prices.iloc[0] = 100.0
    for t in range(T - 1):
        prices.iloc[t + 1] = prices.iloc[t] * (1.0 + g.iloc[t])

    universe = Universe(
        name="syn",
        instruments=[Instrument(id=c, asset_class=AssetClass.CRYPTO, exchange="x") for c in cols],
    )
    factors: list[Factor] = [_PanelFactor("f1", f1), _PanelFactor("f2", f2)]
    return prices, universe, factors


def test_to_training_frame_alignment(synthetic):
    prices, universe, factors = synthetic
    panels = feat.feature_panels(factors, prices, universe)
    X, y, idx = feat.to_training_frame(panels, prices, HORIZON)

    assert X.shape[0] == y.shape[0] == len(idx)
    assert X.shape[1] == len(factors)
    assert not np.isnan(X).any() and not np.isnan(y).any()
    # The trailing `horizon` dates have no label and must be excluded.
    assert idx.get_level_values("date").max() < prices.index[-1]
    # Spot-check the label equals the realized forward return for a sampled (date, instrument).
    fwd = forward_returns(prices, HORIZON)
    d, inst = idx[0]
    assert y[0] == pytest.approx(fwd.loc[d, inst])


def test_train_recovers_positive_ic(synthetic):
    prices, universe, factors = synthetic
    est, metrics = train(ModelSpec(kind="ridge"), factors, prices, universe, horizon=HORIZON)
    assert metrics["n"] > 0
    assert metrics["ic_mean"] > 0.05          # a clear, learnable linear signal
    assert hasattr(est, "predict")


def test_model_strategy_served_weights_are_valid(synthetic):
    prices, universe, factors = synthetic
    est, _ = train(ModelSpec(kind="ridge"), factors, prices, universe, horizon=HORIZON)
    strat = ModelStrategy(
        factors, estimator=est,
        params=ModelStrategyParams(horizon=HORIZON, quantile=0.2, gross=1.0),
    )
    w = strat.generate_weights(prices, universe)

    assert w.shape == prices.shape
    active = w[(w != 0).any(axis=1)]
    assert len(active) > 0
    last = active.iloc[-1]
    assert last.abs().sum() == pytest.approx(1.0, abs=1e-9)   # gross == 1.0
    assert (last > 0).any() and (last < 0).any()             # both legs present
    assert not ((last > 0) & (last < 0)).any()               # legs disjoint


def test_model_strategy_refit_runs_through_walk_forward(synthetic):
    prices, universe, factors = synthetic
    cfg = WalkForwardConfig(train_days=200, test_days=50, step_days=50, purge_days=5)
    embargo = cfg.test_days + cfg.purge_days
    strat = ModelStrategy(
        factors, spec=ModelSpec(kind="ridge"),
        params=ModelStrategyParams(horizon=HORIZON, refit=True, embargo=embargo),
    )
    results = walk_forward(strat, prices, universe, BacktestEngine(), cfg)
    oos = concat_oos(results)
    assert len(results) > 0
    assert len(oos) > 0


def test_repository_roundtrip_predicts_identically(synthetic, tmp_path):
    prices, universe, factors = synthetic
    est, metrics = train(ModelSpec(kind="ridge"), factors, prices, universe, horizon=HORIZON)
    panels = feat.feature_panels(factors, prices, universe)
    X, _ = feat.to_feature_matrix(panels)

    repo = ModelRepository(tmp_path)
    card = repo.save("forecaster", est, framework="sklearn", metrics=metrics,
                     features=[f.name for f in factors])
    loaded, loaded_card = repo.load("forecaster", "latest")

    assert loaded_card.version == card.version == 1
    assert loaded_card.framework == "sklearn"
    np.testing.assert_allclose(loaded.predict(X), est.predict(X))


def test_build_estimator_kinds():
    for kind in ("ridge", "lasso", "elasticnet", "gbr", "rf"):
        est = build_estimator(ModelSpec(kind=kind, params={}))
        assert hasattr(est, "fit") and hasattr(est, "predict")
    with pytest.raises(ValueError):
        ModelSpec(kind="nope")
