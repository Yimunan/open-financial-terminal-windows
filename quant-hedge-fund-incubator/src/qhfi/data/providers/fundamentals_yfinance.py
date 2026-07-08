# NOTE: Phase-2 reconstruction. PIT fundamentals via yfinance quarterly statements, with an
# .info snapshot fallback so cross-sectional screens always have a current value. Metrics:
# eps_ttm (rolling 4Q diluted EPS), book_value_per_share, roe (TTM NI / equity), gross_margin.
from __future__ import annotations

from datetime import date

import pandas as pd

from ..base import DataProvider

_SNAPSHOT_KEY = {
    "eps_ttm": "trailingEps",
    "book_value_per_share": "bookValue",
    "roe": "returnOnEquity",
    "gross_margin": "grossMargins",
}


def _stmt(ticker, kind):
    attrs = {
        "income": ["quarterly_income_stmt", "quarterly_financials"],
        "balance": ["quarterly_balance_sheet"],
    }[kind]
    for a in attrs:
        try:
            df = getattr(ticker, a, None)
        except Exception:
            df = None
        if df is not None and not df.empty:
            return df.T  # -> index = period-end dates, columns = line items
    return None


def _row(frame, *names):
    if frame is None:
        return None
    for n in names:
        if n in frame.columns:
            s = pd.to_numeric(frame[n], errors="coerce").dropna()
            if len(s):
                return s.sort_index()
    return None


class YFinanceFundamentalsProvider(DataProvider):
    def fetch(self, instrument, metric, span=None, *a, **k) -> pd.Series:
        import yfinance as yf

        symbol = getattr(instrument, "id", instrument)
        try:
            t = yf.Ticker(symbol)
        except Exception:
            return pd.Series(dtype=float)

        s = self._metric_series(t, metric)
        if s is None or len(s) == 0:
            return pd.Series(dtype=float)
        try:
            s.index = pd.to_datetime(s.index, utc=True)
        except Exception:
            return pd.Series(dtype=float)
        s = s[~s.index.duplicated(keep="last")].sort_index().dropna()
        if span is not None and len(s):
            try:
                s = s[s.index >= pd.Timestamp(str(span.start), tz="UTC")]
            except Exception:
                pass
        return s

    # provider protocol alias used by some callers
    def get(self, instrument, metric, span=None, *a, **k):
        return self.fetch(instrument, metric, span)

    def _metric_series(self, t, metric):
        inc = _stmt(t, "income")
        bal = _stmt(t, "balance")
        s = None
        if metric == "eps_ttm":
            eps = _row(inc, "Diluted EPS", "Basic EPS")
            if eps is not None:
                s = eps.rolling(4, min_periods=1).sum()
        elif metric == "book_value_per_share":
            eq = _row(bal, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity")
            sh = _row(bal, "Diluted Average Shares", "Ordinary Shares Number", "Share Issued", "Basic Average Shares")
            if eq is not None and sh is not None:
                df = pd.concat([eq.rename("eq"), sh.rename("sh")], axis=1).dropna()
                if len(df):
                    s = df["eq"] / df["sh"].replace(0, pd.NA)
            elif eq is not None:
                so = (self._info(t) or {}).get("sharesOutstanding")
                if so:
                    s = eq / float(so)
        elif metric == "roe":
            ni = _row(inc, "Net Income", "Net Income Common Stockholders", "Net Income Continuous Operations")
            eq = _row(bal, "Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity")
            if ni is not None and eq is not None:
                ni_ttm = ni.rolling(4, min_periods=1).sum()
                df = pd.concat([ni_ttm.rename("ni"), eq.rename("eq")], axis=1).dropna()
                if len(df):
                    s = df["ni"] / df["eq"].replace(0, pd.NA)
        elif metric == "gross_margin":
            gp = _row(inc, "Gross Profit")
            rev = _row(inc, "Total Revenue", "Operating Revenue")
            if gp is not None and rev is not None:
                df = pd.concat([gp.rename("gp"), rev.rename("rev")], axis=1).dropna()
                if len(df):
                    s = df["gp"] / df["rev"].replace(0, pd.NA)

        if s is None or len(s.dropna()) == 0:
            s = self._snapshot(t, metric)
        return s

    @staticmethod
    def _info(t):
        try:
            return t.info or {}
        except Exception:
            return {}

    def _snapshot(self, t, metric):
        key = _SNAPSHOT_KEY.get(metric)
        v = self._info(t).get(key) if key else None
        if v is None:
            return pd.Series(dtype=float)
        try:
            return pd.Series([float(v)], index=pd.to_datetime([date.today()]))
        except Exception:
            return pd.Series(dtype=float)
