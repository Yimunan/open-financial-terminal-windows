"""Unit tests for the market-model (benchmark-relative) analytics in `backtest.shape_result`.

These exercise `_benchmark_stats` directly with synthetic return series — deterministic, no
network, no data lake. They pin the regression maths (beta = cov/var, Jensen's alpha,
information ratio, tracking error, R²) so the dashboard's "Market model" panel can't silently
drift.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from qhfi.evaluation import metrics as M

from app.services.backtest import _benchmark_stats

_PPY = 252


def _summary(s: pd.Series) -> dict:
    return M.summary(s, periods_per_year=_PPY)


def test_levered_clone_recovers_beta_and_zero_alpha():
    """strategy = 1.5 × benchmark exactly → beta 1.5, ~0 alpha, perfect fit (R²≈1, corr≈1)."""
    idx = pd.date_range("2022-01-03", periods=300, freq="B")
    rng = np.random.default_rng(7)
    bench = pd.Series(rng.normal(0.0004, 0.01, len(idx)), index=idx)
    strat = 1.5 * bench  # pure leverage, no idiosyncratic return

    out = _benchmark_stats(strat, bench, _summary(strat), _PPY)
    assert out is not None
    assert out["beta"] == 1.5
    assert abs(out["alpha"]) < 0.01          # no alpha when it's a pure clone
    assert out["correlation"] == 1.0
    assert out["r_squared"] == 1.0
    # excess CAGR sign tracks the benchmark's own sign under leverage
    assert (out["excess_cagr"] > 0) == (out["bench_cagr"] > 0)


def test_positive_alpha_is_detected():
    """strategy = benchmark + a steady daily premium → beta≈1, clearly positive annual alpha."""
    idx = pd.date_range("2022-01-03", periods=300, freq="B")
    rng = np.random.default_rng(11)
    bench = pd.Series(rng.normal(0.0, 0.01, len(idx)), index=idx)
    premium = 0.0005  # +5bps/day ≈ +12.6%/yr of pure alpha
    strat = bench + premium

    out = _benchmark_stats(strat, bench, _summary(strat), _PPY)
    assert out is not None
    assert abs(out["beta"] - 1.0) < 1e-6
    assert out["alpha"] > 10.0               # ≈ 0.0005 * 252 * 100
    assert out["information_ratio"] is not None and out["information_ratio"] > 0


def test_too_short_overlap_returns_none():
    """Fewer than 20 overlapping days → not enough to fit a market model."""
    idx = pd.date_range("2022-01-03", periods=10, freq="B")
    s = pd.Series(np.linspace(-0.01, 0.01, 10), index=idx)
    assert _benchmark_stats(s, s.copy(), _summary(s), _PPY) is None


def test_zero_variance_benchmark_returns_none():
    """A flat benchmark has no variance to regress against → None, no divide-by-zero."""
    idx = pd.date_range("2022-01-03", periods=60, freq="B")
    bench = pd.Series(0.0, index=idx)
    rng = np.random.default_rng(3)
    strat = pd.Series(rng.normal(0.0, 0.01, len(idx)), index=idx)
    assert _benchmark_stats(strat, bench, _summary(strat), _PPY) is None
