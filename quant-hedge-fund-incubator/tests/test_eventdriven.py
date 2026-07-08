"""Tests for the event-driven backtester. The headline property is **numerical equivalence**
to the vectorized BacktestEngine on dense daily data (same models, same accounting); the rest
re-pin the accounting/lag/cost behaviour on the event engine and exercise the framework seams
(native push strategy, pluggable execution handler, scorecard + walk-forward compatibility).

Synthetic, offline, seeded.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine, ExecutionConfig
from qhfi.backtest.eventdriven import (
    EventDrivenEngine,
    EventLoop,
    EventStrategy,
    SignalEvent,
    WeightStrategyAdapter,
)
from qhfi.backtest.eventdriven.data import PanelDataHandler
from qhfi.backtest.eventdriven.portfolio import Portfolio
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.core.types import AssetClass, Instrument, Universe


def _uni(ids, lot=1e-9, mult=1.0, shortable=True):
    return Universe(name="t", instruments=[
        Instrument(id=i, asset_class=AssetClass.CRYPTO, exchange="x",
                   lot_size=lot, contract_multiplier=mult, shortable=shortable) for i in ids])


def _prices(vals):
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="D", tz="UTC")
    return pd.DataFrame({"X": vals}, index=idx)


_FRICTIONLESS = dict(
    cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0), financing=FinancingModel(0.0, 0.0, 0.0),
    execution=ExecutionConfig(signal_lag=1, allow_fractional=True), initial_equity=10_000.0,
)


# ── equivalence to the vectorized engine (the anchor) ─────────────────────────────
def test_equivalent_single_name_frictionless():
    prices = _prices([100.0, 110.0, 121.0, 133.1])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 1.0]}, index=prices.index)
    vec = BacktestEngine(**_FRICTIONLESS).run(w, prices, _uni(["X"]))
    evt = EventDrivenEngine(**_FRICTIONLESS).run(w, prices, _uni(["X"]))
    assert np.allclose(vec.equity_curve.values, evt.equity_curve.values, atol=1e-9)
    assert np.allclose(vec.returns.values, evt.returns.values, atol=1e-9)


def test_equivalent_multiname_long_short_with_costs():
    rng = np.random.default_rng(0)
    n, t = 5, 150
    idx = pd.date_range("2022-01-01", periods=t, freq="B", tz="UTC")
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (t, n)), axis=0),
                      index=idx, columns=[f"A{i}" for i in range(n)])
    w = pd.DataFrame(rng.normal(0, 1, (t, n)), index=idx, columns=px.columns)
    w = w.div(w.abs().sum(axis=1), axis=0)                       # dollar-scaled long/short
    kw = dict(cost_model=BpsCostModel(10.0), slippage=SlippageModel(5.0),
              financing=FinancingModel(50.0, 100.0, 0.0),
              execution=ExecutionConfig(signal_lag=1, allow_fractional=True), initial_equity=1_000_000.0)
    vec = BacktestEngine(**kw).run(w, px, _uni(px.columns))
    evt = EventDrivenEngine(**kw).run(w, px, _uni(px.columns))
    assert np.allclose(vec.equity_curve.values, evt.equity_curve.values, rtol=1e-9, atol=1e-6)
    assert np.allclose(vec.returns.values, evt.returns.values, atol=1e-9)
    assert np.allclose(vec.turnover.values, evt.turnover.values, atol=1e-9)
    assert np.allclose(vec.commission.values, evt.commission.values, atol=1e-6)
    assert np.allclose(vec.financing.values, evt.financing.values, atol=1e-6)


def test_equivalent_nan_bar_carry():
    prices = _prices([100.0, np.nan, 121.0])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=prices.index)
    vec = BacktestEngine(**_FRICTIONLESS).run(w, prices, _uni(["X"]))
    evt = EventDrivenEngine(**_FRICTIONLESS).run(w, prices, _uni(["X"]))
    assert not evt.equity_curve.isna().any()
    assert evt.positions["X"].iloc[1] == evt.positions["X"].iloc[0]      # carried across the gap
    assert np.allclose(vec.equity_curve.values, evt.equity_curve.values, atol=1e-9)


# ── accounting / lag / cost re-pinned on the event engine ─────────────────────────
def test_accounting_identity_and_lag():
    prices = _prices([100.0, 110.0, 121.0, 133.1])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 1.0]}, index=prices.index)
    r = EventDrivenEngine(**_FRICTIONLESS).run(w, prices, _uni(["X"]))
    assert r.returns.iloc[1] == pytest.approx(0.0, abs=1e-9)             # one-bar lag: no day-1 gain
    assert r.returns.iloc[2] == pytest.approx(0.10, abs=1e-9)
    assert r.equity_curve.iloc[-1] == pytest.approx(12_100.0, rel=1e-9)
    assert np.allclose((r.cash + r.positions["X"] * prices["X"]).values, r.equity_curve.values)


def test_costs_and_short_borrow_drag():
    prices = _prices([100.0] * 8)
    short = pd.DataFrame({"X": [-1.0] * 8}, index=prices.index)
    r = EventDrivenEngine(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
        financing=FinancingModel(short_borrow_bps=100.0, leverage_bps=0.0, cash_bps=0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=True), initial_equity=10_000.0,
    ).run(short, prices, _uni(["X"]))
    assert r.equity_curve.iloc[-1] < 10_000.0 and r.financing.sum() > 0   # borrow bleed


def test_whole_lot_rounding():
    prices = _prices([100.0, 100.0, 100.0])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=prices.index)
    r = EventDrivenEngine(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0), financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=False), initial_equity=250.0,
    ).run(w, prices, _uni(["X"], lot=1.0))
    held = r.positions["X"].iloc[-1]
    assert held == pytest.approx(round(held))                            # whole units only


# ── native push strategy ──────────────────────────────────────────────────────────
class EqualLongEventStrategy(EventStrategy):
    """Go equal-weight long every instrument that printed a bar this timestamp."""

    def on_market(self, event, book):
        ids = list(event.prices)
        if not ids:
            return []
        return [SignalEvent(timestamp=event.timestamp, targets={c: 1.0 / len(ids) for c in ids})]


def test_native_event_strategy_runs():
    rng = np.random.default_rng(1)
    n, t = 3, 60
    idx = pd.date_range("2023-01-01", periods=t, freq="B", tz="UTC")
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.0005, 0.01, (t, n)), axis=0),
                      index=idx, columns=[f"A{i}" for i in range(n)])
    r = EventDrivenEngine(**_FRICTIONLESS).run_strategy(EqualLongEventStrategy(), px, _uni(px.columns))
    assert len(r.equity_curve) == t and np.isfinite(r.equity_curve.to_numpy()).all()
    assert (r.gross_exposure.iloc[2:] > 0).any()                         # actually took positions


# ── pluggable execution handler (the framework seam) ──────────────────────────────
class FreeExecutionHandler:
    """A drop-in ExecutionHandler: fills at the reference price, zero commission/slippage."""

    def execute(self, order, instrument):
        from qhfi.backtest.eventdriven.events import FillEvent
        return FillEvent(timestamp=order.timestamp, instrument=order.instrument,
                         delta_units=order.delta_units, fill_price=order.ref_price,
                         ref_price=order.ref_price, commission=0.0, slippage=0.0,
                         margined=instrument.is_margined)


def test_custom_execution_handler_is_pluggable():
    prices = _prices([100.0, 110.0, 121.0, 133.1])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0, 1.0]}, index=prices.index)
    uni = _uni(["X"])
    instruments = {c: uni.by_id(c) for c in prices.columns}
    portfolio = Portfolio(instruments, execution=ExecutionConfig(signal_lag=1, allow_fractional=True),
                          financing=FinancingModel(0.0, 0.0, 0.0), initial_equity=10_000.0)
    loop = EventLoop(PanelDataHandler(prices), WeightStrategyAdapter(w), portfolio,
                     FreeExecutionHandler(), instruments)
    r = loop.run()
    assert r.commission.sum() == 0.0 and r.slippage.sum() == 0.0
    assert r.equity_curve.iloc[-1] == pytest.approx(12_100.0, rel=1e-9)  # frictionless result


# ── compatibility with the evaluation stack ───────────────────────────────────────
def test_result_feeds_scorecard_and_walk_forward():
    from qhfi.backtest.validation import WalkForwardConfig, concat_oos, walk_forward
    from qhfi.core.types import Panel, Universe as Uni
    from qhfi.evaluation.scorecard import Scorecard
    from qhfi.strategy.base import Strategy

    rng = np.random.default_rng(2)
    n, t = 4, 400
    idx = pd.date_range("2021-01-01", periods=t, freq="B", tz="UTC")
    px = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0, 0.01, (t, n)), axis=0),
                      index=idx, columns=[f"A{i}" for i in range(n)])
    uni = _uni(px.columns)

    class EqualWeight(Strategy):
        name = "eqw"
        def generate_weights(self, prices: Panel, universe: Uni):
            return pd.DataFrame(1.0 / prices.shape[1], index=prices.index, columns=prices.columns)

    engine = EventDrivenEngine()
    result = engine.run(EqualWeight().generate_weights(px, uni), px, uni)
    card = Scorecard().grade(result)                                     # consumes the result unchanged
    assert "sharpe" in card.metrics

    folds = walk_forward(EqualWeight(), px, uni, engine, WalkForwardConfig(
        train_days=150, test_days=60, step_days=60, purge_days=5))
    assert len(folds) > 0 and len(concat_oos(folds)) > 0                 # drop-in for walk_forward
