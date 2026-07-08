"""Tests for the Kalman layer: the dynamic-regression filter (hedge-ratio recovery + causality)
and KalmanPairsStrategy (dollar-neutral 2-leg weights, backtest integration, wiring).

Synthetic, offline, seeded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.engine import BacktestEngine
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.kalman.filter import kalman_hedge
from qhfi.strategy.library.kalman_pairs import KalmanPairsParams, KalmanPairsStrategy


def _geometric_rw(rng: np.random.Generator, t: int, start: float = 100.0, sig: float = 0.01):
    return start * np.exp(np.cumsum(rng.normal(0.0, sig, t)))


# ── the filter ─────────────────────────────────────────────────────────────────
def test_kalman_hedge_recovers_constant_beta():
    rng = np.random.default_rng(1)
    t = 800
    dates = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    x = pd.Series(_geometric_rw(rng, t), index=dates)
    y = pd.Series(1.5 * x.to_numpy() + rng.normal(0.0, 0.05, t), index=dates)  # beta=1.5, alpha=0

    hedge = kalman_hedge(y, x)
    assert list(hedge.columns) == ["alpha", "beta", "spread", "spread_var", "z"]
    assert hedge.index.equals(x.index)
    # converges to the true hedge ratio in the tail
    assert abs(hedge["beta"].iloc[-1] - 1.5) < 0.15
    assert abs(hedge["alpha"].iloc[-1]) < 5.0
    # the spread (forecast error) is mean-reverting → roughly centred, finite z
    assert abs(hedge["spread"].tail(200).mean()) < hedge["spread"].tail(200).std() + 1e-9
    assert np.isfinite(hedge["z"].iloc[-1])


def test_kalman_hedge_is_causal_prefix_stable():
    """An online/causal filter: running on a prefix reproduces the full run's first k rows
    exactly — proof that row t uses no information after t (no look-ahead)."""
    rng = np.random.default_rng(2)
    t, k = 500, 300
    dates = pd.date_range("2020-01-01", periods=t, freq="B", tz="UTC")
    x = pd.Series(_geometric_rw(rng, t), index=dates)
    y = pd.Series(2.0 * x.to_numpy() + rng.normal(0.0, 0.1, t), index=dates)

    full = kalman_hedge(y, x)
    prefix = kalman_hedge(y.iloc[:k], x.iloc[:k])
    pd.testing.assert_frame_equal(full.iloc[:k], prefix, check_exact=False, rtol=1e-12)


def test_kalman_hedge_empty_when_no_overlap():
    a = pd.Series([1.0, 2.0], index=pd.date_range("2021-01-01", periods=2, tz="UTC"))
    b = pd.Series([1.0, 2.0], index=pd.date_range("2022-01-01", periods=2, tz="UTC"))
    assert kalman_hedge(a, b).empty


# ── the strategy ─────────────────────────────────────────────────────────────────
@pytest.fixture
def cointegrated_market():
    """A cointegrated pair (Y ≈ a + b·X + stationary spread) plus an unrelated leg Z."""
    rng = np.random.default_rng(7)
    t = 900
    dates = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    x = _geometric_rw(rng, t)
    spread = np.zeros(t)                       # mean-reverting AR(1) spread
    for i in range(1, t):
        spread[i] = 0.95 * spread[i - 1] + rng.normal(0.0, 0.4)
    y = 2.0 + 1.5 * x + spread
    z = _geometric_rw(rng, t, start=50.0)     # unrelated → must stay flat
    prices = pd.DataFrame({"Y": y, "X": x, "Z": z}, index=dates)
    uni = Universe(name="t", instruments=[
        Instrument(id=c, asset_class=AssetClass.EQUITY, exchange="x") for c in prices.columns])
    return prices, uni


def test_pairs_weights_are_two_leg_and_hedge_neutral(cointegrated_market):
    prices, uni = cointegrated_market
    strat = KalmanPairsStrategy("Y", "X", KalmanPairsParams(entry_z=1.0, exit_z=0.0, gross=1.0))
    w = strat.generate_weights(prices, uni)

    assert w.shape == prices.shape
    assert (w["Z"] == 0.0).all()                       # the unrelated leg is never traded
    active = w["Y"] != 0.0
    assert active.any() and (~active).any()            # both in-position and flat days exist

    wa = w.loc[active]
    assert (np.sign(wa["Y"]) == -np.sign(wa["X"])).all()      # legs always opposite sign
    gross = wa["Y"].abs() + wa["X"].abs()
    np.testing.assert_allclose(gross.to_numpy(), 1.0, atol=1e-9)   # gross == target on active days
    assert (w.sum(axis=1).abs() < 0.15).all()                # ~dollar-neutral (price-ratio hedge)


def test_pairs_runs_through_backtest_engine(cointegrated_market):
    prices, uni = cointegrated_market
    strat = KalmanPairsStrategy("Y", "X")
    result = BacktestEngine().run(strat.generate_weights(prices, uni), prices, uni)
    assert len(result.equity_curve) == len(prices)
    assert np.isfinite(result.equity_curve.to_numpy()).all()
    assert result.equity_curve.iloc[-1] > 0.0


def test_pairs_validates_inputs(cointegrated_market):
    prices, uni = cointegrated_market
    with pytest.raises(ValueError):
        KalmanPairsStrategy("Y", "Y")                         # a pair needs two distinct legs
    with pytest.raises(KeyError):
        KalmanPairsStrategy("Y", "MISSING").generate_weights(prices, uni)


def test_kalman_pairs_classified_live_but_not_string_registered():
    import qhfi.strategy.library  # noqa: F401  — populate the registry
    from qhfi.strategy.registry import all_names
    from qhfi.strategy.taxonomy import Status, StrategyStyle, get

    # carries its inputs (the pair) → not zero-arg, so not in the string registry (cf. FactorStrategy)
    assert "kalman_pairs" not in set(all_names())
    kind = get("kalman_pairs")
    assert kind.status is Status.LIVE and kind.style is StrategyStyle.STAT_ARB
