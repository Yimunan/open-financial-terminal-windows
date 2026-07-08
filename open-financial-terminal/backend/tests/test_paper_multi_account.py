"""Multi-account sim paper trading: per-account isolation of cash, positions, orders, equity curve,
realized P&L, and config — plus the migration of a legacy single-book DB.

Brokers are bound to a `TerminalStore` account id and their price source is stubbed, so fills are
deterministic. Run: `cd backend && pytest tests/test_paper_multi_account.py -v`
"""

from __future__ import annotations

import sqlite3

import pytest
from qhfi.execution.base import Order, OrderSide

from app.services.broker import SimBroker
from app.store import TerminalStore


def _broker(store, account_id, cash, prices, comm=0.0, slip=0.0):
    b = SimBroker(store, dm=None, initial_cash=cash, commission_bps=comm,
                  slippage_bps=slip, account_id=account_id)
    b._prices = dict(prices)
    b.last_price = lambda symbol, asset="equity": b._prices.get(symbol)  # type: ignore[method-assign]
    return b


def _buy(b, sym, qty):
    return b.submit(Order(instrument_id=sym, side=OrderSide.BUY, quantity=qty, type="market"))


def test_two_accounts_isolate_cash_positions_orders(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()  # default 100k for account 1 seed metadata
    a_id = store.create_paper_account("Momentum", 100_000.0)
    b_id = store.create_paper_account("MM Sandbox", 1_000_000.0)
    a = _broker(store, a_id, 100_000.0, {"AAPL": 100.0})
    b = _broker(store, b_id, 1_000_000.0, {"BTC/USDT": 50_000.0})

    _buy(a, "AAPL", 10)               # account A trades AAPL
    b.submit(Order(instrument_id="BTC/USDT", side=OrderSide.BUY, quantity=2, type="market"))

    a_pos = {p["symbol"]: p for p in store.paper_positions(a_id)}
    b_pos = {p["symbol"]: p for p in store.paper_positions(b_id)}
    assert set(a_pos) == {"AAPL"} and set(b_pos) == {"BTC/USDT"}  # no bleed across books
    assert store.paper_cash(a_id) == pytest.approx(99_000.0)       # 100k - 10*100
    assert store.paper_cash(b_id) == pytest.approx(900_000.0)      # 1M - 2*50k
    assert len(store.list_paper_orders(account_id=a_id)) == 1
    assert len(store.list_paper_orders(account_id=b_id)) == 1


def test_realized_and_equity_curve_are_per_account(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    a_id = store.create_paper_account("A", 100_000.0)
    b_id = store.create_paper_account("B", 100_000.0)
    a = _broker(store, a_id, 100_000.0, {"AAPL": 100.0})

    _buy(a, "AAPL", 10)
    a._prices["AAPL"] = 130.0
    a.submit(Order(instrument_id="AAPL", side=OrderSide.SELL, quantity=10, type="market"))  # +300

    assert store.paper_realized_total(a_id) == pytest.approx(300.0)
    assert store.paper_realized_total(b_id) == 0.0  # B untouched

    store.add_equity_snapshot(100_300.0, 100_300.0, min_interval_s=0, account_id=a_id)
    store.add_equity_snapshot(100_000.0, 100_000.0, min_interval_s=0, account_id=b_id)
    assert len(store.paper_equity_curve(account_id=a_id)) == 1
    assert len(store.paper_equity_curve(account_id=b_id)) == 1  # B's curve isn't starved by A


def test_reset_wipes_only_one_account(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    a_id = store.create_paper_account("A", 100_000.0)
    b_id = store.create_paper_account("B", 100_000.0)
    a = _broker(store, a_id, 100_000.0, {"AAPL": 100.0})
    b = _broker(store, b_id, 100_000.0, {"AAPL": 100.0})
    _buy(a, "AAPL", 10)
    _buy(b, "AAPL", 5)

    a.reset()
    assert store.paper_positions(a_id) == [] and store.paper_cash(a_id) == pytest.approx(100_000.0)
    assert {p["symbol"] for p in store.paper_positions(b_id)} == {"AAPL"}  # B survives
    assert store.get_paper_account(a_id) is not None  # the account row itself is kept


def test_bare_account_one_is_the_default_book(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    b = _broker(store, 1, 100_000.0, {"AAPL": 100.0})  # account 1 created lazily by the broker
    _buy(b, "AAPL", 3)
    # default-arg (bare) reads target account 1 — same book the broker traded
    assert {p["symbol"] for p in store.paper_positions()} == {"AAPL"}
    assert store.paper_cash() == pytest.approx(99_700.0)


def test_per_account_realism_differs(tmp_path):
    store = TerminalStore(tmp_path / "term.db")
    store.init()
    a_id = store.create_paper_account("Friction", 100_000.0, commission_bps=0.0, slippage_bps=100.0)
    a = _broker(store, a_id, 100_000.0, {"AAPL": 100.0}, slip=100.0)  # 1% slippage
    _buy(a, "AAPL", 10)
    pos = {p["symbol"]: p for p in store.paper_positions(a_id)}["AAPL"]
    assert pos["avg_price"] == pytest.approx(101.0)  # crossed up by 1%


def test_legacy_single_book_migrates_to_account_one(tmp_path):
    """A pre-multi-account DB (singleton paper_account + symbol-PK positions) migrates on init():
    the legacy cash/realized become Default account 1 and old positions re-home under account 1."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        "CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE paper_account (id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL NOT NULL, "
        "  realized_total REAL NOT NULL DEFAULT 0);"
        "CREATE TABLE paper_positions (symbol TEXT PRIMARY KEY, asset TEXT NOT NULL DEFAULT 'equity', "
        "  quantity REAL NOT NULL DEFAULT 0, avg_price REAL NOT NULL DEFAULT 0);"
        "CREATE TABLE paper_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
        "  symbol TEXT NOT NULL, asset TEXT NOT NULL DEFAULT 'equity', side TEXT NOT NULL, "
        "  quantity REAL NOT NULL, type TEXT NOT NULL DEFAULT 'market', limit_price REAL, "
        "  status TEXT NOT NULL, fill_price REAL, broker_order_id TEXT);"
        "CREATE TABLE paper_equity (ts TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL);"
        "INSERT INTO paper_account(id, cash, realized_total) VALUES (1, 42_000, 1_500);"
        "INSERT INTO paper_positions(symbol, asset, quantity, avg_price) VALUES ('AAPL','equity',7,150);"
        "INSERT INTO paper_orders(ts, symbol, asset, side, quantity, type, status, fill_price) "
        "  VALUES (datetime('now'),'AAPL','equity','buy',7,'market','filled',150);"
        "INSERT INTO paper_equity(ts, equity, cash) VALUES (datetime('now'), 43_050, 42_000);"
    )
    conn.commit()
    conn.close()

    store = TerminalStore(db)
    store.init()

    acct = store.get_paper_account(1)
    assert acct is not None and acct["name"] == "Default"
    assert acct["cash"] == pytest.approx(42_000.0) and acct["realized_total"] == pytest.approx(1_500.0)
    assert {p["symbol"]: p["quantity"] for p in store.paper_positions(1)} == {"AAPL": 7.0}
    assert len(store.list_paper_orders(account_id=1)) == 1
    assert len(store.paper_equity_curve(account_id=1)) == 1
