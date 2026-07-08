"""Offline tests for IndexStore (benchmark-index bars) + taxonomy wiring."""

from __future__ import annotations

import pandas as pd

from qhfi.data.indices import IndexStore


def _bars(dates, closes):
    idx = pd.to_datetime(dates, utc=True)
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [0] * len(closes)}, index=idx)


def test_index_store_partition_and_caret_stripped(tmp_path):
    store = IndexStore(tmp_path)
    store.save("^GSPC", _bars(["2024-01-02", "2024-01-03"], [4700.0, 4720.0]))
    # caret stripped, partitioned by source
    assert store._path("^GSPC").parts[-3:] == ("index", "yfinance", "GSPC.parquet")
    assert store.has("^GSPC")
    assert store.load("^GSPC")["close"].tolist() == [4700.0, 4720.0]


def test_index_store_merges_dedups_and_catalogs(tmp_path):
    store = IndexStore(tmp_path)
    store.save("^VIX", _bars(["2024-01-02", "2024-01-03"], [13.0, 14.0]))
    store.save("^VIX", _bars(["2024-01-03", "2024-01-04"], [99.0, 15.0]))  # 01-03 overwrites → 99
    out = store.load("^VIX")
    assert out["close"].tolist() == [13.0, 99.0, 15.0]          # merged, deduped, sorted
    cat = store.catalog()
    row = cat[cat.symbol == "VIX"].iloc[0]
    assert row["bars"] == 3 and row["last"] == 15.0 and row["source"] == "yfinance"


def test_taxonomy_registers_market_index():
    from qhfi.data.taxonomy import DataDomain, by_domain

    names = {d.name for d in by_domain(DataDomain.REFERENCE)}
    assert "market_index" in names
