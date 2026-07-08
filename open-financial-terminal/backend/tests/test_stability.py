"""Unit tests for the sub-period stability analytics in `backtest.shape_result`.

Deterministic synthetic return streams (no network / data lake) pin the segmentation maths and
the guard rails of `_subperiod_stability`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _subperiod_stability

_PPY = 252


def _series(values: list[float] | np.ndarray) -> pd.Series:
    idx = pd.date_range("2021-01-04", periods=len(values), freq="B")
    return pd.Series(values, index=idx)


def test_splits_into_k_segments_and_counts_positive():
    """A steadily-positive stream splits into k=4 sub-periods, all positive → consistency 1.0."""
    s = _series(np.full(200, 0.001))  # +10bps/day every day
    out = _subperiod_stability(s, _PPY, k=4)
    assert out is not None
    assert out["n_periods"] == 4
    assert out["positive_periods"] == 4
    assert out["consistency"] == 1.0
    # segments are contiguous and cover the whole window in order
    assert out["periods"][0]["start"] == s.index[0].strftime("%Y-%m-%d")
    assert out["periods"][-1]["end"] == s.index[-1].strftime("%Y-%m-%d")


def test_detects_concentrated_edge():
    """Edge lives only in the first half → first segments positive, later ones negative.

    Returns carry realistic daily volatility (so each segment has a finite Sharpe) but the mean
    flips sign at the midpoint.
    """
    rng = np.random.default_rng(19)
    first = rng.normal(0.002, 0.01, 100)    # strong positive drift
    second = rng.normal(-0.0008, 0.01, 100)  # negative drift
    out = _subperiod_stability(_series(np.concatenate([first, second])), _PPY, k=4)
    assert out is not None
    assert 0.0 < out["consistency"] < 1.0          # not all periods win
    assert out["sharpe_max"] > out["sharpe_min"]   # dispersion across regimes
    assert out["periods"][0]["ret"] > 0 and out["periods"][-1]["ret"] < 0


def test_too_short_returns_none():
    """Under 40 observations there isn't enough history to segment."""
    assert _subperiod_stability(_series(np.full(30, 0.001)), _PPY) is None


def test_segment_count_shrinks_for_short_history():
    """Just over the floor: k caps so each segment keeps ~15+ observations."""
    out = _subperiod_stability(_series(np.full(45, 0.001)), _PPY, k=4)
    assert out is not None
    assert 2 <= out["n_periods"] <= 3  # 45 // 15 = 3, never the requested 4
