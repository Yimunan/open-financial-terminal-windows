"""Offline tests for the CUSIP→ticker crosswalk: OpenFigiMapper (mocked) + CusipTickerStore."""

from __future__ import annotations

import httpx

from qhfi.data.crosswalk import CusipTickerStore
from qhfi.data.providers.openfigi import OpenFigiMapper


def _openfigi_handler(request: httpx.Request) -> httpx.Response:
    import json
    jobs = json.loads(request.content)
    table = {
        "037833100": [{"ticker": "AAPL", "name": "APPLE INC", "exchCode": "US", "securityType": "Common Stock"}],
        "191216100": [{"ticker": "KO", "name": "COCA-COLA CO/THE", "exchCode": "US", "securityType": "Common Stock"},
                      {"ticker": "KO", "name": "COCA-COLA CO/THE", "exchCode": "LN", "securityType": "Common Stock"}],
        "000000000": [],  # unmappable
    }
    return httpx.Response(200, json=[{"data": table.get(j["idValue"], [])} for j in jobs])


def _mapper():
    return OpenFigiMapper(transport=httpx.MockTransport(_openfigi_handler))


def test_mapper_resolves_and_prefers_us_listing():
    out = _mapper().map(["037833100", "191216100", "000000000"])
    assert out["037833100"]["ticker"] == "AAPL"
    assert out["191216100"]["ticker"] == "KO" and out["191216100"]["exch"] == "US"  # US line preferred
    assert "000000000" not in out                                                    # unmappable dropped


def test_store_upsert_dedups_and_to_ticker(tmp_path):
    store = CusipTickerStore(tmp_path)
    store.upsert(_mapper().map(["037833100", "191216100"]))
    assert store.known() == {"037833100", "191216100"}
    store.upsert({"037833100": {"ticker": "AAPL", "name": "APPLE INC", "exch": "US", "sec_type": "Common Stock"}})
    assert len(store.load()) == 2                                  # re-upsert doesn't duplicate
    assert store.to_ticker()["191216100"] == "KO"


def test_taxonomy_registers_cusip_ticker():
    from qhfi.data.taxonomy import DATASETS

    assert "cusip_ticker" in {d.name for d in DATASETS}
