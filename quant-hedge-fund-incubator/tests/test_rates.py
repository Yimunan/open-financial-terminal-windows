"""Offline tests for the FRED rates provider (fake HTTP) and RatesStore."""

from __future__ import annotations

import pandas as pd

from qhfi.api.client import ManagedClient
from qhfi.data.providers.fred import FredRatesProvider
from qhfi.data.rates import RatesStore

_CSV = {
    "DGS10": "observation_date,DGS10\n2026-06-01,4.40\n2026-06-02,.\n2026-06-03,4.35\n",
    "DGS2": "observation_date,DGS2\n2026-06-01,3.90\n2026-06-03,3.88\n",
}


class _Resp:
    def __init__(self, text):
        self.text, self.status_code = text, 200

    def raise_for_status(self):
        pass


class _FakeHttp:
    def get(self, url, params=None):
        return _Resp(_CSV[params["id"]])


def _provider():
    return FredRatesProvider(http=_FakeHttp(), managed=ManagedClient(backoff_base=0.0))


def test_fetch_series_parses_and_drops_missing():
    s = _provider().fetch_series("DGS10")
    assert len(s) == 2                                   # the '.' row dropped
    assert s.loc[pd.Timestamp("2026-06-03", tz="UTC")] == 4.35
    assert str(s.index.tz) == "UTC"


def test_treasury_curve_assembles_wide():
    curve = _provider().treasury_curve({"10Y": "DGS10", "2Y": "DGS2"})
    assert list(curve.columns) == ["10Y", "2Y"]
    assert curve.index.is_monotonic_increasing
    # 2y < 10y on a normal curve (here both dates)
    assert curve["2Y"].dropna().iloc[-1] < curve["10Y"].dropna().iloc[-1]


def test_rates_store_roundtrip(tmp_path):
    store = RatesStore(tmp_path)
    curve = _provider().treasury_curve({"10Y": "DGS10", "2Y": "DGS2"})
    store.save("treasury_curve", curve)
    assert store.has("treasury_curve")
    assert store._path("treasury_curve").parts[-2:] == ("rates", "treasury_curve.parquet")
    assert list(store.load("treasury_curve").columns) == ["10Y", "2Y"]
