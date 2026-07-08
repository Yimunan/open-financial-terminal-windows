"""Smoke tests for the background data-refresh management (DataRefreshRunner + routes).

Offline by design: the engine — registry, config round-trip + SQLite persistence, due-scheduling,
serialized manual trigger + busy/error handling, active-symbol set, and the equity market-hours
gate — is driven directly against a fresh ``TerminalStore`` with the network-bound refresh fns
stubbed. One TestClient pass exercises the real ``/api/settings/data-refresh`` status + config
routes (no ``/run`` — that would hit providers). Nothing touches the real ``oft.sqlite`` (a temp
store is monkeypatched in), so the user's saved config is left untouched.

Run: `cd backend && pytest tests/test_data_refresh.py -v`
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.data_refresh import MIN_INTERVAL_S, DataRefreshRunner
from app.store import TerminalStore

_JOBS = {"market_bars", "news", "rates", "macro", "filings"}


def _runner(monkeypatch, tmp_path) -> tuple[DataRefreshRunner, TerminalStore]:
    """A runner wired to a throwaway store (so config writes never touch the real oft.sqlite)."""
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    monkeypatch.setattr("app.deps.get_store", lambda: store)
    return DataRefreshRunner(), store


# ── registry + status shape ──────────────────────────────────────────────────────
def test_registry_and_status_shape(monkeypatch, tmp_path):
    r, _ = _runner(monkeypatch, tmp_path)
    assert set(r._jobs) == _JOBS
    st = r.status()
    assert st["master_enabled"] is True
    assert st["market_hours_only"] is True
    assert st["jobs"]["filings"]["enabled"] is False      # EDGAR is opt-in
    assert st["jobs"]["market_bars"]["enabled"] is True
    for job in st["jobs"].values():
        assert {"label", "enabled", "interval_minutes", "last_run", "next_run", "running"} <= job.keys()


# ── config round-trip + persistence + clamping ───────────────────────────────────
def test_config_round_trip_and_persistence(monkeypatch, tmp_path):
    r, store = _runner(monkeypatch, tmp_path)
    r.set_enabled("news", False)
    r.set_interval_s("market_bars", 1200)
    r.set_master_enabled(False)
    r.set_market_hours_only(False)

    assert r._enabled("news") is False
    assert r._interval_s("market_bars") == 1200
    assert r._master_enabled() is False
    assert r._market_hours_only() is False

    # a fresh runner reading the same store sees the persisted overrides (survives a restart)
    r2 = DataRefreshRunner()
    assert r2._enabled("news") is False
    assert r2._interval_s("market_bars") == 1200
    assert r2._master_enabled() is False

    # interval is floored to the minimum, and an unknown job is rejected
    r.set_interval_s("rates", 5)
    assert r._interval_s("rates") == MIN_INTERVAL_S
    with pytest.raises(ValueError):
        r.set_enabled("ghost", True)


# ── due-scheduling ───────────────────────────────────────────────────────────────
def test_due_scheduling(monkeypatch, tmp_path):
    r, store = _runner(monkeypatch, tmp_path)
    now = datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc)
    assert r._due("rates", now) is True                                   # never run → due
    store.set_config("data_refresh:rates:last_run", now.isoformat())
    assert r._due("rates", now + timedelta(seconds=120)) is False         # within the daily interval
    assert r._due("rates", now + timedelta(days=2)) is True               # past the interval → due


# ── manual trigger: runs, records, persists; error + busy handling ───────────────
def test_trigger_runs_records_and_persists(monkeypatch, tmp_path):
    r, store = _runner(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(r._jobs["rates"], "fn", lambda: (calls.append(1), {"rows": 7})[1])

    out = r.trigger_now("rates")

    assert out["status"] == "ok" and out["rows"] == 7
    assert calls == [1]
    assert store.get_config("data_refresh:rates:last_run") is not None
    assert r.status()["jobs"]["rates"]["last_result"]["rows"] == 7


def test_trigger_busy_guard(monkeypatch, tmp_path):
    r, _ = _runner(monkeypatch, tmp_path)
    r._running.add("macro")                                               # simulate a pass in flight
    assert r.trigger_now("macro")["status"] == "busy"


def test_trigger_error_is_isolated(monkeypatch, tmp_path):
    r, store = _runner(monkeypatch, tmp_path)

    def boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr(r._jobs["macro"], "fn", boom)
    out = r.trigger_now("macro")

    assert out["status"] == "error" and "provider down" in out["error"]
    # last_run still advances so a failing job doesn't hot-loop every tick
    assert store.get_config("data_refresh:macro:last_run") is not None
    # the in-flight guard is released even after a crash
    assert "macro" not in r._running


# ── active symbol set (watchlist ∪ holdings, grouped by asset class incl. fx/rates) ──
def test_active_set_groups_all_asset_classes(monkeypatch, tmp_path):
    r, store = _runner(monkeypatch, tmp_path)
    store.add_watch("AAPL", "equity")
    store.add_watch("BTC/USDT", "crypto")
    store.add_watch("EUR/USD", "fx")
    store.add_watch("ZN", "rates")
    store.add_watch("GC", "commodity")
    store.upsert_holding("MSFT", "equity", 10.0, 100.0)

    by = r._active_by_asset()
    assert sorted(s for s, _a, _i in by["equity"]) == ["AAPL", "MSFT"]
    assert [s for s, _a, _i in by["crypto"]] == ["BTC/USDT"]
    assert [s for s, _a, _i in by["fx"]] == ["EUR/USD"]
    assert [s for s, _a, _i in by["rates"]] == ["ZN"]
    assert [s for s, _a, _i in by["commodity"]] == ["GC"]

    st = r.status()
    assert st["active_by_asset"] == {"equity": 2, "crypto": 1, "fx": 1, "rates": 1, "commodity": 1}
    assert st["active_equities"] == 2 and st["active_crypto"] == 1   # legacy mirrors


# ── the news job follows the News + News Topics settings ─────────────────────────
def test_news_job_warms_symbols_and_configured_topics(monkeypatch, tmp_path):
    r, store = _runner(monkeypatch, tmp_path)
    store.add_watch("AAPL", "equity")
    store.add_watch("BTC/USDT", "crypto")          # crypto is skipped (news is equity-oriented)

    from app.services import news_router

    sym_calls: list[str] = []
    topic_calls: list[str] = []
    monkeypatch.setattr(news_router, "news", lambda s, limit=None: sym_calls.append(s) or [])
    monkeypatch.setattr(news_router, "topic_news", lambda c, limit=None: topic_calls.append(c) or [])
    # built-ins + a user keyword topic that appears twice (multi-label) → warmed once
    monkeypatch.setattr(news_router, "available_topics", lambda: [
        {"key": "market", "label": "Market", "builtin": True},
        {"key": "macro", "label": "Macro", "builtin": True},
        {"key": "tech", "label": "Tech", "builtin": False},
        {"key": "tech", "label": "AI", "builtin": False},
    ])

    out = r._refresh_news()

    assert sym_calls == ["AAPL"]                     # per-symbol news, equities only
    assert topic_calls == ["market", "macro", "tech"]  # follows News Topics config, deduped by key
    assert out == {"symbols": 1, "warmed_symbols": 1, "warmed_topics": 3}


# ── equity market-hours gate ─────────────────────────────────────────────────────
def test_equity_market_hours_gate(monkeypatch, tmp_path):
    r, _ = _runner(monkeypatch, tmp_path)
    mon_open = datetime(2024, 6, 3, 15, 0, tzinfo=timezone.utc)   # Mon 11:00 ET → open
    mon_evening = datetime(2024, 6, 3, 23, 0, tzinfo=timezone.utc)  # Mon 19:00 ET → closed
    sunday = datetime(2024, 6, 2, 15, 0, tzinfo=timezone.utc)     # weekend

    assert r._equity_gate_open(mon_open) is True
    assert r._equity_gate_open(mon_evening) is False
    assert r._equity_gate_open(sunday) is False

    r.set_market_hours_only(False)                               # gate off → always open
    assert r._equity_gate_open(mon_evening) is True
    assert r._equity_gate_open(sunday) is True


def test_bars_gate_per_asset(monkeypatch, tmp_path):
    r, _ = _runner(monkeypatch, tmp_path)
    mon_evening = datetime(2024, 6, 3, 23, 0, tzinfo=timezone.utc)  # Mon 19:00 ET
    sunday = datetime(2024, 6, 2, 15, 0, tzinfo=timezone.utc)

    # crypto is 24/7; equity gated to the US session; fx/rates trade ~24h on weekdays
    assert r._bars_gate_open("crypto", sunday) is True
    assert r._bars_gate_open("equity", mon_evening) is False       # outside 09:00-17:30 ET
    assert r._bars_gate_open("fx", mon_evening) is True            # weekday → open
    assert r._bars_gate_open("rates", mon_evening) is True
    assert r._bars_gate_open("commodity", mon_evening) is True
    assert r._bars_gate_open("fx", sunday) is False               # weekend → closed
    assert r._bars_gate_open("rates", sunday) is False
    assert r._bars_gate_open("commodity", sunday) is False

    r.set_market_hours_only(False)                                 # gate off → everything open
    assert r._bars_gate_open("equity", mon_evening) is True
    assert r._bars_gate_open("fx", sunday) is True


# ── HTTP routes (status + config) through the real FastAPI app ───────────────────
def test_routes_status_and_config(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app import deps
    from app.main import app

    store = TerminalStore(tmp_path / "route.db")
    store.init()
    monkeypatch.setattr("app.deps.get_store", lambda: store)
    deps.get_data_refresh_runner.cache_clear()                  # fresh singleton on the temp store

    client = TestClient(app)  # no `with` → lifespan/background loop does not start (no network)

    st = client.get("/api/settings/data-refresh/status")
    assert st.status_code == 200
    body = st.json()
    assert set(body["jobs"]) == _JOBS
    assert body["jobs"]["filings"]["enabled"] is False

    put = client.put(
        "/api/settings/data-refresh/config",
        json={"market_hours_only": False, "jobs": {"news": {"enabled": False, "interval_minutes": 20}}},
    )
    assert put.status_code == 200
    j = put.json()
    assert j["jobs"]["news"]["enabled"] is False
    assert j["jobs"]["news"]["interval_minutes"] == 20
    assert j["market_hours_only"] is False
    assert store.get_config("data_refresh:news:enabled") == "0"   # persisted

    bad = client.put("/api/settings/data-refresh/config", json={"jobs": {"ghost": {"enabled": True}}})
    assert bad.status_code == 400

    deps.get_data_refresh_runner.cache_clear()                  # don't leak the temp-store runner
