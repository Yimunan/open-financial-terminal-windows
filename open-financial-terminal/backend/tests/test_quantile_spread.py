"""Unit tests for the factor quantile return-spread in `backtest._quantile_spread`.

Synthetic score/price panels with a known scores→forward-return relationship pin monotonicity,
the long-short spread sign, and the breadth guard — no network, no data lake.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _quantile_spread


def _panels(n_months: int, n_names: int, sign: float):
    """Month-constant scores + prices where the forward return between month-ends m and m+1 equals
    `sign × score`. sign +1 → top scores earn the most (monotone up); −1 → inverted."""
    days = pd.date_range("2021-01-01", periods=n_months * 21, freq="B", tz="UTC")
    names = [f"N{i}" for i in range(n_names)]
    rng = np.random.default_rng(1)
    month_scores = rng.normal(0, 1, (n_months, n_names))
    rets = sign * month_scores * 0.04

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


def test_monotone_increasing_for_predictive_factor():
    scores, prices = _panels(12, 25, sign=+1.0)
    out = _quantile_spread(scores, prices, prices.index[0].date(), prices.index[-1].date())
    assert out is not None
    assert out["n_buckets"] == 5
    assert out["monotonicity"] == 1.0          # top bucket earns most, bottom least
    assert out["spread"] > 0                    # long-short (top − bottom) positive
    assert out["buckets"][-1] > out["buckets"][0]


def test_inverted_factor_has_negative_spread_and_monotonicity():
    scores, prices = _panels(12, 25, sign=-1.0)
    out = _quantile_spread(scores, prices, prices.index[0].date(), prices.index[-1].date())
    assert out is not None
    assert out["spread"] < 0
    assert out["monotonicity"] == -1.0


def test_narrow_universe_returns_none():
    # 6 names can't fill 5 buckets at ≥2 names each.
    scores, prices = _panels(12, 6, sign=1.0)
    assert _quantile_spread(scores, prices, prices.index[0].date(), prices.index[-1].date()) is None


def test_buckets_length_matches_n_buckets():
    scores, prices = _panels(10, 30, sign=1.0)
    out = _quantile_spread(scores, prices, prices.index[0].date(), prices.index[-1].date())
    assert out is not None
    assert len(out["buckets"]) == out["n_buckets"] == 5
