"""Tests for the market-data serialization + quote math + refresh throttle (services.market).

Hermetic: the only IO seam here is qhfi's incremental lake refresh, which lives inside
fetch_bars/fetch_bars_intraday — those are NEVER called. Everything under test is pure: the
candle/quote serializers run on synthetic OHLCV frames (UTC DatetimeIndex, mirroring the parquet
lake), _span's history window is sealed by monkeypatching mkt.get_history_years to a fixed int,
and the refresh-throttle test calls clear_refresh_throttle() first so it is isolated from any
process-wide state. make_instrument is real (pure for equities). No network, no lake.

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_market.py -q`
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.deps import make_instrument
from app.services import market as mkt


def _synthetic_bars(periods: int = 5, base: float = 100.0) -> pd.DataFrame:
    """A small OHLCV frame with a daily UTC-aware DatetimeIndex ending today.

    UTC-aware to mirror the real parquet lake (datetime64[*, UTC]); to_candles/quote_from_bars
    serialize the index via .strftime / .timestamp so the tz must round-trip cleanly.
    """
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=periods, freq="D", tz="UTC")
    close = base + np.arange(periods, dtype=float)
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.arange(periods, dtype=float) + 1_000_000.0,
        },
        index=idx,
    )


# --------------------------------------------------------------------------- to_candles


def test_to_candles_daily_uses_date_strings():
    bars = _synthetic_bars(periods=4)
    out = mkt.to_candles(bars, intraday=False)

    assert set(out) == {"candles", "volume"}
    assert len(out["candles"]) == 4
    # daily -> "YYYY-MM-DD" string times
    for c, ts in zip(out["candles"], bars.index):
        assert c["time"] == ts.strftime("%Y-%m-%d")
        assert isinstance(c["time"], str)
        assert set(c) == {"time", "open", "high", "low", "close"}
    # OHLC rounded to 6dp (high = close*1.01 is exact to 6dp here)
    first = out["candles"][0]
    assert first["open"] == round(float(bars.iloc[0]["open"]), 6)
    assert first["high"] == round(float(bars.iloc[0]["high"]), 6)
    # volume aligned 1:1 with candles, same time keys, float values
    assert len(out["volume"]) == len(out["candles"])
    for v, c in zip(out["volume"], out["candles"]):
        assert v["time"] == c["time"]
        assert isinstance(v["value"], float)


def test_to_candles_intraday_uses_unix_seconds():
    bars = _synthetic_bars(periods=3)
    out = mkt.to_candles(bars, intraday=True)

    assert len(out["candles"]) == 3
    for c, ts in zip(out["candles"], bars.index):
        # intraday -> int unix seconds (what lightweight-charts wants for sub-day)
        assert c["time"] == int(ts.timestamp())
        assert isinstance(c["time"], int)
    # volume times match the candle (int) times
    for v, c in zip(out["volume"], out["candles"]):
        assert v["time"] == c["time"]


def test_to_candles_rounds_ohlc_to_six_decimals():
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=1, freq="D", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": [1.123456789],
            "high": [2.000000499],   # rounds down to 2.0
            "low": [0.0000005],      # banker's rounding -> 0.0 at 6dp
            "close": [3.1415926535],
            "volume": [42.0],
        },
        index=idx,
    )
    c = mkt.to_candles(bars)["candles"][0]
    assert c["open"] == 1.123457
    assert c["high"] == 2.0
    assert c["close"] == 3.141593


# --------------------------------------------------------------------------- quote_from_bars


def test_quote_from_bars_empty_and_single_and_normal():
    # empty -> all-None payload (exactly the 4 None keys, no high/low/volume)
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    q0 = mkt.quote_from_bars(empty)
    assert q0 == {"price": None, "change": None, "change_pct": None, "asof": None}

    # single row -> change/pct are 0 (prev == last)
    one = _synthetic_bars(periods=1, base=100.0)
    q1 = mkt.quote_from_bars(one)
    assert q1["price"] == 100.0
    assert q1["change"] == 0.0
    assert q1["change_pct"] == 0.0
    assert q1["asof"] == one.index[-1].strftime("%Y-%m-%d")
    assert q1["high"] == round(float(one.iloc[-1]["high"]), 6)
    assert q1["low"] == round(float(one.iloc[-1]["low"]), 6)
    assert q1["volume"] == float(one.iloc[-1]["volume"])

    # normal -> last close + day change/pct vs prior close
    bars = _synthetic_bars(periods=5, base=100.0)  # closes 100..104
    q = mkt.quote_from_bars(bars)
    assert q["price"] == 104.0
    assert q["change"] == 1.0                       # 104 - 103
    assert q["change_pct"] == round(1.0 / 103.0 * 100, 4)
    assert q["asof"] == bars.index[-1].strftime("%Y-%m-%d")


def test_quote_from_bars_zero_prev_close_pct_is_zero():
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=2, freq="D", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": [0.0, 1.0],
            "high": [0.0, 1.0],
            "low": [0.0, 1.0],
            "close": [0.0, 5.0],   # prev close == 0 -> guard makes pct 0.0
            "volume": [10.0, 20.0],
        },
        index=idx,
    )
    q = mkt.quote_from_bars(bars)
    assert q["price"] == 5.0
    assert q["change"] == 5.0
    assert q["change_pct"] == 0.0


# --------------------------------------------------------------------------- _span


def test_span_defaults_from_history_years(monkeypatch):
    # Seal the config seam: history window is a fixed 3 years regardless of Settings.
    monkeypatch.setattr(mkt, "get_history_years", lambda asset: 3)

    span = mkt._span(None, None, "equity")
    today = date.today()
    assert span.end == today
    assert span.start == today - timedelta(days=365 * 3)

    # explicit start/end are honored verbatim (config not consulted)
    s, e = date(2020, 1, 1), date(2021, 6, 30)
    span2 = mkt._span(s, e, "equity")
    assert span2.start == s and span2.end == e

    # explicit end, default start -> start derived from the given end, not today
    span3 = mkt._span(None, e, "crypto")
    assert span3.end == e
    assert span3.start == e - timedelta(days=365 * 3)


# --------------------------------------------------------------------------- refresh throttle


def test_claim_refresh_is_once_per_window_then_cleared():
    mkt.clear_refresh_throttle()  # isolate from any process-wide state

    ins = make_instrument("AAPL", "equity")
    # first call in the window claims the slot
    assert mkt._claim_refresh(ins, "equity") is True
    # immediate second call is throttled (thundering-herd guard)
    assert mkt._claim_refresh(ins, "equity") is False

    # a different (id, asset) key is independent
    other = make_instrument("MSFT", "equity")
    assert mkt._claim_refresh(other, "equity") is True

    # clearing forgets timestamps -> next call claims again
    dropped = mkt.clear_refresh_throttle()
    assert dropped == 2  # AAPL + MSFT keys were present
    assert mkt._claim_refresh(ins, "equity") is True


def test_clear_intraday_cache_returns_dropped_count():
    mkt.clear_intraday_cache()  # start clean
    assert mkt.clear_intraday_cache() == 0
    # seed the cache directly (no provider call) and confirm the drop count
    mkt._intraday_cache[("AAPL", "equity", "1m")] = (0.0, _synthetic_bars(periods=1))
    mkt._intraday_cache[("MSFT", "equity", "5m")] = (0.0, _synthetic_bars(periods=1))
    assert mkt.clear_intraday_cache() == 2
    assert mkt.clear_intraday_cache() == 0
