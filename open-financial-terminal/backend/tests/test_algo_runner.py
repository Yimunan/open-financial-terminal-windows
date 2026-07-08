"""AlgoRunner — the live signal → reconcile → paper-submit cycle.

Drives ``run_cycle`` end to end against a real local SimBroker (no network): the template path
computes a real StrategyLab signal from a synthetic uptrend, the xsection path is exercised
through the shared executor with a deterministic two-name book. Also covers the read-only
preview (no order) and the risk-gate rejection.

Run: `cd backend && pytest tests/test_algo_runner.py -v`
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.algo_runner import AlgoRunner
from app.services.broker import SimBroker
from app.store import TerminalStore


def _uptrend_frame(n: int = 60, px0: float = 50.0) -> pd.DataFrame:
    """A steadily rising daily OHLC frame so a fast/slow SMA cross is unambiguously long."""
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.linspace(px0, px0 * 2, n)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
         "volume": np.full(n, 1_000.0)},
        index=idx,
    )


def _wire(monkeypatch, tmp_path, *, prices=None):
    """Patch the deps singletons the runner reaches for to a fresh store + a stubbed SimBroker."""
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    broker = SimBroker(store, dm=None, initial_cash=100_000.0)
    px = dict(prices or {"AAPL": 100.0})
    broker.last_price = lambda symbol, asset="equity": px.get(symbol)  # type: ignore[method-assign]

    monkeypatch.setattr("app.deps.get_store", lambda: store)
    monkeypatch.setattr("app.deps.get_broker", lambda: broker)
    monkeypatch.setattr("app.deps.broker_kind", lambda: "sim")
    monkeypatch.setattr("app.deps.get_data_manager", lambda: None)
    # Deterministic bars for the template path (no yfinance call).
    monkeypatch.setattr("app.services.market.fetch_bars", lambda *a, **k: (None, _uptrend_frame()))
    return store, broker


def _template_algo(**over) -> dict:
    base = {
        "name": "AAPL SMA", "kind": "template", "symbol": "AAPL", "asset": "equity",
        "timeframe": "1d", "strategy": "sma_cross", "params": {"fast": 3, "slow": 5},
        "direction": "both", "size_pct": 0.5, "cadence": {"kind": "daily"},
        "risk": {}, "armed": True, "last_run": None,
    }
    base.update(over)
    return base


def test_run_cycle_template_submits_to_paper(monkeypatch, tmp_path):
    store, broker = _wire(monkeypatch, tmp_path)
    aid = "algo1"
    store.save_algo(aid, _template_algo())

    runner = AlgoRunner()
    summary = runner.run_cycle(aid)

    assert summary["status"] == "ok"
    assert summary["signal"]["signal"] == 1  # rising series → long
    # target 0.5 * 100k / $100 = 500 shares
    held = {p["symbol"]: p for p in store.paper_positions()}
    assert held["AAPL"]["quantity"] == pytest.approx(500.0)
    # the cycle is logged and scheduling state advanced
    runs = store.list_algo_runs(aid)
    assert len(runs) == 1 and runs[0]["status"] == "ok"
    assert store.get_algo(aid)["last_run"] is not None


def test_preview_does_not_submit(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    runner = AlgoRunner()
    out = runner.preview(_template_algo())

    assert out["status"] == "preview"
    assert out["orders"][0]["symbol"] == "AAPL" and out["orders"][0]["side"] == "buy"
    assert store.paper_positions() == []  # nothing traded


def test_risk_gate_blocks_oversized_weight(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    runner = AlgoRunner()
    # an explicit tight per-name cap rejects the 0.5-weight book before any order
    out = runner.preview(_template_algo(size_pct=0.5, risk={"max_position": 0.20}))

    assert out["status"] == "rejected" and "max_position" in out["reason"]
    assert store.paper_positions() == []


def test_daily_cadence_close_time_gate():
    from datetime import datetime, timezone

    runner = AlgoRunner()
    cad = {"kind": "daily", "at": "16:10", "tz": "America/New_York"}
    # 14:00 ET (19:00 UTC) — before the close gate → not due
    before = datetime(2024, 6, 3, 19, 0, tzinfo=timezone.utc)
    assert runner._due({"cadence": cad, "last_run": None}, before) is False
    # 16:30 ET (20:30 UTC) — after the gate, never run → due
    after = datetime(2024, 6, 3, 20, 30, tzinfo=timezone.utc)
    assert runner._due({"cadence": cad, "last_run": None}, after) is True
    # already ran earlier today (local) → not due again
    algo = {"cadence": cad, "last_run": datetime(2024, 6, 3, 20, 15, tzinfo=timezone.utc).isoformat()}
    assert runner._due(algo, after) is False


def test_run_cycle_xsection_through_shared_executor(monkeypatch, tmp_path):
    store, broker = _wire(monkeypatch, tmp_path, prices={"AAPL": 100.0, "MSFT": 50.0})
    from qhfi.core.types import AssetClass, Instrument

    # Bypass the network-bound factor/universe build; exercise the executor with a known book.
    plan = {
        "weights": {"AAPL": 0.10, "MSFT": -0.05},
        "prices": {"AAPL": 100.0, "MSFT": 50.0},
        "instruments": {
            "AAPL": Instrument(id="AAPL", asset_class=AssetClass.EQUITY),
            "MSFT": Instrument(id="MSFT", asset_class=AssetClass.EQUITY),
        },
        "assets": {"AAPL": "equity", "MSFT": "equity"},
        "signal": {"kind": "xsection", "n_names": 2},
    }
    monkeypatch.setattr(AlgoRunner, "_xsection_plan", lambda self, algo: plan)

    aid = "algo2"
    store.save_algo(aid, {"kind": "xsection", "armed": True, "cadence": {"kind": "daily"},
                          "risk": {}, "size_pct": 1.0, "last_run": None, "name": "x"})
    runner = AlgoRunner()
    summary = runner.run_cycle(aid)

    assert summary["status"] == "ok"
    held = {p["symbol"]: p for p in store.paper_positions()}
    assert held["AAPL"]["quantity"] == pytest.approx(100.0)   # 0.10*100k/100
    assert held["MSFT"]["quantity"] == pytest.approx(-100.0)  # -0.05*100k/50, short
