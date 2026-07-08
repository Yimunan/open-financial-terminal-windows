"""Offline tests for the high-frequency stores — asset-class + source partitioning, merge/
dedup, and order-book snapshots with source provenance."""

from __future__ import annotations

import pandas as pd

from qhfi.data.highfreq import MinuteBarStore, OrderBookStore


def _bars(start, n):
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


def test_minute_store_partitioned_by_asset_class_and_source(tmp_path):
    store = MinuteBarStore(tmp_path)
    store.save("BTC/USDT", _bars("2025-06-01", 100), source="okx")
    store.save("BTC/USDT", _bars("2025-06-01 01:00", 100), source="okx")  # overlaps → dedup
    # path: intraday_1m/crypto/okx/BTC_USDT.parquet
    assert store._path("BTC/USDT", source="okx").parts[-4:] == ("intraday_1m", "crypto", "okx", "BTC_USDT.parquet")
    df = store.load("BTC/USDT", source="okx")
    assert not df.index.has_duplicates and df.index.is_monotonic_increasing
    assert store.last_ms("BTC/USDT", source="okx") == int(df.index.max().timestamp() * 1000)

    cat = store.catalog()
    assert cat.iloc[0]["asset_class"] == "crypto" and cat.iloc[0]["source"] == "okx"


def test_orderbook_stamps_source_on_path_and_rows(tmp_path):
    store = OrderBookStore(tmp_path)
    ob = {"timestamp": 1717000000000,
          "bids": [[100.0, 2.0, 9], [99.5, 5.0, 3]], "asks": [[100.5, 1.0, 1]]}  # extra fields ignored
    n = store.save_snapshot("BTC/USDT", ob, source="okx")
    assert n == 3
    assert store._path("BTC/USDT", source="okx").parts[-4:] == ("orderbook", "crypto", "okx", "BTC_USDT.parquet")
    df = store.load("BTC/USDT", source="okx")
    assert (df["source"] == "okx").all()                          # source on every row
    cat = store.catalog()
    assert cat.iloc[0]["asset_class"] == "crypto" and cat.iloc[0]["source"] == "okx"
