"""Offline tests for the EDGAR client (fake HTTP — no network) and the FilingsStore."""

from __future__ import annotations

from qhfi.api.client import ManagedClient
from qhfi.data.filings import FilingsStore
from qhfi.data.providers.edgar import EdgarClient, Filing


class _Resp:
    def __init__(self, payload=None, text=""):
        self._payload, self.text, self.status_code = payload, text, 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeHttp:
    """Maps URL substrings to canned responses."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url):
        self.calls.append(url)
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected url {url}")


def _client(routes):
    return EdgarClient(http=_FakeHttp(routes), managed=ManagedClient(backoff_base=0.0))


def test_ticker_to_cik_and_caching():
    http = _FakeHttp({"company_tickers": _Resp(payload={
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},
    })})
    c = EdgarClient(http=http, managed=ManagedClient(backoff_base=0.0))
    assert c.ticker_to_cik("aapl") == 320193          # case-insensitive
    assert c.ticker_to_cik("MSFT") == 789019
    assert http.calls.count(  # tickers fetched once (cached map)
        "https://www.sec.gov/files/company_tickers.json") == 1


def test_list_filings_parses_and_filters():
    sub = {"filings": {"recent": {
        "form": ["10-Q", "8-K", "10-K", "10-Q"],
        "filingDate": ["2026-05-01", "2026-04-01", "2025-11-01", "2026-01-30"],
        "reportDate": ["2026-03-28", "", "2025-09-27", "2025-12-27"],
        "accessionNumber": ["a-1", "a-2", "a-3", "a-4"],
        "primaryDocument": ["q1.htm", "x.htm", "k.htm", "q0.htm"],
    }}}
    c = _client({"submissions/CIK0000320193": _Resp(payload=sub)})
    filings = c.list_filings(320193, forms=("10-Q", "10-K"))
    assert [f.form for f in filings] == ["10-Q", "10-Q", "10-K"]   # 8-K filtered, newest first
    assert filings[0].filing_date == "2026-05-01" and filings[0].ext == "htm"


def test_document_url():
    c = _client({})
    f = Filing(cik=320193, form="10-Q", filing_date="2026-05-01", report_date="2026-03-28",
               accession="0000320193-26-000013", primary_document="aapl-20260328.htm")
    assert c.document_url(f) == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000013/aapl-20260328.htm")


def test_filings_store_roundtrip_and_manifest(tmp_path):
    store = FilingsStore(tmp_path)
    f = Filing(cik=320193, form="10-Q", filing_date="2026-05-01", report_date="2026-03-28",
               accession="0000320193-26-000013", primary_document="aapl.htm")
    store.save("AAPL", f, "<html>10-Q body</html>")

    assert store.has("AAPL", "0000320193-26-000013")
    assert "10-Q body" in store.load("AAPL", "0000320193-26-000013")
    m = store.manifest()
    assert len(m) == 1 and m.iloc[0]["form"] == "10-Q" and m.iloc[0]["bytes"] > 0
