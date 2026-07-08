"""Per-symbol metrics tearsheet — asset-class-tailored analytics.

One instrument's "is it cheap, is it good, what is its risk" in a single payload. Reuses the
same qhfi metric functions the backtester and the portfolio risk endpoint use, so the numbers
agree across the app. Equities add valuation/quality/growth from the fundamentals snapshot +
income statement; crypto omits those (no issuer) and adds BTC-relative beta/correlation.

The payload is a generic list of labelled ``sections`` so the frontend renders uniformly — the
*selection* of sections is what differs by asset class. Annualization follows metrics.py:
252 trading days for equities, 365 for 24/7 crypto. Every percentage-flagged value is emitted
in PERCENT units (4.3 == 4.3%) so the client formats it directly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from qhfi.data.manager import DataManager
from qhfi.evaluation import metrics as M

from app.services import fundamentals as fa
from app.services.market import fetch_bars, quote_from_bars

# Trailing-return windows in trading days (daily bars), minus YTD/1Y which are appended specially.
_EQUITY_WINDOWS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126)]
_CRYPTO_WINDOWS = [("24h", 1), ("7d", 7), ("30d", 30), ("90d", 90)]

# Lookback windows for the period-comparison chart — trading days for equities, calendar days
# for 24/7 crypto. Only windows with enough history are emitted (short-history names show fewer).
_EQUITY_PERIODS = [("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 252), ("2Y", 504), ("3Y", 756)]
_CRYPTO_PERIODS = [("1M", 30), ("3M", 90), ("6M", 180), ("1Y", 365), ("2Y", 730), ("3Y", 1095)]


def _num(v: Any) -> float | None:
    """Coerce to a JSON-safe rounded float, mapping NaN/inf/None → None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if np.isfinite(f) else None


def _row(label: str, value: Any, fmt: str, hint: str | None = None) -> dict:
    out = {"label": label, "value": _num(value), "fmt": fmt}
    if hint:
        out["hint"] = hint
    return out


def _first(lst: list | None) -> float | None:
    return lst[0] if lst and lst[0] is not None else None


def _ratio_pct(a: Any, b: Any) -> float | None:
    """`a / b` as a percent, or None if either side is missing / b is zero."""
    a, b = _num(a), _num(b)
    if a is None or b is None or b == 0:
        return None
    return a / b * 100


def _yoy_pct(lst: list | None) -> float | None:
    """Year-over-year change between the two most-recent statement columns, in percent."""
    if not lst or len(lst) < 2:
        return None
    return _ratio_pct(lst[0] - lst[1] if lst[0] is not None and lst[1] is not None else None, lst[1])


def _frac_to_pct(v: Any) -> float | None:
    """yfinance ratios (roe, profitMargins, dividendYield) are fractions → percent."""
    v = _num(v)
    return None if v is None else v * 100


def _trailing_returns(close: pd.Series, windows: list[tuple[str, int]], crypto: bool) -> list[dict]:
    rows: list[dict] = []
    last = float(close.iloc[-1])
    for label, k in windows:
        if len(close) > k:
            prev = float(close.iloc[-1 - k])
            rows.append(_row(label, (last / prev - 1) * 100 if prev else None, "pct"))
        else:
            rows.append(_row(label, None, "pct"))

    # YTD vs the prior year's last close (falls back to the first print of the year).
    # Match the index timezone — lake bars are UTC-aware, so a naive cutoff won't compare.
    yr = date.today().year
    jan1 = pd.Timestamp(year=yr, month=1, day=1, tz=close.index.tz)
    prior = close[close.index < jan1]
    in_year = close[close.index >= jan1]
    ytd = None
    if len(in_year):
        base = float(prior.iloc[-1]) if len(prior) else float(in_year.iloc[0])
        ytd = (last / base - 1) * 100 if base else None
    rows.append(_row("YTD", ytd, "pct"))

    rows.append(_row("1Y", (last / float(close.iloc[-1 - (365 if crypto else 252)]) - 1) * 100
                      if len(close) > (365 if crypto else 252) else None, "pct"))
    return rows


def _risk_rows(rets: pd.Series, ppy: int, beta: Any = None) -> list[dict]:
    s = M.summary(rets, periods_per_year=ppy)
    rows = [
        _row("Ann. Volatility", s["ann_vol"] * 100, "pctp"),
        _row("Sharpe", s["sharpe"], "num"),
        _row("Sortino", s["sortino"], "num"),
        _row("Max Drawdown", s["max_drawdown"] * 100, "pct"),
        _row("Calmar", s["calmar"], "num"),
        _row("CAGR", s["cagr"] * 100, "pct"),
    ]
    if beta is not None:
        rows.append(_row("Beta (mkt)", beta, "num"))
    return rows


