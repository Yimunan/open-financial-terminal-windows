"""Unit tests for the rolling-beta series in `backtest._rolling_beta_series`.

Synthetic aligned return series with a known beta pin the level, the drift detection, and the
short-history guard — no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _rolling_beta_series


def _series(values: np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2022-01-03", periods=len(values), freq="B"))


def test_constant_beta_recovered():
    rng = np.random.default_rng(3)
    bench = _series(rng.normal(0, 0.01, 300))
    strat = _series((1.5 * bench.to_numpy()) + rng.normal(0, 0.0005, 300))  # beta ≈ 1.5
    out = _rolling_beta_series(strat, bench)
    assert len(out) > 0
    betas = [p["value"] for p in out]
    assert abs(sum(betas) / len(betas) - 1.5) < 0.1  # average rolling beta ≈ 1.5


def test_detects_beta_drift():
    rng = np.random.default_rng(9)
    bench = _series(rng.normal(0, 0.01, 400))
    b = bench.to_numpy()
    # first half beta 0 (market-neutral), second half beta 1 (fully exposed)
    strat = np.concatenate([rng.normal(0, 0.01, 200), b[200:] + rng.normal(0, 0.0005, 200)])
    out = _rolling_beta_series(_series(strat), bench)
    betas = [p["value"] for p in out]
    assert betas[0] < 0.5 and betas[-1] > 0.5  # exposure rises across the window


def test_short_history_returns_empty():
    s = _series(np.linspace(-0.01, 0.01, 30))
    assert _rolling_beta_series(s, s.copy()) == []
