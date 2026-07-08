"""Chart Studio agent — turn a natural-language request into a rendered chart.

Mirrors the factor-monitor / backtest chat agents, but instead of a multi-step ReAct loop it
makes ONE structured LLM call per user message (chart requests are single-shot) that returns a
validated ChartAction ``{tool, args...}``. The backend resolves the action into a ready-to-render
payload and streams it back as a ``chart`` frame. Refinement works by feeding the previous action
back into the prompt as "Current chart:" so "add RSI" returns a full amended action.

Every tool maps to existing data: price→bars+indicators, compare→normalized returns over bars,
rolling→metrics.rolling, macro→macro/rates lake, correlation→portfolio.risk.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from qhfi.data.macro import MacroStore
from qhfi.data.manager import DataManager
from qhfi.data.rates import RatesStore
from qhfi.research.client import LLMClient

from app.indicators import compute_spec
from app.services import fundamentals as fa
from app.services import macro as macro_svc
from app.services import metrics as mx
from app.services import portfolio as pf
from app.services.agent_assistant import _strip_to_json
from app.services.market import fetch_bars, fetch_bars_intraday, to_candles

TIMEFRAMES = ["1m", "5m", "15m", "1h", "1d"]
STYLES = ["candles", "area"]
LOOKBACKS = {"1M": 31, "3M": 93, "6M": 186, "YTD": None, "1Y": 372, "3Y": 1100}
METRICS = ["return", "ann_vol", "sharpe", "drawdown"]
WINDOWS = [30, 60, 90, 180]
# Volatility-cone horizons (trading days) and the percentile bands drawn at each.
_CONE_WINDOWS = [10, 21, 42, 63, 126, 252]
_CONE_LEVELS = [
    ("min", "series3"), ("p25", "series2"), ("median", "accent"),
    ("p75", "series2"), ("max", "series3"), ("current", "up"),
]
# Trailing-return periods as (label, lookback days); YTD is computed specially. Trading days for
# equities, calendar days for 24/7 crypto.
_RET_PERIODS_EQ = [("1M", 21), ("3M", 63), ("6M", 126), ("YTD", None), ("1Y", 252), ("3Y", 756)]
_RET_PERIODS_CR = [("1M", 30), ("3M", 90), ("6M", 180), ("YTD", None), ("1Y", 365), ("3Y", 1095)]
TENORS = ["1M", "3M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
# Tenor → x-axis position in years, for the yield-curve snapshot (yield vs maturity).
_TENOR_YEARS = {
    "1M": 1 / 12, "3M": 0.25, "6M": 0.5, "1Y": 1.0, "2Y": 2.0, "3Y": 3.0,
    "5Y": 5.0, "7Y": 7.0, "10Y": 10.0, "20Y": 20.0, "30Y": 30.0,
}
# Fundamentals: absolute income-statement $ lines (rendered as bars) + computed margins/EPS (lines).
_FUND_LINES = {
    "revenue": "Total Revenue", "gross_profit": "Gross Profit",
    "operating_income": "Operating Income", "net_income": "Net Income",
}
_FUND_MARGINS = {  # numerator line ÷ Total Revenue, in percent
    "gross_margin": "Gross Profit", "operating_margin": "Operating Income", "net_margin": "Net Income",
}
FUND_METRICS = [*_FUND_LINES, "eps", *_FUND_MARGINS]
FUND_FREQS = ["annual", "quarterly"]
# Curated macro series reliably in the lake (the agent may also try other FRED ids).
MACRO_HINTS = {
    "CPIAUCSL": "US CPI", "GDPC1": "US real GDP", "UNRATE": "US unemployment",
    "FEDFUNDS": "Fed funds rate", "PAYEMS": "US payrolls", "M2SL": "US M2 money supply",
    "INDPRO": "US industrial production", "UMCSENT": "US consumer sentiment",
}
_SERIES_COLORS = ["accent", "series1", "series2", "series3", "series4", "up"]

CHART_ACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "enum": ["price", "compare", "ratio", "rolling", "rolling_corr", "rolling_beta", "distribution", "vol_cone", "trailing_returns", "seasonality", "macro", "macro_compare", "curve", "term_spread", "correlation", "fundamentals"]},
        "title": {"type": "string"},
        "rationale": {"type": "string"},
        "symbol": {"type": "string"},
        "symbols": {"type": "array", "items": {"type": "string"}},
        "asset": {"type": "string", "enum": ["equity", "crypto"]},
        "timeframe": {"type": "string", "enum": TIMEFRAMES},
        "style": {"type": "string", "enum": STYLES},
        "indicators": {"type": "array", "items": {"type": "string"}},
        "lookback": {"type": "string", "enum": list(LOOKBACKS)},
        "metric": {"type": "string", "enum": METRICS},
        "window": {"type": "integer", "enum": WINDOWS},
        "series_id": {"type": "string"},
        "series_ids": {"type": "array", "items": {"type": "string"}},
        "norm": {"type": "string", "enum": ["index", "zscore", "raw"]},
        "tenor": {"type": "string", "enum": TENORS},
        "tenor2": {"type": "string", "enum": TENORS},
        "days": {"type": "integer"},
        "fund_metric": {"type": "string", "enum": FUND_METRICS},
        "freq": {"type": "string", "enum": FUND_FREQS},
        "n_periods": {"type": "integer"},
        "bins": {"type": "integer"},
        "years": {"type": "integer"},
        "benchmark": {"type": "string"},
    },
    "required": ["tool", "title"],
}


def _system() -> str:
    macro_list = ", ".join(f"{k} ({v})" for k, v in MACRO_HINTS.items())
    return (
        "You translate a user's request into ONE chart specification as a single JSON object. "
        "Pick exactly one tool and fill only the fields that tool needs:\n"
        "- price: a single instrument's price chart. fields: symbol, asset(equity|crypto), "
        f"timeframe({'/'.join(TIMEFRAMES)}), style(candles|area), indicators[] (spec strings: "
        "sma:N, ema:N, bollinger:N, rsi:N, macd).\n"
        "- compare: overlay normalized cumulative returns of several names. fields: symbols[] "
        f"(2-6), asset, lookback({'/'.join(LOOKBACKS)}).\n"
        "- ratio: the price ratio (relative-value spread) A/B of two instruments over time, with a "
        f"mean line. fields: symbols[] (exactly 2, order = A then B), asset, lookback({'/'.join(LOOKBACKS)}). "
        "Use for 'GLD vs SLV ratio', 'AAPL/MSFT spread', pairs/relative-value questions.\n"
        "- rolling_corr: rolling return-correlation BETWEEN two instruments over time (a single "
        f"line in [-1,1]). fields: symbols[] (exactly 2), asset, window({'/'.join(str(w) for w in WINDOWS)}), "
        f"lookback({'/'.join(LOOKBACKS)}). Use for 'is BTC still correlated with the Nasdaq?', "
        "'rolling correlation of AAPL and MSFT'. (correlation = a static heatmap; this is over time.)\n"
        "- rolling_beta: rolling beta of ONE instrument vs a benchmark over time (default SPY for "
        f"equities, BTC/USDT for crypto). fields: symbol, asset, benchmark, window({'/'.join(str(w) for w in WINDOWS)}), "
        f"lookback({'/'.join(LOOKBACKS)}). Use for 'AAPL beta to the S&P over time', 'is NVDA's market "
        "sensitivity rising?'.\n"
        "- distribution: a histogram of an instrument's DAILY RETURNS (return % on x, frequency on "
        f"y) with mean + 5% VaR markers. fields: symbol, asset, lookback({'/'.join(LOOKBACKS)}), bins. "
        "Use for 'distribution of AAPL returns', 'how fat are BTC's tails?', return-histogram requests.\n"
        "- vol_cone: a volatility cone — annualized realized-vol percentile bands across horizons + the "
        f"current reading. fields: symbol, asset, lookback({'/'.join(LOOKBACKS)}). Use for 'AAPL volatility "
        "cone', 'is NVDA's vol high vs history?', vol-term-structure questions.\n"
        "- trailing_returns: trailing total returns over 1M/3M/6M/YTD/1Y/3Y as bars. fields: symbol, "
        "asset. Use for 'AAPL trailing returns', 'how has NVDA done across timeframes', performance "
        "summary.\n"
        "- rolling: a rolling-window analytic over time. fields: symbol, asset, "
        f"metric({'/'.join(METRICS)}), window({'/'.join(str(w) for w in WINDOWS)}).\n"
        "- macro: a macro time series. fields: series_id (one of: " + macro_list + ") OR "
        f"tenor({'/'.join(TENORS)}) for a US Treasury yield over time.\n"
        "- macro_compare: overlay SEVERAL macro series on one chart, normalized. fields: series_ids[] "
        "(2-5 FRED ids from the list above), norm(index|zscore|raw), years. Use for 'CPI vs unemployment "
        "vs Fed funds', 'compare M2 and inflation'. index=growth-since-start, zscore=co-movement.\n"
        "- curve: the US Treasury yield-curve SNAPSHOT (yield vs maturity) at the latest date, with a "
        "prior-date overlay. fields: days (how far back the comparison curve is, default 365). Use for "
        "'show the yield curve', 'is the curve inverted?', 'yield curve vs a year ago'. (macro tenor = "
        "one maturity over time; curve = all maturities at one date.)\n"
        "- term_spread: a Treasury term spread over time = long-tenor yield − short-tenor yield "
        f"(default 10Y − 3M). fields: tenor (long), tenor2 (short), both from {'/'.join(TENORS)}. Use for "
        "'10Y minus 3M spread', 'is the curve inverted over time', '2s10s'. Below zero = inverted.\n"
        "- correlation: a correlation heatmap. fields: symbols[] (2-12), asset, days.\n"
        "- seasonality: a monthly-return calendar heatmap (year × month) of one instrument, with an "
        "Avg row. fields: symbol, asset, years. Use for 'monthly returns of AAPL', 'seasonality of "
        "NVDA', 'which months are strong'. (Lake history is ~3y, so the calendar is short.)\n"
        "- fundamentals: a company's income-statement metric over reporting periods (equities only). "
        f"fields: symbol, fund_metric({'/'.join(FUND_METRICS)}), freq(annual|quarterly), n_periods. "
        "Use this for revenue/profit/EPS/margin history questions (e.g. 'AAPL revenue over time', "
        "'NVDA net margin by quarter').\n"
        "Crypto symbols look like BTC/USDT and use asset=crypto; equities are bare tickers "
        "(AAPL) with asset=equity. Always include a short human title. If the user is refining a "
        "previous chart (shown as 'Current chart'), return the FULL updated spec, not just the change."
    )


@dataclass
class ChartDeps:
    dm: DataManager
    macro_store: MacroStore
    rates_store: RatesStore
    llm: LLMClient
    model: str | None = None


# ── tool resolvers ───────────────────────────────────────────────────────────────

def _series_points(s: pd.Series) -> list[dict]:
    s = s.dropna()
    return [{"time": i.strftime("%Y-%m-%d"), "value": round(float(v), 6)} for i, v in s.items()]


def _resolve_price(deps: ChartDeps, a: dict) -> dict:
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    tf = a.get("timeframe") if a.get("timeframe") in TIMEFRAMES else "1d"
    style = a.get("style") if a.get("style") in STYLES else "candles"
    intraday = tf != "1d"
    if intraday:
        _, bars = fetch_bars_intraday(symbol, asset, tf)
    else:
        _, bars = fetch_bars(deps.dm, symbol, asset)
    if bars.empty:
        raise ValueError(f"no price history for {symbol}")
    cdata = to_candles(bars, intraday)
    inds: list[dict] = []
    for spec in (a.get("indicators") or [])[:5]:
        try:
            inds.append(compute_spec(bars["close"], str(spec), intraday))
        except Exception:  # noqa: BLE001 - skip an unknown/garbled indicator spec
            continue
    return {
        "engine": "price",
        "price": {
            "symbol": symbol, "asset": asset, "timeframe": tf, "style": style,
            "candles": cdata["candles"], "volume": cdata["volume"], "indicators": inds,
        },
        "open_params": {
            "symbol": symbol, "asset": asset, "timeframe": tf, "chartType": style,
            "indicators": [i["name"] for i in inds],
        },
    }


def _lookback_start(lookback: str) -> date:
    today = date.today()
    if lookback == "YTD":
        return date(today.year, 1, 1)
    return today - timedelta(days=LOOKBACKS.get(lookback) or 372)


def _resolve_compare(deps: ChartDeps, a: dict) -> dict:
    symbols = [str(s).upper() for s in (a.get("symbols") or [])][:6]
    if len(symbols) < 2:
        raise ValueError("comparison needs at least 2 symbols")
    asset = a.get("asset") or ("crypto" if any("/" in s for s in symbols) else "equity")
    lookback = a.get("lookback") if a.get("lookback") in LOOKBACKS else "1Y"
    start = _lookback_start(lookback)
    specs = []
    for idx, sym in enumerate(symbols):
        try:
            _, bars = fetch_bars(deps.dm, sym, asset, start, date.today())
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the overlay
            continue
        close = bars["close"].dropna()
        if close.empty:
            continue
        norm = (close / float(close.iloc[0]) - 1.0) * 100.0
        specs.append({
            "points": _series_points(norm),
            "colorKey": _SERIES_COLORS[idx % len(_SERIES_COLORS)],
            "kind": "line",
            "title": sym,
        })
    if not specs:
        raise ValueError("no price history for the requested symbols")
    return {"engine": "series", "series": {"title": f"Normalized return · {lookback}", "specs": specs}}


def _two_closes(deps: ChartDeps, symbols: list[str], asset: str, start: date) -> pd.DataFrame:
    """Fetch two instruments' closes and inner-join on common dates (the pairwise basis for
    ratio / rolling-correlation). Raises if either lacks history or they never overlap."""
    closes = []
    for sym in symbols:
        _, bars = fetch_bars(deps.dm, sym, asset, start, date.today())
        close = bars["close"].dropna() if not bars.empty else pd.Series(dtype=float)
        if close.empty:
            raise ValueError(f"no price history for {sym}")
        closes.append(close.rename(sym))
    joined = pd.concat(closes, axis=1, join="inner").dropna()
    if joined.empty:
        raise ValueError(f"no overlapping history for {symbols[0]} and {symbols[1]}")
    return joined


def _resolve_ratio(deps: ChartDeps, a: dict) -> dict:
    """Price ratio (relative-value spread) of two instruments over time, with its mean band.

    A/B above its mean = A rich vs B; below = A cheap. The flat mean line makes mean-reversion
    setups legible at a glance. This differs from `compare`, which overlays each name's own return.
    """
    symbols = [str(s).upper() for s in (a.get("symbols") or [])][:2]
    if len(symbols) < 2:
        raise ValueError("a ratio needs exactly 2 symbols (A/B)")
    asset = a.get("asset") or ("crypto" if any("/" in s for s in symbols) else "equity")
    lookback = a.get("lookback") if a.get("lookback") in LOOKBACKS else "1Y"
    joined = _two_closes(deps, symbols, asset, _lookback_start(lookback))
    ratio = joined[symbols[0]] / joined[symbols[1]]
    pts = _series_points(ratio)
    mean = round(float(ratio.mean()), 6)
    label = f"{symbols[0]} / {symbols[1]}"
    specs = [
        {"points": pts, "colorKey": "accent", "kind": "line", "title": label},
        {"points": [{"time": pts[0]["time"], "value": mean}, {"time": pts[-1]["time"], "value": mean}],
         "colorKey": "series4", "kind": "line", "title": "mean"},
    ]
    return {"engine": "series", "series": {"title": f"{label} · ratio ({lookback})", "specs": specs}}


def _resolve_rolling_corr(deps: ChartDeps, a: dict) -> dict:
    """Rolling return-correlation between two instruments over time.

    Answers "is this pair's relationship stable, or does it break down in stress?" — the
    diversification-regime / pairs-stability view. Single line bounded [-1, 1]; the engine draws
    a zero baseline automatically when it crosses zero. Distinct from `correlation` (a static
    snapshot heatmap) and `ratio` (the price spread).
    """
    symbols = [str(s).upper() for s in (a.get("symbols") or [])][:2]
    if len(symbols) < 2:
        raise ValueError("rolling correlation needs exactly 2 symbols")
    asset = a.get("asset") or ("crypto" if any("/" in s for s in symbols) else "equity")
    window = a.get("window") if a.get("window") in WINDOWS else 90
    lookback = a.get("lookback") if a.get("lookback") in LOOKBACKS else "1Y"
    # Pull extra warm-up so the visible line fills the whole requested lookback.
    start = _lookback_start(lookback) - timedelta(days=window * 2)
    joined = _two_closes(deps, symbols, asset, start)
    if len(joined) < window + 5:
        raise ValueError(f"not enough overlapping history for a {window}d rolling correlation")
    rets = joined.pct_change()
    corr = rets[symbols[0]].rolling(window).corr(rets[symbols[1]])
    pts = _series_points(corr)
    if not pts:
        raise ValueError("rolling correlation produced no points")
    label = f"{symbols[0]}↔{symbols[1]} {window}d"
    return {"engine": "series", "series": {"title": f"Rolling correlation · {window}d", "specs": [
        {"points": pts, "colorKey": "accent", "kind": "line", "title": label},
    ]}}


def _resolve_distribution(deps: ChartDeps, a: dict) -> dict:
    """Histogram of an instrument's DAILY RETURNS over a lookback, with mean + 5%-VaR markers.

    Answers "how are this name's returns distributed — fat tails? skew? where's the downside?".
    Uses the numeric ``value`` x-axis (return % on x, frequency on y); the mean/VaR markers are
    drawn as vertical 2-point lines so the tail risk is legible against the bars.
    """
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    lookback = a.get("lookback") if a.get("lookback") in LOOKBACKS else "1Y"
    bins = max(10, min(int(a.get("bins") or 30), 60))
    _, bars = fetch_bars(deps.dm, symbol, asset, _lookback_start(lookback), date.today())
    close = bars["close"].dropna() if not bars.empty else pd.Series(dtype=float)
    if len(close) < 20:
        raise ValueError(f"not enough history for a return distribution of {symbol}")
    rets = (close.pct_change().dropna() * 100.0)
    counts, edges = np.histogram(rets.to_numpy(), bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    hist_pts = [{"time": round(float(c), 4), "value": int(n)} for c, n in zip(centers, counts)]
    ymax = int(counts.max()) or 1
    mean = round(float(rets.mean()), 4)
    var5 = round(float(rets.quantile(0.05)), 4)

    def _vline(x: float, key: str, title: str) -> dict:
        return {"points": [{"time": x, "value": 0}, {"time": x, "value": ymax}],
                "colorKey": key, "kind": "line", "title": title}

    specs = [
        {"points": hist_pts, "colorKey": "accent", "kind": "histogram", "title": "frequency"},
        _vline(mean, "series2", f"mean {mean:.2f}%"),
        _vline(var5, "down", f"5% VaR {var5:.2f}%"),
    ]
    return {"engine": "series", "series": {
        "title": f"{symbol} daily-return distribution · {lookback}",
        "specs": specs, "xMode": "value", "xUnit": "%",
    }}


def _resolve_vol_cone(deps: ChartDeps, a: dict) -> dict:
    """Volatility cone — annualized realized-vol percentile bands (min/p25/median/p75/max) across
    horizons, plus the CURRENT reading, so you can see whether today's vol is rich or cheap at each
    horizon. x = horizon in trading days (value-axis); y = annualized vol %.
    """
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    lookback = a.get("lookback") if a.get("lookback") in LOOKBACKS else "3Y"
    _, bars = fetch_bars(deps.dm, symbol, asset, _lookback_start(lookback), date.today())
    close = bars["close"].dropna() if not bars.empty else pd.Series(dtype=float)
    if len(close) < 80:
        raise ValueError(f"not enough history for a volatility cone of {symbol}")
    rets = close.pct_change().dropna()
    sq = np.sqrt(365 if asset == "crypto" else 252)
    windows = [w for w in _CONE_WINDOWS if len(rets) >= w + 20]
    if len(windows) < 3:
        raise ValueError(f"not enough history for a volatility cone of {symbol}")

    band: dict[str, list[dict]] = {lvl: [] for lvl, _ in _CONE_LEVELS}
    for w in windows:
        v = (rets.rolling(w).std(ddof=0) * sq * 100).dropna()
        if v.empty:
            continue
        x = float(w)
        band["min"].append({"time": x, "value": round(float(v.min()), 4)})
        band["p25"].append({"time": x, "value": round(float(v.quantile(0.25)), 4)})
        band["median"].append({"time": x, "value": round(float(v.median()), 4)})
        band["p75"].append({"time": x, "value": round(float(v.quantile(0.75)), 4)})
        band["max"].append({"time": x, "value": round(float(v.max()), 4)})
        band["current"].append({"time": x, "value": round(float(v.iloc[-1]), 4)})

    specs = [{"points": band[lvl], "colorKey": color, "kind": "line", "title": lvl}
             for lvl, color in _CONE_LEVELS if band[lvl]]
    xticks = [{"x": float(w), "label": f"{w}d"} for w in windows]
    return {"engine": "series", "series": {
        "title": f"{symbol} volatility cone · ann. % ({lookback})",
        "specs": specs, "xMode": "value", "xTicks": xticks, "xUnit": "d",
    }}


def _resolve_trailing_returns(deps: ChartDeps, a: dict) -> dict:
    """Trailing total returns over standard periods (1M/3M/6M/YTD/1Y/3Y) as bars — a performance
    summary. Bars above the zero baseline are gains, below are losses. x = period (value-axis with
    label ticks); periods without enough history are skipped.
    """
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    _, bars = fetch_bars(deps.dm, symbol, asset, date.today() - timedelta(days=1300), date.today())
    close = bars["close"].dropna() if not bars.empty else pd.Series(dtype=float)
    if len(close) < 5:
        raise ValueError(f"not enough history for trailing returns of {symbol}")
    last = float(close.iloc[-1])
    defs = _RET_PERIODS_CR if asset == "crypto" else _RET_PERIODS_EQ

    pts: list[dict] = []
    ticks: list[dict] = []
    i = 0
    for label, k in defs:
        val: float | None = None
        if label == "YTD":
            jan1 = pd.Timestamp(year=date.today().year, month=1, day=1, tz=close.index.tz)
            prior, in_year = close[close.index < jan1], close[close.index >= jan1]
            if len(in_year):
                base = float(prior.iloc[-1]) if len(prior) else float(in_year.iloc[0])
                val = (last / base - 1) * 100 if base else None
        elif k is not None and len(close) > k:
            prev = float(close.iloc[-1 - k])
            val = (last / prev - 1) * 100 if prev else None
        if val is not None:
            pts.append({"time": float(i), "value": round(val, 4)})
            ticks.append({"x": float(i), "label": label})
            i += 1
    if not pts:
        raise ValueError(f"no trailing-return periods could be computed for {symbol}")
    spec = {"points": pts, "colorKey": "accent", "kind": "histogram", "title": "return %"}
    return {"engine": "series", "series": {
        "title": f"{symbol} trailing returns (%)", "specs": [spec],
        "xMode": "value", "xTicks": ticks, "xUnit": "",
    }}


def _resolve_rolling_beta(deps: ChartDeps, a: dict) -> dict:
    """Rolling beta of an instrument vs a benchmark over time (default SPY for equities, BTC for
    crypto), with a β=1 reference line.

    beta = cov(asset, benchmark) / var(benchmark) on a trailing window — "how market-sensitive is
    this name, and is that sensitivity drifting?". β>1 amplifies the benchmark, β<1 dampens it.
    """
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    bench = str(a.get("benchmark") or ("BTC/USDT" if asset == "crypto" else "SPY")).upper()
    if bench == symbol:
        raise ValueError("benchmark must differ from the symbol")
    window = a.get("window") if a.get("window") in WINDOWS else 90
    lookback = a.get("lookback") if a.get("lookback") in LOOKBACKS else "1Y"
    start = _lookback_start(lookback) - timedelta(days=window * 2)
    joined = _two_closes(deps, [symbol, bench], asset, start)
    if len(joined) < window + 5:
        raise ValueError(f"not enough overlapping history for a {window}d rolling beta")
    rets = joined.pct_change()
    cov = rets[symbol].rolling(window).cov(rets[bench])
    var = rets[bench].rolling(window).var()
    beta = cov / var.replace(0, np.nan)
    pts = _series_points(beta)
    if not pts:
        raise ValueError("rolling beta produced no points")
    label = f"{symbol} β vs {bench} ({window}d)"
    specs = [
        {"points": pts, "colorKey": "accent", "kind": "line", "title": label},
        {"points": [{"time": pts[0]["time"], "value": 1.0}, {"time": pts[-1]["time"], "value": 1.0}],
         "colorKey": "series4", "kind": "line", "title": "β=1"},
    ]
    return {"engine": "series", "series": {"title": f"Rolling beta · {symbol} vs {bench}", "specs": specs}}


def _resolve_rolling(deps: ChartDeps, a: dict) -> dict:
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    metric = a.get("metric") if a.get("metric") in METRICS else "sharpe"
    window = a.get("window") if a.get("window") in WINDOWS else 90
    out = mx.rolling(deps.dm, symbol, asset, window)
    pts = out["series"].get(metric, [])
    if not pts:
        raise ValueError(f"not enough history for a rolling {window}d {metric}")
    is_dd = metric == "drawdown"
    spec = {
        "points": pts,
        "colorKey": "down" if is_dd else "accent",
        "kind": "area" if is_dd else "line",
        "title": f"{symbol} {metric} ({window}d)",
    }
    return {"engine": "series", "series": {"title": f"{symbol} rolling {metric}", "specs": [spec]}}


def _resolve_macro(deps: ChartDeps, a: dict) -> dict:
    tenor = a.get("tenor")
    if tenor in TENORS:
        curve = deps.rates_store.load("treasury_curve")
        if tenor not in curve.columns:
            raise ValueError(f"{tenor} tenor not in the rates lake")
        pts = _series_points(curve[tenor])
        title = f"US {tenor} Treasury yield"
    else:
        sid = a.get("series_id") or "CPIAUCSL"
        if not deps.macro_store.has(sid):
            raise ValueError(f"macro series '{sid}' not in the lake")
        s = macro_svc.series(deps.macro_store, sid, None, None)
        pts = [{"time": o["date"], "value": o["value"]} for o in s["observations"] if o["value"] is not None]
        title = s["label"]
    if not pts:
        raise ValueError("macro series has no observations")
    return {"engine": "series", "series": {"title": title, "specs": [{"points": pts, "colorKey": "accent", "kind": "line", "title": title}]}}


def _resolve_curve(deps: ChartDeps, a: dict) -> dict:
    """US Treasury yield-curve SNAPSHOT — yield vs maturity at the latest date, with a prior-date
    overlay (default ~1Y back) so steepening/flattening is visible. Uses the numeric ``value`` x-axis
    (tenor in years) rather than time; this differs from the macro tool's single-tenor-over-time view.
    """
    curve = deps.rates_store.load("treasury_curve").sort_index()
    if curve.empty:
        raise ValueError("the rates lake has no treasury curve")
    cols = sorted((c for c in curve.columns if c in _TENOR_YEARS), key=lambda c: _TENOR_YEARS[c])
    if len(cols) < 2:
        raise ValueError("not enough recognized tenors in the rates lake for a curve")

    def _row_points(row: pd.Series) -> list[dict]:
        return [{"time": _TENOR_YEARS[c], "value": round(float(row[c]), 4)}
                for c in cols if pd.notna(row[c])]

    panel = curve[cols].dropna(how="all")
    if panel.empty:
        raise ValueError("treasury curve has no populated rows")
    latest = panel.iloc[-1]
    as_of = latest.name
    latest_pts = _row_points(latest)
    if len(latest_pts) < 2:
        raise ValueError("the latest curve row has fewer than 2 tenors")
    as_of_str = as_of.strftime("%Y-%m-%d") if hasattr(as_of, "strftime") else str(as_of)
    specs = [{"points": latest_pts, "colorKey": "accent", "kind": "line", "title": as_of_str}]

    # Prior-date overlay: the most recent row at or before (as_of − days).
    days = int(a.get("days") or 365)
    try:
        target = as_of - pd.Timedelta(days=days)
        prior_panel = panel.loc[:target]
        if not prior_panel.empty:
            prior = prior_panel.iloc[-1]
            prior_pts = _row_points(prior)
            if len(prior_pts) >= 2:
                p_str = prior.name.strftime("%Y-%m-%d") if hasattr(prior.name, "strftime") else str(prior.name)
                specs.append({"points": prior_pts, "colorKey": "series4", "kind": "line", "title": p_str})
    except (TypeError, KeyError):  # non-datetime index → skip the comparison gracefully
        pass

    xticks = [{"x": _TENOR_YEARS[c], "label": c} for c in cols]
    return {"engine": "series", "series": {
        "title": f"US Treasury yield curve · {as_of_str}",
        "specs": specs, "xMode": "value", "xTicks": xticks, "xUnit": "y",
    }}


def _resolve_term_spread(deps: ChartDeps, a: dict) -> dict:
    """A Treasury term spread over time: long-tenor yield − short-tenor yield (default 10Y − 3M).

    The classic inversion / recession indicator — below zero = inverted. A single line/area over
    time; the engine draws the zero baseline automatically. Differs from `curve` (a snapshot across
    maturities) and `macro` tenor (one maturity's level over time).
    """
    curve = deps.rates_store.load("treasury_curve").sort_index()
    if curve.empty:
        raise ValueError("the rates lake has no treasury curve")
    avail = [c for c in TENORS if c in curve.columns]
    long_t = a.get("tenor") if a.get("tenor") in curve.columns else ("10Y" if "10Y" in curve.columns else None)
    short_t = a.get("tenor2") if a.get("tenor2") in curve.columns else ("3M" if "3M" in curve.columns else None)
    if not long_t or not short_t or long_t == short_t:
        raise ValueError(f"need two distinct tenors present in the rates lake (have: {', '.join(avail)})")
    spread = (curve[long_t] - curve[short_t]).dropna()
    if spread.empty:
        raise ValueError(f"no overlapping history for {long_t} and {short_t}")
    label = f"{long_t} − {short_t}"
    spec = {"points": _series_points(spread), "colorKey": "accent", "kind": "area", "title": label}
    return {"engine": "series", "series": {"title": f"Treasury term spread · {label} (pp)", "specs": [spec]}}


def _resolve_macro_compare(deps: ChartDeps, a: dict) -> dict:
    """Overlay several macro series on one chart. Their levels live on wildly different scales (CPI
    ~330 vs unemployment ~4 vs Fed funds ~5), so each is normalized: ``index`` (=100 at the window
    start, the default — shows relative growth), ``zscore`` (standardized — shows co-movement), or
    ``raw`` (only sensible for same-unit series, e.g. UNRATE vs FEDFUNDS).
    """
    ids = [str(s).upper() for s in (a.get("series_ids") or []) if s][:5]
    if len(ids) < 2:
        raise ValueError("a macro comparison needs at least 2 series_ids")
    norm = a.get("norm") if a.get("norm") in ("index", "zscore", "raw") else "index"
    years = max(1, min(int(a.get("years") or 10), 80))
    cutoff = (date.today() - timedelta(days=int(years * 365.25))).isoformat()
    specs = []
    for idx, sid in enumerate(ids):
        if not deps.macro_store.has(sid):
            continue
        s = macro_svc.series(deps.macro_store, sid, None, None)
        obs = [(o["date"], o["value"]) for o in s["observations"]
               if o["value"] is not None and o["date"] >= cutoff]
        if len(obs) < 2:
            continue
        ser = pd.Series([v for _, v in obs], index=pd.to_datetime([d for d, _ in obs]))
        if norm == "index":
            base = float(ser.iloc[0])
            ser = ser / base * 100.0 if base else ser
        elif norm == "zscore":
            sd = float(ser.std(ddof=0))
            ser = (ser - ser.mean()) / sd if sd else ser * 0.0
        specs.append({"points": _series_points(ser), "colorKey": _SERIES_COLORS[idx % len(_SERIES_COLORS)],
                      "kind": "line", "title": s["label"]})
    if len(specs) < 2:
        raise ValueError("need at least 2 macro series with data in the window")
    norm_label = {"index": "indexed to 100", "zscore": "z-score", "raw": "raw"}[norm]
    return {"engine": "series", "series": {"title": f"Macro comparison · {norm_label}", "specs": specs}}


def _resolve_correlation(deps: ChartDeps, a: dict) -> dict:
    symbols = [str(s).upper() for s in (a.get("symbols") or [])][:12]
    if len(symbols) < 2:
        raise ValueError("correlation needs at least 2 symbols")
    asset = a.get("asset") or ("crypto" if any("/" in s for s in symbols) else "equity")
    days = int(a.get("days") or 365)
    out = pf.risk(deps.dm, [{"symbol": s, "asset": asset} for s in symbols], days=days)
    if not out.get("symbols"):
        raise ValueError("no overlapping price history for these symbols")
    return {"engine": "heatmap", "heatmap": {"labels": out["symbols"], "matrix": out["correlation"]}}


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _resolve_seasonality(deps: ChartDeps, a: dict) -> dict:
    """Monthly-return calendar — a year × month heatmap of actual monthly returns, plus an Avg row.

    Each cell is that month's real return (not an over-claimed long-run average); reads as "which
    months were strong/weak". NOTE: the local lake holds ~3y rolling, so this is a short calendar,
    not a multi-decade seasonality study — the title shows the realized span so it never overclaims.
    Uses the rectangular (rows × cols) heatmap path with diverging up/down coloring.
    """
    symbol = (a.get("symbol") or "AAPL").upper()
    asset = a.get("asset") or ("crypto" if "/" in symbol else "equity")
    years = max(2, min(int(a.get("years") or 8), 15))
    start = date.today() - timedelta(days=int(years * 365.25) + 5)
    _, bars = fetch_bars(deps.dm, symbol, asset, start, date.today())
    close = bars["close"].dropna() if not bars.empty else pd.Series(dtype=float)
    if len(close) < 60:
        raise ValueError(f"not enough history for a monthly-return calendar of {symbol}")
    monthly = close.resample("ME").last().pct_change().dropna() * 100.0
    if monthly.empty:
        raise ValueError(f"no monthly returns for {symbol}")

    yrs = sorted({int(d.year) for d in monthly.index})
    by = {(int(d.year), int(d.month)): round(float(v), 2) for d, v in monthly.items()}
    matrix: list[list[float | None]] = [[by.get((y, m)) for m in range(1, 13)] for y in yrs]
    rows = [str(y) for y in yrs]

    # Per-month average across the available years (the seasonality signal, caveated by short span).
    avg_row: list[float | None] = []
    for j in range(12):
        col = [matrix[i][j] for i in range(len(matrix)) if matrix[i][j] is not None]
        avg_row.append(round(sum(col) / len(col), 2) if col else None)
    matrix.append(avg_row)
    rows.append("Avg")

    absvals = [abs(v) for r in matrix for v in r if v is not None]
    vmax = max(5.0, round(float(np.percentile(absvals, 95)), 2)) if absvals else 5.0
    return {"engine": "heatmap", "heatmap": {
        "labels": _MONTHS, "matrix": matrix, "rows": rows, "cols": _MONTHS, "vmax": vmax, "fmt": "pct",
        "title": f"{symbol} monthly returns · {yrs[0]}–{yrs[-1]}",
    }}


def _resolve_fundamentals(deps: ChartDeps, a: dict) -> dict:
    symbol = (a.get("symbol") or "AAPL").upper()
    metric = a.get("fund_metric") if a.get("fund_metric") in FUND_METRICS else "revenue"
    freq = a.get("freq") if a.get("freq") in FUND_FREQS else "annual"
    n = max(2, min(int(a.get("n_periods") or (8 if freq == "quarterly" else 5)), 16))
    fin = fa.financials(symbol, n, freq)
    periods, rows = fin.get("periods") or [], fin.get("rows") or {}
    if not periods:
        raise ValueError(f"no {freq} financials on file for {symbol}")
    # yfinance returns most-recent-first; reverse to chronological so the x-axis reads left→right.
    order = list(range(len(periods) - 1, -1, -1))
    times = [periods[i] for i in order]

    if metric in _FUND_MARGINS:
        num, rev = rows.get(_FUND_MARGINS[metric]), rows.get("Total Revenue")
        if not num or not rev:
            raise ValueError(f"can't compute {metric} for {symbol} — missing line items")
        vals = [(round(num[i] / rev[i] * 100, 4) if num[i] is not None and rev[i] else None) for i in order]
        kind, color, label = "line", "accent", metric.replace("_", " ").title()
    elif metric == "eps":
        line = rows.get("Diluted EPS")
        if not line:
            raise ValueError(f"no EPS on file for {symbol}")
        vals = [(round(line[i], 4) if line[i] is not None else None) for i in order]
        kind, color, label = "line", "accent", "Diluted EPS"
    else:
        line = rows.get(_FUND_LINES[metric])
        if not line:
            raise ValueError(f"no {_FUND_LINES[metric]} on file for {symbol}")
        vals = [(round(line[i], 4) if line[i] is not None else None) for i in order]
        # Loss-making lines dip below zero; color the whole series down if every print is negative.
        nonnull = [v for v in vals if v is not None]
        kind = "histogram"
        color = "down" if nonnull and all(v < 0 for v in nonnull) else "accent"
        label = _FUND_LINES[metric]

    points = [{"time": t, "value": v} for t, v in zip(times, vals) if v is not None]
    if not points:
        raise ValueError(f"{symbol} {metric}: no non-empty periods")
    title = f"{symbol} {label} · {freq}"
    return {"engine": "series", "series": {"title": title, "specs": [
        {"points": points, "colorKey": color, "kind": kind, "title": label},
    ]}}


_RESOLVERS = {
    "price": _resolve_price,
    "compare": _resolve_compare,
    "ratio": _resolve_ratio,
    "rolling_corr": _resolve_rolling_corr,
    "rolling_beta": _resolve_rolling_beta,
    "distribution": _resolve_distribution,
    "vol_cone": _resolve_vol_cone,
    "trailing_returns": _resolve_trailing_returns,
    "rolling": _resolve_rolling,
    "macro": _resolve_macro,
    "macro_compare": _resolve_macro_compare,
    "curve": _resolve_curve,
    "term_spread": _resolve_term_spread,
    "correlation": _resolve_correlation,
    "seasonality": _resolve_seasonality,
    "fundamentals": _resolve_fundamentals,
}


def resolve_action(action: dict, deps: ChartDeps) -> dict:
    """Run a validated action through its tool resolver → chart frame fields (sans id/type)."""
    tool = action.get("tool") if action.get("tool") in _RESOLVERS else "price"
    out = _RESOLVERS[tool](deps, action)
    out["title"] = action.get("title") or tool
    out["action"] = {"tool": tool, "args": action}
    return out


def _get_action(deps: ChartDeps, message: str, context: dict) -> dict:
    """One structured LLM call → a chart action. Falls back to prose+strip if structured fails."""
    prior = context.get("action")
    user = message if not prior else f"Current chart: {prior}\n\nRequest: {message}"
    try:
        action = deps.llm.structured(_system(), user, CHART_ACTION_SCHEMA, model=deps.model)
    except Exception:  # noqa: BLE001 - proxy may not honor response_format; recover from prose
        raw = deps.llm.complete(_system() + " Return ONLY a single JSON object.", user, model=deps.model, temperature=0.2)
        action = _strip_to_json(raw) or {}
    if action.get("tool") not in _RESOLVERS:
        action["tool"] = "price"
    return action


async def arun_chart_studio_agent(
    message: str, context: dict | None, deps: ChartDeps
) -> AsyncIterator[dict]:
    """Stream: thought → chart (or obs error) → done."""
    context = context or {}
    try:
        action = await asyncio.to_thread(functools.partial(_get_action, deps, message, context))
    except Exception as e:  # noqa: BLE001
        yield {"type": "error", "detail": f"LLM call failed: {type(e).__name__}: {e}"}
        return

    yield {"type": "thought", "text": action.get("rationale") or f"Building a {action['tool']} chart."}

    try:
        fields = await asyncio.to_thread(functools.partial(resolve_action, action, deps))
    except Exception as e:  # noqa: BLE001 - a bad symbol / empty series is a normal outcome
        yield {"type": "obs", "text": f"Couldn't build that chart: {e}", "ok": False}
        yield {"type": "done", "message": "No chart produced — try a different symbol or timeframe."}
        return

    yield {"type": "chart", "id": f"c{abs(hash(message)) % 100000}", **fields}
    yield {"type": "done", "message": fields["title"]}
