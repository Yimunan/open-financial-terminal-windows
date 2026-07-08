"""Offline tests for the Treasury-futures complex (services.rates over the qhfi lake).

The Rates module's futures grid + per-contract bars read daily OHLCV through a ``DataStore``
rooted at the qhfi research lake. These tests are fully hermetic: a ``FakeStore`` keyed by
``Instrument.id`` returns synthetic daily OHLCV, so the substance under test is the
service's own assembly logic — one grid row per *present* contract (absent contracts skipped,
no KeyError), the name/tenor/multiplier/duration merge from ``FUTURES_META``, the quote +
sparkline shaping, the unknown-symbol guard, and the start/end slice on the per-contract bars.
The single router test pins the 404 contract for an unknown future via ``TestClient`` +
``app.dependency_overrides`` (try/finally pop, like test_listings.py).

Run: `cd backend && pytest tests/test_rates.py -q`
"""

from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

from app.deps import get_rates_futures_store
from app.main import app
from app.services import rates as rt


def _synthetic_bars(periods: int = 90, base: float = 110.0) -> pd.DataFrame:
    """A gently trending daily OHLCV frame with a plain (tz-naive) DatetimeIndex.

    The lake's rates-futures parquet uses a date-only daily index; a tz-naive index mirrors
    that and keeps the ``bars.loc[str(start):str(end)]`` label slice in services.rates honest.
    """
    idx = pd.date_range(end=pd.Timestamp("2026-06-01"), periods=periods, freq="D")
    close = base + pd.Series(range(periods), index=idx) * 0.01
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": [1_000_000.0] * periods,
        },
        index=idx,
    )


class FakeStore:
    """A lake DataStore keyed by Instrument.id (Instrument is unhashable, so we key on .id).

    ``frames`` maps a futures symbol → its bars (or to None / an empty frame to exercise the
    absent / empty branches of services.rates._load).
    """

    def __init__(self, frames: dict[str, pd.DataFrame | None]):
        self._frames = frames

    def has(self, ins) -> bool:
        return ins.id in self._frames and self._frames[ins.id] is not None

    def load(self, ins) -> pd.DataFrame:
        return self._frames[ins.id]


# ---------------------------------------------------------------------------
# _load
# ---------------------------------------------------------------------------

def test_load_returns_none_for_missing_or_empty():
    bars = _synthetic_bars()
    store = FakeStore({
        "ZN": bars,                                       # present, non-empty
        "ZF": None,                                       # store.has → False
        "ZB": pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),  # empty frame
    })
    # present + non-empty → the frame itself
    assert rt._load(store, "ZN") is bars
    # not in the store at all → None
    assert rt._load(store, "ZQ") is None
    # in the store but has() is False (None sentinel) → None
    assert rt._load(store, "ZF") is None
    # present but empty frame → None
    assert rt._load(store, "ZB") is None


# ---------------------------------------------------------------------------
# futures_grid
# ---------------------------------------------------------------------------

def test_futures_grid_one_row_per_present_contract():
    # Every contract present → one row each, in FUTURES_ORDER, with the full meta merged.
    store = FakeStore({s: _synthetic_bars() for s in rt.FUTURES_ORDER})
    out = rt.futures_grid(store, spark_points=10)
    rows = out["futures"]

    assert [r["symbol"] for r in rows] == rt.FUTURES_ORDER  # order preserved
    for r in rows:
        meta = rt.FUTURES_META[r["symbol"]]
        assert r["name"] == meta["name"]
        assert r["tenor"] == meta["tenor"]
        assert r["contract_multiplier"] == meta["mult"]
        assert r["modified_duration"] == meta["mod_dur"]
        # quote carries the last close + the standard quote_from_bars shape
        assert set(r["quote"]) >= {"price", "change", "change_pct", "asof"}
        assert r["quote"]["price"] is not None
        # spark is capped to spark_points closes, each rounded to 4dp
        assert len(r["spark"]) == 10
        assert all(isinstance(v, float) for v in r["spark"])
        assert all(round(v, 4) == v for v in r["spark"])


def test_futures_grid_skips_absent_contracts():
    # Only two of the six contracts have data; the rest are absent (None) or empty.
    store = FakeStore({
        "ZN": _synthetic_bars(),
        "ZB": _synthetic_bars(),
        "ZQ": None,
        "ZF": pd.DataFrame(columns=["open", "high", "low", "close", "volume"]),
        # ZT, UB not in the store at all
    })
    out = rt.futures_grid(store)
    syms = [r["symbol"] for r in out["futures"]]
    assert syms == ["ZN", "ZB"]  # absent ones skipped, no KeyError, order from FUTURES_ORDER


# ---------------------------------------------------------------------------
# futures_bars
# ---------------------------------------------------------------------------

def test_futures_bars_unknown_symbol_is_none():
    store = FakeStore({s: _synthetic_bars() for s in rt.FUTURES_ORDER})
    # not in FUTURES_META → None (the unknown-contract guard fires before the store read)
    assert rt.futures_bars(store, "NOPE", None, None) is None
    # known symbol but absent from the store → None
    empty_store = FakeStore({})
    assert rt.futures_bars(empty_store, "ZN", None, None) is None


def test_futures_bars_slices_and_merges_meta():
    bars = _synthetic_bars(periods=90)  # daily index ending 2026-06-01
    store = FakeStore({"ZN": bars})

    # lowercase symbol is upper-cased; start/end slice applied (inclusive label slice)
    start = pd.Timestamp("2026-05-20").date()
    end = pd.Timestamp("2026-05-25").date()
    out = rt.futures_bars(store, "zn", start, end)

    assert out is not None
    meta = rt.FUTURES_META["ZN"]
    assert out["symbol"] == "ZN"
    assert out["name"] == meta["name"]
    assert out["tenor"] == meta["tenor"]
    # candles payload from to_candles (daily → date-string times) + a merged quote
    assert "candles" in out and "volume" in out
    assert set(out["quote"]) >= {"price", "change", "change_pct", "asof"}

    # the slice kept exactly the in-window trading days (inclusive on both bounds)
    times = [c["time"] for c in out["candles"]]
    assert times == ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23", "2026-05-24", "2026-05-25"]
    assert all("2026-05-20" <= t <= "2026-05-25" for t in times)
    # the quote's asof is the last in-window date, not the frame's global last bar
    assert out["quote"]["asof"] == "2026-05-25"


# ---------------------------------------------------------------------------
# Router — 404 for an unknown contract
# ---------------------------------------------------------------------------

def test_rates_futures_endpoint_404_unknown():
    # The store has real data, but the requested contract isn't a Treasury future at all.
    store = FakeStore({s: _synthetic_bars() for s in rt.FUTURES_ORDER})
    app.dependency_overrides[get_rates_futures_store] = lambda: store
    try:
        client = TestClient(app)
        r = client.get("/api/rates/futures/NOPE")
        assert r.status_code == 404
        assert "NOPE" in r.json()["detail"]
        # sanity: a present, known contract resolves 200 through the same override
        ok = client.get("/api/rates/futures/ZN")
        assert ok.status_code == 200
        assert ok.json()["symbol"] == "ZN"
    finally:
        app.dependency_overrides.pop(get_rates_futures_store, None)