def _range_rows(bars: pd.DataFrame, price: float, n: int) -> list[dict]:
    window = bars.tail(n)
    hi = float(window["high"].max())
    lo = float(window["low"].min())
    pos = (price - lo) / (hi - lo) * 100 if hi > lo else None
    return [
        _row("52W High", hi, "price"),
        _row("52W Low", lo, "price"),
        _row("% From High", (price / hi - 1) * 100 if hi else None, "pct"),
        _row("Range Position", pos, "pctp"),
    ]


def _period_metrics(rets: pd.Series, ppy: int, crypto: bool) -> dict | None:
    """Key risk/return metrics computed over a ladder of trailing windows, for the Chart tab's
    period comparison. Each metric is a parallel array aligned to ``windows``."""
    defs = _CRYPTO_PERIODS if crypto else _EQUITY_PERIODS
    labels: list[str] = []
    ret: list[float | None] = []
    vol: list[float | None] = []
    shp: list[float | None] = []
    mdd: list[float | None] = []
    for label, wd in defs:
        if len(rets) < wd:
            continue
        r = rets.tail(wd)
        s = M.summary(r, periods_per_year=ppy)
        labels.append(label)
        ret.append(_num(((1 + r).prod() - 1) * 100))
        vol.append(_num(s["ann_vol"] * 100))
        shp.append(_num(s["sharpe"]))
        mdd.append(_num(s["max_drawdown"] * 100))
    if len(labels) < 2:
        return None
    return {
        "windows": labels,
        "metrics": [
            {"key": "return", "label": "Return", "fmt": "pct", "values": ret},
            {"key": "ann_vol", "label": "Ann. Volatility", "fmt": "pctp", "values": vol},
            {"key": "sharpe", "label": "Sharpe", "fmt": "num", "values": shp},
            {"key": "max_drawdown", "label": "Max Drawdown", "fmt": "pct", "values": mdd},
        ],
    }


def rolling(dm: DataManager, symbol: str, asset: str = "equity", window: int = 90) -> dict:
    """Rolling-window time series for the Chart tab's Rolling view.

    Return / Ann. Volatility / Sharpe are computed over a trailing ``window``-day window at each
    point; Drawdown is the underwater curve off the running peak (window-independent). All series
    are ``{time, value}`` lists; pct-style values are in percent units.
    """
    asset = asset.lower()
    crypto = asset == "crypto"
    window = max(5, min(int(window), 365))
    end = date.today()
    start = end - timedelta(days=1200)
    _, bars = fetch_bars(dm, symbol, asset, start, end)

    out = {
        "symbol": symbol.upper(),
        "asset": asset,
        "window": window,
        "series": {"return": [], "ann_vol": [], "sharpe": [], "drawdown": []},
    }
    if bars.empty or "close" not in bars or bars["close"].dropna().empty:
        return out

    close = bars["close"].dropna()
    rets = close.pct_change()
    sq = np.sqrt(365 if crypto else 252)

    std = rets.rolling(window).std(ddof=0)
    ret_s = (close / close.shift(window) - 1) * 100
    vol_s = std * sq * 100
    sharpe_s = (rets.rolling(window).mean() / std.replace(0, np.nan)) * sq
    dd_s = (close / close.cummax() - 1) * 100

    def pts(s: pd.Series) -> list[dict]:
        return [
            {"time": i.strftime("%Y-%m-%d"), "value": _num(v)}
            for i, v in s.dropna().items()
            if _num(v) is not None
        ]

    out["series"] = {
        "return": pts(ret_s),
        "ann_vol": pts(vol_s),
        "sharpe": pts(sharpe_s),
        "drawdown": pts(dd_s),
    }
    return out


def _section(key: str, label: str, rows: list[dict]) -> dict | None:
    """Drop sections whose rows are entirely missing (e.g. no fundamentals on file)."""
    rows = [r for r in rows if r is not None]
    if not rows or all(r["value"] is None for r in rows):
        return None
    return {"key": key, "label": label, "rows": rows}


