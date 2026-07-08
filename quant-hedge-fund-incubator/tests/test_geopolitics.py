"""Offline tests for the geopolitics domain: GeoRiskStore + GprProvider + GdeltProvider.

No network: the GPR workbook is built in-memory and fed via a fake http client; the GDELT JSON
is served through an httpx.MockTransport.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from qhfi.data.geopolitics import GeoRiskStore
from qhfi.data.providers.gdelt import GdeltProvider
from qhfi.data.providers.gpr import GprProvider


# ── fixtures ────────────────────────────────────────────────────────────────────
def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


class _FakeResp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _FakeHttp:
    """Stand-in for httpx.Client.get returning canned workbook bytes."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def get(self, _url):
        return _FakeResp(self._content)


# ── GeoRiskStore ────────────────────────────────────────────────────────────────
def test_georisk_store_roundtrip_and_catalog(tmp_path):
    store = GeoRiskStore(tmp_path)
    s = pd.Series([100.0, 120.0, 110.0],
                  index=pd.to_datetime(["1985-01-01", "1985-02-01", "1985-03-01"]))
    store.save("gpr_monthly", s)

    assert store._path("gpr_monthly").parts[-3:] == ("geopolitics", "risk", "gpr_monthly.parquet")
    assert store.has("gpr_monthly")
    loaded = store.load("gpr_monthly")
    assert loaded.tolist() == [100.0, 120.0, 110.0]
    cat = store.catalog()
    assert cat.iloc[0]["series"] == "gpr_monthly" and cat.iloc[0]["obs"] == 3


def test_georisk_store_dedupes_and_sorts(tmp_path):
    store = GeoRiskStore(tmp_path)
    idx = pd.to_datetime(["1985-02-01", "1985-01-01", "1985-02-01"])
    store.save("x", pd.Series([2.0, 1.0, 99.0], index=idx))  # dup 02-01 → keep last (99)
    out = store.load("x")
    assert list(out.index) == list(pd.to_datetime(["1985-01-01", "1985-02-01"]))
    assert out.loc["1985-02-01"] == 99.0


# ── GprProvider ─────────────────────────────────────────────────────────────────
def test_gpr_parse_datetime_month_column():
    df = pd.DataFrame({
        "month": pd.to_datetime(["1985-01-01", "1985-02-01"]),
        "GPR": [100.0, 120.0], "GPRT": [90.0, 110.0], "GPRC_USA": [80.0, 95.0],
    })
    frame = GprProvider._parse(_xlsx_bytes(df))
    assert isinstance(frame.index, pd.DatetimeIndex)
    assert list(frame.columns) == ["GPR", "GPRT", "GPRC_USA"]
    assert GprProvider.series(frame, "GPR").tolist() == [100.0, 120.0]


def test_gpr_parse_yyyymm_integer_column():
    df = pd.DataFrame({"month": [198501, 198502], "GPR": [100.0, 120.0]})
    frame = GprProvider._parse(_xlsx_bytes(df))
    assert list(frame.index) == list(pd.to_datetime(["1985-01-01", "1985-02-01"]))


def test_gpr_fetch_monthly_via_fake_http():
    df = pd.DataFrame({"month": pd.to_datetime(["1985-01-01"]), "GPR": [100.0]})
    prov = GprProvider(http=_FakeHttp(_xlsx_bytes(df)))
    frame = prov.fetch_monthly()
    assert frame["GPR"].iloc[0] == 100.0


# ── GdeltProvider ───────────────────────────────────────────────────────────────
_GDELT_JSON = {
    "timeline": [{"series": "Average Tone", "data": [
        {"date": "2026-01-01T000000Z", "value": -2.5},
        {"date": "2026-01-02T000000Z", "value": -3.1},
    ]}]
}


def test_gdelt_parse_canned_payload():
    s = GdeltProvider._parse(_GDELT_JSON)
    assert s.tolist() == [-2.5, -3.1]
    assert isinstance(s.index, pd.DatetimeIndex) and len(s) == 2


def test_gdelt_parse_empty_payload():
    assert GdeltProvider._parse({}).empty
    assert GdeltProvider._parse({"timeline": []}).empty


def test_gdelt_fetch_timeline_via_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "api/v2/doc/doc" in str(request.url)
        return httpx.Response(200, json=_GDELT_JSON)

    prov = GdeltProvider(transport=httpx.MockTransport(handler))
    s = prov.fetch_timeline('"sanctions"', mode="timelinetone", timespan="1m")
    assert s.tolist() == [-2.5, -3.1]


# ── taxonomy wiring ─────────────────────────────────────────────────────────────
def test_taxonomy_registers_geopolitics_domain():
    from qhfi.data.taxonomy import DataDomain, by_domain

    names = {d.name for d in by_domain(DataDomain.GEOPOLITICS)}
    assert {"geopolitical_risk", "event_tone"} <= names
