"""Fundamentals service + low-level news fetchers.

Company snapshot + financials come straight from yfinance (already a qhfi dependency).

This module also holds the per-ticker news *fetch primitives* (yfinance, Yahoo Finance RSS,
Google News RSS, plus a custom-RSS factory). Routing across active sources, dedupe, ranking and
LLM sentiment/relevance scoring live in :mod:`app.services.news_router`, which imports these.
"""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) OFT/2.0 news-reader"

_SNAPSHOT_FIELDS = {
    "longName": "name",
    "sector": "sector",
    "industry": "industry",
    "marketCap": "market_cap",
    "trailingPE": "pe",
    "forwardPE": "forward_pe",
    "priceToBook": "pb",
    "dividendYield": "dividend_yield",
    "beta": "beta",
    "fiftyTwoWeekHigh": "high_52w",
    "fiftyTwoWeekLow": "low_52w",
    "trailingEps": "eps",
    "profitMargins": "profit_margin",
    "returnOnEquity": "roe",
    "currency": "currency",
}


def _ticker(symbol: str):
    import yfinance as yf

    return yf.Ticker(symbol)


def snapshot(symbol: str) -> dict:
    info: dict[str, Any] = {}
    try:
        info = _ticker(symbol).info or {}
    except Exception:  # noqa: BLE001 - yfinance .info is occasionally flaky
        info = {}
    out = {dest: info.get(src) for src, dest in _SNAPSHOT_FIELDS.items()}
    out["symbol"] = symbol.upper()
    out["summary"] = (info.get("longBusinessSummary") or "")[:1200]
    return out


def financials(symbol: str, n_periods: int = 4, freq: str = "annual") -> dict:
    """Key income-statement lines for the latest reporting periods.

    ``freq`` selects yfinance's annual (``income_stmt``) or quarterly
    (``quarterly_income_stmt``) statement; quarterly gives a denser time series for charting.
    """
    try:
        tk = _ticker(symbol)
        stmt = tk.quarterly_income_stmt if freq == "quarterly" else tk.income_stmt
    except Exception:  # noqa: BLE001
        return {"periods": [], "rows": {}}
    if stmt is None or stmt.empty:
        return {"periods": [], "rows": {}}

    wanted = ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "Diluted EPS"]
    cols = list(stmt.columns)[:n_periods]
    periods = [str(c.date()) if hasattr(c, "date") else str(c) for c in cols]
    rows: dict[str, list] = {}
    for line in wanted:
        if line in stmt.index:
            vals = stmt.loc[line, cols]
            rows[line] = [None if v != v else float(v) for v in vals]  # NaN-safe
    return {"periods": periods, "rows": rows}


def _norm(title: str) -> str:
    """Dedup key: lowercased, punctuation-stripped, source-suffix removed."""
    t = re.sub(r"\s+-\s+[^-]+$", "", title.strip())  # drop " - Reuters" style suffix
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def _epoch(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:  # ISO 8601 (yfinance content.pubDate)
        from datetime import datetime

        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except Exception:  # noqa: BLE001
        pass
    try:  # RFC 822 (RSS pubDate)
        return int(parsedate_to_datetime(str(value)).timestamp())
    except Exception:  # noqa: BLE001
        return None


def _yf_news(symbol: str) -> list[dict]:
    try:
        raw = _ticker(symbol).news or []
    except Exception:  # noqa: BLE001
        return []
    items = []
    for n in raw:
        content = n.get("content", n)
        title = content.get("title") or n.get("title")
        if not title:
            continue
        prov = content.get("provider")
        publisher = prov.get("displayName") if isinstance(prov, dict) else n.get("publisher")
        cu = content.get("canonicalUrl")
        link = cu.get("url") if isinstance(cu, dict) else n.get("link")
        published = _epoch(content.get("pubDate") or n.get("providerPublishTime"))
        items.append(
            {"title": title, "publisher": publisher or "Yahoo Finance", "link": link, "published": published}
        )
    return items


def _fetch_rss(url: str, default_source: str) -> list[dict]:
    try:
        r = httpx.get(url, timeout=4.0, headers={"User-Agent": _UA}, follow_redirects=True)
        root = ET.fromstring(r.content)
    except Exception:  # noqa: BLE001 - a dead/slow feed shouldn't break the aggregate
        return []
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        src_el = item.find("source")
        publisher = (src_el.text.strip() if src_el is not None and src_el.text else None) or default_source
        items.append(
            {
                "title": title,
                "publisher": publisher,
                "link": (item.findtext("link") or "").strip() or None,
                "published": _epoch(item.findtext("pubDate")),
            }
        )
    return items


def _yahoo_rss(symbol: str) -> list[dict]:
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
    return _fetch_rss(url, "Yahoo Finance")


def _gnews_rss(symbol: str) -> list[dict]:
    q = httpx.QueryParams({"q": f"{symbol} stock", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return _fetch_rss(f"https://news.google.com/rss/search?{q}", "Google News")


def _custom_source(name: str, url_tpl: str):
    """A fetcher for a user-defined RSS feed; `{symbol}` in the URL is filled per request."""
    def fetch(symbol: str) -> list[dict]:
        url = url_tpl.replace("{symbol}", quote_plus(symbol))
        return _fetch_rss(url, name or "Custom feed")
    return fetch
