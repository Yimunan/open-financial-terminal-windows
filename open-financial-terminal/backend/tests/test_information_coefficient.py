"""Unit tests for the factor Information Coefficient in `backtest._information_coefficient`.

Synthetic score/price panels with a known scores→forward-return relationship pin the IC sign,
hit rate, and guard rails — no network, no data lake.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _information_coefficient


def _panels(n_months: int, n_names: int, sign: float):
    """Daily grid with month-constant scores and prices. The forward return between month-end m and
    m+1 is exactly `sign × month_scores[m]`, so Spearman(score, forward_ret) ≈ `sign` each month —
    a clean IC ≈ +1 (sign +1) or −1 (sign −1)."""
    days = pd.date_range("2021-01-01", periods=n_months * 21, freq="B", tz="UTC")
    names = [f"N{i}" for i in range(n_names)]
    rng = np.random.default_rng(0)
    month_scores = rng.normal(0, 1, (n_months, n_names))
    rets = sign * month_scores * 0.05  # ret[m] realised from month-end m → m+1

    price_end = np.empty((n_months, n_names))
    price_end[0] = 100.0
    for m in range(1, n_months):
        price_end[m] = price_end[m - 1] * (1 + rets[m - 1])

    scores = pd.DataFrame(index=days, columns=names, dtype=float)
    prices = pd.DataFrame(index=days, columns=names, dtype=float)
    for m in range(n_months):
        seg = days[m * 21:(m + 1) * 21]
        scores.loc[seg] = month_scores[m]
        prices.loc[seg] = price_end[m]
    return scores.astype(float), prices.astype(float)


def test_positive_predictive_factor_has_positive_ic():
    scores, prices = _panels(12, 20, sign=+1.0)
    out = _information_coefficient(scores, prices, prices.index[0].date(), prices.index[-1].date())
    assert out is not None
    assert out["mean_ic"] > 0.5
    assert out["hit_rate"] > 60
    assert out["n_periods"] >= 3


def test_inverted_factor_has_negative_ic():
    scores, prices = _panels(12, 20, sign=-1.0)
    out = _information_coefficient(scores, prices, prices.index[0].date(), prices.index[-1].date())
    assert out is not None
    assert out["mean_ic"] < -0.5


def test_too_few_names_returns_none():
    scores, prices = _panels(12, 3, sign=1.0)  # < 5 names
    assert _information_coefficient(scores, prices, prices.index[0].date(), prices.index[-1].date()) is None


def test_series_aligns_with_n_periods():
    scores, prices = _panels(10, 15, sign=1.0)
    out = _information_coefficient(scores, prices, prices.index[0].date(), prices.index[-1].date())
    assert out is not None
    assert len(out["series"]) == out["n_periods"]
    assert all(-1.0 <= p["value"] <= 1.0 for p in out["series"])
