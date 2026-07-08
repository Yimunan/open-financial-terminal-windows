"""Market data service — fetch/cache bars through qhfi and serialize for the frontend.

Daily bars go through qhfi's DataManager (parquet lake, incremental refresh). Intraday bars
are fetched directly from the providers (ccxt for crypto, yfinance for equities) into a small
in-memory TTL cache — they are transient view data and deliberately never written to the lake.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from threading import BoundedSemaphore, Lock

import pandas as pd
from qhfi.core.types import AssetClass, DateRange, Instrument, Universe
from qhfi.data.manager import DataManager

from app.config import get_history_years, get_intraday_ttl
from app.deps import make_instrument

#: timeframe → (yfinance interval, yfinance period). Crypto uses the timeframe string as-is.
INTRADAY_TIMEFRAMES: dict[str, tuple[str, str]] = {
    "1m": ("1m", "5d"),      # yfinance caps 1m history at ~7 days
    "5m": ("5m", "1mo"),
    "15m": ("15m", "1mo"),
    "1h": ("1h", "6mo"),
}

#: Intraday-bars TTL cache. The lifetime + the default daily-history window are read from
#: Settings → Market Data at call-time (get_intraday_ttl / get_history_years), so a saved change
#: applies without a restart.
_intraday_cache: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}
_intraday_lock = Lock()


def clear_intraday_cache() -> int:
    """Empty the intraday-bars TTL cache; returns how many entries were dropped."""
    with _intraday_lock:
        n = len(_intraday_cache)
        _intraday_cache.clear()
    return n


#: Daily-refresh throttle + concurrency cap. The watchlist polls /api/quote for each symbol every
#: few seconds; left unchecked, every call triggers a qhfi network refresh (yfinance). When the lake
#: is stale (e.g. the clock is ahead of the provider's data) those refreshes are slow/failing, and
#: because /api/quote is a sync endpoint they accumulate in FastAPI's thread pool until even
#: /api/health can't get a worker — the whole server wedges. Two guards make that impossible:
#:   1. per-symbol TTL — refresh a symbol's lake at most once per window (daily bars change ≤1×/day);
#:   2. a non-blocking semaphore — at most N refreshes run at once, so the path can never saturate
#:      the pool no matter how many distinct symbols are requested in a burst (e.g. a board).
#: Store reads (local parquet) stay live on every call, so quotes never go stale beyond the lake.
_DAILY_REFRESH_TTL = 60.0  # seconds
_REFRESH_MAX_CONCURRENCY = 4
_refresh_seen: dict[tuple[str, str], float] = {}
_refresh_lock = Lock()
_refresh_sem = BoundedSemaphore(_REFRESH_MAX_CONCURRENCY)


def _claim_refresh(instrument: Instrument, asset: str) -> bool:
    """Atomically claim the refresh slot for this symbol if its TTL window has elapsed.

    Returns True for exactly one caller per window; concurrent callers (the thundering herd a
    watchlist creates) get False and serve straight from the lake.
    """
    key = (instrument.id, asset)
    now = time.monotonic()
    with _refresh_lock:
        last = _refresh_seen.get(key)
        if last is not None and now - last < _DAILY_REFRESH_TTL:
            return False
        _refresh_seen[key] = now
        return True


def clear_refresh_throttle() -> int:
    """Forget all refresh timestamps so the next call for each symbol refreshes immediately."""
    with _refresh_lock:
        n = len(_refresh_seen)
        _refresh_seen.clear()
    return n


def _span(start: date | None, end: date | None, asset: str = "equity") -> DateRange:
    end = end or date.today()
    start = start or (end - timedelta(days=365 * get_history_years(asset)))
    return DateRange(start=start, end=end)


def fetch_bars(
    dm: DataManager, symbol: str, asset: str = "equity",
    start: date | None = None, end: date | None = None,
) -> tuple[Instrument, pd.DataFrame]:
    """Bring the symbol's cache up to date, then return (instrument, OHLCV frame).

    Uses qhfi's incremental refresh so repeat calls are offline/cheap. The default history window
    (when no explicit start is given) is the asset class's per-category setting.
    """
    instrument = make_instrument(symbol, asset)
    span = _span(start, end, asset)
    # Refresh the lake at most once per symbol per window, and never more than
    # _REFRESH_MAX_CONCURRENCY at a time, so polling can't wedge the thread pool (see throttle note).
    if _claim_refresh(instrument, asset) and _refresh_sem.acquire(blocking=False):
        try:
            universe = Universe(name=f"_adhoc_{instrument.id}", instruments=[instrument])
            dm.update(universe, span)
        except Exception:  # noqa: BLE001 - a provider hiccup must not break a quote we can serve from the lake
            pass
        finally:
            _refresh_sem.release()
    if not dm.store.has(instrument):
        # FICC instruments (rates futures, spot FX) are also pre-pulled into the qhfi research lake
        # (a different root from the terminal's own cache). Serve that copy if the DataManager hasn't
        # fetched its own yet (e.g. first call offline) so the watchlist row shows data immediately.
        if instrument.asset_class in (AssetClass.RATES, AssetClass.FX, AssetClass.COMMODITY):
            from app.deps import get_qhfi_market_store

            lake = get_qhfi_market_store()
            if lake.has(instrument):
                return instrument, lake.load(instrument).loc[str(span.start):str(span.end)]
        return instrument, pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    bars = dm.store.load(instrument)
    bars = bars.loc[str(span.start):str(span.end)]
    return instrument, bars


def fetch_bars_intraday(symbol: str, asset: str, timeframe: str) -> tuple[Instrument, pd.DataFrame]:
    """Intraday OHLCV straight from the provider, behind a short TTL cache.

    Crypto (ccxt) has deep intraday history; equities (yfinance) are shallow — 1m is capped
    at ~7 days. Callers surface that honestly rather than padding.
    """
    if timeframe not in INTRADAY_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe '{timeframe}' (use {list(INTRADAY_TIMEFRAMES)} or 1d)")
    instrument = make_instrument(symbol, asset)
    key = (instrument.id, asset, timeframe)
    now = time.monotonic()
    with _intraday_lock:
        hit = _intraday_cache.get(key)
        if hit and now - hit[0] < get_intraday_ttl(asset):
            return instrument, hit[1]

    if instrument.asset_class == AssetClass.CRYPTO:
        frame = _intraday_crypto(instrument, timeframe)
    else:
        frame = _intraday_yfinance(instrument, timeframe)

    with _intraday_lock:
        _intraday_cache[key] = (now, frame)
    return instrument, frame


def _intraday_crypto(instrument: Instrument, timeframe: str, limit: int = 500) -> pd.DataFrame:
    import ccxt

    exchange = getattr(ccxt, instrument.exchange or "binance")({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(instrument.id, timeframe=timeframe, limit=limit)
    if not raw:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    frame.index = pd.to_datetime(frame.pop("ts"), unit="ms", utc=True)
    return frame


def _intraday_yfinance(instrument: Instrument, timeframe: str) -> pd.DataFrame:
    import yfinance as yf

    interval, period = INTRADAY_TIMEFRAMES[timeframe]
    hist = yf.Ticker(instrument.id).history(period=period, interval=interval, auto_adjust=True)
    if hist.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
    frame.columns = ["open", "high", "low", "close", "volume"]
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame


def to_candles(bars: pd.DataFrame, intraday: bool = False) -> dict:
    """Frame → lightweight-charts candlestick + volume payload.

    Daily bars use date strings; intraday bars use unix seconds (what lightweight-charts
    expects for sub-day resolutions).
    """
    candles, volume = [], []
    for ts, row in bars.iterrows():
        t = int(ts.timestamp()) if intraday else ts.strftime("%Y-%m-%d")
        candles.append({
            "time": t,
            "open": round(float(row["open"]), 6),
            "high": round(float(row["high"]), 6),
            "low": round(float(row["low"]), 6),
            "close": round(float(row["close"]), 6),
        })
        volume.append({"time": t, "value": float(row["volume"])})
    return {"candles": candles, "volume": volume}


def quote_from_bars(bars: pd.DataFrame) -> dict:
    """Last close + day/period change from a bars frame (EOD/polled, not realtime)."""
    if bars.empty:
        return {"price": None, "change": None, "change_pct": None, "asof": None}
    last = bars.iloc[-1]
    prev = bars.iloc[-2] if len(bars) > 1 else last
    price = float(last["close"])
    change = price - float(prev["close"])
    pct = (change / float(prev["close"]) * 100) if prev["close"] else 0.0
    return {
        "price": round(price, 6),
        "change": round(change, 6),
        "change_pct": round(pct, 4),
        "high": round(float(last["high"]), 6),
        "low": round(float(last["low"]), 6),
        "volume": float(last["volume"]),
        "asof": bars.index[-1].strftime("%Y-%m-%d"),
    }
