"""Tests for the 3-leg price-butterfly stat-arb: the multivariate Kalman regression, both
weighting modes of ButterflyStrategy (weight structure, neutrality, backtest), and wiring.

Synthetic, offline, seeded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.engine import BacktestEngine
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.kalman.filter import kalman_regression
from qhfi.strategy.library.butterfly import ButterflyParams, ButterflyStrategy


def _rw(rng, t, start=100.0, sig=0.01):
    return start * np.exp(np.cumsum(rng.normal(0.0, sig, t)))


# ── the multivariate filter ──────────────────────────────────────────────────────
def test_kalman_regression_recovers_loadings():
    rng = np.random.default_rng(11)
    t = 900
    idx = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    w1 = pd.Series(_rw(rng, t), index=idx)
    w2 = pd.Series(_rw(rng, t, start=80.0), index=idx)
    belly = pd.Series(0.6 * w1.to_numpy() + 0.4 * w2.to_numpy() + rng.normal(0, 0.05, t), index=idx)

    reg = kalman_regression(belly, {"w1": w1, "w2": w2})
    assert list(reg.columns) == ["alpha", "beta_w1", "beta_w2", "spread", "spread_var", "z"]
    assert abs(reg["beta_w1"].iloc[-1] - 0.6) < 0.1
    assert abs(reg["beta_w2"].iloc[-1] - 0.4) < 0.1


def test_kalman_regression_is_causal_prefix_stable():
    rng = np.random.default_rng(12)
    t, k = 600, 350
    idx = pd.date_range("2020-01-01", periods=t, freq="B", tz="UTC")
    w1 = pd.Series(_rw(rng, t), index=idx)
    w2 = pd.Series(_rw(rng, t, start=120.0), index=idx)
    belly = pd.Series(0.5 * (w1 + w2).to_numpy() + rng.normal(0, 0.1, t), index=idx)

    full = kalman_regression(belly, {"w1": w1, "w2": w2})
    prefix = kalman_regression(belly.iloc[:k], {"w1": w1.iloc[:k], "w2": w2.iloc[:k]})
    pd.testing.assert_frame_equal(full.iloc[:k], prefix, check_exact=False, rtol=1e-12)


# ── the strategy ─────────────────────────────────────────────────────────────────
@pytest.fixture
def fly_market():
    """A cointegrated triplet: belly ≈ ½(w1+w2) + stationary spread, plus an unrelated leg U."""
    rng = np.random.default_rng(21)
    t = 1000
    idx = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    w1, w2 = _rw(rng, t, start=100.0), _rw(rng, t, start=90.0)
    spread = np.zeros(t)
    for i in range(1, t):
        spread[i] = 0.94 * spread[i - 1] + rng.normal(0.0, 0.3)
    belly = 0.5 * (w1 + w2) + spread
    u = _rw(rng, t, start=40.0)
    prices = pd.DataFrame({"B": belly, "W1": w1, "W2": w2, "U": u}, index=idx)
    uni = Universe(name="t", instruments=[
        Instrument(id=c, asset_class=AssetClass.EQUITY, exchange="x") for c in prices.columns])
    return prices, uni


@pytest.mark.parametrize("weighting", ["kalman", "fixed"])
def test_butterfly_weights_are_three_leg(fly_market, weighting):
    prices, uni = fly_market
    strat = ButterflyStrategy("B", ("W1", "W2"),
                              ButterflyParams(weighting=weighting, entry_z=1.0, gross=1.0))
    w = strat.generate_weights(prices, uni)

    assert w.shape == prices.shape
    assert (w["U"] == 0.0).all()                          # the unrelated leg is never traded
    active = (w[["B", "W1", "W2"]] != 0).any(axis=1)
    assert active.any() and (~active).any()               # both in-position and flat days

    wa = w.loc[active, ["B", "W1", "W2"]]
    np.testing.assert_allclose(wa.abs().sum(axis=1).to_numpy(), 1.0, atol=1e-9)  # gross == target
    # belly is hedged opposite to its wings (belly long ⇒ both wings short, and vice-versa)
    assert (np.sign(wa["B"]) == -np.sign(wa["W1"])).all()
    assert (np.sign(wa["B"]) == -np.sign(wa["W2"])).all()


def test_butterfly_kalman_is_approximately_dollar_neutral(fly_market):
    prices, uni = fly_market
    strat = ButterflyStrategy("B", ("W1", "W2"), ButterflyParams(weighting="kalman", gross=1.0))
    w = strat.generate_weights(prices, uni)
    # fitted hedge ⇒ small net exposure relative to gross
    assert w.sum(axis=1).abs().max() < 0.2


def test_butterfly_runs_through_backtest_engine(fly_market):
    prices, uni = fly_market
    strat = ButterflyStrategy("B", ("W1", "W2"))
    result = BacktestEngine().run(strat.generate_weights(prices, uni), prices, uni)
    assert len(result.equity_curve) == len(prices)
    assert np.isfinite(result.equity_curve.to_numpy()).all()
    assert result.equity_curve.iloc[-1] > 0.0


def test_butterfly_validates_inputs(fly_market):
    prices, uni = fly_market
    with pytest.raises(ValueError):
        ButterflyStrategy("B", ("W1", "B"))               # legs must be distinct
    with pytest.raises(ValueError):
        ButterflyStrategy("B", ("W1", "W2"),
                          ButterflyParams(weighting="nope")).generate_weights(prices, uni)
    with pytest.raises(KeyError):
        ButterflyStrategy("B", ("W1", "GONE")).generate_weights(prices, uni)


def test_butterfly_classified_live_but_not_string_registered():
    import qhfi.strategy.library  # noqa: F401  — populate the registry
    from qhfi.strategy.registry import all_names
    from qhfi.strategy.taxonomy import Status, StrategyStyle, get

    assert "butterfly" not in set(all_names())             # carries its legs → not zero-arg
    kind = get("butterfly")
    assert kind.status is Status.LIVE and kind.style is StrategyStyle.STAT_ARB
