"""Unit tests for the drawdown-episode decomposition in `backtest._drawdown_table`.

Hand-built equity curves with known peaks/troughs pin the episode boundaries, depth, recovery
detection, and calendar-day durations — no network, no data lake.
"""

from __future__ import annotations

import pandas as pd

from app.services.backtest import _drawdown_table


def _eq(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2023-01-01", periods=len(values), freq="D"))


def test_single_recovered_drawdown():
    # 100 →110 peak →88 trough →110 recover. Depth = 88/110 − 1 = −20%.
    eq = _eq([100, 110, 99, 88, 95, 110])
    dd = _drawdown_table(eq)
    assert len(dd) == 1
    d = dd[0]
    assert d["depth"] == -20.0
    assert d["peak_date"] == "2023-01-02"      # the 110
    assert d["trough_date"] == "2023-01-04"    # the 88
    assert d["recovery_date"] == "2023-01-06"  # back to 110
    assert d["ongoing"] is False
    assert d["decline_days"] == 2              # 02 → 04
    assert d["recovery_days"] == 2             # 04 → 06
    assert d["underwater_days"] == 4           # 02 → 06


def test_ongoing_drawdown_has_no_recovery():
    eq = _eq([100, 120, 90, 80])  # still underwater at the end
    dd = _drawdown_table(eq)
    assert len(dd) == 1
    d = dd[0]
    assert d["ongoing"] is True
    assert d["recovery_date"] is None
    assert d["recovery_days"] is None
    assert d["depth"] == round((80 / 120 - 1) * 100, 2)
    assert d["underwater_days"] == 2  # peak(120 @ 01-02) → last bar (01-04)


def test_multiple_episodes_sorted_deepest_first():
    # First dip −10% (100→90→100), then a deeper −30% (100→70→100).
    eq = _eq([100, 90, 100, 70, 100])
    dd = _drawdown_table(eq)
    assert len(dd) == 2
    assert dd[0]["depth"] == -30.0 and dd[0]["rank"] == 1
    assert dd[1]["depth"] == -10.0 and dd[1]["rank"] == 2


def test_monotonic_curve_has_no_drawdowns():
    assert _drawdown_table(_eq([100, 101, 102, 103])) == []


def test_top_n_limit():
    # Five separate 10/20/30/40/50% dips, then ask for the worst 3.
    vals = []
    base = 100.0
    for depth in (0.5, 0.4, 0.3, 0.2, 0.1):
        vals += [base, base * (1 - depth), base]
    dd = _drawdown_table(_eq(vals), top=3)
    assert len(dd) == 3
    assert [d["depth"] for d in dd] == [-50.0, -40.0, -30.0]
