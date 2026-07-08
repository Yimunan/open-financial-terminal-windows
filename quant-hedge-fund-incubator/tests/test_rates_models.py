"""Tests for the rates term-structure model layer (curve metrics, PCA, Nelson-Siegel,
forecaster) and the taxonomy-partitioned ModelRepository.

Synthetic, offline, seeded — a curve is built from known level/slope/curvature factors so the
analytics must recover them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.core.types import AssetClass
from qhfi.models.card import ModelDomain, ModelStage
from qhfi.models.predictive import ModelSpec
from qhfi.models.repository import ModelRepository
from qhfi.rates import curve as cv
from qhfi.rates.forecast import (
    forward_change,
    train_and_save_curve_forecaster,
    train_curve_forecaster,
)
from qhfi.rates.nelson_siegel import NelsonSiegel, _loadings, nelson_siegel_factors
from qhfi.rates.pca import CurvePCA

TENORS = ["3M", "5Y", "10Y", "30Y"]
YEARS = np.array([0.25, 5.0, 10.0, 30.0])


@pytest.fixture
def synthetic_curve() -> pd.DataFrame:
    """A Nelson-Siegel curve driven by random-walk level/slope/curvature factors."""
    rng = np.random.default_rng(11)
    T = 600
    dates = pd.date_range("2020-01-01", periods=T, freq="B", tz="UTC")
    design = _loadings(YEARS, lam=2.0)                       # (4 tenors × 3 loadings)
    level = 3.0 + np.cumsum(rng.normal(0, 0.02, T))
    slope = -1.0 + np.cumsum(rng.normal(0, 0.02, T))
    curv = 0.5 + np.cumsum(rng.normal(0, 0.02, T))
    betas = np.column_stack([level, slope, curv])           # (T × 3)
    yields = betas @ design.T + rng.normal(0, 0.005, (T, len(TENORS)))
    return pd.DataFrame(yields, index=dates, columns=TENORS)


# ── curve analytics ───────────────────────────────────────────────────────────
def test_tenor_years_parsing():
    assert cv.tenor_years("3M") == pytest.approx(0.25)
    assert cv.tenor_years("10Y") == pytest.approx(10.0)
    assert cv.tenor_years("18M") == pytest.approx(1.5)      # parsed, not in the table


def test_order_tenors_sorts_by_maturity():
    scrambled = pd.DataFrame(np.zeros((2, 4)), columns=["30Y", "3M", "10Y", "5Y"])
    assert list(cv.order_tenors(scrambled).columns) == ["3M", "5Y", "10Y", "30Y"]


def test_curve_metrics_slope_sign(synthetic_curve):
    m = cv.curve_metrics(synthetic_curve)
    assert set(m.columns) == {"level", "slope", "curvature"}
    expected = synthetic_curve["30Y"] - synthetic_curve["3M"]    # long − short
    pd.testing.assert_series_equal(m["slope"], expected, check_names=False)


def test_carry_rolldown_shapes(synthetic_curve):
    cr = cv.carry_rolldown(synthetic_curve, "10Y", horizon_days=21)
    assert set(cr.columns) == {"carry", "rolldown", "carry_roll"}
    assert len(cr) == len(synthetic_curve)
    assert np.allclose(cr["carry_roll"], cr["carry"] + cr["rolldown"])


def test_pca_recovers_level_slope_curvature(synthetic_curve):
    pca = CurvePCA(n_components=3).fit(synthetic_curve)
    ev = pca.explained()
    assert list(ev.index) == ["level", "slope", "curvature"]
    assert ev.sum() > 0.95                                   # 3 PCs explain ~all variance
    assert (pca.loadings()["level"] > 0).all()              # level = parallel shift
    assert pca.transform(synthetic_curve).shape == (len(synthetic_curve), 3)


def test_nelson_siegel_fits_its_own_curve(synthetic_curve):
    ns = NelsonSiegel(lam=2.0).fit(synthetic_curve)
    assert ns.rmse(synthetic_curve) < 0.05                  # noise floor was 0.005%
    betas = ns.factors(synthetic_curve)
    assert list(betas.columns) == ["level", "slope", "curvature"]
    fitted = ns.fitted(betas)
    assert np.sqrt(((synthetic_curve - fitted) ** 2).to_numpy().mean()) < 0.05


def test_nelson_siegel_factors_convenience(synthetic_curve):
    assert nelson_siegel_factors(synthetic_curve).shape == (len(synthetic_curve), 3)


# ── forecaster ────────────────────────────────────────────────────────────────
def test_forward_change_alignment(synthetic_curve):
    fc = forward_change(synthetic_curve, "10Y", horizon=21)
    expected = synthetic_curve["10Y"].shift(-21) - synthetic_curve["10Y"]
    pd.testing.assert_series_equal(fc, expected, check_names=False)
    assert fc.iloc[-21:].isna().all()                       # last `horizon` rows undefined


def test_curve_forecaster_trains(synthetic_curve):
    est, metrics, names = train_curve_forecaster(
        synthetic_curve, ModelSpec(kind="ridge"), target="10Y", horizon=21
    )
    assert metrics["n"] > 0 and hasattr(est, "predict")
    assert "level" in names and "carry_roll" in names


# ── ModelRepository taxonomy ──────────────────────────────────────────────────
class _Toy:
    def __init__(self, w):
        self.w = w

    def predict(self, x):
        return self.w


def test_repository_taxonomy_partitioned_layout(tmp_path):
    repo = ModelRepository(tmp_path)
    card = repo.save("ust-10y", _Toy(1), domain=ModelDomain.CURVE, asset_class=AssetClass.RATES)
    assert card.domain is ModelDomain.CURVE and card.asset_class is AssetClass.RATES
    # on-disk path mirrors the lake: <root>/<domain>/<asset_class>/<name>/v1/
    assert (tmp_path / "curve" / "rates" / "ust-10y" / "v1" / "model.pkl").exists()


def test_repository_loads_taxonomy_model_without_taxonomy(tmp_path):
    repo = ModelRepository(tmp_path)
    repo.save("ust-10y", _Toy(7), domain=ModelDomain.CURVE, asset_class=AssetClass.RATES)
    model, card = repo.load("ust-10y")                       # bare load locates by scan
    assert model.predict(None) == 7 and card.asset_class is AssetClass.RATES
    model2, _ = repo.load("ust-10y", asset_class=AssetClass.RATES, domain=ModelDomain.CURVE)
    assert model2.predict(None) == 7


def test_repository_flat_and_partitioned_coexist(tmp_path):
    repo = ModelRepository(tmp_path)
    repo.save("flatmodel", _Toy(1))                                          # flat (no taxonomy)
    repo.save("taxmodel", _Toy(2), domain=ModelDomain.ALPHA, asset_class=AssetClass.EQUITY)
    assert (tmp_path / "flatmodel" / "v1").exists()
    assert (tmp_path / "alpha" / "equity" / "taxmodel" / "v1").exists()
    assert {c.name for c in repo.cards()} == {"flatmodel", "taxmodel"}
    assert repo.cards("taxmodel")[0].asset_class is AssetClass.EQUITY


def test_repository_versioning_and_promotion_under_taxonomy(tmp_path):
    repo = ModelRepository(tmp_path)
    kw = {"domain": ModelDomain.CURVE, "asset_class": AssetClass.RATES}
    repo.save("m", _Toy(1), **kw)
    repo.save("m", _Toy(2), **kw)
    assert repo.latest("m", AssetClass.RATES, ModelDomain.CURVE) == 2
    repo.promote("m", 1, ModelStage.PRODUCTION)
    prod = repo.production("m")
    assert prod is not None and prod[1].version == 1


def test_save_and_load_curve_forecaster_under_rates(tmp_path, synthetic_curve):
    repo = ModelRepository(tmp_path)
    card = train_and_save_curve_forecaster(
        repo, "ust-10y-ridge", synthetic_curve, ModelSpec(kind="ridge"), target="10Y", horizon=21
    )
    assert card.domain is ModelDomain.CURVE and card.asset_class is AssetClass.RATES
    assert (tmp_path / "curve" / "rates" / "ust-10y-ridge" / "v1" / "card.json").exists()
    model, loaded = repo.load("ust-10y-ridge")
    assert hasattr(model, "predict") and loaded.metrics["n"] > 0
