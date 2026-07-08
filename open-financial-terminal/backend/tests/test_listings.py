"""Offline tests for new-listing detection (Method 3, SEC EDGAR full-text search).

No network: a fake EdgarClient returns canned EFTS hits. We assert the service parses
company/ticker/CIK/url, dedupes by accession, applies the ticker filter, and that the endpoint
wires through. A separate test feeds an empty fake to confirm the graceful 'unavailable' fallback.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.deps import get_edgar_client
from app.main import app
from app.services import listings as ls


def _hit(adsh, cik, display, file_type, file_date, doc):
    return {
        "_id": f"{adsh}:{doc}",
        "_source": {
            "adsh": adsh, "ciks": [cik], "display_names": [display],
            "file_type": file_type, "file_date": file_date,
        },
    }


# A realistic EFTS page: an IPO (424B4) with ticker, an exchange listing (8-A12B), a duplicate of the
# IPO (same accession), and a ticker-less note registration that the default filter should drop.
_HITS = [
    _hit("0001-26-001", "0000001", "KARDIGAN, INC.  (KARD)  (CIK 0000001)", "424B4", "2026-06-18", "a.htm"),
    _hit("0002-26-002", "0000002", "LENDINGCLUB CORP  (LC)  (CIK 0000002)", "8-A12B", "2026-06-17", "b.htm"),
    _hit("0001-26-001", "0000001", "KARDIGAN, INC.  (KARD)  (CIK 0000001)", "424B4", "2026-06-18", "a.htm"),
    _hit("0003-26-003", "0000003", "SOME NOTE TRUST  (CIK 0000003)", "8-A12B", "2026-06-16", "c.htm"),
]


class FakeEdgar:
    def __init__(self, hits):
        self._hits = hits
        self.calls = []

    def full_text_search(self, forms, startdt, enddt, q="", frm=0):
        self.calls.append((tuple(forms), startdt, enddt, frm))
        if frm:  # single page
            return {"total": len(self._hits), "hits": []}
        # only return hits whose file_type matches the requested form (matches EFTS behaviour)
        form = forms[0]
        hits = [h for h in self._hits if h["_source"]["file_type"].startswith(form)]
        return {"total": len(hits), "hits": hits}


def test_parses_dedupes_and_filters(tmp_path):
    edgar = FakeEdgar(_HITS)
    out = ls.new_listings(edgar, Path(tmp_path), days=14)
    assert out["coverage"] == "live"
    by_ticker = {tuple(r["tickers"]): r for r in out["items"]}
    # the duplicate 424B4 collapsed to one; the ticker-less note registration was dropped
    assert ("KARD",) in by_ticker and ("LC",) in by_ticker
    assert all(r["tickers"] for r in out["items"])  # with_ticker_only default
    assert len(out["items"]) == 2
    # newest first
    assert out["items"][0]["filing_date"] >= out["items"][1]["filing_date"]
    kard = by_ticker[("KARD",)]
    assert kard["company"] == "KARDIGAN, INC." and kard["kind"] == "IPO prospectus"
    assert kard["url"] == "https://www.sec.gov/Archives/edgar/data/1/000126001/a.htm"


def test_with_ticker_only_false_keeps_note(tmp_path):
    edgar = FakeEdgar(_HITS)
    out = ls.new_listings(edgar, Path(tmp_path), days=14, with_ticker_only=False)
    assert any(r["tickers"] == [] for r in out["items"])  # the note trust is kept


def test_unavailable_when_no_cache(tmp_path):
    class Boom:
        def full_text_search(self, *a, **k):
            raise RuntimeError("EDGAR down")

    out = ls.new_listings(Boom(), Path(tmp_path), days=14)
    assert out["coverage"] == "unavailable" and out["items"] == []


def test_search_cached_matches_ticker_and_name(tmp_path):
    edgar = FakeEdgar(_HITS)
    data_dir = Path(tmp_path)
    ls.new_listings(edgar, data_dir, days=14)  # writes the snapshot
    # by ticker fragment
    assert [h["symbol"] for h in ls.search_cached(data_dir, "kar")] == ["KARD"]
    # by company name
    name_hits = ls.search_cached(data_dir, "lendingclub")
    assert name_hits and name_hits[0]["symbol"] == "LC"
    assert name_hits[0]["universe"] == "new listing" and name_hits[0]["name"] == "LENDINGCLUB CORP"
    # nothing matches → empty
    assert ls.search_cached(data_dir, "zzzz") == []


def test_endpoint(tmp_path, monkeypatch):
    # point the snapshot cache at a temp dir and inject the fake edgar
    monkeypatch.setattr(ls, "_snapshot_path", lambda _d: Path(tmp_path) / "listings_new.json")
    app.dependency_overrides[get_edgar_client] = lambda: FakeEdgar(_HITS)
    try:
        client = TestClient(app)
        r = client.get("/api/listings/new?days=7")
        assert r.status_code == 200
        body = r.json()
        assert body["days"] == 7 and body["count"] == 2
        assert {t for it in body["items"] for t in it["tickers"]} == {"KARD", "LC"}
    finally:
        app.dependency_overrides.pop(get_edgar_client, None)
