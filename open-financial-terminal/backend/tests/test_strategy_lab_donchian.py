"""Unit tests for the Donchian-breakout Strategy-Lab template.

Deterministic synthetic price paths (no network) pin the breakout direction, the no-look-ahead
shift, and that the simulator turns the signal into trades.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app import indicators as ind
from app.services.strategy_lab import TEMPLATES, _donchian, simulate


def _ohlc(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=len(closes), freq="B")
    c = pd.Series(closes, index=idx, dtype=float)
    # high == low == close: no intrabar range, so breakouts are unambiguous.
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c})


def test_registered_in_templates():
    assert "donchian" in TEMPLATES
    assert [p.key for p in TEMPLATES["donchian"].params] == ["window"]


def test_long_on_upside_break_short_on_downside_break():
    closes = [100.0] * 25 + [120.0] * 6 + [70.0] * 6
    f = _ohlc(closes)
    sig = _donchian(f["close"], f["high"], f["low"], {"window": 20})
    assert sig[10] == 0   # channel not formed yet → flat
    assert sig[26] == 1   # close 120 broke the prior 20-day high (100) → long
    assert sig[-1] == -1  # close 70 broke the prior 20-day low → short


def test_breakout_bar_does_not_use_its_own_high():
    # shift(1): the bar that prints a new high can't trigger its own breakout off that high.
    f = _ohlc([100.0] * 20 + [130.0])
    ch = ind.donchian(f["high"], f["low"], 20)
    assert ch["upper"].iloc[20] == 100.0  # prior-window high, excludes the 130 bar itself
    sig = _donchian(f["close"], f["high"], f["low"], {"window": 20})
    assert sig[20] == 1  # 130 > prior high 100 → long


def test_flat_price_never_trades():
    f = _ohlc([100.0] * 60)
    sig = _donchian(f["close"], f["high"], f["low"], {"window": 20})
    assert set(np.unique(sig)) == {0}


def test_simulator_produces_trades_from_donchian_signal():
    closes = [100.0] * 25 + [120.0] * 10 + [70.0] * 10 + [130.0] * 10
    out = simulate(_ohlc(closes), "donchian", {"window": 20}, direction="both")
    assert out["stats"]["total_trades"] >= 1
    assert len(out["equity_curve"]) == len(closes)
