"""Newly-listed-security detection via SEC EDGAR full-text search (Method 3).

New listings surface in EDGAR as two form types:

* **8-A12B** — registration of a class of securities on a national exchange. This is the actual
  *listing* trigger, but it is broad: IPOs, ETF launches, new note series, spin-offs, and uplistings
  from OTC all file it.
* **424B4** — the final IPO prospectus (pricing). The cleanest signal for an operating-company IPO.

We query EDGAR full-text search (EFTS) for these forms over a recent window through qhfi's
rate-limited ``EdgarClient`` (SEC fair-access), parse company / ticker(s) / CIK / filing date, and
snapshot the result in the data dir so a repeat view works offline. Detection is best-effort: a
failure returns the last snapshot (or an empty payload) with a ``coverage`` note, never a 500.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Form → human label. Order also sets which forms we query.
FORM_KINDS: dict[str, str] = {
    "8-A12B": "Exchange listing",
    "424B4": "IPO prospectus",
}

# "ACME CORP  (ACME, ACME.W)  (CIK 0001234567)" → name + optional ticker group before the CIK group.
_DISPLAY_RE = re.compile(r"^(?P<name>.*?)\s*(?:\((?P<tickers>[^)]*)\)\s*)?\(CIK\s*\d+\)\s*$")

_SNAPSHOT = "listings_new.json"
_PAGE_CAP = 1000  # safety bound on EFTS pagination per form


def _epoch(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def _parse_display(display: str) -> tuple[str, list[str]]:
    """'NAME  (TICK1, TICK2)  (CIK …)' → ('NAME', ['TICK1','TICK2']); no ticker group → []."""
    m = _DISPLAY_RE.match(display or "")
    if not m:
        return (display or "").strip(), []
    name = (m.group("name") or "").strip()
    raw = m.group("tickers") or ""
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return name, tickers


def _doc_url(cik: str, accession: str, hit_id: str) -> str:
    acc = accession.replace("-", "")
    doc = hit_id.split(":", 1)[1] if ":" in hit_id else ""
    try:
        cik_int = int(cik)
    except (TypeError, ValueError):
        return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}"
    return f"{base}/{doc}" if doc else f"{base}/"


def _record(hit: dict) -> dict | None:
    src = hit.get("_source", {}) if isinstance(hit, dict) else {}
    ciks = src.get("ciks") or []
    cik = ciks[0] if ciks else ""
    accession = src.get("adsh") or (hit.get("_id", "").split(":", 1)[0])
    if not accession:
        return None
    form = src.get("file_type") or (src.get("root_forms") or [""])[0] or ""
    name, tickers = _parse_display((src.get("display_names") or [""])[0])
    filing_date = src.get("file_date")
    return {
        "form": form,
        "kind": FORM_KINDS.get(form, form),
        "company": name,
        "tickers": tickers,
        "cik": str(cik),
        "filing_date": filing_date,
        "filed": _epoch(filing_date),
        "accession": accession,
        "url": _doc_url(str(cik), accession, hit.get("_id", "")),
    }


def _snapshot_path(data_dir: Path) -> Path:
    return data_dir / _SNAPSHOT


def _write_cache(data_dir: Path, items: list[dict]) -> None:
    try:
        p = _snapshot_path(data_dir)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(items), encoding="utf-8")
    except OSError:
        pass


def _read_cache(data_dir: Path) -> list[dict]:
    try:
        p = _snapshot_path(data_dir)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def search_cached(data_dir: Path, query: str) -> list[dict]:
    """Symbol/company matches from the cached new-listings snapshot, shaped as search hits.

    Lets the global search box surface freshly listed tickers that aren't in any static universe
    YAML. Matches the (uppercased) query against each ticker or the company name. Reads only the
    on-disk snapshot — never hits EDGAR — so it stays fast enough for per-keystroke search.
    """
    q = query.strip().upper()
    if not q:
        return []
    out: dict[str, dict] = {}
    for r in _read_cache(data_dir):
        company = r.get("company") or ""
        name_match = q in company.upper()
        for tk in r.get("tickers", []):
            if tk in out:
                continue
            if q in tk.upper() or name_match:
                out[tk] = {"symbol": tk, "asset": "equity", "sector": None,
                           "universe": "new listing", "name": company}
    return list(out.values())


def new_listings(
    edgar,
    data_dir: Path,
    days: int = 14,
    forms: tuple[str, ...] = ("8-A12B", "424B4"),
    limit: int = 200,
    with_ticker_only: bool = True,
) -> dict:
    """Newly listed securities filed in the last ``days``, newest first.

    ``with_ticker_only`` keeps only filings whose issuer has a ticker (drops note registrations and
    ticker-less fund share classes — a light filter toward actual listed stocks/ETFs). Note: 8-A12B
    still includes ETFs/uplistings, so the UI labels each row by ``kind``/``form``.
    """
    end = date.today()
    start = end - timedelta(days=days)
    by_accession: dict[str, dict] = {}
    coverage = "live"
    try:
        for form in forms:
            frm = 0
            while frm < _PAGE_CAP:
                res = edgar.full_text_search([form], start.isoformat(), end.isoformat(), frm=frm)
                hits = res.get("hits", [])
                if not hits:
                    break
                for hit in hits:
                    rec = _record(hit)
                    if rec and rec["accession"] not in by_accession:
                        by_accession[rec["accession"]] = rec
                frm += len(hits)
                if len(hits) < 100 or frm >= int(res.get("total", 0)):
                    break
        items = list(by_accession.values())
        if with_ticker_only:
            items = [r for r in items if r["tickers"]]
        items.sort(key=lambda r: (r["filing_date"] or "", r["company"]), reverse=True)
        items = items[:limit]
        _write_cache(data_dir, items)
    except Exception:  # noqa: BLE001 — fall back to the last snapshot rather than 500 the widget
        items = _read_cache(data_dir)
        coverage = "cached" if items else "unavailable"
    return {"days": days, "coverage": coverage, "count": len(items), "items": items}
