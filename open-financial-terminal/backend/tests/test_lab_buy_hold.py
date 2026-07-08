"""Tests for the Strategy-Lab buy-and-hold baseline added to `simulate`.

Synthetic OHLC (no network) pins the buy-hold return, the strategy-minus-hold outperformance, and
that the benchmark curve aligns to the equity curve.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.strategy_lab import simulate


def _ohlc(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=len(closes), freq="B")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c})


def test_buy_hold_return_matches_price_change():
    # +50% over the window (100 → 150) regardless of strategy activity.
    closes = list(np.linspace(100.0, 150.0, 80))
    out = simulate(_ohlc(closes), "sma_cross", {"fast": 5, "slow": 20}, direction="long_only")
    assert out["stats"]["buy_hold_pct"] == 50.0
    # outperformance is strategy net return minus the 50% hold.
    assert out["stats"]["vs_buy_hold"] == round(out["stats"]["net_pnl_pct"] - 50.0, 2)


def test_benchmark_curve_aligns_with_equity_curve():
    closes = list(100 + 10 * np.sin(np.linspace(0, 6, 120)))
    out = simulate(_ohlc(closes), "rsi_reversion", {"period": 14, "low": 30, "high": 70})
    assert "benchmark_curve" in out
    assert len(out["benchmark_curve"]) == len(out["equity_curve"])
    # buy-hold curve starts at the initial equity (100k default).
    assert out["benchmark_curve"][0]["value"] == 100_000.0


def test_strategy_that_never_trades_underperforms_a_rising_hold():
    # Monotone rise; a flat strategy (no trades) returns 0 while buy-hold gains → negative vs_hold.
    closes = list(np.linspace(100.0, 200.0, 60))
    out = simulate(_ohlc(closes), "sma_cross", {"fast": 5, "slow": 20}, direction="short_only")
    # short_only on a pure uptrend rarely profits; buy-hold doubled → strategy should trail it.
    assert out["stats"]["vs_buy_hold"] < 0
