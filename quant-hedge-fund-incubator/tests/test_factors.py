"""Tests for the factor layer's real (non-stub) bodies: transforms, evaluation, and the
price-based factors. These assert the analytical invariants the design depends on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.factors import evaluation as fe
from qhfi.factors import transforms as tf
from qhfi.factors.library import MomentumFactor, VolatilityFactor


@pytest.fixture
def prices() -> pd.DataFrame:
    # 200 days, 6 instruments with distinct drifts → a real momentum ordering exists.
    dates = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    drifts = np.linspace(-0.001, 0.001, 6)
    data = {f"A{i}": 100 * np.cumprod(1 + np.full(200, d)) for i, d in enumerate(drifts)}
    return pd.DataFrame(data, index=dates)


@pytest.fixture
def universe() -> Universe:
    return Universe(
        name="t",
        instruments=[Instrument(id=f"A{i}", asset_class=AssetClass.CRYPTO, exchange="x") for i in range(6)],
    )


def test_zscore_is_row_standardized(prices):
    z = tf.zscore(prices)
    last = z.iloc[-1].dropna()
    assert abs(last.mean()) < 1e-9
    assert abs(last.std(ddof=0) - 1.0) < 1e-9


def test_rank_normalized_is_centered_and_bounded(prices):
    r = tf.rank(prices)
    row = r.iloc[-1].dropna()
    assert row.min() >= -0.5 - 1e-9 and row.max() <= 0.5 + 1e-9
    assert abs(row.mean()) < 1e-9


def test_neutralize_zeros_group_means(prices):
    groups = {"A0": "g1", "A1": "g1", "A2": "g1", "A3": "g2", "A4": "g2", "A5": "g2"}
    n = tf.neutralize(prices, groups)
    g1 = n.iloc[-1][["A0", "A1", "A2"]]
    assert abs(g1.mean()) < 1e-9


def test_momentum_factor_orders_by_drift(prices, universe):
    mom = MomentumFactor().compute(prices, universe)
    last = mom.iloc[-1]
    # highest-drift instrument (A5) should have the largest momentum score
    assert last.idxmax() == "A5"
    assert last.idxmin() == "A0"


def test_volatility_direction_is_low_vol_long(universe):
    assert VolatilityFactor().direction == -1


def test_information_coefficient_detects_momentum_signal(prices, universe):
    mom = MomentumFactor(MomentumFactor.params_model(lookback=20, gap=1)).compute(prices, universe)
    ic = fe.information_coefficient(mom, prices, horizon=5)
    summ = fe.ic_summary(ic)
    # monotone-drift world → momentum should be positively predictive on average
    assert summ.mean_ic > 0
    assert summ.n > 0


def test_forward_returns_alignment(prices):
    fwd = fe.forward_returns(prices, horizon=1)
    # fwd at t equals price_{t+1}/price_t - 1
    expected = prices.iloc[1, 0] / prices.iloc[0, 0] - 1
    assert abs(fwd.iloc[0, 0] - expected) < 1e-12
