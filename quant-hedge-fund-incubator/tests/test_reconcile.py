"""diff_to_orders weight→order math, and tax-aware order application through a LotBook."""

from __future__ import annotations

from datetime import date

import pytest

from qhfi.core.types import AssetClass, Instrument
from qhfi.execution.base import Account, Order, OrderSide, Position
from qhfi.tax import LotBook, LotMethod, apply_orders
from qhfi.trading.reconcile import diff_to_orders


def _ins(lot: float = 1.0, mult: float = 1.0) -> dict[str, Instrument]:
    return {"AAPL": Instrument(id="AAPL", asset_class=AssetClass.EQUITY,
                               lot_size=lot, contract_multiplier=mult)}


def _acct(equity: float = 100_000.0, positions=None) -> Account:
    return Account(equity=equity, cash=equity, positions=positions or {})


# ── diff_to_orders ────────────────────────────────────────────────────────────
def test_buy_from_flat():
    orders = diff_to_orders({"AAPL": 0.10}, _acct(), {"AAPL": 200.0}, _ins())
    assert len(orders) == 1
    o = orders[0]
    assert o.side is OrderSide.BUY and o.quantity == pytest.approx(50.0)  # 0.10*100k/200


def test_sell_to_reduce_position():
    acct = _acct(positions={"AAPL": Position("AAPL", quantity=100.0, avg_price=150.0)})
    orders = diff_to_orders({"AAPL": 0.05}, acct, {"AAPL": 200.0}, _ins())
    assert orders[0].side is OrderSide.SELL
    assert orders[0].quantity == pytest.approx(75.0)   # target 25 − current 100


def test_dust_trade_is_dropped():
    # target ~0.1 share * $200 = $20 notional < $25 min; lot 0.01 keeps it non-zero
    orders = diff_to_orders({"AAPL": 0.0002}, _acct(), {"AAPL": 200.0}, _ins(lot=0.01))
    assert orders == []


def test_delta_rounds_to_lot_size():
    orders = diff_to_orders({"AAPL": 0.108}, _acct(), {"AAPL": 200.0}, _ins(lot=10.0))
    assert orders[0].quantity == pytest.approx(50.0)   # 54 rounded to nearest 10


def test_missing_price_or_instrument_is_skipped():
    orders = diff_to_orders({"AAPL": 0.1, "MSFT": 0.1}, _acct(), {"AAPL": 200.0}, _ins())
    assert [o.instrument_id for o in orders] == ["AAPL"]   # MSFT has no price/instrument


# ── apply_orders (tax-aware processing) ───────────────────────────────────────
def test_apply_buy_then_long_term_sale():
    book = LotBook()
    apply_orders([Order("AAPL", OrderSide.BUY, 100)], book, {"AAPL": 100.0}, date(2023, 1, 1))
    assert book.quantity("AAPL") == pytest.approx(100.0)

    rep = apply_orders([Order("AAPL", OrderSide.SELL, 100)], book, {"AAPL": 130.0},
                       date(2024, 6, 1), method=LotMethod.FIFO)
    assert rep.lt_gain == pytest.approx(3000.0) and rep.st_gain == pytest.approx(0.0)
    assert rep.est_tax == pytest.approx(3000.0 * 0.20)   # long-term rate


def test_apply_flags_same_batch_wash_sale():
    book = LotBook()
    book.buy("AAPL", 100, price=150.0, when=date(2024, 1, 1))
    rep = apply_orders(
        [Order("AAPL", OrderSide.SELL, 100), Order("AAPL", OrderSide.BUY, 100)],
        book, {"AAPL": 130.0}, date(2024, 6, 1), method=LotMethod.FIFO,
    )
    assert rep.wash_disallowed == pytest.approx(2000.0)
    assert rep.est_tax == pytest.approx(0.0)   # disallowed loss can't offset → no benefit
