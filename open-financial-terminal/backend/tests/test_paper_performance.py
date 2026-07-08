"""Tests for paper-trading Phase 1: realized P&L, equity snapshots, and the /performance endpoint.

The SimBroker's price source (``last_price`` → qhfi data fetch) is replaced with a mutable dict so
fills happen at deterministic prices; everything else (cash, positions, realized ledger, equity
snapshots) runs against a real on-disk TerminalStore in a tmp dir.

Run: `cd backend && pytest tests/test_paper_performance.py -v`
"""

from __future__ import annotations

import pytest
from qhfi.execution.base import Order, OrderSide

from app.routers import paper as paper_router
from app.services.broker import SimBroker
from app.store import TerminalStore

INITIAL = 100_000.0


@pytest.fixture()
def broker(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    b = SimBroker(store, dm=None, initial_cash=INITIAL, commission_bps=0.0)  # type: ignore[arg-type]
    prices = {"AAPL": 100.0}
    b._prices = prices  # test handle
    b.last_price = lambda symbol, asset="equity": prices.get(symbol)  # type: ignore[method-assign]
    return b


def _buy(b, sym, qty):
    return b.submit(Order(instrument_id=sym, side=OrderSide.BUY, quantity=qty, type="market"))


def _sell(b, sym, qty):
    return b.submit(Order(instrument_id=sym, side=OrderSide.SELL, quantity=qty, type="market"))


def _realized_by_order(store) -> dict[int, float]:
    return {o["id"]: o["realized_pnl"] for o in store.list_paper_orders() if o["realized_pnl"] is not None}


def test_open_then_add_books_no_realized(broker):
    _buy(broker, "AAPL", 10)
    broker._prices["AAPL"] = 110.0
    _buy(broker, "AAPL", 10)  # adding in the same direction → no realized P&L
    assert broker.store.paper_realized_total() == 0.0
    assert _realized_by_order(broker.store) == {}
    pos = {p["symbol"]: p for p in broker.store.paper_positions()}["AAPL"]
    assert pos["quantity"] == 20 and pos["avg_price"] == pytest.approx(105.0)  # (10*100 + 10*110)/20


def test_long_partial_close_then_flip_through_zero(broker):
    _buy(broker, "AAPL", 10)                 # long 10 @ 100
    broker._prices["AAPL"] = 110.0
    oid_sell4 = _sell(broker, "AAPL", 4)     # close 4 → +40
    broker._prices["AAPL"] = 120.0
    oid_sell10 = _sell(broker, "AAPL", 10)   # close remaining 6 (+120), flip to short 4 @ 120

    booked = _realized_by_order(broker.store)
    assert booked[int(oid_sell4)] == pytest.approx(40.0)
    assert booked[int(oid_sell10)] == pytest.approx(120.0)
    assert broker.store.paper_realized_total() == pytest.approx(160.0)
    pos = {p["symbol"]: p for p in broker.store.paper_positions()}["AAPL"]
    assert pos["quantity"] == pytest.approx(-4.0) and pos["avg_price"] == pytest.approx(120.0)

    broker._prices["AAPL"] = 100.0
    _buy(broker, "AAPL", 4)                  # cover short at lower price → +80
    assert broker.store.paper_realized_total() == pytest.approx(240.0)
    assert broker.store.paper_positions() == []  # flat


def test_short_open_then_cover_for_profit(broker):
    broker._prices["AAPL"] = 50.0
    _sell(broker, "AAPL", 10)                # open short 10 @ 50, no realized
    assert broker.store.paper_realized_total() == 0.0
    broker._prices["AAPL"] = 40.0
    _buy(broker, "AAPL", 10)                 # cover at 40 → short profit 10*(50-40) = 100
    assert broker.store.paper_realized_total() == pytest.approx(100.0)


def test_equity_snapshot_throttle(broker):
    store = broker.store
    store.add_equity_snapshot(100.0, 100.0)          # default 60s throttle
    store.add_equity_snapshot(200.0, 200.0)          # within the window → skipped
    assert len(store.paper_equity_curve()) == 1
    store.add_equity_snapshot(300.0, 300.0, min_interval_s=0)  # forced → appends
    curve = store.paper_equity_curve()
    assert len(curve) == 2 and curve[-1]["equity"] == 300.0


def test_reset_clears_realized_and_curve(broker):
    _buy(broker, "AAPL", 10)
    broker._prices["AAPL"] = 110.0
    _sell(broker, "AAPL", 10)
    broker.store.add_equity_snapshot(123.0, 123.0)
    assert broker.store.paper_realized_total() != 0.0
    broker.reset()
    assert broker.store.paper_realized_total() == 0.0
    assert broker.store.paper_equity_curve() == []
    assert broker.store.paper_positions() == []


def test_performance_endpoint_shape(broker, monkeypatch):
    # route get_store()/broker_kind() (imported into the router namespace) at the test store
    monkeypatch.setattr(paper_router, "get_store", lambda: broker.store)
    monkeypatch.setattr(paper_router, "broker_kind", lambda: "sim")

    # < 2 snapshots → null metrics, but the shape is stable
    out = paper_router.performance(broker=broker)
    assert set(out["metrics"]) == {"cagr", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar"}
    assert all(v is None for v in out["metrics"].values())
    # the metrics-layer blocks are always present (empty/null when there's no data yet)
    assert out["risk"] == {"var_95": None, "cvar_95": None, "rolling_sharpe": []}
    assert out["exposure_curve"] == []
    assert out["trade_stats"]["n_trades"] == 0
    assert out["benchmark"] is None  # single-session / no curve

    # generate a closed trade + a varying equity curve (with exposure carried on the snapshot)
    _buy(broker, "AAPL", 10)
    broker._prices["AAPL"] = 110.0
    _sell(broker, "AAPL", 10)  # realized +100
    for eq in (100_000.0, 100_500.0, 100_400.0, 100_900.0):
        broker.store.add_equity_snapshot(eq, eq, min_interval_s=0, gross=eq / 2, net=eq / 4)

    out = paper_router.performance(broker=broker)
    assert len(out["equity_curve"]) >= 2
    assert isinstance(out["metrics"]["sharpe"], float)
    assert out["realized_total"] == pytest.approx(100.0)
    assert len(out["closed_trades"]) == 1
    assert out["closed_trades"][0]["realized_pnl"] == pytest.approx(100.0)
    # risk block populates from the snapshot return series
    assert out["risk"]["var_95"] is not None and out["risk"]["cvar_95"] is not None
    assert len(out["risk"]["rolling_sharpe"]) == len(out["equity_curve"]) - 1
    # exposure curve mirrors the snapshots' gross/net
    assert len(out["exposure_curve"]) == len(out["equity_curve"])
    assert out["exposure_curve"][-1]["gross"] == pytest.approx(100_900.0 / 2)
    assert out["exposure_curve"][-1]["net"] == pytest.approx(100_900.0 / 4)
    # trade-level analytics over the single winning close
    assert out["trade_stats"]["n_trades"] == 1 and out["trade_stats"]["n_wins"] == 1
    assert out["trade_stats"]["win_rate"] == 1.0
    assert out["pnl_by_symbol"] == [{"symbol": "AAPL", "realized_pnl": pytest.approx(100.0)}]


def test_ops_endpoint(broker, monkeypatch):
    monkeypatch.setattr(paper_router, "get_store", lambda: broker.store)
    monkeypatch.setattr(paper_router, "broker_kind", lambda: "sim")

    # no orders yet → stable empty shape
    out = paper_router.ops(broker=broker)
    assert out["applicable"] is True and out["total"] == 0
    assert out["fill_rate"] is None and out["latency"] is None

    # two market fills (latency ~0) + one resting limit that we cancel
    _buy(broker, "AAPL", 10)
    _sell(broker, "AAPL", 5)
    broker.submit(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=1, type="limit", limit_price=1.0))
    open_ids = [o["id"] for o in broker.store.open_paper_orders()]
    broker.cancel(open_ids[0])

    out = paper_router.ops(broker=broker)
    assert out["counts"]["filled"] == 2 and out["counts"]["cancelled"] == 1
    assert out["total"] == 3
    assert out["fill_rate"] == pytest.approx(2 / 3, abs=1e-4)  # filled / (filled+cancelled)
    assert out["latency"]["n"] == 2 and out["latency"]["max_s"] >= 0.0


def test_filled_ts_stamped_on_market_and_resting(broker):
    # market fill → filled_ts present immediately (≈ ts)
    _buy(broker, "AAPL", 10)
    lats = broker.store.paper_fill_latencies()
    assert len(lats) == 1 and lats[0] >= 0.0
    # a resting limit has no filled_ts until it fills
    broker._prices["AAPL"] = 200.0
    broker.submit(Order(instrument_id="AAPL", side=OrderSide.BUY, quantity=1, type="limit", limit_price=150.0))
    assert len(broker.store.paper_fill_latencies()) == 1  # still just the market fill
    broker._prices["AAPL"] = 140.0  # now marketable (buy limit 150 ≥ price 140)
    broker.list_orders()  # _fill_pending stamps it
    assert len(broker.store.paper_fill_latencies()) == 2
