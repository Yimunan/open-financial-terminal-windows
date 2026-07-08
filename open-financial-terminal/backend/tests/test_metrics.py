"""Tests for the per-symbol metrics tearsheet (services.metrics.metrics).

Hermetic: price bars and the fundamentals snapshot/income-statement are monkeypatched with
synthetic data, so the test exercises the section-shaping logic deterministically without the
network. The substance under test is what differs by asset class — equities carry
valuation/quality/growth, crypto carries none of those (no issuer) but adds a BTC-relative
section — plus the percent-unit and derived-metric contracts.

Run: `cd backend && pytest tests/test_metrics.py -v`
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services import metrics as mx


def _synthetic_bars(periods: int = 420, base: float = 100.0) -> pd.DataFrame:
    """A gently trending OHLCV frame with a daily DatetimeIndex ending today.

    Index is UTC-aware to mirror the real parquet lake (datetime64[*, UTC]) — a tz-naive
    index would silently sidestep the YTD index-comparison bug that broke live data.
    """
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=periods, freq="D", tz="UTC")
    close = base + np.arange(periods) * 0.1 + np.sin(np.arange(periods) / 5.0)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(periods, 1_000_000.0),
        },
        index=idx,
    )


def _fake_fetch_bars(_dm, symbol, asset="equity", start=None, end=None):
    return None, _synthetic_bars()


_FAKE_SNAPSHOT = {
    "name": "Test Corp",
    "sector": "Technology",
    "currency": "USD",
    "market_cap": 2.5e12,
    "pe": 28.0,
    "forward_pe": 24.0,
    "pb": 12.0,
    "eps": 6.0,
    "dividend_yield": 0.005,  # fraction → 0.50%
    "beta": 1.2,
    "roe": 0.30,             # fraction → 30%
    "profit_margin": 0.25,   # fraction → 25%
}

_FAKE_FINANCIALS = {
    "periods": ["2024-09-30", "2023-09-30"],
    "rows": {
        "Total Revenue": [400.0, 350.0],
        "Gross Profit": [160.0, 140.0],
        "Operating Income": [120.0, 100.0],
        "Net Income": [100.0, 80.0],
        "Diluted EPS": [6.0, 5.0],
    },
}


@pytest.fixture(autouse=True)
def _patch_sources(monkeypatch):
    monkeypatch.setattr(mx, "fetch_bars", _fake_fetch_bars)
    monkeypatch.setattr(mx.fa, "snapshot", lambda symbol: dict(_FAKE_SNAPSHOT))
    monkeypatch.setattr(mx.fa, "financials", lambda symbol, *a, **k: dict(_FAKE_FINANCIALS))


def _row(out: dict, label: str):
    for sec in out["sections"]:
        for r in sec["rows"]:
            if r["label"] == label:
                return r
    return None


def test_equity_carries_fundamentals_and_derived_metrics():
    out = mx.metrics(None, "AAPL", "equity")
    keys = {s["key"] for s in out["sections"]}
    assert {"valuation", "quality", "growth", "risk", "returns", "range", "liquidity"} <= keys

    assert out["name"] == "Test Corp" and out["sector"] == "Technology"
    assert _row(out, "P/E (TTM)")["value"] == 28.0
    # Derived margins/growth, all in PERCENT units.
    assert _row(out, "Gross Margin")["value"] == pytest.approx(40.0)        # 160/400
    assert _row(out, "Operating Margin")["value"] == pytest.approx(30.0)    # 120/400
    assert _row(out, "ROE")["value"] == pytest.approx(30.0)                 # 0.30 fraction
    assert _row(out, "EPS Growth")["value"] == pytest.approx(20.0)          # 6/5 - 1
    assert _row(out, "Revenue Growth")["value"] == pytest.approx(400 / 350 * 100 - 100)
    assert _row(out, "Beta (mkt)")["value"] == 1.2


def test_crypto_omits_fundamentals_and_adds_btc_relative():
    out = mx.metrics(None, "ETH/USDT", "crypto")
    keys = {s["key"] for s in out["sections"]}
    assert "valuation" not in keys and "quality" not in keys and "growth" not in keys
    assert {"returns", "risk", "btc", "liquidity", "range"} <= keys
    assert out["note"] and "365" in out["note"]
    assert out["currency"] == "USDT"
    # BTC-relative is computed (synthetic BTC == this series → corr ≈ 1, beta ≈ 1, both finite).
    assert _row(out, "Correlation vs BTC")["value"] is not None


def test_percent_values_are_in_percent_units():
    out = mx.metrics(None, "AAPL", "equity")
    # A 420-day uptrend → 1Y return should be a double-digit percent, not a ~0.x fraction.
    assert abs(_row(out, "1Y")["value"]) > 1.0


def test_empty_history_returns_graceful_payload(monkeypatch):
    monkeypatch.setattr(
        mx, "fetch_bars",
        lambda *a, **k: (None, pd.DataFrame(columns=["open", "high", "low", "close", "volume"])),
    )
    out = mx.metrics(None, "NOPE", "equity")
    assert out["sections"] == [] and out["price"] is None and out["note"]
    assert out["period_metrics"] is None


def test_period_metrics_grid_for_chart_tab():
    out = mx.metrics(None, "AAPL", "equity")
    pm = out["period_metrics"]
    assert pm is not None
    # 420 days of synthetic history → 1M/3M/6M/1Y windows qualify; 2Y/3Y don't.
    assert pm["windows"] == ["1M", "3M", "6M", "1Y"]
    keys = {m["key"] for m in pm["metrics"]}
    assert keys == {"return", "ann_vol", "sharpe", "max_drawdown"}
    # every series is aligned to the window count
    for m in pm["metrics"]:
        assert len(m["values"]) == len(pm["windows"])
    # uptrending synthetic series → longer windows show larger cumulative return
    ret = next(m for m in pm["metrics"] if m["key"] == "return")["values"]
    assert ret[-1] > ret[0]


def test_rolling_series_for_chart_tab():
    out = mx.rolling(None, "AAPL", "equity", window=90)
    assert out["window"] == 90
    s = out["series"]
    assert set(s) == {"return", "ann_vol", "sharpe", "drawdown"}
    # 420-day synthetic history, 90d window → ~330 windowed points; drawdown has no warmup.
    assert len(s["return"]) > 200 and len(s["drawdown"]) > len(s["return"])
    # points are {time, value} with ISO dates and finite values
    p = s["sharpe"][-1]
    assert set(p) == {"time", "value"} and isinstance(p["value"], (int, float))
    assert p["time"].count("-") == 2
    # drawdown (underwater curve) is never positive
    assert all(pt["value"] <= 0.0001 for pt in s["drawdown"])


def test_rolling_window_is_clamped():
    # absurd window clamps into [5, 365] rather than erroring
    assert mx.rolling(None, "AAPL", "equity", window=99999)["window"] == 365
    assert mx.rolling(None, "AAPL", "equity", window=1)["window"] == 5
