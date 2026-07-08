"""Unit tests for the return-distribution / tail-risk analytics in `backtest`.

Synthetic return series with known shape pin the skew sign, VaR/CVaR ordering, up-day share, and
the guard rail — no network, no data lake.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _distribution_stats


def _series(values: np.ndarray) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2022-01-03", periods=len(values), freq="B"))


def test_too_short_returns_none():
    assert _distribution_stats(_series(np.full(10, 0.001))) is None


def test_symmetric_normal_has_near_zero_skew_and_half_up_days():
    rng = np.random.default_rng(5)
    out = _distribution_stats(_series(rng.normal(0.0, 0.01, 2000)))
    assert out is not None
    assert abs(out["skew"]) < 0.2
    assert abs(out["kurtosis"]) < 0.5          # ~mesokurtic for a normal
    assert 45 <= out["pct_positive"] <= 55     # roughly half up days


def test_var_is_more_negative_than_cvar_and_worst_day_is_min():
    rng = np.random.default_rng(8)
    r = rng.normal(0.0005, 0.012, 1000)
    s = _series(r)
    out = _distribution_stats(s)
    assert out is not None
    # CVaR (mean of the worst 5%) is at least as bad as VaR (the 5% cutoff).
    assert out["cvar95"] <= out["var95"]
    assert out["worst_day"] == round(float(s.min()) * 100, 2)
    assert out["best_day"] == round(float(s.max()) * 100, 2)


def test_left_skew_detected():
    """Small gains, frequent larger losses → negative skew and a left tail fatter than the right.

    Crashes occur on ~8% of days (every 12th) so they sit *inside* the 5% VaR cutoff and widen
    the left tail — making tail_ratio (right/left) < 1.
    """
    rng = np.random.default_rng(2)
    base = rng.normal(0.001, 0.003, 500)
    base[::12] = -0.05  # periodic crashes on >5% of days
    out = _distribution_stats(_series(base))
    assert out is not None
    assert out["skew"] < 0
    assert out["tail_ratio"] is not None and out["tail_ratio"] < 1