def metrics(dm: DataManager, symbol: str, asset: str = "equity") -> dict:
    asset = asset.lower()
    crypto = asset == "crypto"
    end = date.today()
    start = end - timedelta(days=1200)  # ~3.3y, enough history for the 3Y period comparison

    _, bars = fetch_bars(dm, symbol, asset, start, end)
    base = {
        "symbol": symbol.upper(),
        "asset": asset,
        "as_of": None,
        "price": None,
        "change_pct": None,
        "currency": None,
        "name": None,
        "sector": None,
        "note": None,
        "sections": [],
        "period_metrics": None,
    }
    if bars.empty or "close" not in bars or bars["close"].dropna().empty:
        base["note"] = "No price history available for this symbol."
        return base

    close = bars["close"].dropna()
    price = float(close.iloc[-1])
    q = quote_from_bars(bars)
    ppy = 365 if crypto else 252
    n52 = 365 if crypto else 252
    rets = close.pct_change().dropna()

    base.update(as_of=q["asof"], price=q["price"], change_pct=q["change_pct"])

    sections: list[dict | None] = []

    if crypto:
        base["currency"] = symbol.split("/")[-1] if "/" in symbol else "USD"
        base["note"] = (
            "Annualized on a 365-day calendar. Crypto has no issuer fundamentals — "
            "valuation/quality metrics don't apply."
        )
        sections.append(_section("returns", "Trailing Returns",
                                 _trailing_returns(close, _CRYPTO_WINDOWS, crypto)))
        sections.append(_section("risk", "Risk / Return", _risk_rows(rets, ppy)))

        # BTC-relative (skip when the symbol *is* BTC).
        btc_rows: list[dict] = []
        if not symbol.upper().startswith("BTC/"):
            try:
                _, btc = fetch_bars(dm, "BTC/USDT", "crypto", start, end)
                if not btc.empty:
                    joined = pd.concat(
                        [rets.rename("a"), btc["close"].pct_change().rename("b")],
                        axis=1, sort=False,
                    ).dropna()
                    if len(joined) >= 20 and joined["b"].var() > 0:
                        btc_rows = [
                            _row("Beta vs BTC", joined["a"].cov(joined["b"]) / joined["b"].var(), "num"),
                            _row("Correlation vs BTC", joined["a"].corr(joined["b"]), "ratio"),
                        ]
            except Exception:  # noqa: BLE001 - BTC reference is best-effort
                btc_rows = []
        sections.append(_section("btc", "BTC-Relative", btc_rows))

        notional = float(bars["volume"].iloc[-1]) * price
        sections.append(_section("liquidity", "Liquidity", [
            _row("24h Volume ($)", notional, "usd"),
            _row("24h Volume (base)", float(bars["volume"].iloc[-1]), "num"),
        ]))
        sections.append(_section("range", "52-Week Range", _range_rows(bars, price, n52)))

    else:
        snap = fa.snapshot(symbol)
        fin = fa.financials(symbol)
        rows_fin = fin.get("rows", {})
        rev, gp = rows_fin.get("Total Revenue"), rows_fin.get("Gross Profit")
        oi, eps_row = rows_fin.get("Operating Income"), rows_fin.get("Diluted EPS")

        base["currency"] = snap.get("currency")
        base["name"] = snap.get("name")
        base["sector"] = snap.get("sector")

        sections.append(_section("valuation", "Valuation", [
            _row("Market Cap", snap.get("market_cap"), "usd"),
            _row("P/E (TTM)", snap.get("pe"), "x"),
            _row("Forward P/E", snap.get("forward_pe"), "x"),
            _row("P/B", snap.get("pb"), "x"),
            _row("EPS (TTM)", snap.get("eps"), "num"),
            _row("Dividend Yield", _frac_to_pct(snap.get("dividend_yield")), "pct"),
        ]))
        sections.append(_section("quality", "Profitability & Quality", [
            _row("ROE", _frac_to_pct(snap.get("roe")), "pct"),
            _row("Gross Margin", _ratio_pct(_first(gp), _first(rev)), "pct"),
            _row("Operating Margin", _ratio_pct(_first(oi), _first(rev)), "pct"),
            _row("Net Margin", _frac_to_pct(snap.get("profit_margin")), "pct"),
        ]))
        sections.append(_section("growth", "Growth (YoY)", [
            _row("Revenue Growth", _yoy_pct(rev), "pct"),
            _row("EPS Growth", _yoy_pct(eps_row), "pct"),
        ]))
        sections.append(_section("risk", "Risk / Return", _risk_rows(rets, ppy, beta=snap.get("beta"))))
        sections.append(_section("returns", "Trailing Returns",
                                 _trailing_returns(close, _EQUITY_WINDOWS, crypto)))
        sections.append(_section("range", "52-Week Range", _range_rows(bars, price, n52)))
        sections.append(_section("liquidity", "Liquidity", [
            _row("Avg Daily $ Vol (20d)", float((bars["close"] * bars["volume"]).tail(20).mean()), "usd"),
        ]))

    base["sections"] = [s for s in sections if s is not None]
    base["period_metrics"] = _period_metrics(rets, ppy, crypto)
    return base
