"""Offline tests for the macro provider (FRED→DBnomics fallback) and MacroStore."""

from __future__ import annotations

import httpx
import pandas as pd

from qhfi.data.macro import MacroStore
from qhfi.data.providers.macro import MacroProvider


class _Resp:
    def __init__(self, text=None, payload=None):
        self.text, self._payload, self.status_code = text, payload, 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeHttp:
    """FRED blocked (raises); DBnomics returns canned JSON."""

    def __init__(self):
        self.fred_calls = 0

    def get(self, url, params=None):
        if "fredgraph" in url:
            self.fred_calls += 1
            raise httpx.ConnectError("blocked")
        if "db.nomics" in url:
            return _Resp(payload={"series": {"docs": [
                {"period": ["2026-01-01", "2026-02-01"], "value": ["3.1", "NA"]},
            ]}})
        raise AssertionError(url)


def test_falls_back_to_dbnomics_when_fred_blocked():
    http = _FakeHttp()
    prov = MacroProvider(http=http)
    s = prov.fetch_series("CPIAUCSL")
    assert len(s) == 1 and s.iloc[0] == 3.1            # 'NA' dropped
    assert str(s.index.tz) == "UTC"

    prov.fetch_series("UNRATE")                          # FRED already marked dead → not retried
    assert http.fred_calls == 1


def test_worldbank_parses_country_indicator():
    from qhfi.data.providers.worldbank import WorldBankProvider

    class _WBHttp:
        def get(self, url, params=None):
            return _Resp(payload=[
                {"page": 1, "total": 2},
                [{"date": "2024", "value": 2.8}, {"date": "2023", "value": None},
                 {"date": "2022", "value": 1.9}],
            ])

    s = WorldBankProvider(http=_WBHttp()).fetch("US", "NY.GDP.MKTP.KD.ZG")
    assert len(s) == 2                                   # null dropped
    assert s.iloc[-1] == 2.8                             # sorted ascending → 2024 last
    assert str(s.index.tz) == "UTC"


def test_macro_store_roundtrip_and_catalog(tmp_path):
    store = MacroStore(tmp_path)
    s = pd.Series([3.1, 3.2], index=pd.to_datetime(["2026-01-01", "2026-02-01"], utc=True))
    store.save("CPIAUCSL", s)
    assert store.has("CPIAUCSL")
    assert store._path("CPIAUCSL").parts[-2:] == ("macro", "CPIAUCSL.parquet")
    assert store.load("CPIAUCSL").iloc[-1] == 3.2
    cat = store.catalog()
    assert cat.iloc[0]["series"] == "CPIAUCSL" and cat.iloc[0]["obs"] == 2
