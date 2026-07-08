"""Unit tests for the investable-index benchmark resolution in `backtest`.

`_benchmark_symbol` is pure; `index_benchmark_returns` is tested through its fallback paths
(empty grid, fetch failure) with `fetch_bars` monkeypatched — so these run with no network.
"""

from __future__ import annotations

import pandas as pd
import pytest
from qhfi.core.types import Universe

from app.deps import make_instrument
from app.services import backtest as bt


def _universe(*symbols_assets: tuple[str, str]) -> Universe:
    return Universe(name="_t", instruments=[make_instrument(s, a) for s, a in symbols_assets])


def test_equity_universe_maps_to_spy():
    assert bt._benchmark_symbol(_universe(("AAPL", "equity"), ("MSFT", "equity"))) == ("SPY", "equity")


def test_all_crypto_universe_maps_to_btc():
    assert bt._benchmark_symbol(_universe(("BTC/USDT", "crypto"), ("ETH/USDT", "crypto"))) == (
        "BTC/USDT",
        "crypto",
    )


def test_mixed_universe_prefers_equity_proxy():
    # Any equity present → SPY (the equity market is the dominant risk factor).
    assert bt._benchmark_symbol(_universe(("AAPL", "equity"), ("BTC/USDT", "crypto"))) == ("SPY", "equity")


def test_empty_grid_falls_back_to_equal_weight():
    ret, label = bt.index_benchmark_returns(None, _universe(("AAPL", "equity")), pd.DatetimeIndex([]))
    assert ret is None and label == "equal-weight"


def test_fetch_failure_falls_back_to_equal_weight(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr("app.services.market.fetch_bars", boom)
    grid = pd.date_range("2023-01-02", periods=60, freq="B", tz="UTC")
    ret, label = bt.index_benchmark_returns(object(), _universe(("AAPL", "equity")), grid)
    assert ret is None and label == "equal-weight"


def test_sparse_fetch_falls_back_to_equal_weight(monkeypatch):
    """A benchmark series that barely overlaps the grid is rejected (can't trust the beta)."""
    grid = pd.date_range("2023-01-02", periods=100, freq="B", tz="UTC")
    sparse = pd.DataFrame({"close": [100.0, 101.0, 102.0]}, index=grid[:3])

    monkeypatch.setattr("app.services.market.fetch_bars", lambda *a, **k: (None, sparse))
    ret, label = bt.index_benchmark_returns(object(), _universe(("AAPL", "equity")), grid)
    assert ret is None and label == "equal-weight"
