"""Tests for the Alpha101 operator library and a couple of alpha formulas. Deterministic,
offline — pins the operator math (the part most likely to be subtly wrong)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.factors import operators as op
from qhfi.factors.alpha101 import Alpha006, Alpha101
from qhfi.factors.market import MarketPanels


def _panel(data):
    idx = pd.date_range("2024-01-01", periods=len(data), freq="D", tz="UTC")
    return pd.DataFrame(data, index=idx, columns=["A", "B", "C"])


# ── operators ──────────────────────────────────────────────────────────────────
def test_rank_is_cross_sectional_pct():
    p = _panel([[10, 20, 30]])
    r = op.rank(p).iloc[0]
    assert r["A"] == pytest.approx(1 / 3) and r["C"] == pytest.approx(1.0)


def test_delay_and_delta():
    p = _panel([[1, 1, 1], [3, 3, 3], [7, 7, 7]])
    assert op.delay(p, 1).iloc[2]["A"] == 3
    assert op.delta(p, 1).iloc[2]["A"] == 4


def test_ts_argmax_and_rank():
    p = _panel([[1, 0, 0], [3, 0, 0], [2, 0, 0], [5, 0, 0]])
    # window 3 ending at row 3: values [3,2,5] → max at position 2
    assert op.ts_argmax(p, 3).iloc[3]["A"] == 2
    # last value 5 is the largest in [3,2,5] → top rank 1.0
    assert op.ts_rank(p, 3).iloc[3]["A"] == pytest.approx(1.0)


def test_decay_linear_weights():
    p = _panel([[1, 0, 0], [2, 0, 0], [3, 0, 0]])
    # weights (1,2,3)/6 over [1,2,3] = (1*1 + 2*2 + 3*3)/6 = 14/6
    assert op.decay_linear(p, 3).iloc[2]["A"] == pytest.approx(14 / 6)


def test_correlation_perfect_and_inverse():
    x = _panel([[i, 0, 0] for i in range(6)])
    up = _panel([[2 * i, 0, 0] for i in range(6)])
    down = _panel([[-i, 0, 0] for i in range(6)])
    assert op.correlation(x, up, 5).iloc[5]["A"] == pytest.approx(1.0, abs=1e-9)
    assert op.correlation(x, down, 5).iloc[5]["A"] == pytest.approx(-1.0, abs=1e-9)


# ── alpha expressions ──────────────────────────────────────────────────────────
def _market(n=15):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    cols = ["A", "B", "C"]
    close = pd.DataFrame(100 + rng.normal(0, 1, (n, 3)).cumsum(0), index=idx, columns=cols)
    volume = pd.DataFrame(1e6 + rng.normal(0, 1e5, (n, 3)), index=idx, columns=cols)
    return MarketPanels(
        open=close.shift(1).fillna(close), high=close + 1, low=close - 1,
        close=close, volume=volume,
    )


def test_alpha101_formula():
    m = _market()
    out = Alpha101(m).compute(m.close, Universe(name="t", instruments=[]))
    expected = (m.close - m.open) / ((m.high - m.low) + 0.001)
    assert np.allclose(out.values, expected.values, equal_nan=True)


def test_correlation_alpha_is_finite_and_aligned():
    m = _market(20)
    uni = Universe(name="t", instruments=[Instrument(id=c, asset_class=AssetClass.EQUITY) for c in "ABC"])
    out = Alpha006(m).compute(m.close, uni)
    assert out.shape == m.close.shape
    assert np.isfinite(out.iloc[-1].values).all()      # warm window → finite
