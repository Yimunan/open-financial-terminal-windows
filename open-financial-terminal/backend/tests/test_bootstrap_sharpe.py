"""Unit tests for the block-bootstrap Sharpe CI in `backtest._bootstrap_sharpe`.

Seeded → deterministic. Synthetic return streams pin the ordering of the percentile band, the
P(SR>0) extremes, the short-history guard, and reproducibility.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _bootstrap_sharpe

_PPY = 252


def _series(values: np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2022-01-03", periods=len(values), freq="B"))


def test_band_is_ordered_and_reproducible():
    rng = np.random.default_rng(0)
    s = _series(rng.normal(0.0006, 0.01, 500))
    a = _bootstrap_sharpe(s, _PPY)
    b = _bootstrap_sharpe(s, _PPY)
    assert a is not None
    assert a["p5"] <= a["p50"] <= a["p95"]   # percentile ordering
    assert a == b                             # seeded → identical


def test_strong_positive_drift_is_almost_surely_positive():
    rng = np.random.default_rng(1)
    s = _series(rng.normal(0.0015, 0.008, 600))  # high, steady Sharpe
    out = _bootstrap_sharpe(s, _PPY)
    assert out["prob_positive"] > 95
    assert out["p5"] > 0


def test_zero_realized_mean_straddles_zero():
    rng = np.random.default_rng(2)
    raw = rng.normal(0.0, 0.01, 600)
    raw -= raw.mean()  # force the *realised* mean to 0 (the bootstrap centers on the sample stat)
    out = _bootstrap_sharpe(_series(raw), _PPY)
    assert out["p5"] < 0 < out["p95"]        # CI straddles zero for a no-edge series
    assert 35 < out["prob_positive"] < 65    # roughly even odds of a positive Sharpe


def test_short_history_returns_none():
    assert _bootstrap_sharpe(_series(np.zeros(40) + 0.001), _PPY) is None
