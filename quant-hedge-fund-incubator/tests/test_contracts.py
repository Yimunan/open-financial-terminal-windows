"""Contract-level tests that pass against the skeleton — they exercise the parts with real
bodies (types, metrics, lifecycle, scorecard math) and assert the design's invariants.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.evaluation import metrics
from qhfi.registry.models import LifecycleState, StrategyRecord


def test_instrument_calendar_mapping():
    btc = Instrument(id="BTC/USDT", asset_class=AssetClass.CRYPTO, exchange="binance")
    spy = Instrument(id="SPY", asset_class=AssetClass.EQUITY, exchange="XNYS")
    assert btc.calendar_name == "24/7"
    assert spy.calendar_name == "XNYS"


def test_universe_lookup():
    u = Universe(name="t", instruments=[Instrument(id="AAPL", asset_class=AssetClass.EQUITY, exchange="XNAS")])
    assert u.ids == ["AAPL"]
    assert u.by_id("AAPL").asset_class is AssetClass.EQUITY
    with pytest.raises(KeyError):
        u.by_id("MSFT")


def test_metrics_on_known_series():
    # flat positive drift → positive sharpe, zero drawdown
    r = pd.Series(np.full(252, 0.001))
    s = metrics.summary(r)
    assert s["sharpe"] > 0
    assert s["max_drawdown"] == 0.0


def test_lifecycle_legal_and_illegal_transitions():
    now = datetime(2026, 1, 1)
    rec = StrategyRecord(name="momentum")
    rec.advance(LifecycleState.IMPLEMENTED, artifact="code@v1", now=now)
    rec.advance(LifecycleState.BACKTESTED, artifact="bt-1", now=now)
    assert rec.state is LifecycleState.BACKTESTED
    # cannot skip straight to PAPER
    with pytest.raises(ValueError):
        rec.advance(LifecycleState.PAPER, artifact="x", now=now)


def test_live_is_blocked():
    now = datetime(2026, 1, 1)
    rec = StrategyRecord(name="m", state=LifecycleState.PAPER)
    with pytest.raises(PermissionError):
        rec.advance(LifecycleState.LIVE, artifact="x", now=now)
