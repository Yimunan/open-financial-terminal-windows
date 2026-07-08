"""The no-trade band (``rebalance_threshold``) must (a) suppress sub-band drift so turnover
falls, and (b) never freeze a position out of reaching a target when the drift is large.

These lock the behaviour the calibration relies on (scripts/tune_rebalance.py): a non-zero
band is strictly a turnover trim, not a correctness change for real rebalances.
"""

from __future__ import annotations

import pandas as pd
import pytest

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine, ExecutionConfig
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.core.types import AssetClass, Instrument, Universe


def _uni(lot=1e-9, mult=1.0):
    return Universe(name="t", instruments=[
        Instrument(id="X", asset_class=AssetClass.CRYPTO, exchange="x",
                   lot_size=lot, contract_multiplier=mult, shortable=True),
    ])


def _prices(vals):
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="D", tz="UTC")
    return pd.DataFrame({"X": vals}, index=idx)


def _engine(band: float, equity: float = 1_000_000.0) -> BacktestEngine:
    # Frictionless so turnover differences come only from the band, not from cost drift.
    return BacktestEngine(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
        financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=True, rebalance_threshold=band),
        initial_equity=equity,
    )


def test_band_suppresses_subband_drift_and_cuts_turnover():
    # Flat price; target oscillates by 0.1% of equity each day — below a 0.25% band.
    # Without a band every wiggle trades; with the band only the initial entry trades.
    prices = _prices([100.0] * 12)
    osc = [0.500 if i % 2 == 0 else 0.501 for i in range(12)]
    w = pd.DataFrame({"X": osc}, index=prices.index)

    no_band = _engine(0.0).run(w, prices, _uni())
    band = _engine(0.0025).run(w, prices, _uni())

    assert len(band.trades) < len(no_band.trades)
    assert band.turnover.sum() < no_band.turnover.sum()
    # The sub-band wiggles are fully suppressed: a single establishing trade remains.
    assert len(band.trades) == 1


def test_band_still_reaches_target_when_drift_exceeds_it():
    # A full-size establishing move (0 -> 1.0) dwarfs the band and must execute to target.
    prices = _prices([100.0, 100.0, 100.0])
    w = pd.DataFrame({"X": [0.0, 1.0, 1.0]}, index=prices.index)
    r = _engine(0.0025).run(w, prices, _uni())

    held = r.positions["X"].iloc[-1]
    assert held == pytest.approx(1_000_000.0 / 100.0)   # weight 1.0 reached, no permanent freeze
    assert r.weights["X"].iloc[-1] == pytest.approx(1.0, abs=1e-9)
