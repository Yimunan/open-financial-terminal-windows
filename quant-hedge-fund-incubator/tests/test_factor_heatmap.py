"""Tests for the factor heatmap builders + renderer (qhfi.factors.heatmap)."""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest
from rich.console import Console

from qhfi.factors import heatmap as hm


@pytest.fixture
def prices() -> pd.DataFrame:
    # 200 days, 6 instruments with distinct drifts → a real momentum ordering exists.
    dates = pd.date_range("2024-01-01", periods=200, freq="D", tz="UTC")
    drifts = np.linspace(-0.001, 0.001, 6)
    data = {f"A{i}": 100 * np.cumprod(1 + np.full(200, d)) for i, d in enumerate(drifts)}
    return pd.DataFrame(data, index=dates)


@pytest.fixture
def signals(prices) -> dict[str, pd.DataFrame]:
    # Three signals: momentum-like, its near-duplicate (collinear), and an unrelated one.
    mom = prices.pct_change(20)
    return {
        "mom": mom,
        "mom_dup": mom * 1.01,                 # ~perfectly collinear with mom
        "rev": -prices.pct_change(5),          # short-term reversal, different signal
    }


def test_factor_correlation_is_square_symmetric_unit_diagonal(signals):
    corr = hm.factor_correlation(signals)
    assert corr.shape == (3, 3)
    assert list(corr.index) == list(corr.columns) == ["mom", "mom_dup", "rev"]
    assert np.allclose(np.diag(corr.to_numpy()), 1.0)
    assert np.allclose(corr.to_numpy(), corr.to_numpy().T)
    # the duplicate is near-perfectly correlated with its source
    assert corr.loc["mom", "mom_dup"] > 0.99


def test_ic_over_time_shape_and_labels(signals, prices):
    iot = hm.ic_over_time(signals, prices, horizon=5, freq="ME")
    assert list(iot.columns) == ["mom", "mom_dup", "rev"]
    assert isinstance(iot.index, pd.DatetimeIndex)
    assert len(iot) > 0  # several monthly buckets over 200 days


def test_ic_scorecard_shape_and_metric_columns(signals, prices):
    sc = hm.ic_scorecard(signals, prices, horizon=5)
    assert list(sc.index) == ["mom", "mom_dup", "rev"]
    assert list(sc.columns) == hm.SCORECARD_METRICS


def test_ic_decay_matrix_shape(signals, prices):
    horizons = (1, 2, 3, 5, 10, 21)
    decay = hm.ic_decay_matrix(signals, prices, horizons=horizons)
    assert list(decay.index) == ["mom", "mom_dup", "rev"]
    assert list(decay.columns) == list(horizons)


def test_asset_correlation_ungrouped_is_instrument_matrix(prices):
    corr = hm.asset_correlation(prices)
    assert corr.shape == (6, 6)
    assert list(corr.index) == list(prices.columns)
    assert np.allclose(np.diag(corr.to_numpy()), 1.0)
    assert np.allclose(corr.to_numpy(), corr.to_numpy().T)


def test_asset_correlation_grouped_collapses_to_groups(prices):
    groups = {"A0": "g1", "A1": "g1", "A2": "g1", "A3": "g2", "A4": "g2", "A5": "g2"}
    corr = hm.asset_correlation(prices, groups)
    assert corr.shape == (2, 2)
    assert set(corr.index) == {"g1", "g2"}
    assert np.allclose(np.diag(corr.to_numpy()), 1.0)


def test_render_heatmap_smoke(signals, prices):
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=True)
    hm.render_heatmap(hm.factor_correlation(signals), "corr", console=console)
    hm.render_heatmap(hm.ic_scorecard(signals, prices), "sc", per_column=True, console=console)
    out = buf.getvalue()
    assert "corr" in out and "sc" in out
    assert "mom" in out
