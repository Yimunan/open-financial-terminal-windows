"""Tests for the MDP layer: the generic solver, regime estimation, the allocation MDP, and the
MDPStrategy (weights + backtest + walk-forward + repository round-trip).

Synthetic, offline, seeded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.engine import BacktestEngine
from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.mdp.allocation import RegimeAllocationMDP
from qhfi.mdp.core import MDP, policy_iteration, value_iteration
from qhfi.mdp.regime import RegimeModel, regime_return_stats, transition_matrix
from qhfi.models.card import ModelDomain
from qhfi.models.repository import ModelRepository
from qhfi.strategy.library.mdp_strategy import MDPStrategy, MDPStrategyParams


# ── generic solver ────────────────────────────────────────────────────────────
def test_value_iteration_matches_policy_iteration():
    rng = np.random.default_rng(0)
    s, a = 4, 3
    P = rng.random((s, s))
    P /= P.sum(axis=1, keepdims=True)
    R = rng.standard_normal((s, a))
    mdp = MDP(P, R, np.arange(a), gamma=0.9)
    v_vi, pi_vi = value_iteration(mdp)
    v_pi, pi_pi = policy_iteration(mdp)
    assert np.array_equal(pi_vi, pi_pi)
    np.testing.assert_allclose(v_vi, v_pi, atol=1e-6)


def test_gamma_zero_is_greedy():
    P = np.eye(2)
    R = np.array([[1.0, 0.0], [0.0, 2.0]])      # best action differs per state
    _, policy = value_iteration(MDP(P, R, np.array([0, 1]), gamma=0.0))
    assert list(policy) == [0, 1]               # argmax of immediate reward


def test_mdp_validates_shapes():
    with pytest.raises(ValueError):
        MDP(np.eye(3), np.zeros((2, 2)), np.arange(2), gamma=0.9)
    with pytest.raises(ValueError):
        MDP(np.eye(2), np.zeros((2, 2)), np.arange(2), gamma=1.0)   # gamma must be < 1


# ── regime estimation ─────────────────────────────────────────────────────────
@pytest.fixture
def two_regime_returns() -> pd.Series:
    """A calm low-vol stretch followed by a volatile drawdown stretch."""
    rng = np.random.default_rng(3)
    dates = pd.date_range("2018-01-01", periods=1000, freq="B", tz="UTC")
    calm = rng.normal(0.0005, 0.005, 500)
    storm = rng.normal(-0.0008, 0.025, 500)
    return pd.Series(np.concatenate([calm, storm]), index=dates)


def test_transition_matrix_rows_sum_to_one():
    labels = pd.Series([0, 0, 1, 1, 2, 0, 1])
    P = transition_matrix(labels, n_regimes=3)
    assert P.shape == (3, 3)
    np.testing.assert_allclose(P.sum(axis=1), 1.0)


def test_regime_labels_are_causal_and_vol_ordered(two_regime_returns):
    rm = RegimeModel(n_regimes=2, lookback=63).fit(two_regime_returns)
    labels = rm.label(two_regime_returns)
    assert labels.index.equals(two_regime_returns.index)    # aligned, no NaN gaps
    assert not labels.isna().any()
    # the volatile second half should sit mostly in the higher regime id (vol-ordered)
    assert labels.iloc[600:].mean() > labels.iloc[:400].mean()


def test_regime_return_stats_fallback():
    r = pd.Series([0.01, -0.02, 0.03, 0.0])
    labels = pd.Series([0, 0, 0, 0])                         # regime 1 unobserved
    mu, var = regime_return_stats(r, labels, n_regimes=2)
    assert len(mu) == 2 and len(var) == 2
    assert var[1] > 0                                        # pooled fallback, never zero


# ── allocation MDP ────────────────────────────────────────────────────────────
def test_allocation_derisks_in_volatile_regime(two_regime_returns):
    mdp = RegimeAllocationMDP(n_regimes=2, lookback=63, risk_aversion=3.0).fit(
        two_regime_returns, two_regime_returns
    )
    table = mdp.policy_table()
    calm, storm = table["risky_fraction"].iloc[0], table["risky_fraction"].iloc[-1]
    assert calm >= storm                                     # de-risk as vol rises
    assert table["ann_vol"].iloc[-1] > table["ann_vol"].iloc[0]


def test_higher_risk_aversion_lowers_exposure(two_regime_returns):
    r = two_regime_returns
    lo = RegimeAllocationMDP(n_regimes=2, risk_aversion=1.0).fit(r, r)
    hi = RegimeAllocationMDP(n_regimes=2, risk_aversion=10.0).fit(r, r)
    assert hi.policy_.mean() <= lo.policy_.mean()


# ── MDPStrategy end to end ────────────────────────────────────────────────────
@pytest.fixture
def market() -> tuple[pd.DataFrame, Universe]:
    rng = np.random.default_rng(5)
    n, t = 6, 900
    dates = pd.date_range("2019-01-01", periods=t, freq="B", tz="UTC")
    vol = np.concatenate([np.full(t // 2, 0.006), np.full(t - t // 2, 0.02)])
    rets = rng.normal(0.0003, 1.0, (t, n)) * vol[:, None]
    prices = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates,
                          columns=[f"A{i}" for i in range(n)])
    uni = Universe(name="t", instruments=[Instrument(id=f"A{i}", asset_class=AssetClass.CRYPTO,
                                                     exchange="x") for i in range(n)])
    return prices, uni


def test_mdp_strategy_weights_valid(market):
    prices, uni = market
    strat = MDPStrategy(MDPStrategyParams(n_regimes=2, max_leverage=1.5))
    w = strat.generate_weights(prices, uni)
    assert w.shape == prices.shape
    row_gross = w.sum(axis=1)
    assert (row_gross >= -1e-9).all() and (row_gross <= 1.5 + 1e-9).all()   # within [0, max_lev]
    assert (w >= -1e-9).all().all()                                        # long-only book


def test_mdp_strategy_runs_through_walk_forward(market):
    prices, uni = market
    strat = MDPStrategy(MDPStrategyParams(n_regimes=2))
    cfg = WalkForwardConfig(train_days=300, test_days=100, step_days=100, purge_days=5)
    oos = concat_oos(walk_forward(strat, prices, uni, BacktestEngine(), cfg))
    assert len(oos) > 0


def test_mdp_strategy_registered():
    from qhfi.strategy.registry import get
    assert get("mdp") is MDPStrategy


def test_policy_versioned_under_allocation(tmp_path, two_regime_returns):
    mdp = RegimeAllocationMDP(n_regimes=2).fit(two_regime_returns, two_regime_returns)
    repo = ModelRepository(tmp_path)
    repo.save("regime-allocator", mdp, framework="custom",
              domain=ModelDomain.ALLOCATION, asset_class=AssetClass.EQUITY)
    assert (tmp_path / "allocation" / "equity" / "regime-allocator" / "v1" / "model.pkl").exists()
    loaded, lc = repo.load("regime-allocator")
    assert lc.domain is ModelDomain.ALLOCATION
    assert np.array_equal(loaded.policy_, mdp.policy_)        # picklable, round-trips
