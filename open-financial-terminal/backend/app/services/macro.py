"""Macro data service.

Read-only helpers over the qhfi parquet lake's `macro` (one indicator series per file) and
`rates` (wide Treasury yield curve) categories. The data is produced by qhfi's pull scripts;
the terminal just slices, labels, and serializes it for the Macro module. Series labels reuse
qhfi's own ``MACRO_SERIES`` map so they stay in sync with the engine.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from qhfi.data.macro import MacroStore
from qhfi.data.providers.macro import MACRO_SERIES
from qhfi.data.rates import RatesStore

# Eight headline US indicators for the at-a-glance grid (those reliably in the lake).
GRID_SERIES: list[str] = [
    "CPIAUCSL", "GDPC1", "UNRATE", "FEDFUNDS",
    "PAYEMS", "M2SL", "INDPRO", "UMCSENT",
]

# Treasury tenors in maturity order (the lake may hold a 4- or 11-tenor curve depending on
# whether FRED or the yfinance fallback was used). YEARS gives each a numeric x for the curve plot.
TENOR_ORDER: list[str] = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
TENOR_YEARS: dict[str, float] = {
    "1M": 1 / 12, "3M": 0.25, "6M": 0.5, "1Y": 1, "2Y": 2, "3Y": 3,
    "5Y": 5, "7Y": 7, "10Y": 10, "20Y": 20, "30Y": 30,
}

# World Bank cross-country panel: WB_<CC>_<indicator>.
WB_COUNTRIES: dict[str, str] = {
    "US": "United States", "CN": "China", "DE": "Germany", "JP": "Japan", "GB": "United Kingdom",
    "IN": "India", "BR": "Brazil", "FR": "France", "CA": "Canada", "KR": "South Korea",
}
WB_INDICATORS: dict[str, str] = {
    "gdp_growth": "GDP growth (%)",
    "inflation": "Inflation (%)",
    "unemployment": "Unemployment (%)",
    "govt_debt_pct_gdp": "Govt debt (% GDP)",
    "current_account_pct_gdp": "Current account (% GDP)",
}


def _label(series_id: str) -> str:
    """Human label: qhfi's FRED map for US series, parsed country+indicator for World Bank ones."""
    if series_id in MACRO_SERIES:
        return MACRO_SERIES[series_id]
    if series_id.startswith("WB_"):
        _, cc, ind = series_id.split("_", 2)
        return f"{WB_COUNTRIES.get(cc, cc)} · {WB_INDICATORS.get(ind, ind)}"
    return series_id


def _group(series_id: str) -> str:
    if series_id.startswith("WB_"):
        return "cross_country"
    return "us"


def _frequency_hint(idx: pd.Index) -> str:
    """Coarse native frequency from the median spacing of observations (for axis formatting)."""
    if len(idx) < 3:
        return "unknown"
    days = pd.Series(idx).diff().dt.days.dropna().median()
    if days <= 3:
        return "daily"
    if days <= 45:
        return "monthly"
    if days <= 150:
        return "quarterly"
    return "annual"


def _slice(s, start: date | None, end: date | None):
    """Date-slice a Series or DataFrame, tolerating a tz-aware index (FRED serves UTC-stamped)."""
    tz = getattr(s.index, "tz", None)

    def bound(d: date) -> pd.Timestamp:
        ts = pd.Timestamp(d)
        return ts.tz_localize(tz) if tz is not None and ts.tzinfo is None else ts

    if start is not None:
        s = s[s.index >= bound(start)]
    if end is not None:
        s = s[s.index <= bound(end)]
    return s


def _observations(s: pd.Series) -> list[dict]:
    return [
        {"date": ts.date().isoformat(), "value": None if pd.isna(v) else float(v)}
        for ts, v in s.items()
    ]


def catalog(store: MacroStore) -> dict:
    """Every series in the lake with coverage + label + group, for the explorer dropdown."""
    cat = store.catalog()
    series = []
    for _, row in cat.iterrows():
        sid = row["series"]
        series.append({
            "id": sid,
            "label": _label(sid),
            "group": _group(sid),
            "obs": int(row["obs"]),
            "start": row["start"].isoformat() if row["start"] is not None else None,
            "end": row["end"].isoformat() if row["end"] is not None else None,
        })
    series.sort(key=lambda r: (r["group"], r["label"]))
    return {"series": series}


def series(store: MacroStore, series_id: str, start: date | None, end: date | None) -> dict:
    s = _slice(store.load(series_id).dropna(), start, end)
    return {
        "series_id": series_id,
        "label": _label(series_id),
        "frequency_hint": _frequency_hint(s.index),
        "observations": _observations(s),
    }


def _latest(s: pd.Series) -> dict:
    """Last value + absolute / pct change vs the prior observation."""
    s = s.dropna()
    if s.empty:
        return {"value": None, "date": None, "prev": None, "change": None, "change_pct": None}
    value = float(s.iloc[-1])
    last_date = s.index[-1].date().isoformat()
    if len(s) < 2:
        return {"value": value, "date": last_date, "prev": None, "change": None, "change_pct": None}
    prev = float(s.iloc[-2])
    change = value - prev
    change_pct = (change / prev * 100.0) if prev != 0 else None
    return {"value": value, "date": last_date, "prev": prev, "change": change, "change_pct": change_pct}


def grid(store: MacroStore, spark_points: int = 36) -> dict:
    """Latest value + change + a short recent sparkline for each headline US indicator."""
    cards = []
    for sid in GRID_SERIES:
        if not store.has(sid):
            continue
        s = store.load(sid).dropna()
        if s.empty:
            continue
        spark = [float(v) for v in s.iloc[-spark_points:].tolist()]
        cards.append({
            "id": sid,
            "label": _label(sid),
            "frequency_hint": _frequency_hint(s.index),
            "latest": _latest(s),
            "spark": spark,
        })
    return {"cards": cards}


def rates_curve(store: RatesStore, start: date | None, end: date | None) -> dict:
    """Treasury curve: maturity-ordered tenors, the latest curve, and a date×rates history."""
    curve = store.load("treasury_curve")
    tenors = [t for t in TENOR_ORDER if t in curve.columns]
    # any unexpected columns appended after the known order, so nothing is silently dropped
    tenors += [c for c in curve.columns if c not in tenors]

    latest_row = curve.dropna(how="all").iloc[-1] if not curve.dropna(how="all").empty else None
    latest = {
        "date": curve.dropna(how="all").index[-1].date().isoformat() if latest_row is not None else None,
        "points": [
            {"tenor": t, "years": TENOR_YEARS.get(t), "value": None if latest_row is None or pd.isna(latest_row[t]) else float(latest_row[t])}
            for t in tenors
        ],
    }

    hist = _slice(curve, start, end)
    rows = [
        {"date": ts.date().isoformat(), "rates": {t: (None if pd.isna(r[t]) else float(r[t])) for t in tenors}}
        for ts, r in hist.iterrows()
    ]
    return {"tenors": tenors, "latest": latest, "rows": rows}


def cross_country(store: MacroStore, indicator: str, start: date | None, end: date | None) -> dict:
    """All WB_<country>_<indicator> series for one indicator, one entry per country."""
    countries = []
    for cc, name in WB_COUNTRIES.items():
        sid = f"WB_{cc}_{indicator}"
        if not store.has(sid):
            continue
        s = _slice(store.load(sid).dropna(), start, end)
        if s.empty:
            continue
        countries.append({"country": cc, "name": name, "observations": _observations(s)})
    return {
        "indicator": indicator,
        "label": WB_INDICATORS.get(indicator, indicator),
        "countries": countries,
    }
