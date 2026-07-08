"""Offline tests for the news domain: Alpaca / GDELT / yfinance providers + NewsStore."""

from __future__ import annotations

import types

import httpx
import pandas as pd
import yfinance

from qhfi.data.news import COLUMNS, NewsStore
from qhfi.data.providers.gdelt import GdeltProvider
from qhfi.data.providers.news_alpaca import AlpacaNewsProvider
from qhfi.data.providers.news_yfinance import YFinanceNewsProvider


# ── Alpaca (fake NewsClient) ──────────────────────────────────────────────────────
def _news_obj(i, sym):
    return types.SimpleNamespace(
        id=i, headline=f"headline {i}", summary=f"summary {i}", author="a. reporter",
        source="Benzinga", url=f"http://x/{i}", symbols=[sym],
        created_at=pd.Timestamp(f"2024-01-0{i}", tz="UTC"))


class _FakeNewsClient:
    """One page then stop (next_page_token None on 2nd call)."""

    def __init__(self):
        self.calls = 0

    def get_news(self, req):
        self.calls += 1
        if self.calls == 1:
            data = [_news_obj(1, "AAPL"), _news_obj(2, "AAPL")]
            return types.SimpleNamespace(data={"news": data}, next_page_token="tok")
        return types.SimpleNamespace(data={"news": [_news_obj(3, "AAPL")]}, next_page_token=None)


def test_alpaca_provider_paginates_and_normalizes():
    p = AlpacaNewsProvider(client=_FakeNewsClient())
    df = p.fetch("AAPL", start="2024-01-01", end="2024-02-01")
    assert list(df.columns) == COLUMNS
    assert len(df) == 3 and (df["provider"] == "alpaca").all()
    assert df["publisher"].iloc[0] == "Benzinga" and df["symbols"].iloc[0] == "AAPL"


def test_alpaca_available_flag():
    assert not AlpacaNewsProvider().available()
    assert AlpacaNewsProvider(api_key="k", api_secret="s").available()


# ── GDELT artlist (MockTransport) ─────────────────────────────────────────────────
_ARTLIST = {"articles": [
    {"url": "http://n/1", "title": "War escalates", "seendate": "20260101T120000Z", "domain": "reuters.com"},
    {"url": "http://n/2", "title": "Sanctions imposed", "seendate": "20260102T120000Z", "domain": "bbc.co.uk"},
]}


def test_gdelt_fetch_articles_normalizes():
    def handler(req):
        assert b"artlist" in req.url.query or "artlist" in str(req.url)
        return httpx.Response(200, json=_ARTLIST)

    g = GdeltProvider(transport=httpx.MockTransport(handler))
    df = g.fetch_articles('"war"', timespan="1m")
    assert list(df.columns) == COLUMNS and len(df) == 2
    assert (df["provider"] == "gdelt").all() and df["publisher"].iloc[0] == "reuters.com"
    assert df["headline"].tolist() == ["War escalates", "Sanctions imposed"]


# ── yfinance (monkeypatched, both schemas) ────────────────────────────────────────
def test_yfinance_news_new_and_legacy_schema(monkeypatch):
    new = {"id": "n1", "content": {"title": "Apple ships", "summary": "lots",
           "pubDate": "2026-01-01T00:00:00Z", "provider": {"displayName": "Reuters"},
           "canonicalUrl": {"url": "http://a/1"}}}
    legacy = {"uuid": "n2", "title": "Old story", "publisher": "WSJ", "link": "http://a/2",
              "providerPublishTime": 1735689600}
    monkeypatch.setattr(yfinance, "Ticker", lambda s: types.SimpleNamespace(news=[new, legacy]))
    df = YFinanceNewsProvider().fetch("AAPL")
    assert len(df) == 2 and (df["provider"] == "yfinance").all()
    assert set(df["headline"]) == {"Apple ships", "Old story"}
    assert (df["symbols"] == "AAPL").all()


# ── NewsStore ─────────────────────────────────────────────────────────────────────
def _df(ids, ts):
    return pd.DataFrame({"id": ids, "created_at": pd.to_datetime(ts, utc=True),
                         "headline": [f"h{i}" for i in ids], "summary": "", "author": None,
                         "publisher": "p", "url": "u", "symbols": "AAPL", "provider": "alpaca"})


def test_news_store_append_dedup_and_catalog(tmp_path):
    store = NewsStore(tmp_path)
    store.save("equity", "alpaca", "AAPL", _df(["1", "2"], ["2024-01-01", "2024-01-02"]))
    added = store.save("equity", "alpaca", "AAPL", _df(["2", "3"], ["2024-01-02", "2024-01-03"]))
    assert added == 1                                              # id "2" deduped
    df = store.load("equity", "alpaca", "AAPL")
    assert df["id"].tolist() == ["1", "2", "3"]                    # sorted by created_at
    assert store._path("equity", "alpaca", "AAPL").parts[-4:] == ("news", "equity", "alpaca", "AAPL.parquet")
    cat = store.catalog()
    assert cat.iloc[0]["articles"] == 3 and cat.iloc[0]["source"] == "alpaca"


def test_taxonomy_registers_news_domain():
    from qhfi.data.taxonomy import DataDomain, by_domain

    assert {d.name for d in by_domain(DataDomain.NEWS)} == {"equity_news", "macro_news"}
