"""Technical indicators computed on a close-price series (pure pandas/numpy).

Each returns a pandas Series aligned to the input index; the router serializes them to
lightweight-charts line data. Kept here (not in qhfi) because they're presentation-layer
overlays, not part of the quant engine.
"""

from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, window: int = 20) -> pd.Series:
    return close.rolling(window).mean()


def ema(close: pd.Series, window: int = 20) -> pd.Series:
    return close.ewm(span=window, adjust=False).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / window, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return {"macd": macd_line, "signal": signal_line, "hist": macd_line - signal_line}


def bollinger(close: pd.Series, window: int = 20, n_std: float = 2.0) -> dict[str, pd.Series]:
    mid = close.rolling(window).mean()
    sd = close.rolling(window).std()
    return {"mid": mid, "upper": mid + n_std * sd, "lower": mid - n_std * sd}


def donchian(high: pd.Series, low: pd.Series, window: int = 20) -> dict[str, pd.Series]:
    """Donchian channel over the PRIOR ``window`` bars (shifted by one so the current bar can't
    trigger its own breakout). ``upper`` = highest high, ``lower`` = lowest low."""
    return {
        "upper": high.rolling(window).max().shift(1),
        "lower": low.rolling(window).min().shift(1),
    }


# Registry so the API can resolve "sma:20", "rsi:14" style spec strings.
_OVERLAYS = {"sma": sma, "ema": ema}            # render on the price pane
_OSCILLATORS = {"rsi": rsi}                      # render on a separate pane


def compute_spec(close: pd.Series, spec: str, intraday: bool = False) -> dict:
    """Resolve a single indicator spec string like ``sma:20`` or ``macd`` → JSON-ready dict.

    Returns ``{"name", "pane", "series": {label: [{time, value}, ...]}}``. Point times must
    match the bar payload exactly — date strings for daily, unix seconds for intraday — or
    the chart can't align them to candles.
    """
    name, _, arg = spec.partition(":")
    name = name.lower()

    def points(s: pd.Series) -> list[dict]:
        s = s.dropna()
        return [
            {
                "time": int(t.timestamp()) if intraday else t.strftime("%Y-%m-%d"),
                "value": round(float(v), 6),
            }
            for t, v in s.items()
        ]

    if name in _OVERLAYS:
        window = int(arg) if arg else 20
        return {"name": spec, "pane": "price", "series": {spec: points(_OVERLAYS[name](close, window))}}
    if name in _OSCILLATORS:
        window = int(arg) if arg else 14
        return {"name": spec, "pane": "lower", "series": {spec: points(_OSCILLATORS[name](close, window))}}
    if name == "macd":
        out = macd(close)
        return {"name": "macd", "pane": "lower", "series": {k: points(v) for k, v in out.items()}}
    if name in ("boll", "bollinger", "bb"):
        window = int(arg) if arg else 20
        out = bollinger(close, window)
        return {"name": "bollinger", "pane": "price", "series": {k: points(v) for k, v in out.items()}}
    raise ValueError(f"unknown indicator: {spec}")
