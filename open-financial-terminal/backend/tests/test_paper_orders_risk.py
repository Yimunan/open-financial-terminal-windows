"""Tests for paper-trading Phase 3: stop/stop-limit/trailing-stop orders, buying-power blocks,
pre-trade risk-gate preview, and close/flatten quick actions.

Resting-order triggers are processed inside ``_fill_pending`` (called by ``list_orders``), so each
test moves the stubbed price then calls ``broker.list_orders()`` to drive the engine.

Run: `cd backend && pytest tests/test_paper_orders_risk.py -v`
"""

from __future__ import annotations

import pytest
from qhfi.execution.base import Order, OrderSide

from app.routers import paper as paper_router
from app.services.broker import SimBroker
from app.store import TerminalStore


def make_broker(tmp_path, cash=100_000.0, prices=None, name="term.db"):
    store = TerminalStore(tmp_path / name)
    store.init()
    b = SimBroker(store, dm=None, initial_cash=cash, commission_bps=0.0, slippage_bps=0.0)
    b._prices = dict(prices or {"AAPL": 100.0})
    b.last_price = lambda symbol, asset="equity": b._prices.get(symbol)  # type: ignore[method-assign]
    return b


def _order(sym, side, qty, otype="market", limit=None):
    return Order(instrument_id=sym, side=OrderSide(side), quantity=qty, type=otype, limit_price=limit)


def _pos(b, sym):
    return next((p for p in b.store.paper_positions() if p["symbol"] == sym), None)


def _latest(b):
    return b.store.list_paper_orders()[0]


# ── stop orders ─────────────────────────────────────────────────────────────────
def test_sell_stop_loss_triggers_on_drop(tmp_path):
    b = make_broker(tmp_path)
    b.submit(_order("AAPL", "buy", 10))                              # long 10 @ 100
    oid = b.submit(_order("AAPL", "sell", 10, "stop"), stop_price=95.0)
    assert b.store.list_paper_orders()[0]["status"] == "open"        # rests above the stop
    b._prices["AAPL"] = 94.0
    b.list_orders()                                                  # drives _fill_pending
    filled = next(o for o in b.store.list_paper_orders() if o["id"] == int(oid))
    assert filled["status"] == "filled" and filled["fill_price"] == pytest.approx(94.0)
    assert _pos(b, "AAPL") is None                                   # position closed


def test_buy_stop_triggers_on_rise(tmp_path):
    b = make_broker(tmp_path)
    b.submit(_order("AAPL", "buy", 5, "stop"), stop_price=105.0)     # breakout buy stop, rests
    assert _latest(b)["status"] == "open"
    b._prices["AAPL"] = 106.0
    b.list_orders()
    assert _pos(b, "AAPL")["quantity"] == 5 and _latest(b)["status"] == "filled"


def test_stop_already_through_fills_immediately(tmp_path):
    b = make_broker(tmp_path)
    b.submit(_order("AAPL", "buy", 10))
    # stop already breached at submit (px 100 <= stop 105 for a sell stop) → fills now
    b.submit(_order("AAPL", "sell", 10, "stop"), stop_price=105.0)
    assert _latest(b)["status"] == "filled" and _pos(b, "AAPL") is None


# ── stop-limit ──────────────────────────────────────────────────────────────────
def test_stop_limit_converts_to_resting_limit_when_not_marketable(tmp_path):
    b = make_broker(tmp_path)
    b.submit(_order("AAPL", "buy", 10))
    oid = int(b.submit(_order("AAPL", "sell", 10, "stop_limit", limit=95.5), stop_price=95.0))
    b._prices["AAPL"] = 94.5                                         # triggers (<=95) but < limit 95.5
    b.list_orders()
    o = next(x for x in b.store.list_paper_orders() if x["id"] == oid)
    assert o["status"] == "open" and o["type"] == "limit" and o["limit_price"] == pytest.approx(95.5)
    b._prices["AAPL"] = 96.0                                         # now marketable for the sell limit
    b.list_orders()
    o = next(x for x in b.store.list_paper_orders() if x["id"] == oid)
    assert o["status"] == "filled" and o["fill_price"] == pytest.approx(95.5)


