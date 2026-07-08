# NOTE: Phase-1 placeholder for the qhfi data/model layer omitted from the public
# release. Permissive stub — lets the terminal backend import and boot. Replace with
# real implementations in Phase 2 (yfinance / ccxt / FRED / EDGAR providers).

from __future__ import annotations

import pandas as pd


def book_features(book, *a, levels=None, **k):
    return pd.DataFrame()


def forward_return_on_obi(feat, *a, horizon=1, **k):
    return pd.DataFrame()


def microprice(bid_px=float("nan"), ask_px=float("nan"), bid_sz=0.0, ask_sz=0.0, *a, **k):
    try:
        return (float(bid_px) + float(ask_px)) / 2.0
    except Exception:
        return float("nan")


def order_book_imbalance(bids=None, asks=None, decay=0.0, *a, **k):
    return 0.0
