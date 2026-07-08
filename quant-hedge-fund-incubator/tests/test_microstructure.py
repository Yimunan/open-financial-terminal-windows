"""Microstructure primitives — closed-form oracles for OBI, microprice, and book_features.

Synthetic, offline. These pin the *math* of the signal layer independent of any data realism.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qhfi.data.microstructure import (
    book_features,
    fit_arrival_intensity,
    microprice,
    order_book_imbalance,
    realized_vol,
)


def test_microprice_leans_to_thin_side():
    # Heavy bid (10 vs 1 ask) ⇒ microprice pulled toward the ask (up-move predicted).
    mp = microprice(bid_px=100.0, ask_px=101.0, bid_size=10.0, ask_size=1.0)
    assert 100.5 < mp < 101.0
    # Closed form: (100*1 + 101*10) / 11.
    assert mp == pytest.approx((100.0 * 1.0 + 101.0 * 10.0) / 11.0)


def test_microprice_balanced_is_mid():
    assert microprice(100.0, 101.0, 5.0, 5.0) == pytest.approx(100.5)
    assert microprice(100.0, 101.0, 0.0, 0.0) == pytest.approx(100.5)   # zero-size fallback


def test_obi_sign_and_bounds():
    assert order_book_imbalance([3, 2, 1], [1, 1, 1]) == pytest.approx((6 - 3) / 9)
    assert order_book_imbalance([1], [1]) == pytest.approx(0.0)
    assert order_book_imbalance([10], [0]) == pytest.approx(1.0)
    assert order_book_imbalance([0], [10]) == pytest.approx(-1.0)


def test_obi_decay_weights_top_levels_more():
    # Imbalance only at deep levels should matter less under decay than a flat sum.
    bids, asks = [1, 1, 5], [1, 1, 1]
    flat = order_book_imbalance(bids, asks, decay=0.0)
    decayed = order_book_imbalance(bids, asks, decay=1.0)
    assert decayed < flat                                   # deep bid surplus discounted


def _long_book(snapshot_ts, bids, asks):
    rows = []
    for lv, (p, a) in enumerate(bids):
        rows.append({"snapshot_ts": snapshot_ts, "side": "bid", "level": lv, "price": p, "amount": a})
    for lv, (p, a) in enumerate(asks):
        rows.append({"snapshot_ts": snapshot_ts, "side": "ask", "level": lv, "price": p, "amount": a})
    return rows


def test_book_features_matches_closed_form():
    rows = _long_book(1_000, bids=[(100.0, 8.0), (99.0, 2.0)], asks=[(101.0, 2.0), (102.0, 1.0)])
    feat = book_features(pd.DataFrame(rows), levels=10)
    assert len(feat) == 1
    row = feat.iloc[0]
    assert row["mid"] == pytest.approx(100.5)
    assert row["spread"] == pytest.approx(1.0)
    assert row["microprice"] == pytest.approx((100.0 * 2.0 + 101.0 * 8.0) / 10.0)
    assert row["obi"] == pytest.approx((10.0 - 3.0) / 13.0)   # depth: bids 8+2, asks 2+1
    assert isinstance(feat.index, pd.DatetimeIndex) and feat.index.tz is not None


def test_realized_vol_positive_and_windowed():
    mid = pd.Series(100 * np.cumprod(1 + np.full(50, 0.001)))
    v = realized_vol(mid, window=10)
    assert v.iloc[-1] >= 0 and np.isfinite(v.iloc[-1])
    assert v.iloc[0] != v.iloc[0]                            # NaN before min_periods


def test_fit_arrival_intensity_recovers_kappa():
    kappa_true, a_true = 2.0, 50.0
    d = np.linspace(0.0, 2.0, 20)
    counts = a_true * np.exp(-kappa_true * d)
    a_hat, k_hat = fit_arrival_intensity(d, counts)
    assert k_hat == pytest.approx(kappa_true, rel=1e-6)
    assert a_hat == pytest.approx(a_true, rel=1e-6)