# ── trailing stop ─────────────────────────────────────────────────────────────────
def test_trailing_stop_ratchets_up_then_triggers(tmp_path):
    b = make_broker(tmp_path)
    b.submit(_order("AAPL", "buy", 10))                              # long
    oid = int(b.submit(_order("AAPL", "sell", 10, "trailing_stop"), trail_pct=5.0))  # stop 95
    b._prices["AAPL"] = 110.0
    b.list_orders()                                                 # ratchets: hwm 110, stop 104.5
    o = next(x for x in b.store.list_paper_orders() if x["id"] == oid)
    assert o["status"] == "open" and o["stop_price"] == pytest.approx(104.5)
    b._prices["AAPL"] = 104.0                                        # pulls back through the trailed stop
    b.list_orders()
    o = next(x for x in b.store.list_paper_orders() if x["id"] == oid)
    assert o["status"] == "filled" and _pos(b, "AAPL") is None


def test_trailing_stop_requires_positive_trail(tmp_path):
    b = make_broker(tmp_path)
    with pytest.raises(ValueError, match="trail_pct"):
        b.submit(_order("AAPL", "sell", 10, "trailing_stop"), trail_pct=0.0)


# ── buying power ──────────────────────────────────────────────────────────────────
def test_market_buy_blocked_over_buying_power(tmp_path):
    b = make_broker(tmp_path, cash=1_000.0)
    with pytest.raises(ValueError, match="insufficient buying power"):
        b.submit(_order("AAPL", "buy", 20))                         # 20*100 = 2000 > 1000
    assert _pos(b, "AAPL") is None                                  # nothing filled


def test_sell_not_blocked_by_buying_power(tmp_path):
    b = make_broker(tmp_path, cash=1_000.0)
    b.submit(_order("AAPL", "sell", 5))                             # opening a short is allowed
    assert _pos(b, "AAPL")["quantity"] == -5


# ── close / flatten ───────────────────────────────────────────────────────────────
def test_close_position_long_and_short(tmp_path):
    b = make_broker(tmp_path, prices={"AAPL": 100.0, "TSLA": 200.0})
    b.submit(_order("AAPL", "buy", 10))
    b.submit(_order("TSLA", "sell", 4))                             # short
    assert b.close_position("AAPL") is not None and _pos(b, "AAPL") is None
    assert b.close_position("TSLA") is not None and _pos(b, "TSLA") is None
    assert b.close_position("AAPL") is None                         # nothing left → no order


def test_flatten_all_closes_everything(tmp_path):
    b = make_broker(tmp_path, prices={"AAPL": 100.0, "MSFT": 50.0})
    b.submit(_order("AAPL", "buy", 10))
    b.submit(_order("MSFT", "buy", 20))
    ids = b.flatten_all()
    assert len(ids) == 2 and b.store.paper_positions() == []


# ── pre-trade preview (buying power + risk gate) ──────────────────────────────────
def test_preview_flags_buying_power_and_concentration(tmp_path):
    b = make_broker(tmp_path, cash=100_000.0)
    # 30k notional into one name on a 100k book → 30% weight > 20% max_position gate
    body = paper_router.OrderIn(symbol="AAPL", asset="equity", side="buy", quantity=300, type="market")
    out = paper_router.preview(body, broker=b)
    assert out["applicable"] and out["buying_power_ok"] is True       # 30k < 100k cash
    assert any("max_position" in w for w in out["warnings"])

    small = make_broker(tmp_path, cash=1_000.0, name="small.db")     # separate store/account
    body2 = paper_router.OrderIn(symbol="AAPL", side="buy", quantity=20, type="market")
    out2 = paper_router.preview(body2, broker=small)
    assert out2["buying_power_ok"] is False and out2["est_cost"] == pytest.approx(2_000.0)
