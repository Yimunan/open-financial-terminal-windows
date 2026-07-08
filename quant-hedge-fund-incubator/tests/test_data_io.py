"""Offline tests for the parquet read helpers (`qhfi.data._io`) and the read-pattern optimizations
that route through them. Every helper must return exactly what a full read would, so the catalog and
backtest panels stay byte-for-byte identical while reading far less.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from qhfi.core.types import AssetClass, DateRange, Instrument
from qhfi.data import _io
from qhfi.data.base import DataStore
from qhfi.data.news import NewsStore


def _bars(start, periods, px0):
    idx = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    close = np.linspace(px0, px0 * 1.5, periods)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx
    )


# ── _io helpers ───────────────────────────────────────────────────────────────────
def test_index_helpers_match_full_read_unnamed_index(tmp_path):
    bars = _bars("2024-01-01", 30, 100)          # unnamed index → stored as __index_level_0__
    p = tmp_path / "x.parquet"
    bars.to_parquet(p)
    full = pd.read_parquet(p)

    assert list(_io.read_index(p)) == list(full.index)
    assert _io.index_minmax(p) == (full.index.min(), full.index.max())
    assert _io.row_count(p) == len(full)


def test_index_helpers_match_full_read_named_index(tmp_path):
    bars = _bars("2024-03-01", 12, 50)
    bars.index.name = "date"                     # named index → stored as 'date'
    p = tmp_path / "y.parquet"
    bars.to_parquet(p)
    full = pd.read_parquet(p)

    assert _io.index_columns(p) == ["date"]
    assert list(_io.read_index(p)) == list(full.index)
    assert _io.index_minmax(p) == (full.index.min(), full.index.max())


def test_index_minmax_uses_statistics_not_a_full_read(tmp_path):
    # Equivalence is the contract; this also exercises the stats path on a multi-row-group file.
    bars = _bars("2020-01-01", 5000, 10)
    p = tmp_path / "big.parquet"
    bars.to_parquet(p, row_group_size=512)
    assert _io.index_minmax(p) == (bars.index.min(), bars.index.max())


def test_index_minmax_none_on_empty(tmp_path):
    empty = _bars("2024-01-01", 0, 100)
    p = tmp_path / "e.parquet"
    empty.to_parquet(p)
    assert _io.index_minmax(p) is None


def test_read_columns_projects_and_drops_missing(tmp_path):
    bars = _bars("2024-01-01", 10, 100)
    p = tmp_path / "c.parquet"
    bars.to_parquet(p)

    proj = _io.read_columns(p, ["close", "does_not_exist"])
    assert list(proj.columns) == ["close"]                 # missing column dropped, no raise
    assert_series_equal(proj["close"], pd.read_parquet(p)["close"])
    assert list(proj.index) == list(bars.index)            # index preserved by projection


# ── load_panel column projection + span ─────────────────────────────────────────────
def _store_two(tmp_path):
    store = DataStore(tmp_path)
    a = Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)
    b = Instrument(id="B/USDT", asset_class=AssetClass.CRYPTO)
    store.save(a, _bars("2024-01-01", 60, 100))
    store.save(b, _bars("2024-01-01", 60, 50))
    return store, a, b


def test_load_panel_projection_matches_full_field(tmp_path):
    store, a, b = _store_two(tmp_path)
    panel = store.load_panel([a, b], field="close")

    # Reference: the pre-optimization behavior (full read, then pick the field).
    ref = pd.DataFrame(
        {a.id: store.load(a)["close"], b.id: store.load(b)["close"]}
    ).sort_index()
    assert_frame_equal(panel, ref)


def test_load_panel_span_equals_full_then_slice(tmp_path):
    store, a, b = _store_two(tmp_path)
    full = store.load_panel([a, b], field="close")
    span = DateRange(start=pd.Timestamp("2024-01-10").date(), end=pd.Timestamp("2024-02-05").date())

    sliced = store.load_panel([a, b], field="close", span=span)
    assert_frame_equal(sliced, full.loc["2024-01-10":"2024-02-05"])


# ── NewsStore no-op skip ────────────────────────────────────────────────────────────
def _news(ids, ts):
    return pd.DataFrame({"id": ids, "created_at": pd.to_datetime(ts, utc=True),
                         "headline": [f"h{i}" for i in ids], "summary": "", "author": None,
                         "publisher": "p", "url": "u", "symbols": "AAPL", "provider": "alpaca"})


def test_news_save_skips_rewrite_when_nothing_new(tmp_path):
    store = NewsStore(tmp_path)
    store.save("equity", "alpaca", "AAPL", _news(["1", "2"], ["2024-01-01", "2024-01-02"]))
    p = store._path("equity", "alpaca", "AAPL")
    mtime_before = p.stat().st_mtime_ns

    # Fully-overlapping batch → 0 rows added, no rewrite (mtime unchanged), content intact.
    added = store.save("equity", "alpaca", "AAPL", _news(["1", "2"], ["2024-01-01", "2024-01-02"]))
    assert added == 0
    assert p.stat().st_mtime_ns == mtime_before
    assert store.load("equity", "alpaca", "AAPL")["id"].tolist() == ["1", "2"]


def test_news_save_appends_new_rows(tmp_path):
    store = NewsStore(tmp_path)
    store.save("equity", "alpaca", "AAPL", _news(["1", "2"], ["2024-01-01", "2024-01-02"]))
    added = store.save("equity", "alpaca", "AAPL", _news(["2", "3"], ["2024-01-02", "2024-01-03"]))
    assert added == 1                                          # id "2" deduped, "3" added
    assert store.load("equity", "alpaca", "AAPL")["id"].tolist() == ["1", "2", "3"]
