"""Tests for pre-trade risk gates and the drawdown kill-switch."""

from __future__ import annotations

import pandas as pd

from qhfi.risk.gates import RiskGate, RiskLimits


def _gate() -> RiskGate:
    return RiskGate(RiskLimits(max_gross=1.5, max_net=1.0, max_position=0.20, max_drawdown_kill=0.20))


def test_weights_within_limits_approved():
    w = pd.Series({"A": 0.2, "B": -0.2, "C": 0.1})
    assert _gate().check_weights(w).approved


def test_gross_breach_rejected():
    w = pd.Series({"A": 1.0, "B": -1.0})           # gross 2.0 > 1.5
    d = _gate().check_weights(w)
    assert not d.approved and "gross" in d.reason


def test_net_breach_rejected():
    w = pd.Series({"A": 0.8, "B": 0.5})            # net 1.3 > 1.0 (gross 1.3 ok)
    d = _gate().check_weights(w)
    assert not d.approved and "net" in d.reason


def test_position_breach_rejected():
    w = pd.Series({"A": 0.5, "B": -0.1})           # |A| 0.5 > 0.20 (gross/net ok)
    d = _gate().check_weights(w)
    assert not d.approved and "A" in d.reason


def test_frame_checks_last_row():
    frame = pd.DataFrame({"A": [0.1, 0.9], "B": [0.1, 0.9]})   # last row gross 1.8 > 1.5
    assert not _gate().check_weights(frame).approved


def test_drawdown_within_limit_approved():
    curve = pd.Series([100.0, 110.0, 100.0])       # DD = 100/110 − 1 ≈ −0.091
    assert _gate().check_drawdown(curve).approved


def test_drawdown_kill_trips():
    curve = pd.Series([100.0, 120.0, 90.0])        # DD = 90/120 − 1 = −0.25 < −0.20
    d = _gate().check_drawdown(curve)
    assert not d.approved and "drawdown" in d.reason


def test_empty_curve_approved():
    assert _gate().check_drawdown(pd.Series(dtype=float)).approved
