"""Unit tests for the performance/risk/trade metric functions in qhfi.evaluation.metrics.

Covers the analytics added for the paper-trading metrics layer: trade_stats over a
closed-P&L list, historical VaR/CVaR, rolling Sharpe, and benchmark-relative stats —
plus the degenerate (empty / all-wins / all-losses / identical-series) edges.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.evaluation.metrics import (
    benchmark_stats,
    cvar,
    rolling_sharpe,
    trade_stats,
    value_at_risk,
)


# ── trade_stats ───────────────────────────────────────────────────────────────
def test_trade_stats_mixed() -> None:
    s = trade_stats([100.0, -50.0, 200.0, -25.0, 0.0])
    assert s["n_trades"] == 5
    assert s["n_wins"] == 2 and s["n_losses"] == 2  # the 0.0 is neither
    assert s["win_rate"] == 0.5
    assert s["gross_profit"] == 300.0 and s["gross_loss"] == 75.0
    assert s["profit_factor"] == 4.0
    assert s["avg_win"] == 150.0 and s["avg_loss"] == -37.5
    assert s["payoff_ratio"] == 4.0
    assert s["largest_win"] == 200.0 and s["largest_loss"] == -50.0
    assert s["total_realized"] == 225.0
    # expectancy = 0.5*150 + 0.5*(-37.5)
    assert s["expectancy"] == 56.25


def test_trade_stats_all_wins_and_empty() -> None:
    w = trade_stats([10.0, 20.0])
    assert w["n_losses"] == 0 and w["win_rate"] == 1.0
    assert w["gross_loss"] == 0.0
    assert w["profit_factor"] == 30.0  # no losses → falls back to gross_profit
    e = trade_stats([])
    assert e["n_trades"] == 0 and e["win_rate"] == 0.0 and e["profit_factor"] == 0.0


def test_trade_stats_all_losses() -> None:
    s = trade_stats([-10.0, -30.0])
    assert s["n_wins"] == 0 and s["win_rate"] == 0.0
    assert s["gross_profit"] == 0.0 and s["gross_loss"] == 40.0
    assert s["avg_loss"] == -20.0
    assert s["largest_loss"] == -30.0


# ── VaR / CVaR ────────────────────────────────────────────────────────────────
def test_value_at_risk_and_cvar() -> None:
    r = pd.Series([-0.10, -0.05, 0.0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08])
    var = value_at_risk(r, level=0.1)
    assert var > 0  # reported as a positive loss
    cv = cvar(r, level=0.1)
    assert cv >= var  # expected shortfall ≥ VaR
    assert value_at_risk(pd.Series([], dtype="float64")) == 0.0
    assert cvar(pd.Series([], dtype="float64")) == 0.0


# ── rolling Sharpe ────────────────────────────────────────────────────────────
def test_rolling_sharpe_shape_and_flat() -> None:
    r = pd.Series(np.linspace(0.001, 0.01, 20))
    rs = rolling_sharpe(r, window=5)
    assert len(rs) == len(r)
    assert not rs.isna().any()  # NaNs filled with 0.0
    # a zero-variance window → 0.0, not inf
    flat = rolling_sharpe(pd.Series([0.01] * 10), window=3)
    assert (flat == 0.0).all()


# ── benchmark_stats ───────────────────────────────────────────────────────────
def test_benchmark_stats_identical_series() -> None:
    b = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
    s = benchmark_stats(b, b)
    assert abs(s["beta"] - 1.0) < 1e-9
    assert abs(s["alpha"]) < 1e-9
    assert abs(s["tracking_error"]) < 1e-9
    assert abs(s["correlation"] - 1.0) < 1e-9


def test_benchmark_stats_leveraged_and_alignment() -> None:
    b = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02], index=range(5))
    p = (2.0 * b).rename("p")  # 2x the benchmark with no alpha
    s = benchmark_stats(p, b)
    assert abs(s["beta"] - 2.0) < 1e-9
    assert abs(s["alpha"]) < 1e-9
    # non-overlapping index → zeroed, not an error
    z = benchmark_stats(pd.Series([0.01], index=[0]), pd.Series([0.01], index=[99]))
    assert z["beta"] == 0.0 and z["correlation"] == 0.0
