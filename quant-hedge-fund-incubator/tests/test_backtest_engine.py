"""Tests for the granular backtest engine — they pin the accounting identity, the
look-ahead lag, and that each realism feature (slippage, financing, rounding, drift,
per-instrument calendar) actually moves PnL in the expected direction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine, ExecutionConfig
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.core.types import AssetClass, Instrument, Universe


def _uni(lot=1e-9, mult=1.0, shortable=True):
    return Universe(name="t", instruments=[
        Instrument(id="X", asset_class=AssetClass.CRYPTO, exchange="x",
                   lot_size=lot, contract_multiplier=mult, shortable=shortable),
    ])


def _prices(vals):
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="D", tz="UTC")
    return pd.DataFrame({"X": vals}, index=idx)


def _frictionless():
    return BacktestEngine(
        cost_model=BpsCostModel(0.0),
        slippage=SlippageModel(0.0),
        financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=True),
        initial_equity=10_000.0,
    )


def test_accounting_identity_and_lag():
    # +10%/day; full long target every day. With lag=1 we enter at day-1 close (110),
    # so equity should track price from 110 onward: 10k → 12.1k, and day-1 return is 0.
    prices = _prices([100.0, 110.0, 121.0, 133.1])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 1.0]}, index=prices.index)
    r = _frictionless().run(w, prices, _uni())

    assert r.returns.iloc[1] == pytest.approx(0.0, abs=1e-9)     # lag: no day-1 gain
    assert r.returns.iloc[2] == pytest.approx(0.10, abs=1e-9)    # full participation after entry
    assert r.equity_curve.iloc[-1] == pytest.approx(12_100.0, rel=1e-9)
    # equity identity: cash + position value == equity each day
    pos_val = r.positions["X"] * prices["X"]
    assert np.allclose((r.cash + pos_val).values, r.equity_curve.values)


def test_drift_means_no_trade_when_already_on_target():
    # After entering at full weight, a constant-weight target needs no further trades
    # because the position drifts *with* price and stays at 100% — only one fill total.
    prices = _prices([100.0, 110.0, 121.0, 133.1])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 1.0]}, index=prices.index)
    r = _frictionless().run(w, prices, _uni())
    assert len(r.trades) == 1


def test_slippage_and_commission_reduce_equity():
    prices = _prices([100.0, 110.0, 121.0, 133.1])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 1.0]}, index=prices.index)
    clean = _frictionless().run(w, prices, _uni())
    costly = BacktestEngine(
        cost_model=BpsCostModel(10.0), slippage=SlippageModel(50.0),
        financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=True),
        initial_equity=10_000.0,
    ).run(w, prices, _uni())
    assert costly.equity_curve.iloc[-1] < clean.equity_curve.iloc[-1]
    assert costly.slippage.sum() > 0 and costly.commission.sum() > 0


def test_short_borrow_is_a_drag():
    # Flat price, short the name → no PnL but borrow fee bleeds equity below start.
    prices = _prices([100.0] * 10)
    w = pd.DataFrame({"X": [-1.0] * 10}, index=prices.index)
    eng = BacktestEngine(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
        financing=FinancingModel(short_borrow_bps=100.0, leverage_bps=0.0, cash_bps=0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=True),
        initial_equity=10_000.0,
    ).run(w, prices, _uni())
    assert eng.equity_curve.iloc[-1] < 10_000.0
    assert eng.financing.sum() > 0


def test_integer_rounding_for_whole_lots():
    # lot_size=1, small equity → fractional target must round to a whole unit.
    prices = _prices([100.0, 100.0, 100.0])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=prices.index)
    eng = BacktestEngine(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
        financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=False),
        initial_equity=250.0,
    ).run(w, prices, _uni(lot=1.0))
    held = eng.positions["X"].iloc[-1]
    assert held == pytest.approx(round(held))   # whole units only


def test_per_instrument_calendar_carries_through_nan():
    # A missing (NaN) price day must not crash, not trade, and carry the position.
    prices = _prices([100.0, np.nan, 121.0])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=prices.index)
    r = _frictionless().run(w, prices, _uni())
    assert not r.equity_curve.isna().any()
    assert r.positions["X"].iloc[1] == r.positions["X"].iloc[0]  # carried across the gap
