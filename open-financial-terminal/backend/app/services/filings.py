"""Public filings service — SEC EDGAR, live with cache-to-lake.

Three views, all keyed off the active ticker:

* ``feed``    — the chronological list of a company's recent SEC filings (every form type),
                classified into category chips, each linking to the document on SEC.gov.
* ``insider`` — parsed Form 4 insider transactions + a trailing net-buy/sell summary.
* ``holders`` — top institutional (13F) holders, from the qhfi lake via the CUSIP crosswalk.

Everything is fetched live from EDGAR through qhfi's rate-limited ``EdgarClient`` and persisted
into the lake (feed snapshot + parsed insider transactions) so repeat views work offline. Every
entry point is defensive: a failure returns an empty payload + a ``coverage`` note rather than
500-ing the widget.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

# Category chips → the form types they collect. Order matters: the first matching category wins.
# "insider" is matched by an exact set (so 424B-style "offerings" don't get swept up by a "4"
# prefix); the rest match by form prefix.
_INSIDER_FORMS = {"3", "4", "5", "3/A", "4/A", "5/A"}
_CATEGORY_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("financials", ("10-K", "10-Q", "20-F", "40-F")),
    ("events", ("8-K", "6-K")),
    ("ownership", ("SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G", "13F")),
    ("governance", ("DEF 14A", "DEFA14A", "DEFM14A", "PRE 14A")),
    ("offerings", ("S-1", "S-3", "S-4", "424B", "FWP", "144")),
]

_FORM_LABELS = {
    "10-K": "Annual report", "10-Q": "Quarterly report", "8-K": "Current report",
    "20-F": "Annual report (foreign)", "6-K": "Foreign current report",
    "4": "Insider transaction", "3": "Initial insider ownership", "5": "Annual insider ownership",
    "SC 13D": "Activist 5%+ ownership", "SC 13G": "Passive 5%+ ownership",
    "13F-HR": "Institutional holdings", "DEF 14A": "Proxy statement",
    "S-1": "Registration", "S-3": "Shelf registration",
    "424B2": "Prospectus", "424B3": "Prospectus", "424B5": "Prospectus",
    "DEFA14A": "Proxy soliciting material", "DEFM14A": "Merger proxy",
    "PRE 14A": "Preliminary proxy", "11-K": "Employee benefit plan report",
    "NT 10-K": "Late filing notice", "NT 10-Q": "Late filing notice",
    "S-8": "Employee stock registration", "S-4": "M&A registration",
    "SD": "Specialized disclosure", "CORRESP": "SEC correspondence",
    "UPLOAD": "SEC staff letter", "144": "Proposed insider sale",
    "FWP": "Free writing prospectus", "25-NSE": "Delisting notice",
}


def category_of(form: str) -> str:
    f = (form or "").upper()
    if f in _INSIDER_FORMS:
        return "insider"
    for cat, prefixes in _CATEGORY_PREFIXES:
        if any(f.startswith(p) for p in prefixes):
            return cat
    return "other"


def _epoch(date_str: str) -> int | None:
    """'YYYY-MM-DD' → unix seconds (UTC midnight), for the frontend's relative-time helper."""
    if not date_str:
        return None
    try:
        return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _feed_path(filings_store, ticker: str):
    return filings_store.data_dir / ticker.replace("/", "_").upper() / "_feed.json"


def _row(filing) -> dict:
    return {
        "form": filing.form,
        "label": (_FORM_LABELS.get(filing.form)
                  or _FORM_LABELS.get(filing.form.split("/")[0])
                  or filing.form),
        "category": category_of(filing.form),
        "filing_date": filing.filing_date,
        "report_date": filing.report_date or None,
        "filed": _epoch(filing.filing_date),
        "accession": filing.accession,
        "url": _document_url(filing),
    }


def _document_url(filing) -> str:
    acc = filing.accession.replace("-", "")
    doc = filing.primary_document or ""
    base = f"https://www.sec.gov/Archives/edgar/data/{filing.cik}/{acc}"
    return f"{base}/{doc}" if doc else f"{base}/"


def feed(edgar, filings_store, symbol: str, category: str | None = None, limit: int = 80) -> dict:
    """Recent filings for ``symbol`` (newest first), live with an offline lake snapshot.

    ``category`` (one of the chip keys) filters the list; ``None``/'all' returns everything.
    """
    symbol = symbol.upper()
    items: list[dict] = []
    coverage = "live"
    try:
        cik = edgar.ticker_to_cik(symbol)
        filings = edgar.list_filings(cik, forms=None)
        items = [_row(f) for f in filings]
        _write_feed_cache(filings_store, symbol, items)
    except Exception:  # noqa: BLE001 — fall back to the last cached snapshot when offline
        items = _read_feed_cache(filings_store, symbol)
        coverage = "cached" if items else "unavailable"

    if category and category not in ("all", ""):
        items = [it for it in items if it["category"] == category]
    return {"symbol": symbol, "coverage": coverage, "items": items[:limit]}


