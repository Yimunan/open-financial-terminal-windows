"""Auto market-data source selection (services/autopick + config 'auto' resolution). Offline —
every vendor probe is monkeypatched; nothing here touches a network or the databento SDK."""

from __future__ import annotations

import pytest

from app import config
from app.services import autopick


@pytest.fixture(autouse=True)
def _fresh_cache():
    autopick.invalidate()
    yield
    autopick.invalidate()


# ── resolve_depth_source ranking ─────────────────────────────────────────────────────
def test_ranking_picks_first_available(monkeypatch):
    monkeypatch.setattr(autopick, "_depth_candidate_ok", lambda s, a: s in ("databento", "dxfeed"))
    assert autopick.resolve_depth_source("equity") == "databento"  # ibkr down → next in rank


def test_ranking_prefers_ibkr_when_up(monkeypatch):
    monkeypatch.setattr(autopick, "_depth_candidate_ok", lambda s, a: True)
    assert autopick.resolve_depth_source("rates") == "ibkr"


def test_falls_back_to_sim_when_no_vendor(monkeypatch):
    monkeypatch.setattr(autopick, "_depth_candidate_ok", lambda s, a: False)
    assert autopick.resolve_depth_source("fx") == "sim"


def test_crashing_candidate_is_skipped_not_fatal(monkeypatch):
    def boom(source, asset):
        if source == "ibkr":
            raise RuntimeError("vendor sdk exploded")
        return source == "dxfeed"

    monkeypatch.setattr(autopick, "_depth_candidate_ok", boom)
    assert autopick.resolve_depth_source("commodity") == "dxfeed"


def test_crypto_follows_realtime_toggle(monkeypatch):
    monkeypatch.setattr(config, "get_crypto_realtime_enabled", lambda: True)
    assert autopick.resolve_depth_source("crypto") == "exchange"
    monkeypatch.setattr(config, "get_crypto_realtime_enabled", lambda: False)
    assert autopick.resolve_depth_source("crypto") == "sim"


# ── probe cache ──────────────────────────────────────────────────────────────────────
def test_probe_cached_until_invalidate():
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return True

    assert autopick._cached("k", "ibkr", probe) is True
    assert autopick._cached("k", "ibkr", probe) is True
    assert calls["n"] == 1                        # second read served from cache
    autopick.invalidate()
    assert autopick._cached("k", "ibkr", probe) is True
    assert calls["n"] == 2                        # invalidate → re-probed


def test_crashing_probe_counts_as_unavailable():
    def probe():
        raise OSError("gateway on fire")

    assert autopick._cached("k2", "databento", probe) is False


# ── config-level 'auto' resolution ───────────────────────────────────────────────────
def test_depth_resolved_passthrough_when_pinned(monkeypatch):
    monkeypatch.setattr(config, "get_depth_source", lambda a="equity": "dxfeed")
    assert config.get_depth_source_resolved("equity") == "dxfeed"


def test_depth_resolved_and_token_for_auto(monkeypatch):
    monkeypatch.setattr(config, "get_depth_source", lambda a="equity": "auto")
    monkeypatch.setattr(autopick, "resolve_depth_source", lambda a: "sim")
    assert config.get_depth_source_resolved("rates") == "sim"
    assert config.get_depth_topic_token("rates") == "sim.rates"
    assert config.get_depth_enabled("rates") is True


def test_auto_crypto_token_is_exchange_id(monkeypatch):
    monkeypatch.setattr(config, "get_depth_source", lambda a="equity": "auto")
    monkeypatch.setattr(autopick, "resolve_depth_source", lambda a: "exchange")
    monkeypatch.setattr(config, "get_crypto_exchange", lambda: "kraken")
    monkeypatch.setattr(config, "get_crypto_realtime_enabled", lambda: True)
    assert config.get_depth_topic_token("crypto") == "kraken"
    assert config.get_depth_enabled("crypto") is True


def test_equity_realtime_auto_resolves_on_creds(monkeypatch):
    monkeypatch.setattr(config, "get_realtime_source", lambda a="equity": "auto")
    monkeypatch.setattr(config, "get_alpaca_creds", lambda: ("KEY", "SECRET", True))
    assert config.get_equity_realtime_resolved() == "alpaca"
    assert config.equity_realtime_enabled() is True
    monkeypatch.setattr(config, "get_alpaca_creds", lambda: ("", "", True))
    assert config.get_equity_realtime_resolved() == "none"
    assert config.equity_realtime_enabled() is False


def test_equity_tape_follows_auto_resolution(monkeypatch):
    # The tape must share equity_stream's gate: configured 'auto' + creds → tape ON (the original
    # port missed get_trades_enabled, silently killing Time & Sales under the 'auto' default).
    monkeypatch.setattr(config, "get_realtime_source", lambda a="equity": "auto")
    monkeypatch.setattr(config, "get_alpaca_creds", lambda: ("K", "S", True))
    assert config.get_trades_enabled("equity") is True
    assert config.get_trades_topic_token("equity") == "alpaca"
    monkeypatch.setattr(config, "get_alpaca_creds", lambda: ("", "", True))
    assert config.get_trades_enabled("equity") is False


def test_depth_status_is_single_resolution(monkeypatch):
    # depth_status must resolve 'auto' exactly once — three independent resolutions could pair
    # one source with another's token across a probe-cache expiry.
    calls = {"n": 0}

    def fake_resolve(asset):
        calls["n"] += 1
        return "sim"

    monkeypatch.setattr(config, "get_depth_source", lambda a="equity": "auto")
    monkeypatch.setattr(autopick, "resolve_depth_source", fake_resolve)
    st = config.depth_status("rates")
    assert st == {"source": "sim", "configured": "auto", "token": "sim.rates", "enabled": True}
    assert calls["n"] == 1


def test_equity_realtime_pinned_none_stays_off(monkeypatch):
    # Explicit Off must win even with creds saved — auto is opt-out-able.
    monkeypatch.setattr(config, "get_realtime_source", lambda a="equity": "none")
    monkeypatch.setattr(config, "get_alpaca_creds", lambda: ("KEY", "SECRET", True))
    assert config.equity_realtime_enabled() is False


def test_auto_tokens_are_selectable():
    assert "auto" in config.DEPTH_SOURCES
    assert "auto" in config.CRYPTO_DEPTH_SOURCES
    assert "auto" in config.EQUITY_REALTIME_SOURCES
