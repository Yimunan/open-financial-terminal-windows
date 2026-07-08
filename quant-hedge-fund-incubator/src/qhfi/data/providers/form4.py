# NOTE: Phase-2 partial. InsiderClient lists a company's Form 3/4/5 filings via EdgarClient.
# Parsing the ownership XML into per-transaction rows (fetch_transactions) is not implemented in
# Phase 2b — it returns empty so the insider view degrades gracefully with a coverage note.
from __future__ import annotations

import pandas as pd

_INSIDER_FORMS = {"3", "4", "5", "3/A", "4/A", "5/A"}


class InsiderClient:
    def __init__(self, edgar=None, *args, **kwargs):
        self.edgar = edgar

    def list_insider(self, cik, limit=60):
        if self.edgar is None:
            return []
        try:
            filings = self.edgar.list_filings(cik, forms=None, limit=400)
        except Exception:
            return []
        return [f for f in filings if str(getattr(f, "form", "")).upper() in _INSIDER_FORMS][:limit]

    def fetch_transactions(self, filing, *a, **k) -> pd.DataFrame:
        # Form 4 ownership-XML parsing is out of scope for Phase 2b.
        return pd.DataFrame()