def _write_feed_cache(filings_store, symbol: str, items: list[dict]) -> None:
    try:
        p = _feed_path(filings_store, symbol)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(items), encoding="utf-8")
    except OSError:
        pass


def _read_feed_cache(filings_store, symbol: str) -> list[dict]:
    try:
        p = _feed_path(filings_store, symbol)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


# ── insider (Form 4) ──────────────────────────────────────────────────────────────
_MAX_INSIDER_FILINGS = 30   # bound the cold-start live pull so the first view stays responsive


def insider(edgar, insider_store, symbol: str, limit: int = 40) -> dict:
    """Parsed insider transactions for ``symbol`` + a trailing net buy/sell summary.

    Reads the lake first; if cold, does a bounded live pull of the most recent Form 3/4/5s and
    persists them. Returns an empty payload + coverage note if the issuer has no insider XML.
    """
    from qhfi.data.providers.form4 import InsiderClient

    symbol = symbol.upper()
    coverage = "lake"
    try:
        if not insider_store.has(symbol):
            coverage = "live"
            client = InsiderClient(edgar)
            cik = edgar.ticker_to_cik(symbol)
            for f in client.list_insider(cik)[:_MAX_INSIDER_FILINGS]:
                txns = client.fetch_transactions(f)
                if not txns.empty:
                    insider_store.save(symbol, txns)
        df = insider_store.load(symbol)
    except Exception:  # noqa: BLE001
        return {"symbol": symbol, "coverage": "unavailable", "summary": _empty_summary(), "items": []}

    if df is None or df.empty:
        return {"symbol": symbol, "coverage": coverage, "summary": _empty_summary(), "items": []}

    df = df.sort_values("txn_date", ascending=False)
    return {
        "symbol": symbol,
        "coverage": coverage,
        "summary": _insider_summary(df),
        "items": [_insider_item(r) for _, r in df.head(limit).iterrows()],
    }


def _insider_item(r) -> dict:
    return {
        "insider": r.get("insider") or "", "role": r.get("role") or "",
        "date": r.get("txn_date") or "", "filed": _epoch(str(r.get("filed") or "")),
        "code": r.get("code") or "", "acq_disp": r.get("acq_disp") or "",
        "shares": _f(r.get("shares")), "price": _f(r.get("price")),
        "value": _f(r.get("shares")) * _f(r.get("price")),
        "shares_after": _f(r.get("shares_after")),
        "security": r.get("security") or "", "derivative": bool(r.get("derivative")),
    }


def _empty_summary() -> dict:
    return {w: {"buy_shares": 0.0, "sell_shares": 0.0, "buy_value": 0.0, "sell_value": 0.0,
                "net_shares": 0.0, "net_value": 0.0, "n_buys": 0, "n_sells": 0}
            for w in ("d90", "m6")}


def _insider_summary(df: pd.DataFrame) -> dict:
    """Net open-market buys (code P) vs sales (code S) over trailing 90d / 6m windows."""
    now = datetime.now(timezone.utc)
    dates = pd.to_datetime(df["txn_date"], errors="coerce", utc=True)
    out = {}
    for key, days in (("d90", 90), ("m6", 182)):
        cutoff = now - pd.Timedelta(days=days)
        win = df[dates >= cutoff]
        buys = win[win["code"] == "P"]
        sells = win[win["code"] == "S"]
        bs, ss = float(buys["shares"].sum()), float(sells["shares"].sum())
        bv = float((buys["shares"] * buys["price"]).sum())
        sv = float((sells["shares"] * sells["price"]).sum())
        out[key] = {"buy_shares": bs, "sell_shares": ss, "buy_value": bv, "sell_value": sv,
                    "net_shares": bs - ss, "net_value": bv - sv,
                    "n_buys": int(len(buys)), "n_sells": int(len(sells))}
    return out


# ── institutional holders (13F) ────────────────────────────────────────────────────
def holders(holdings_store, cusip_store, symbol: str, top: int = 25) -> dict:
    """Top 13F institutional holders of ``symbol`` for its latest reported quarter."""
    symbol = symbol.upper()
    try:
        df = holdings_store.holders_of(symbol, cusip_store, top=top)
    except Exception:  # noqa: BLE001
        return {"symbol": symbol, "coverage": "unavailable", "period": None, "items": []}

    if df is None or df.empty:
        return {"symbol": symbol, "coverage": "none", "period": None, "items": []}

    period = str(df["period"].iloc[0])
    items = [{
        "manager": r["manager"], "shares": _f(r["shares"]), "value_usd": _f(r["value_usd"]),
        "pct_of_book": _f(r["pct_of_book"]), "change_shares": _f(r["change_shares"]),
        "change_pct": _f(r["change_pct"]),
    } for _, r in df.iterrows()]
    return {"symbol": symbol, "coverage": "lake", "period": period, "items": items}


def _f(v) -> float:
    try:
        f = float(v)
        return 0.0 if f != f else f  # NaN-safe
    except (TypeError, ValueError):
        return 0.0
