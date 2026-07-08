"""Tests for paper-trading Phase 2: exposure analytics + commission/slippage realism.

Exposure math is pure (``paper._exposure``); realism is exercised through a SimBroker whose price
source is stubbed, asserting market fills cross the spread and the config round-trips/clamps.

Run: `cd backend && pytest tests/test_paper_exposure_realism.py -v`
"""

from __future__ import annotations

import pytest
from qhfi.execution.base import Order, OrderSide

from app.routers import paper as paper_router
from app.services.broker import SimBroker
from app.store import TerminalStore


# ── exposure analytics ──────────────────────────────────────────────────────────
def _pos(symbol, qty, mv, asset="equity"):
    return {"symbol": symbol, "quantity": qty, "market_value": mv, "asset": asset}


def test_exposure_long_short_split_and_concentration():
    # long 60k (AAPL 40k + BTC 20k), short 40k (TSLA) → gross 100k, net 20k
    positions = [
        _pos("AAPL", 100, 40_000),
        _pos("BTC/USDT", 1, 20_000, asset="crypto"),
        _pos("TSLA", -50, -40_000),
    ]
    exp = paper_router._exposure(positions, equity=100_000.0)
    assert exp["gross"] == 100_000.0 and exp["net"] == 20_000.0
    assert exp["long"] == 60_000.0 and exp["short"] == 40_000.0
    assert exp["gross_pct"] == 100.0 and exp["net_pct"] == 20.0
    assert exp["long_count"] == 2 and exp["short_count"] == 1
    assert exp["largest_pct"] == 40.0  # AAPL & TSLA both 40k → 40% of gross
    # HHI = 0.4^2 + 0.2^2 + 0.4^2 = 0.36
    assert exp["concentration_hhi"] == pytest.approx(0.36)
    assert exp["by_asset"]["equity"]["market_value"] == 80_000.0  # |40k| + |-40k|
    assert exp["by_asset"]["crypto"]["pct"] == 20.0


def test_exposure_empty_book_is_zeroed():
    exp = paper_router._exposure([], equity=100_000.0)
    assert exp["gross"] == 0.0 and exp["concentration_hhi"] == 0.0 and exp["by_asset"] == {}


# ── commission + slippage realism ───────────────────────────────────────────────
@pytest.fixture()
def broker(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    b = SimBroker(store, dm=None, initial_cash=100_000.0, commission_bps=0.0, slippage_bps=50.0)  # 0.5%
    b._prices = {"AAPL": 100.0}
    b.last_price = lambda symbol, asset="equity": b._prices.get(symbol)  # type: ignore[method-assign]
    return b


def test_market_buy_crosses_up_sell_crosses_down(broker):
    broker.submit(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=10, type="market"))
    pos = {p["symbol"]: p for p in broker.store.paper_positions()}["AAPL"]
    assert pos["avg_price"] == pytest.approx(100.5)  # 100 * (1 + 0.005)
    last = broker.store.list_paper_orders()[0]
    assert last["fill_price"] == pytest.approx(100.5)

    broker._prices["AAPL"] = 200.0
    broker.submit(Order(instrument_id="AAPL", side=OrderSide.SELL, quantity=10, type="market"))
    sell = broker.store.list_paper_orders()[0]  # most recent first
    assert sell["fill_price"] == pytest.approx(199.0)  # 200 * (1 - 0.005)


def test_limit_fills_ignore_slippage(broker):
    # a marketable limit fills AT the limit price, no extra slippage
    broker.submit(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=10, type="limit", limit_price=101.0))
    o = broker.store.list_paper_orders()[0]
    assert o["status"] == "filled" and o["fill_price"] == pytest.approx(101.0)


def test_commission_charged_per_fill(tmp_path):
    store = TerminalStore(tmp_path / "comm.db")
    store.init()
    b = SimBroker(store, dm=None, initial_cash=100_000.0, commission_bps=10.0, slippage_bps=0.0)  # 0.1%
    b._prices = {"AAPL": 100.0}
    b.last_price = lambda symbol, asset="equity": b._prices.get(symbol)  # type: ignore[method-assign]
    b.submit(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=10, type="market"))
    # cash = 100_000 - (10*100) - commission(10*100*0.001=1.0) = 98_999.0
    assert store.paper_cash() == pytest.approx(98_999.0)


# ── realism config round-trip + clamping ────────────────────────────────────────
def test_realism_config_roundtrip_and_clamp(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    assert paper_router._get_bps(store, "paper_commission_bps") == 0.0  # default
    assert paper_router._set_bps(store, "paper_commission_bps", 12.5) == 12.5
    assert paper_router._get_bps(store, "paper_commission_bps") == 12.5
    assert paper_router._set_bps(store, "paper_slippage_bps", 99_999) == paper_router._BPS_MAX  # clamp hi
    assert paper_router._set_bps(store, "paper_slippage_bps", -5) == 0.0  # clamp lo
