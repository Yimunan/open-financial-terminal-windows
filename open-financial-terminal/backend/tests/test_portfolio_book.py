"""Tests for the Portfolio module: normalize math, store CRUD, and weight→share allocation.

Normalize + CRUD are hermetic; the allocation test prices symbols off the local lake and is
skipped if nothing could be priced (no cached data / offline).

Run: `cd backend && pytest tests/test_portfolio_book.py -v`
"""

from __future__ import annotations

import pytest

from app import deps as appdeps
from app.services import portfolio_book as pb
from app.store import TerminalStore


def test_normalize_long_short_is_dollar_neutral_gross_one():
    out = pb.normalize(
        [{"symbol": "AAPL", "weight": 2}, {"symbol": "MSFT", "weight": 1}, {"symbol": "IBM", "weight": -1}],
        mode="long_short",
    )
    exp = out["exposures"]
    assert exp["gross"] == pytest.approx(1.0, abs=1e-6)
    assert exp["net"] == pytest.approx(0.0, abs=1e-6)
    assert exp["n_long"] == 2 and exp["n_short"] == 1


def test_normalize_long_only_drops_shorts_and_sums_to_one():
    out = pb.normalize(
        [{"symbol": "AAPL", "weight": 3}, {"symbol": "MSFT", "weight": 1}, {"symbol": "IBM", "weight": -5}],
        mode="long_only",
    )
    assert [a["symbol"] for a in out["allocations"]] == ["AAPL", "MSFT"]  # short dropped
    assert sum(a["weight"] for a in out["allocations"]) == pytest.approx(1.0, abs=1e-6)
    assert out["allocations"][0]["weight"] == pytest.approx(0.75, abs=1e-6)


def test_clean_allocations_drops_blanks_and_uppercases():
    out = pb.normalize([{"symbol": "aapl", "weight": 1}, {"symbol": "", "weight": 5}], mode="long_only")
    assert [a["symbol"] for a in out["allocations"]] == ["AAPL"]


def test_store_crud_roundtrip(tmp_path):
    store = TerminalStore(tmp_path / "t.sqlite")
    store.init()
    pb.save_portfolio(store, "Book A", {
        "mode": "long_short",
        "allocations": [{"symbol": "aapl", "weight": 0.5}, {"symbol": "msft", "weight": -0.5}],
        "tags": ["demo"], "notes": "x",
    })
    listing = pb.list_portfolios(store)["portfolios"]
    assert len(listing) == 1
    p = listing[0]
    assert p["name"] == "Book A" and p["mode"] == "long_short"
    assert {a["symbol"] for a in p["allocations"]} == {"AAPL", "MSFT"}

    # search matches a held symbol
    assert pb.list_portfolios(store, "msft")["portfolios"]
    assert pb.list_portfolios(store, "zzz")["portfolios"] == []

    pb.remove_portfolio(store, "Book A")
    assert pb.list_portfolios(store)["portfolios"] == []


def test_allocate_values_weights_into_shares():
    dm = appdeps.get_data_manager()
    out = pb.allocate(dm, [{"symbol": "AAPL", "weight": 0.6}, {"symbol": "MSFT", "weight": 0.4}], capital=1_000_000)
    if out["priced"] == 0:
        pytest.skip("no cached prices for AAPL/MSFT")
    assert out["capital"] == 1_000_000
    priced = [r for r in out["rows"] if r["price"] is not None]
    for r in priced:
        assert r["notional"] == pytest.approx(r["weight"] * 1_000_000, rel=1e-6)
        assert r["shares"] == pytest.approx(r["notional"] / r["price"], rel=1e-6)
        assert r["side"] == "long"
