"""Offline tests for the macro service (services.macro) + its two thin router contracts.

The substance under test is the read-only shaping logic the Macro module depends on:
series labeling (FRED map vs parsed World Bank id vs verbatim fallback), the coarse
frequency inference from median observation spacing, the inclusive + tz-tolerant date
slice, the latest-value / change / change_pct math (with its 1-obs and zero-prev edges),
the headline-grid card assembly, and the Treasury-curve serializer's tenor ordering /
unknown-column passthrough / NaN→None contract.

Hermetic: synthetic pandas Series/DataFrames and a FakeMacroStore/FakeRatesStore — no
network, no parquet lake. The two router tests use TestClient(app) with
app.dependency_overrides (try/finally pop, per test_listings.py) so nothing touches deps.

Run: `cd backend && pytest tests/test_macro.py -v`
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.deps import get_macro_store, get_rates_store
from app.main import app
from app.services import macro as mc


# ── fakes ────────────────────────────────────────────────────────────────────────

class FakeMacroStore:
    """Minimal MacroStore: a dict of {series_id: pd.Series} with .has/.load/.catalog."""

    def __init__(self, data: dict[str, pd.Series]):
        self._data = data

    def has(self, series_id: str) -> bool:
        return series_id in self._data

    def load(self, series_id: str) -> pd.Series:
        return self._data[series_id]

    def catalog(self) -> pd.DataFrame:
        rows = []
        for sid, s in self._data.items():
            rows.append({
                "series": sid,
                "obs": int(len(s)),
                "start": s.index[0].date() if len(s) else None,
                "end": s.index[-1].date() if len(s) else None,
            })
        return pd.DataFrame(rows)


class FakeRatesStore:
    """Minimal RatesStore: one wide DataFrame keyed 'treasury_curve'."""

    def __init__(self, curve: pd.DataFrame):
        self._curve = curve

    def has(self, key: str) -> bool:
        return key == "treasury_curve"

    def load(self, key: str) -> pd.DataFrame:
        return self._curve


def _series(values, freq="MS", tz=None) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=len(values), freq=freq, tz=tz)
    return pd.Series(values, index=idx, dtype="float64")


# ── _label ───────────────────────────────────────────────────────────────────────

def test_label_fred_worldbank_and_unknown():
    # FRED id resolves via qhfi's MACRO_SERIES map (kept in sync with the engine).
    assert mc._label("CPIAUCSL") == mc.MACRO_SERIES["CPIAUCSL"]
    # World Bank id is parsed into "<country> · <indicator>" (middot separator).
    assert mc._label("WB_US_gdp_growth") == "United States · GDP growth (%)"
    # Unknown country / indicator codes fall back to the raw code on each side.
    assert mc._label("WB_ZZ_made_up") == "ZZ · made_up"
    # Anything else is returned verbatim.
    assert mc._label("TOTALLY_UNKNOWN") == "TOTALLY_UNKNOWN"


# ── _group ───────────────────────────────────────────────────────────────────────

def test_group_classifies_worldbank_vs_us():
    assert mc._group("WB_US_gdp_growth") == "cross_country"
    assert mc._group("CPIAUCSL") == "us"
    assert mc._group("anything_else") == "us"


# ── _frequency_hint ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "freq, periods, expected",
    [
        ("D", 10, "daily"),       # 1-day spacing
        ("MS", 10, "monthly"),    # ~30-day spacing
        ("QS", 10, "quarterly"),  # ~91-day spacing
        ("YS", 10, "annual"),     # ~365-day spacing
    ],
)
def test_frequency_hint_by_spacing(freq, periods, expected):
    idx = pd.date_range("2010-01-01", periods=periods, freq=freq)
    assert mc._frequency_hint(idx) == expected


def test_frequency_hint_too_few_obs_is_unknown():
    idx = pd.date_range("2010-01-01", periods=2, freq="MS")  # < 3 obs
    assert mc._frequency_hint(idx) == "unknown"


# ── _slice ────────────────────────────────────────────────────────────────────────

def test_slice_inclusive_and_tz_tolerant():
    from datetime import date

    s = _series([1.0, 2.0, 3.0, 4.0, 5.0], freq="MS")  # 2020-01..2020-05
    # bounds are inclusive on both ends
    out = mc._slice(s, date(2020, 2, 1), date(2020, 4, 1))
    assert [ts.date().isoformat() for ts in out.index] == ["2020-02-01", "2020-03-01", "2020-04-01"]
    assert list(out.values) == [2.0, 3.0, 4.0]

    # a tz-AWARE index with naive date bounds must NOT raise (FRED serves UTC-stamped data);
    # the localized bound is applied and the same inclusive window comes back.
    s_tz = _series([1.0, 2.0, 3.0, 4.0, 5.0], freq="MS", tz="UTC")
    out_tz = mc._slice(s_tz, date(2020, 2, 1), date(2020, 4, 1))
    assert [ts.date().isoformat() for ts in out_tz.index] == ["2020-02-01", "2020-03-01", "2020-04-01"]

    # None bounds pass the series through unchanged on that side
    assert len(mc._slice(s, None, None)) == 5
    assert list(mc._slice(s, date(2020, 4, 1), None).values) == [4.0, 5.0]


# ── _latest ───────────────────────────────────────────────────────────────────────

def test_latest_change_and_edge_cases():
    # normal: change + pct vs prior observation
    s = _series([100.0, 110.0], freq="MS")
    out = mc._latest(s)
    assert out["value"] == 110.0
    assert out["prev"] == 100.0
    assert out["change"] == pytest.approx(10.0)
    assert out["change_pct"] == pytest.approx(10.0)
    assert out["date"] == "2020-02-01"

    # single observation: no prior → prev/change/change_pct all None
    one = _series([42.0], freq="MS")
    out1 = mc._latest(one)
    assert out1["value"] == 42.0 and out1["prev"] is None
    assert out1["change"] is None and out1["change_pct"] is None

    # prev == 0 → change is defined but change_pct guards against div-by-zero → None
    zero_prev = _series([0.0, 5.0], freq="MS")
    outz = mc._latest(zero_prev)
    assert outz["change"] == pytest.approx(5.0) and outz["change_pct"] is None

    # empty → everything None
    empty = pd.Series([], dtype="float64", index=pd.DatetimeIndex([]))
    oute = mc._latest(empty)
    assert all(oute[k] is None for k in ("value", "date", "prev", "change", "change_pct"))

    # NaN tail is dropped before computing latest (dropna inside _latest)
    nan_tail = pd.Series(
        [10.0, 20.0, float("nan")],
        index=pd.date_range("2020-01-01", periods=3, freq="MS"),
    )
    outn = mc._latest(nan_tail)
    assert outn["value"] == 20.0 and outn["prev"] == 10.0


# ── grid ──────────────────────────────────────────────────────────────────────────

def test_grid_emits_card_per_series():
    # two of the GRID_SERIES present (one with a NaN-only / empty series that must be skipped),
    # plus a non-grid series that must never appear.
    present = mc.GRID_SERIES[0]   # CPIAUCSL
    present2 = mc.GRID_SERIES[2]  # UNRATE
    empty_grid = mc.GRID_SERIES[3]  # FEDFUNDS — present but all-NaN → skipped
    store = FakeMacroStore({
        present: _series([1.0, 2.0, 3.0, 4.0], freq="MS"),
        present2: _series([5.0, 5.5], freq="MS"),
        empty_grid: pd.Series(
            [float("nan"), float("nan")],
            index=pd.date_range("2020-01-01", periods=2, freq="MS"),
        ),
        "NOT_A_GRID_SERIES": _series([9.0, 9.0], freq="MS"),
    })

    out = mc.grid(store, spark_points=2)
    ids = [c["id"] for c in out["cards"]]
    assert ids == [present, present2]  # only present, non-empty GRID_SERIES, in GRID_SERIES order
    card = out["cards"][0]
    assert set(card) == {"id", "label", "frequency_hint", "latest", "spark"}
    assert card["label"] == mc._label(present)
    assert card["latest"]["value"] == 4.0
    # spark is capped at spark_points (2) — the most recent closes
    assert card["spark"] == [3.0, 4.0]


# ── rates_curve ────────────────────────────────────────────────────────────────────

def test_rates_curve_orders_tenors_and_keeps_unknown_columns():
    from datetime import date

    # columns deliberately OUT of maturity order, plus an unknown tenor "XYZ" and a NaN cell.
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    curve = pd.DataFrame(
        {
            "10Y": [4.0, 4.1, 4.2],
            "3M": [5.0, 5.1, 5.2],
            "1Y": [4.5, 4.6, float("nan")],  # NaN in the latest row for 1Y
            "XYZ": [1.0, 1.1, 1.2],          # unknown column — must be appended, never dropped
        },
        index=idx,
    )
    store = FakeRatesStore(curve)

    out = mc.rates_curve(store, None, None)
    # known tenors come back in TENOR_ORDER, unknown columns appended after
    assert out["tenors"] == ["3M", "1Y", "10Y", "XYZ"]

    latest = out["latest"]
    assert latest["date"] == "2024-01-03"
    pts = {p["tenor"]: p for p in latest["points"]}
    # years come from TENOR_YEARS; unknown tenor has no mapping → None
    assert pts["3M"]["years"] == mc.TENOR_YEARS["3M"]
    assert pts["10Y"]["years"] == mc.TENOR_YEARS["10Y"]
    assert pts["XYZ"]["years"] is None
    # NaN in the latest row → None, not NaN
    assert pts["1Y"]["value"] is None
    assert pts["3M"]["value"] == pytest.approx(5.2)

    # history rows carry every tenor, NaN → None
    assert len(out["rows"]) == 3
    assert out["rows"][2]["rates"]["1Y"] is None
    assert out["rows"][0]["rates"]["3M"] == pytest.approx(5.0)

    # the inclusive slice narrows the history (and does not touch the latest curve)
    out2 = mc.rates_curve(store, date(2024, 1, 2), date(2024, 1, 3))
    assert [r["date"] for r in out2["rows"]] == ["2024-01-02", "2024-01-03"]
    assert out2["latest"]["date"] == "2024-01-03"


# ── router: series 404 when missing ─────────────────────────────────────────────────

def test_macro_series_endpoint_404_for_missing():
    store = FakeMacroStore({})  # store.has(...) is always False
    app.dependency_overrides[get_macro_store] = lambda: store
    try:
        client = TestClient(app)
        r = client.get("/api/macro/series/NOPE")
        assert r.status_code == 404
        assert "not in the macro lake" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_macro_store, None)


def test_macro_series_endpoint_returns_observations_when_present():
    sid = "CPIAUCSL"
    store = FakeMacroStore({sid: _series([100.0, 101.0, 102.0], freq="MS")})
    app.dependency_overrides[get_macro_store] = lambda: store
    try:
        client = TestClient(app)
        r = client.get(f"/api/macro/series/{sid}")
        assert r.status_code == 200
        body = r.json()
        assert body["series_id"] == sid
        assert body["label"] == mc._label(sid)
        assert [o["value"] for o in body["observations"]] == [100.0, 101.0, 102.0]
    finally:
        app.dependency_overrides.pop(get_macro_store, None)


# ── router: grid shape ──────────────────────────────────────────────────────────────

def test_macro_grid_endpoint_shape():
    present = mc.GRID_SERIES[0]
    store = FakeMacroStore({present: _series([1.0, 2.0, 3.0], freq="MS")})
    app.dependency_overrides[get_macro_store] = lambda: store
    try:
        client = TestClient(app)
        r = client.get("/api/macro/grid")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"cards"}
        assert isinstance(body["cards"], list)
        assert [c["id"] for c in body["cards"]] == [present]
        card = body["cards"][0]
        assert {"id", "label", "frequency_hint", "latest", "spark"} <= set(card)
    finally:
        app.dependency_overrides.pop(get_macro_store, None)
