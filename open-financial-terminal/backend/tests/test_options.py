"""Tests for the options subsystem — Black-Scholes greeks/IV, OCC symbology, the OptionsSource seam,
the chain service's greeks enrichment, and the options config round-trip (isolated from OHLCV).

Run: `cd backend && pytest tests/test_options.py -v`
"""

from __future__ import annotations

import pytest

from app import config
from app.services import greeks as gk
from app.services import options as opt


# ── Black-Scholes greeks / IV ──────────────────────────────────────────────────────
def test_bs_known_value_and_parity():
    # ATM 1Y, S=K=100, r=0, sigma=0.2 → textbook call ≈ 7.97, delta ≈ 0.54
    call = gk.bs_price(100, 100, 1.0, 0.0, 0.2, "call")
    put = gk.bs_price(100, 100, 1.0, 0.0, 0.2, "put")
    assert call == pytest.approx(7.9656, abs=0.01)
    assert gk.bs_greeks(100, 100, 1.0, 0.0, 0.2, "call")["delta"] == pytest.approx(0.5398, abs=0.005)
    # put-call parity at r=0: C - P == S - K == 0
    assert (call - put) == pytest.approx(0.0, abs=1e-4)


def test_implied_vol_round_trip():
    price = gk.bs_price(100, 110, 0.5, 0.03, 0.35, "put")
    iv = gk.implied_vol(price, 100, 110, 0.5, 0.03, "put")
    assert iv == pytest.approx(0.35, abs=1e-3)


def test_greeks_degenerate_inputs_none():
    g = gk.bs_greeks(100, 100, 0.0, 0.0, 0.2, "call")   # T=0
    assert all(v is None for v in g.values())
    g2 = gk.bs_greeks(100, 100, 1.0, 0.0, 0.0, "call")  # sigma=0
    assert all(v is None for v in g2.values())


# ── OCC symbology + date helpers ────────────────────────────────────────────────────
def test_occ_symbol():
    assert opt.occ_symbol("AAPL", "2026-07-17", "call", 190) == "AAPL260717C00190000"
    assert opt.occ_symbol("SPY", "2026-12-18", "put", 500.5) == "SPY261218P00500500"


def test_is_monthly_and_dte():
    assert opt.is_monthly("2026-07-17") is True     # 3rd Friday
    assert opt.is_monthly("2026-07-10") is False    # weekly
    assert opt.days_to_expiry("1990-01-01") == 0    # past → clamped to 0


# ── OptionsSource seam ──────────────────────────────────────────────────────────────
def test_build_options_source():
    assert isinstance(opt.build_options_source("yfinance"), opt.YFinanceOptionsSource)
    assert opt.build_options_source("none") is None
    assert opt.build_options_source("bogus") is None
    # vendors build but are gated off without creds (never crash)
    assert opt.build_options_source("tradier").enabled() is False
    assert opt.build_options_source("polygon").enabled() is False


# ── chain service greeks enrichment (no network — fake source + stubbed spot) ─────────
class _FakeSource:
    def enabled(self):
        return True

    def capabilities(self):
        return {"chains": True, "iv": True, "greeks": False, "realtime": False}

    def expirations(self, underlying):
        return ["2026-07-17"]

    def chain(self, underlying, expiry):
        return [
            opt.OptionQuote(strike=100.0, right="call", bid=7.5, ask=8.0, last=7.8, volume=10,
                            open_interest=5, iv=0.2, delta=None, gamma=None, theta=None, vega=None,
                            rho=None, in_the_money=None, contract_symbol=None),
            opt.OptionQuote(strike=100.0, right="put", bid=7.4, ask=7.9, last=7.6, volume=8,
                            open_interest=3, iv=None, delta=None, gamma=None, theta=None, vega=None,
                            rho=None, in_the_money=None, contract_symbol=None),
        ]


def test_chain_enriches_greeks(monkeypatch):
    monkeypatch.setattr(opt, "build_options_source", lambda s: _FakeSource())
    monkeypatch.setattr(config, "get_options_source", lambda: "fake")
    monkeypatch.setattr(config, "get_options_caps", lambda s=None: _FakeSource().capabilities())
    monkeypatch.setattr(config, "get_options_greeks_mode", lambda: "auto")
    monkeypatch.setattr("app.services.depth.latest_mid", lambda asset, sym: 100.0)
    monkeypatch.setattr(gk, "risk_free_rate", lambda: 0.03)
    opt.clear_options_cache()
    data = opt.chain("AAPL", "2026-07-17")
    assert data["spot"] == 100.0 and data["greeks_computed"] is True and data["atm_strike"] == 100.0
    call = data["calls"][0]
    assert call["delta"] is not None and call["gamma"] is not None    # greeks filled from source IV
    put = data["puts"][0]
    assert put["iv"] is not None                                      # IV solved from the put mid
    opt.clear_options_cache()


# ── config round-trip (isolated from OHLCV categories) ───────────────────────────────
@pytest.fixture()
def clean_override():
    path = config._market_data_path()
    backup = path.read_bytes() if path.exists() else None
    path.unlink(missing_ok=True)
    try:
        yield
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)


def test_options_config_round_trip_and_isolation(clean_override):
    cats = config.get_market_data_config()["categories"]
    assert cats["options"]["source"] == "yfinance"            # default
    assert "options" not in config.MARKET_DATA_CATEGORIES     # NOT an OHLCV category
    config.set_market_data_config(categories={"options": {
        "source": "tradier", "default_underlying": "tsla", "expiry_window": 9999, "greeks": "passthrough",
    }})
    assert config.get_options_source() == "tradier"
    assert config.get_options_default_underlying() == "TSLA"
    assert config.get_options_expiry_window() == 365          # clamped 9999 → 365
    # invalid source rejected → keeps the last good value
    config.set_market_data_config(categories={"options": {"source": "bogus"}})
    assert config.get_options_source() == "tradier"
