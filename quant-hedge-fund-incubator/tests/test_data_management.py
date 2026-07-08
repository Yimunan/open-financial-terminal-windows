"""Tests for the data management system: quality validation + DataManager orchestration
(incremental refresh, validation-skip, panel serving, catalog). Offline — a fake provider
records what spans it was asked for, so we can assert the manager only fetches the gap.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import AssetClass, DateRange, Instrument, Universe
from qhfi.data.base import DataStore
from qhfi.data.manager import DataManager
from qhfi.data.quality import validate_bars


def _bars(start, end):
    idx = pd.date_range(start, end, freq="D", tz="UTC", inclusive="left")
    close = np.arange(1, len(idx) + 1, dtype=float) + 100
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1.0},
        index=idx,
    )


class FakeProvider:
    asset_class = AssetClass.CRYPTO

    def __init__(self):
        self.calls: list[tuple] = []

    def fetch_daily(self, instrument, span):
        self.calls.append((span.start, span.end))
        return _bars(span.start, span.end)


# ── quality ──────────────────────────────────────────────────────────────────
def test_quality_passes_clean_bars():
    ins = Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)
    assert validate_bars(_bars("2024-01-01", "2024-02-01"), ins).ok


def test_quality_flags_problems():
    ins = Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)
    assert validate_bars(pd.DataFrame(), ins).fatal                      # empty → fatal
    assert validate_bars(_bars("2024-01-01", "2024-01-10")[["close"]], ins).fatal  # missing cols

    bad = _bars("2024-01-01", "2024-01-10")
    bad.iloc[2, bad.columns.get_loc("high")] = bad.iloc[2]["low"] - 5    # high < low
    bad.iloc[4, bad.columns.get_loc("close")] = -1                       # non-positive
    rep = validate_bars(bad, ins)
    assert not rep.fatal and any("high < low" in i for i in rep.issues)
    assert any("non-positive" in i for i in rep.issues)


def test_quality_tolerates_subbp_adjustment_rounding():
    # close lands on the high but a fraction of a bp above it (split/div adjustment rounding)
    ins = Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)
    b = _bars("2024-01-01", "2024-01-10")
    b["close"] = b["high"] * (1 + 5e-5)                # 0.5 bp over → benign
    assert not any("outside" in i for i in validate_bars(b, ins).issues)
    b["close"] = b["high"] * (1 + 1e-2)                # 100 bp over → real violation
    assert any("outside" in i for i in validate_bars(b, ins).issues)


# ── manager ──────────────────────────────────────────────────────────────────
def test_incremental_refresh_only_fetches_the_gap(tmp_path):
    store = DataStore(tmp_path)
    prov = FakeProvider()
    mgr = DataManager(store, {AssetClass.CRYPTO: prov})
    uni = Universe(name="t", instruments=[Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)])

    r1 = mgr.update(uni, DateRange(start=pd.Timestamp("2024-01-01").date(),
                                   end=pd.Timestamp("2024-01-11").date()))
    assert r1[0].fetched_rows == 10 and len(prov.calls) == 1

    # same span again → nothing to do, provider not called again
    r2 = mgr.update(uni, DateRange(start=pd.Timestamp("2024-01-01").date(),
                                   end=pd.Timestamp("2024-01-11").date()))
    assert r2[0].skipped_reason == "up-to-date" and len(prov.calls) == 1

    # extend the end → fetch only the new tail, starting the day after the cached last bar
    r3 = mgr.update(uni, DateRange(start=pd.Timestamp("2024-01-01").date(),
                                   end=pd.Timestamp("2024-01-21").date()))
    assert prov.calls[-1][0] == pd.Timestamp("2024-01-11").date()
    assert r3[0].fetched_rows == 10
    assert store.load(uni.instruments[0]).index.is_monotonic_increasing
    assert len(store.load(uni.instruments[0])) == 20


def test_fatal_batch_is_not_stored(tmp_path):
    class EmptyProvider:
        asset_class = AssetClass.CRYPTO
        def fetch_daily(self, instrument, span):
            return pd.DataFrame()

    store = DataStore(tmp_path)
    mgr = DataManager(store, {AssetClass.CRYPTO: EmptyProvider()})
    uni = Universe(name="t", instruments=[Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)])
    rep = mgr.update(uni, DateRange(start=pd.Timestamp("2024-01-01").date(),
                                    end=pd.Timestamp("2024-01-11").date()))
    assert rep[0].skipped_reason and not store.has(uni.instruments[0])


def test_store_is_domain_partitioned(tmp_path):
    eq = Instrument(id="AAPL", asset_class=AssetClass.EQUITY)
    market = DataStore(tmp_path)                          # default domain = market
    assert market._path(eq).parts[-3:] == ("market", "equity", "AAPL.parquet")
    fund = DataStore(tmp_path, domain="fundamental")
    assert fund.data_dir.name == "fundamental"
    # same root, different domain → different file trees
    assert market.data_dir != fund.data_dir


def test_panel_and_catalog(tmp_path):
    store = DataStore(tmp_path)
    prov = FakeProvider()
    mgr = DataManager(store, {AssetClass.CRYPTO: prov})
    uni = Universe(name="t", instruments=[
        Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO),
        Instrument(id="B/USDT", asset_class=AssetClass.CRYPTO),
    ])
    mgr.update(uni, DateRange(start=pd.Timestamp("2024-01-01").date(),
                              end=pd.Timestamp("2024-01-11").date()))

    panel = mgr.get_panel(uni, "close")
    assert list(panel.columns) == ["A/USDT", "B/USDT"] and len(panel) == 10

    cat = mgr.catalog()
    assert len(cat) == 2
    assert set(cat["rows"]) == {10}
    assert set(cat["asset_class"]) == {"crypto"}
