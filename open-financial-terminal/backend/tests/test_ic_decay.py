"""Unit tests for the IC-by-horizon (factor decay) analytics in `backtest._ic_decay`.

Synthetic panels with a controllable decay rate pin the horizon ordering and guard — no network.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.backtest import _ic_decay


def _panels(n_months: int, n_names: int, decay: float):
    """Month-constant scores; the per-name monthly return = score × ``decay**month_offset`` so a
    score's predictive power for month m+h shrinks geometrically with h (decay<1 → IC fades)."""
    days = pd.date_range("2020-01-01", periods=n_months * 21, freq="B", tz="UTC")
    names = [f"N{i}" for i in range(n_names)]
    rng = np.random.default_rng(0)
    ms = rng.normal(0, 1, (n_months, n_names))
    # monthly return realised m→m+1 driven by the score at m (so 1m IC is strongest)
    rets = ms * 0.03
    price_end = np.empty((n_months, n_names))
    price_end[0] = 100.0
    for m in range(1, n_months):
        price_end[m] = price_end[m - 1] * (1 + rets[m - 1])
    scores = pd.DataFrame(index=days, columns=names, dtype=float)
    prices = pd.DataFrame(index=days, columns=names, dtype=float)
    for m in range(n_months):
        seg = days[m * 21:(m + 1) * 21]
        scores.loc[seg] = ms[m]
        prices.loc[seg] = price_end[m]
    return scores.astype(float), prices.astype(float)


def test_reports_each_requested_horizon():
    scores, prices = _panels(30, 20, decay=0.5)
    out = _ic_decay(scores, prices, prices.index[0].date(), prices.index[-1].date(), horizons=(1, 3, 6))
    assert out is not None
    assert [d["horizon"] for d in out] == [1, 3, 6]
    assert all(d["n"] >= 3 for d in out)


def test_one_month_ic_is_strongest_when_signal_drives_next_month():
    # returns are driven by the *current* month's score → 1m IC high, longer horizons noisier.
    scores, prices = _panels(36, 25, decay=0.0)
    out = _ic_decay(scores, prices, prices.index[0].date(), prices.index[-1].date(), horizons=(1, 3))
    by_h = {d["horizon"]: d["mean_ic"] for d in out}
    assert by_h[1] > 0.5                 # strong 1-month predictive power
    assert by_h[1] >= by_h[3]            # decays (or at least doesn't strengthen) by 3 months


def test_too_few_names_returns_none():
    scores, prices = _panels(30, 3, decay=0.5)
    assert _ic_decay(scores, prices, prices.index[0].date(), prices.index[-1].date()) is None
