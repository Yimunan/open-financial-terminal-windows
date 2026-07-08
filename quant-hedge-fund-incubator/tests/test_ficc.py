"""Tests for the FICC extension: two-axis taxonomy, DV01 sizing, margin (variation-margin)
accounting, and carry-as-return. These pin the behaviors that distinguish FICC from the
cash-equity/crypto path.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.engine import BacktestEngine, ExecutionConfig
from qhfi.backtest.financing import FinancingModel
from qhfi.backtest.fills import SlippageModel
from qhfi.core.types import AssetClass, Instrument, InstrumentForm, RiskBasis, Universe
from qhfi.portfolio.sizing import DV01Sizing, SizingConfig


def _prices(vals):
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="D", tz="UTC")
    return pd.DataFrame({"X": vals}, index=idx)


def _engine(**kw):
    base = dict(
        cost_model=BpsCostModel(0.0), slippage=SlippageModel(0.0),
        financing=FinancingModel(0.0, 0.0, 0.0),
        execution=ExecutionConfig(signal_lag=1, allow_fractional=True),
        initial_equity=100_000.0,
    )
    base.update(kw)
    return BacktestEngine(**base)


# ── taxonomy ──────────────────────────────────────────────────────────────────
def test_two_axis_taxonomy_derives_funding_and_risk_basis():
    spot_btc = Instrument(id="BTC/USDT", asset_class=AssetClass.CRYPTO, form=InstrumentForm.CASH)
    perp = Instrument(id="BTC-PERP", asset_class=AssetClass.CRYPTO, form=InstrumentForm.PERP)
    note = Instrument(id="ZN", asset_class=AssetClass.RATES, form=InstrumentForm.FUTURE,
                      modified_duration=6.5, contract_multiplier=1000.0)
    eurusd = Instrument(id="EUR/USD", asset_class=AssetClass.FX, form=InstrumentForm.CASH)

    assert not spot_btc.is_margined and spot_btc.risk_basis is RiskBasis.NOTIONAL
    assert perp.is_margined                                   # perp form → margined
    assert note.is_margined and note.risk_basis is RiskBasis.DV01   # rates → DV01
    assert eurusd.risk_basis is RiskBasis.NOTIONAL and eurusd.calendar_name == "24/5"


def test_funding_override_beats_form_default():
    fully_funded_future = Instrument(id="ESz", asset_class=AssetClass.EQUITY,
                                     form=InstrumentForm.FUTURE, funding="cash")
    assert not fully_funded_future.is_margined


# ── DV01 sizing ─────────────────────────────────────────────────────────────
def test_dv01_sizing_hits_the_risk_budget():
    note = Instrument(id="ZN", asset_class=AssetClass.RATES, form=InstrumentForm.FUTURE,
                      modified_duration=6.5, contract_multiplier=1000.0)
    cfg = SizingConfig(dv01_budget_per_equity=0.0005)
    equity, price = 100_000.0, 110.0
    units = DV01Sizing(cfg).target_units(note, weight=1.0, equity=equity, price=price)

    dv01_per_unit = 6.5 * price * 1000.0 / 10_000.0
    realized_dv01 = units * dv01_per_unit
    assert realized_dv01 == pytest.approx(equity * cfg.dv01_budget_per_equity, rel=1e-9)


# ── margin accounting (variation margin, no notional debit) ─────────────────
def test_margined_future_uses_variation_margin_not_notional():
    # 1 weight on a future whose notional == equity. Cash-funded would zero out cash;
    # margined keeps cash ~intact and only accrues daily VM.
    prices = _prices([100.0, 100.0, 101.0])  # flat, then +1
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=prices.index)
    fut = Universe(name="t", instruments=[Instrument(
        id="X", asset_class=AssetClass.COMMODITY, form=InstrumentForm.FUTURE,
        contract_multiplier=50.0, lot_size=1e-9)])

    r = _engine().run(w, prices, fut)
    # entered at day-1 close (100): notional = 20 units? units = 100k/(100*50)=20 → notional 100k == equity
    assert r.cash.iloc[1] > 50_000.0                 # NOT spent down to ~0 like a cash buy
    assert r.gross_exposure.iloc[1] == pytest.approx(1.0, rel=1e-6)
    # +1 point on 20 contracts × 50 mult = +1000 → equity 101k
    assert r.equity_curve.iloc[2] == pytest.approx(101_000.0, rel=1e-6)


def test_cash_instrument_still_debits_full_notional():
    prices = _prices([100.0, 100.0, 101.0])
    w = pd.DataFrame({"X": [1.0, 1.0, 1.0]}, index=prices.index)
    spot = Universe(name="t", instruments=[Instrument(
        id="X", asset_class=AssetClass.CRYPTO, form=InstrumentForm.CASH, contract_multiplier=1.0)])
    r = _engine().run(w, prices, spot)
    assert r.cash.iloc[1] == pytest.approx(0.0, abs=1e-6)   # full notional spent
    assert r.equity_curve.iloc[2] == pytest.approx(101_000.0, rel=1e-6)


# ── carry as a return component ─────────────────────────────────────────────
def test_positive_carry_grows_a_flat_book():
    prices = _prices([100.0] * 6)                          # price flat
    w = pd.DataFrame({"X": [1.0] * 6}, index=prices.index)
    carry = pd.DataFrame({"X": [0.0001] * 6}, index=prices.index)  # 1bp/day income
    fx = Universe(name="t", instruments=[Instrument(
        id="X", asset_class=AssetClass.FX, form=InstrumentForm.CASH,
        base_currency="EUR", contract_multiplier=1.0)])

    r = _engine().run(w, prices, fx, carry=carry)
    assert r.equity_curve.iloc[-1] > 100_000.0             # carry alone lifts equity
    assert r.carry.sum() > 0
    assert r.equity_curve.is_monotonic_increasing
