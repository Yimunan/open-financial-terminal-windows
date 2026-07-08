# NOTE: Phase-2 helper. Fetch a FRED series via the keyless CSV endpoint (no API key):
#   https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>
# Fast-fail: once FRED times out (some egress paths block fred.stlouisfed.org), skip FRED for a
# cooldown window so the rest of a batch returns immediately instead of waiting out N timeouts —
# then self-heal, so one transient timeout can't poison the process forever (it used to: a daily
# refresh job that tripped the old permanent flag silently saved 0 series until restart). Rates
# fall back to yfinance (in app/services/data_refresh); macro is simply empty where FRED is blocked.
from __future__ import annotations

import io
import time

import pandas as pd

_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
_FRED_RETRY_S = 600.0  # cooldown after a failure: long enough to fast-fail a batch, short enough to recover
_fred_dead_until = 0.0


def fred_alive() -> bool:
    return time.monotonic() >= _fred_dead_until


def fred_series(series_id: str, http=None, timeout: float = 6.0) -> pd.Series:
    global _fred_dead_until
    if time.monotonic() < _fred_dead_until:
        return pd.Series(dtype=float)
    url = _URL.format(sid=series_id)
    try:
        if http is not None and hasattr(http, "get"):
            text = http.get(url, timeout=timeout).text
        else:
            import httpx

            text = httpx.get(url, timeout=timeout, follow_redirects=True).text
    except Exception:
        # egress blocked / FRED unreachable — fast-fail the rest of this batch, retry after cooldown
        _fred_dead_until = time.monotonic() + _FRED_RETRY_S
        return pd.Series(dtype=float)
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception:
        return pd.Series(dtype=float)
    if df.shape[1] < 2:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(df.iloc[:, 0], errors="coerce", utc=True)
    vals = pd.to_numeric(df.iloc[:, 1].replace(".", pd.NA), errors="coerce")
    s = pd.Series(vals.to_numpy(), index=idx)
    s = s[~s.index.isna()].dropna()
    return s.sort_index()
