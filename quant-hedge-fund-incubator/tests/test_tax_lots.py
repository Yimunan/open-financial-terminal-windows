"""Per-lot accounting: FIFO/LIFO/HIFO/MIN_TAX selection, ST/LT classification, partial lots."""

from __future__ import annotations

from datetime import date

import pytest

from qhfi.tax.lots import LotBook, LotMethod


def _book() -> LotBook:
    b = LotBook()
    b.buy("AAPL", 100, price=100.0, when=date(2023, 1, 1))   # old, low cost  → long-term winner
    b.buy("AAPL", 100, price=150.0, when=date(2024, 5, 1))   # new, high cost → short-term loser
    return b


SELL_DATE = date(2024, 6, 1)   # lot1 held ~517d (LT), lot2 held ~31d (ST)


def test_fifo_sells_oldest_lot():
    r = _book().sell("AAPL", 100, price=130.0, when=SELL_DATE, method=LotMethod.FIFO)
    assert len(r) == 1
    assert r[0].gain == pytest.approx(3000.0)     # (130-100)*100
    assert r[0].long_term is True


def test_lifo_and_hifo_sell_the_high_cost_recent_lot_here():
    for method in (LotMethod.LIFO, LotMethod.HIFO):
        r = _book().sell("AAPL", 100, price=130.0, when=SELL_DATE, method=method)
        assert r[0].gain == pytest.approx(-2000.0)  # (130-150)*100
        assert r[0].long_term is False


def test_min_tax_prefers_the_loss_lot():
    r = _book().sell("AAPL", 100, price=130.0, when=SELL_DATE, method=LotMethod.MIN_TAX)
    assert r[0].gain == pytest.approx(-2000.0)     # realizes the short-term loss first
    assert r[0].long_term is False


def test_methods_disagree_on_the_same_sell():
    fifo = _book().sell("AAPL", 100, 130.0, SELL_DATE, LotMethod.FIFO)[0].gain
    hifo = _book().sell("AAPL", 100, 130.0, SELL_DATE, LotMethod.HIFO)[0].gain
    assert fifo != hifo                            # lot selection materially changes the gain


def test_partial_lot_consumption_spans_lots():
    b = _book()
    r = b.sell("AAPL", 150, price=130.0, when=SELL_DATE, method=LotMethod.FIFO)
    assert len(r) == 2
    assert r[0].quantity == pytest.approx(100.0) and r[0].gain == pytest.approx(3000.0)
    assert r[1].quantity == pytest.approx(50.0) and r[1].gain == pytest.approx(-1000.0)
    assert b.quantity("AAPL") == pytest.approx(50.0)   # 50 of the second lot remain


def test_quantity_and_unrealized_track_open_lots():
    b = _book()
    assert b.quantity("AAPL") == pytest.approx(200.0)
    b.sell("AAPL", 100, 130.0, SELL_DATE, LotMethod.FIFO)
    assert b.quantity("AAPL") == pytest.approx(100.0)
    assert b.unrealized("AAPL", price=130.0) == pytest.approx(-2000.0)  # remaining lot2


def test_sell_more_than_held_is_clamped():
    b = LotBook()
    b.buy("MSFT", 50, 400.0, date(2024, 1, 1))
    r = b.sell("MSFT", 500, price=420.0, when=date(2024, 2, 1), method=LotMethod.FIFO)
    assert sum(x.quantity for x in r) == pytest.approx(50.0)
    assert b.quantity("MSFT") == pytest.approx(0.0)
