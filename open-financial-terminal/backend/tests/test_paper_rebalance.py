"""Tests for paper-trading Phase 4: the Strategy → Paper bridge (target-weight rebalance).

Covers the plan math (weights → diff vs book → orders), gross scaling, closing untargeted names,
the advisory risk gate, and execute ordering (sells before buys, per-order error capture).

Run: `cd backend && pytest tests/test_paper_rebalance.py -v`
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
    b._prices = dict(prices or {"AAPL": 100.0, "MSFT": 50.0, "TSLA": 200.0})
    b.last_price = lambda symbol, asset="equity": b._prices.get(symbol)  # type: ignore[method-assign]
    return b


def _reb(b, weights, gross=None, execute=False, min_ticket=1.0):
    body = paper_router.RebalanceIn(weights=weights, asset="equity", gross=gross,
                                    execute=execute, min_ticket=min_ticket)
    return paper_router.rebalance(body, broker=b)


def test_plan_from_flat_book_buys_to_target(tmp_path):
    b = make_broker(tmp_path)                                    # 100k cash, flat
    plan = _reb(b, {"AAPL": 0.15, "MSFT": 0.15})                 # 15k each (within the 0.20 gate)
    orders = {o["symbol"]: o for o in plan["orders"]}
    assert orders["AAPL"]["side"] == "buy" and orders["AAPL"]["quantity"] == pytest.approx(150.0)   # 15k/100
    assert orders["MSFT"]["quantity"] == pytest.approx(300.0)    # 15k/50
    assert plan["executed"] is False and plan["gate"]["approved"] is True


def test_gross_scales_weights(tmp_path):
    b = make_broker(tmp_path)
    # raw weights sum to 2.0; gross=1.0 rescales to 0.5 each → 50k each
    plan = _reb(b, {"AAPL": 1.0, "MSFT": 1.0}, gross=1.0)
    aapl = next(o for o in plan["orders"] if o["symbol"] == "AAPL")
    assert aapl["target_weight"] == pytest.approx(0.5) and aapl["notional"] == pytest.approx(50_000.0)


def test_rebalance_closes_untargeted_holding(tmp_path):
    b = make_broker(tmp_path)
    b.submit(Order(instrument_id="TSLA", side=OrderSide.BUY, quantity=100, type="market"), "equity")  # 20k TSLA
    plan = _reb(b, {"AAPL": 0.5})                               # TSLA absent → should be sold to 0
    tsla = next(o for o in plan["orders"] if o["symbol"] == "TSLA")
    assert tsla["side"] == "sell" and tsla["quantity"] == pytest.approx(100.0) and tsla["target_weight"] == 0.0


def test_min_ticket_drops_tiny_trades(tmp_path):
    b = make_broker(tmp_path)
    b.submit(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=500, type="market"), "equity")  # 50k → wt .5
    plan = _reb(b, {"AAPL": 0.5})                               # already at target → no order
    assert all(o["symbol"] != "AAPL" for o in plan["orders"])


def test_gate_flags_overweight_target(tmp_path):
    b = make_broker(tmp_path)
    plan = _reb(b, {"AAPL": 0.9})                               # 0.9 > 0.20 max_position → gate fails (advisory)
    assert plan["gate"]["approved"] is False and "max_position" in plan["gate"]["reason"]
    assert plan["orders"]                                       # still proposes the order (non-blocking)


def test_execute_sells_before_buys_and_reaches_target(tmp_path):
    # start fully in TSLA, rebalance into AAPL+MSFT — the TSLA sell must fund the buys
    b = make_broker(tmp_path, cash=0.0)
    b._prices["TSLA"] = 200.0
    # seed a TSLA position worth 100k with no starting cash (paper allows it)
    b.store.upsert_paper_position("TSLA", "equity", 500.0, 200.0)
    b.store.set_paper_cash(0.0)
    plan = _reb(b, {"AAPL": 0.5, "MSFT": 0.5}, execute=True)
    assert plan["executed"] is True
    assert all(r["ok"] for r in plan["results"])               # sells-first freed cash for the buys
    held = {p["symbol"]: p for p in b.store.paper_positions()}
    assert "TSLA" not in held and held["AAPL"]["quantity"] == pytest.approx(500.0)
    assert held["MSFT"]["quantity"] == pytest.approx(1000.0)


def test_execute_captures_per_order_buying_power_error(tmp_path):
    b = make_broker(tmp_path, cash=100.0)                       # tiny cash, flat → buy can't fund
    plan = _reb(b, {"AAPL": 0.5}, execute=True)
    # equity≈100 so target notional≈50; but a flat-book 0.5 weight on 100 equity = 50 notional < 100 cash → ok.
    # Force the failure: a target far exceeding cash.
    plan2 = _reb(b, {"AAPL": 10.0}, execute=True)              # wants 10x equity in AAPL
    failed = [r for r in plan2["results"] if not r["ok"]]
    assert failed and "buying power" in failed[0]["error"]


def test_rebalance_requires_weights(tmp_path):
    from fastapi import HTTPException

    b = make_broker(tmp_path)
    with pytest.raises(HTTPException):
        _reb(b, {})
