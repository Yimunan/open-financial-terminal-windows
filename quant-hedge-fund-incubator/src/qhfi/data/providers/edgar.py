# NOTE: Phase-2 reconstruction. SEC EDGAR client over the keyless data.sec.gov JSON APIs
# (User-Agent required by SEC fair-access policy — set SEC_USER_AGENT). Implements the recent
# filings feed contract used by app/services/filings.py: ticker_to_cik + list_filings -> Filing.
from __future__ import annotations

import os
from dataclasses import dataclass

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"


@dataclass
class Filing:
    form: str
    filing_date: str
    report_date: str
    accession: str
    primary_document: str
    cik: int


class EdgarClient:
    def __init__(self, user_agent=None, *args, **kwargs):
        self.user_agent = (
            user_agent
            or os.getenv("SEC_USER_AGENT")
            or "open-financial-terminal research@example.com"
        )
        self._ticker_map = None

    def _client(self):
        import httpx

        return httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
        )

    def _load_ticker_map(self):
        # Cache only a successfully fetched, non-empty map. A failed fetch must leave the map
        # unloaded so the NEXT call retries — caching {} here latched every later lookup into
        # "no CIK" until process restart (the shared @lru_cache client made that app-wide).
        if self._ticker_map:
            return
        m = {}
        try:
            with self._client() as c:
                data = c.get(_TICKERS_URL).json()
            for row in data.values():
                m[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
        except Exception:
            return  # transient failure — stay unloaded, retry on the next lookup
        self._ticker_map = m or None

    def ticker_to_cik(self, ticker):
        self._load_ticker_map()
        cik = (self._ticker_map or {}).get(str(ticker).upper())
        if not cik:
            raise KeyError(f"no CIK for ticker {ticker}")
        return cik

    def list_filings(self, cik, forms=None, limit=300):
        cik10 = str(cik).zfill(10)
        with self._client() as c:
            data = c.get(_SUBMISSIONS_URL.format(cik10=cik10)).json()
        recent = data.get("filings", {}).get("recent", {})
        form_l = recent.get("form", [])
        fdate = recent.get("filingDate", [])
        rdate = recent.get("reportDate", [])
        acc = recent.get("accessionNumber", [])
        doc = recent.get("primaryDocument", [])
        want = {str(f).upper() for f in forms} if forms else None
        cik_int = int(cik10)
        out = []
        for i in range(len(form_l)):
            form = form_l[i]
            if want and str(form).upper() not in want:
                continue
            out.append(
                Filing(
                    form=form,
                    filing_date=fdate[i] if i < len(fdate) else "",
                    report_date=(rdate[i] if i < len(rdate) else "") or "",
                    accession=acc[i] if i < len(acc) else "",
                    primary_document=doc[i] if i < len(doc) else "",
                    cik=cik_int,
                )
            )
            if len(out) >= limit:
                break
        return out
