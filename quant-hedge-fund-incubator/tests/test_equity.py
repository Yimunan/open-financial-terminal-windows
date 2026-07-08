"""Tests for the equity classification + fundamentals layer: EquityMeta on the instrument,
universe-derived sector groups feeding neutralization, and FundamentalFactor turning a
point-in-time fundamentals panel into an aligned, look-ahead-free factor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import AssetClass, EquityMeta, Instrument, Universe
from qhfi.factors import transforms as tf
from qhfi.factors.library import ValueFactor


def _equity(id_, sector, cap=None):
    return Instrument(id=id_, asset_class=AssetClass.EQUITY,
                      equity=EquityMeta(gics_sector=sector, market_cap=cap,
                                        index_membership=["SP500"]))


def _universe():
    return Universe(name="t", instruments=[
        _equity("AAPL", "InfoTech", 3e12), _equity("MSFT", "InfoTech", 3e12),
        _equity("XOM", "Energy", 5e11), _equity("CVX", "Energy", 3e11),
        Instrument(id="BTC/USDT", asset_class=AssetClass.CRYPTO),  # no equity meta
    ])


def test_equity_meta_and_sector_shortcut():
    u = _universe()
    assert u.by_id("AAPL").sector == "InfoTech"
    assert u.by_id("BTC/USDT").sector is None                # non-equity → None, no crash
    assert "SP500" in u.by_id("XOM").equity.index_membership


def test_universe_groups_for_neutralization():
    u = _universe()
    g = u.groups("gics_sector")
    assert g == {"AAPL": "InfoTech", "MSFT": "InfoTech",
                 "XOM": "Energy", "CVX": "Energy", "BTC/USDT": "__none__"}


def test_neutralize_with_universe_groups_zeros_sector_means():
    u = _universe()
    dates = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
    raw = pd.DataFrame(
        {"AAPL": [1.0, 2, 3], "MSFT": [3.0, 4, 5], "XOM": [10.0, 11, 12], "CVX": [20.0, 21, 22]},
        index=dates,
    )
    neutral = tf.neutralize(raw, u.groups("gics_sector"))
    # within each sector, the per-date mean is removed
    assert np.allclose(neutral.iloc[-1][["AAPL", "MSFT"]].mean(), 0.0)
    assert np.allclose(neutral.iloc[-1][["XOM", "CVX"]].mean(), 0.0)


def test_fundamental_factor_is_point_in_time_ffilled():
    u = _universe()
    daily = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    prices = pd.DataFrame(100.0, index=daily, columns=["AAPL", "MSFT", "XOM", "CVX"])
    # earnings yield reported on two dates only (sparse, PIT)
    ey = pd.DataFrame(
        {"AAPL": [0.04, np.nan], "MSFT": [0.03, np.nan], "XOM": [0.08, 0.09], "CVX": [0.07, 0.07]},
        index=[daily[0], daily[5]],
    )
    f = ValueFactor(ey).compute(prices, u)
    assert f.shape == (10, 4)
    assert f.loc[daily[2], "AAPL"] == 0.04          # ffilled from report date
    assert f.loc[daily[6], "XOM"] == 0.09           # updated after second report
    assert ValueFactor(ey).direction == 1           # higher yield = cheaper = long
