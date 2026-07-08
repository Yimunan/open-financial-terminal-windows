"""Wash-sale flagging and tax-loss harvesting candidate selection."""

from __future__ import annotations

from datetime import date

import pytest

from qhfi.tax.harvest import harvest_candidates
from qhfi.tax.lots import LotBook, LotMethod
from qhfi.tax.wash_sale import flag_wash_sales


def test_wash_sale_flags_loss_with_repurchase_in_window():
    b = LotBook()
    b.buy("AAPL", 100, price=150.0, when=date(2024, 1, 1))
    realized = b.sell("AAPL", 100, price=130.0, when=date(2024, 6, 1), method=LotMethod.FIFO)
    # a repurchase 10 days after the loss sale → wash sale
    flag_wash_sales(realized, buys=[("AAPL", date(2024, 6, 11))], window_days=30)
    assert realized[0].gain == pytest.approx(-2000.0)
    assert realized[0].wash is True
    assert realized[0].disallowed_loss == pytest.approx(2000.0)


def test_wash_sale_ignores_outside_window_and_gains():
    b = LotBook()
    b.buy("AAPL", 100, price=150.0, when=date(2024, 1, 1))
    loss = b.sell("AAPL", 100, 130.0, date(2024, 6, 1), LotMethod.FIFO)
    flag_wash_sales(loss, buys=[("AAPL", date(2024, 8, 1))], window_days=30)  # 61d away
    assert loss[0].wash is False

    b2 = LotBook()
    b2.buy("AAPL", 100, 100.0, date(2024, 1, 1))
    gain = b2.sell("AAPL", 100, 130.0, date(2024, 6, 1), LotMethod.FIFO)     # a gain
    flag_wash_sales(gain, buys=[("AAPL", date(2024, 6, 2))])
    assert gain[0].wash is False                                             # gains never wash


def test_harvest_surfaces_losers_above_threshold():
    b = LotBook()
    b.buy("AAPL", 100, price=150.0, when=date(2024, 1, 1))   # underwater at 130 → -2000
    b.buy("MSFT", 100, price=100.0, when=date(2024, 1, 1))   # winner at 130 → skip
    cands = harvest_candidates(b, prices={"AAPL": 130.0, "MSFT": 130.0}, min_loss=100.0)
    assert [c.instrument_id for c in cands] == ["AAPL"]
    assert cands[0].est_loss == pytest.approx(2000.0)
    assert cands[0].est_tax_benefit == pytest.approx(2000.0 * 0.37)


def test_harvest_skips_wash_sale_names():
    b = LotBook()
    b.buy("AAPL", 100, price=150.0, when=date(2024, 1, 1))
    cands = harvest_candidates(b, prices={"AAPL": 130.0}, min_loss=100.0, recent_buys=["AAPL"])
    assert cands == []                                       # recently bought → would wash


def test_harvest_counts_only_underwater_lots():
    b = LotBook()
    b.buy("AAPL", 100, price=150.0, when=date(2024, 1, 1))   # loser at 130 → -2000
    b.buy("AAPL", 100, price=110.0, when=date(2024, 2, 1))   # winner at 130 → excluded
    cands = harvest_candidates(b, prices={"AAPL": 130.0}, min_loss=100.0)
    assert cands[0].quantity == pytest.approx(100.0)         # only the underwater lot
    assert cands[0].est_loss == pytest.approx(2000.0)
