"""Unit tests for the transaction-cost sensitivity sweep in `backtest._cost_sensitivity`.

A deterministic two-name long/short book run through the real engine at a ladder of slippage
levels: higher slippage must mean higher costs and (weakly) lower net return. Needs no network
(synthetic prices), but exercises the real qhfi BacktestEngine.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.deps import make_instrument
from app.services.backtest import _cost_sensitivity
from qhfi.core.types import Universe


def _setup():
    days = pd.date_range("2021-01-01", periods=400, freq="B", tz="UTC")
    rng = np.random.default_rng(4)
    # Two trending-but-noisy names so the book actually trades and incurs turnover.
    a = 100 * np.cumprod(1 + rng.normal(0.0006, 0.012, len(days)))
    b = 100 * np.cumprod(1 + rng.normal(0.0002, 0.012, len(days)))
    prices = pd.DataFrame({"AAA": a, "BBB": b}, index=days)
    universe = Universe(name="_t", instruments=[make_instrument("AAA", "equity"),
                                                make_instrument("BBB", "equity")])
    # Monthly-flipping dollar-neutral weights → guaranteed turnover.
    weights = pd.DataFrame(0.0, index=days, columns=["AAA", "BBB"])
    flip = (days.to_period("M").astype(str).map(hash) % 2 == 0)
    weights.loc[flip, "AAA"] = 0.5
    weights.loc[flip, "BBB"] = -0.5
    weights.loc[~flip, "AAA"] = -0.5
    weights.loc[~flip, "BBB"] = 0.5
    return weights, prices, universe


def test_curve_spans_the_bps_grid():
    weights, prices, universe = _setup()
    out = _cost_sensitivity(weights, prices, universe, date(2021, 1, 1), date(2022, 7, 1), 252, 100_000.0)
    assert out is not None
    assert [p["bps"] for p in out] == [0.0, 5.0, 10.0, 20.0, 40.0]


def test_costs_rise_monotonically_with_slippage():
    weights, prices, universe = _setup()
    out = _cost_sensitivity(weights, prices, universe, date(2021, 1, 1), date(2022, 7, 1), 252, 100_000.0)
    costs = [p["total_costs"] for p in out]
    assert costs == sorted(costs)          # non-decreasing in bps
    assert costs[-1] > costs[0]            # 40bps strictly costlier than 0bps


def test_higher_slippage_does_not_improve_net_return():
    weights, prices, universe = _setup()
    out = _cost_sensitivity(weights, prices, universe, date(2021, 1, 1), date(2022, 7, 1), 252, 100_000.0)
    assert out[-1]["cagr"] <= out[0]["cagr"] + 1e-9  # frictions can't help
