"""Offline tests for the Public Filings service — form classification, insider P/S window
math, and the coverage-fallback branches.

No network / no EDGAR / no qhfi lake: a FakeEdgar returns SimpleNamespace filing objects
(mirroring test_listings.py's FakeEdgar pattern) and the insider/holders stores are fakes that
return canned DataFrames or raise. We pin: category routing (insider exact-set vs prefix families,
"424B" NOT swept into insider), _epoch UTC-midnight + guards, the document-URL builder, the
NaN-safe float coercer, the trailing-window net buy/sell summary, and feed/insider/holders
coverage fallbacks. One router test exercises GET /api/filings via app.dependency_overrides
(try/finally pop, exactly like test_listings.py).

Run: .venv/Scripts/python.exe -m pytest tests/test_filings.py -q
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.deps import get_edgar_client, get_filings_store
from app.main import app
from app.services import filings as fl


# ── stubs ────────────────────────────────────────────────────────────────────────
def _filing(form, filing_date, accession, cik="0000320193", report_date="", doc="a.htm"):
    return SimpleNamespace(
        form=form,
        filing_date=filing_date,
        report_date=report_date,
        accession=accession,
        cik=cik,
        primary_document=doc,
    )


class FakeEdgar:
    """Minimal EdgarClient: maps a ticker to a CIK and returns canned filing objects."""

    def __init__(self, filings, cik="0000320193"):
        self._filings = filings
        self._cik = cik
        self.calls = []

    def ticker_to_cik(self, symbol):
        self.calls.append(("ticker_to_cik", symbol))
        return self._cik

    def list_filings(self, cik, forms=None):
        self.calls.append(("list_filings", cik, forms))
        return list(self._filings)


class BoomEdgar:
    def ticker_to_cik(self, symbol):
        raise RuntimeError("EDGAR down")

    def list_filings(self, *a, **k):
        raise RuntimeError("EDGAR down")


def _store(tmp_path):
    return SimpleNamespace(data_dir=Path(tmp_path))


_FEED = [
    _filing("10-K", "2026-02-01", "0000320193-26-000001", report_date="2025-12-31"),
    _filing("8-K", "2026-03-15", "0000320193-26-000002"),
    _filing("4", "2026-04-01", "0000320193-26-000003"),
    _filing("424B2", "2026-05-01", "0000320193-26-000004"),
]


# ── category_of ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "form, expected",
    [
        ("3", "insider"),
        ("4", "insider"),
        ("5", "insider"),
        ("4/A", "insider"),
        ("3/a", "insider"),       # case-insensitive
        ("10-K", "financials"),
        ("10-Q", "financials"),
        ("20-F", "financials"),
        ("8-K", "events"),
        ("6-K", "events"),
        ("SC 13D", "ownership"),
        ("SCHEDULE 13G", "ownership"),
        ("13F-HR", "ownership"),
        ("DEF 14A", "governance"),
        ("DEFA14A", "governance"),
        ("S-1", "offerings"),
        ("424B2", "offerings"),   # the "4" rule must NOT sweep this into insider
        ("424B5", "offerings"),
        ("144", "offerings"),
        ("CORRESP", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_category_of_routes_each_form_family(form, expected):
    assert fl.category_of(form) == expected


# ── _epoch ───────────────────────────────────────────────────────────────────────
def test_epoch_parses_and_guards():
    expected = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
    assert fl._epoch("2026-02-01") == expected
    assert fl._epoch("") is None
    assert fl._epoch("not-a-date") is None
    assert fl._epoch("2026/02/01") is None  # wrong separator → ValueError → None


# ── _document_url ────────────────────────────────────────────────────────────────
def test_document_url_with_and_without_doc():
    f = _filing("10-K", "2026-02-01", "0000320193-26-000001", cik="320193", doc="aapl-10k.htm")
    assert fl._document_url(f) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/aapl-10k.htm"
    )
    # no primary document → trailing-slash base (the filing index)
    f2 = _filing("8-K", "2026-03-15", "0000320193-26-000002", cik="320193", doc="")
    assert fl._document_url(f2) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000002/"
    )


# ── _f (NaN-safe float) ──────────────────────────────────────────────────────────
def test_f_is_nan_safe():
    assert fl._f("12.5") == 12.5
    assert fl._f(7) == 7.0
    assert fl._f(None) == 0.0
    assert fl._f("garbage") == 0.0
    assert fl._f(float("nan")) == 0.0


# ── _insider_summary ─────────────────────────────────────────────────────────────
def test_insider_summary_nets_buys_vs_sells_in_window():
    now = datetime.now(timezone.utc)

    def ds(days_ago):
        return (now - pd.Timedelta(days=days_ago)).strftime("%Y-%m-%d")

    # Inside the 90d window: a buy (P) and a sell (S). One older buy lands in 6m but not 90d.
    # One ancient row (400d) is outside both windows and must be excluded.
    df = pd.DataFrame(
        [
            {"txn_date": ds(10), "code": "P", "shares": 100.0, "price": 10.0},   # buy d90+m6
            {"txn_date": ds(20), "code": "S", "shares": 40.0, "price": 12.0},    # sell d90+m6
            {"txn_date": ds(120), "code": "P", "shares": 50.0, "price": 8.0},    # buy m6 only
            {"txn_date": ds(400), "code": "P", "shares": 999.0, "price": 5.0},   # excluded
        ]
    )
    out = fl._insider_summary(df)

    d90 = out["d90"]
    assert d90["n_buys"] == 1 and d90["n_sells"] == 1
    assert d90["buy_shares"] == 100.0 and d90["sell_shares"] == 40.0
    assert d90["buy_value"] == 1000.0 and d90["sell_value"] == 480.0
    assert d90["net_shares"] == 60.0 and d90["net_value"] == 520.0

    m6 = out["m6"]
    # the 120d-ago buy is inside 182d but not 90d → m6 sees 2 buys, 1 sell
    assert m6["n_buys"] == 2 and m6["n_sells"] == 1
    assert m6["buy_shares"] == 150.0
    assert m6["net_shares"] == 150.0 - 40.0  # the 400d row stays excluded


# ── feed coverage / fallback ─────────────────────────────────────────────────────
def test_feed_live_writes_cache_then_falls_back(tmp_path):
    store = _store(tmp_path)
    edgar = FakeEdgar(_FEED)
    live = fl.feed(edgar, store, "aapl")
    assert live["symbol"] == "AAPL"
    assert live["coverage"] == "live"
    assert len(live["items"]) == 4
    # each item carries a category + a resolved url
    assert {it["category"] for it in live["items"]} == {"financials", "events", "insider", "offerings"}
    assert all(it["url"].startswith("https://www.sec.gov/Archives/edgar/data/") for it in live["items"])

    # now EDGAR is down → the just-written cache satisfies the request as "cached"
    cached = fl.feed(BoomEdgar(), store, "AAPL")
    assert cached["coverage"] == "cached"
    assert len(cached["items"]) == 4


def test_feed_falls_back_to_cache_when_edgar_raises(tmp_path):
    store = _store(tmp_path)
    # no cache written and EDGAR down → unavailable, empty
    out = fl.feed(BoomEdgar(), store, "AAPL")
    assert out["coverage"] == "unavailable"
    assert out["items"] == []


def test_feed_category_filter(tmp_path):
    store = _store(tmp_path)
    edgar = FakeEdgar(_FEED)
    out = fl.feed(edgar, store, "AAPL", category="financials")
    assert [it["form"] for it in out["items"]] == ["10-K"]
    # 'all' / '' / None are pass-through (no filtering)
    assert len(fl.feed(edgar, store, "AAPL", category="all")["items"]) == 4
    assert len(fl.feed(edgar, store, "AAPL", category="")["items"]) == 4


# ── insider coverage ─────────────────────────────────────────────────────────────
def test_insider_unavailable_when_store_raises():
    class BoomStore:
        def has(self, symbol):
            raise RuntimeError("lake down")

    out = fl.insider(FakeEdgar(_FEED), BoomStore(), "AAPL")
    assert out["coverage"] == "unavailable"
    assert out["items"] == []
    # graceful empty summary shape preserved
    assert out["summary"]["d90"]["n_buys"] == 0 and out["summary"]["m6"]["n_sells"] == 0


def test_insider_lake_hit_summarizes():
    now = datetime.now(timezone.utc)

    def ds(days_ago):
        return (now - pd.Timedelta(days=days_ago)).strftime("%Y-%m-%d")

    df = pd.DataFrame(
        [
            {"txn_date": ds(5), "code": "P", "shares": 200.0, "price": 5.0,
             "insider": "Jane Doe", "role": "CEO", "filed": "2026-06-20",
             "acq_disp": "A", "shares_after": 1000.0, "security": "Common", "derivative": False},
            {"txn_date": ds(8), "code": "S", "shares": 50.0, "price": 6.0,
             "insider": "John Roe", "role": "CFO", "filed": "2026-06-21",
             "acq_disp": "D", "shares_after": 500.0, "security": "Common", "derivative": False},
        ]
    )

    class LakeStore:
        def has(self, symbol):
            return True

        def load(self, symbol):
            return df

    out = fl.insider(FakeEdgar(_FEED), LakeStore(), "AAPL")
    assert out["coverage"] == "lake"
    assert len(out["items"]) == 2
    assert out["summary"]["d90"]["n_buys"] == 1 and out["summary"]["d90"]["n_sells"] == 1
    # items sorted newest-first by txn_date
    assert out["items"][0]["insider"] == "Jane Doe"
    assert out["items"][0]["value"] == 200.0 * 5.0


# ── holders coverage ─────────────────────────────────────────────────────────────
def test_holders_unavailable_on_store_error():
    class BoomHoldings:
        def holders_of(self, symbol, cusip_store, top=25):
            raise RuntimeError("crosswalk miss")

    out = fl.holders(BoomHoldings(), object(), "AAPL")
    assert out["coverage"] == "unavailable"
    assert out["period"] is None
    assert out["items"] == []


def test_holders_none_when_empty_frame():
    class EmptyHoldings:
        def holders_of(self, symbol, cusip_store, top=25):
            return pd.DataFrame()

    out = fl.holders(EmptyHoldings(), object(), "AAPL")
    assert out["coverage"] == "none"
    assert out["items"] == []


def test_holders_lake_hit_shapes_rows():
    df = pd.DataFrame(
        [
            {"manager": "Vanguard", "shares": 1000.0, "value_usd": 5e6, "pct_of_book": 0.04,
             "change_shares": 100.0, "change_pct": 0.11, "period": "2026Q1"},
        ]
    )

    class Holdings:
        def holders_of(self, symbol, cusip_store, top=25):
            return df

    out = fl.holders(Holdings(), object(), "AAPL")
    assert out["coverage"] == "lake"
    assert out["period"] == "2026Q1"
    assert out["items"][0]["manager"] == "Vanguard" and out["items"][0]["shares"] == 1000.0


# ── router ───────────────────────────────────────────────────────────────────────
def test_filings_endpoint_wires_through(tmp_path):
    edgar = FakeEdgar(_FEED)
    app.dependency_overrides[get_edgar_client] = lambda: edgar
    app.dependency_overrides[get_filings_store] = lambda: _store(tmp_path)
    try:
        client = TestClient(app)
        r = client.get("/api/filings?symbol=aapl")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "AAPL"
        assert body["coverage"] == "live"
        assert len(body["items"]) == 4
        assert {it["category"] for it in body["items"]} == {
            "financials", "events", "insider", "offerings"
        }
        assert all(it["url"].startswith("https://www.sec.gov/Archives/") for it in body["items"])

        # category filter flows through the query string
        r2 = client.get("/api/filings?symbol=AAPL&category=events")
        assert [it["form"] for it in r2.json()["items"]] == ["8-K"]
    finally:
        app.dependency_overrides.pop(get_edgar_client, None)
        app.dependency_overrides.pop(get_filings_store, None)
