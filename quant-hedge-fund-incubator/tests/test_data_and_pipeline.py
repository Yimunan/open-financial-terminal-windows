"""Offline tests for the real data path: DataStore parquet round-trip + panel assembly, and
FactorStrategy weight construction. No network — uses synthetic bars written to a tmp store.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.data.base import DataStore
from qhfi.factors.library import MomentumFactor
from qhfi.strategy.library.factor_strategy import FactorStrategy, FactorStrategyParams


def _bars(start, periods, px0):
    idx = pd.date_range(start, periods=periods, freq="D", tz="UTC")
    close = np.linspace(px0, px0 * 1.5, periods)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx
    )


def test_datastore_roundtrip_and_panel(tmp_path):
    store = DataStore(tmp_path)
    a = Instrument(id="A/USDT", asset_class=AssetClass.CRYPTO)
    b = Instrument(id="B/USDT", asset_class=AssetClass.CRYPTO)
    store.save(a, _bars("2024-01-01", 30, 100))
    store.save(b, _bars("2024-01-01", 30, 50))

    assert store.has(a)
    loaded = store.load(a)
    assert list(loaded.columns) == ["open", "high", "low", "close", "volume"]

    # merge + dedup: re-saving overlapping bars must not duplicate the index
    store.save(a, _bars("2024-01-15", 30, 120))
    assert not store.load(a).index.has_duplicates

    panel = store.load_panel([a, b], field="close")
    assert list(panel.columns) == ["A/USDT", "B/USDT"]
    assert panel.index.is_monotonic_increasing


def test_factor_strategy_long_short_weights():
    # 6 names with monotone drifts → momentum has a clear cross-sectional ordering.
    dates = pd.date_range("2024-01-01", periods=150, freq="D", tz="UTC")
    drifts = np.linspace(-0.001, 0.001, 6)
    prices = pd.DataFrame(
        {f"A{i}": 100 * np.cumprod(1 + np.full(150, d)) for i, d in enumerate(drifts)},
        index=dates,
    )
    uni = Universe(name="t", instruments=[
        Instrument(id=f"A{i}", asset_class=AssetClass.CRYPTO) for i in range(6)
    ])
    strat = FactorStrategy([MomentumFactor()], params=FactorStrategyParams(quantile=0.34, gross=1.0))
    w = strat.generate_weights(prices, uni)

    last = w.iloc[-1]
    assert last.abs().sum() > 0
    assert np.isclose(last.sum(), 0.0, atol=1e-9)          # dollar-neutral long/short
    assert np.isclose(last.abs().sum(), 1.0, atol=1e-9)    # gross == 1.0
    assert last["A5"] > 0 and last["A0"] < 0               # top drift long, bottom short
