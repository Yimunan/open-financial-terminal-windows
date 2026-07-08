"""Strategy Lab — a single-instrument, signal-driven event backtester.

Distinct from the factor backtest (`services/backtest.py`), which is a cross-sectional
monthly-rebalanced portfolio. The Lab runs ONE symbol through a discrete entry/exit
strategy with stop-loss / take-profit, producing per-trade results (△ long / ▽ short /
× exit markers, win/loss, duration) and trade-level KPIs (profit factor, win rate,
expectancy) — the metrics a TradingView-style strategy lab reports.

Pure pandas/numpy, presentation-layer (not part of the qhfi quant engine). Signals are
computed from indicators using only data up to and including each bar, then filled at that
bar's close; stop/target are checked intrabar on the following bars' high/low.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app import indicators as ind


# ── strategy templates ──────────────────────────────────────────────────────────
# Each returns a desired-position array in {-1, 0, +1} per bar (before direction filter).

@dataclass
class Param:
    key: str
    label: str
    default: float
    min: float
    max: float
    step: float


@dataclass
class Template:
    key: str
    label: str
    params: list[Param]
    fn: object  # (close, high, low, params) -> np.ndarray of {-1,0,1}
    sweepable: list[str] = field(default_factory=list)


def _sma_cross(close: pd.Series, _h, _l, p: dict) -> np.ndarray:
    fast = ind.sma(close, int(p["fast"]))
    slow = ind.sma(close, int(p["slow"]))
    sig = np.where(fast > slow, 1, -1)
    sig[(fast.isna() | slow.isna()).values] = 0
    return sig


def _ema_cross(close: pd.Series, _h, _l, p: dict) -> np.ndarray:
    fast = ind.ema(close, int(p["fast"]))
    slow = ind.ema(close, int(p["slow"]))
    sig = np.where(fast > slow, 1, -1)
    sig[: int(p["slow"])] = 0
    return sig


def _rsi_reversion(close: pd.Series, _h, _l, p: dict) -> np.ndarray:
    r = ind.rsi(close, int(p["period"]))
    lo, hi = float(p["low"]), float(p["high"])
    desired = np.zeros(len(close), dtype=int)
    state = 0
    rv = pd.to_numeric(r, errors="coerce").to_numpy(dtype=float)  # pd.NA → nan
    for i in range(len(close)):
        if np.isnan(rv[i]):
            desired[i] = 0
            continue
        if rv[i] < lo:
            state = 1  # oversold → long
        elif rv[i] > hi:
            state = -1  # overbought → short / exit long
        elif (state == 1 and rv[i] > 50) or (state == -1 and rv[i] < 50):
            state = 0  # mean reverted → flat
        desired[i] = state
    return desired


def _macd_cross(close: pd.Series, _h, _l, _p: dict) -> np.ndarray:
    m = ind.macd(close)
    sig = np.where(m["macd"] > m["signal"], 1, -1)
    sig[m["macd"].isna().values | m["signal"].isna().values] = 0
    return sig


def _bollinger(close: pd.Series, _h, _l, p: dict) -> np.ndarray:
    b = ind.bollinger(close, int(p["window"]), float(p["n_std"]))
    upper, lower = b["upper"].values, b["lower"].values
    mode = "breakout" if float(p.get("breakout", 0)) >= 0.5 else "reversion"
    desired = np.zeros(len(close), dtype=int)
    state = 0
    cv = close.values
    for i in range(len(close)):
        if np.isnan(upper[i]):
            desired[i] = 0
            continue
        if mode == "reversion":
            if cv[i] < lower[i]:
                state = 1
            elif cv[i] > upper[i]:
                state = -1
            elif state != 0 and lower[i] < cv[i] < upper[i] and abs(cv[i] - (upper[i] + lower[i]) / 2) < (upper[i] - lower[i]) * 0.15:
                state = 0  # back to the mean
        else:  # breakout
            if cv[i] > upper[i]:
                state = 1
            elif cv[i] < lower[i]:
                state = -1
        desired[i] = state
    return desired


def _donchian(close: pd.Series, high: pd.Series, low: pd.Series, p: dict) -> np.ndarray:
    """Channel breakout (Turtle-style): go long on a close above the prior N-day high, short on a
    close below the prior N-day low, and hold until the opposite breakout. Always-in once started,
    like the MA-cross templates."""
    ch = ind.donchian(high, low, int(p["window"]))
    upper, lower = ch["upper"].values, ch["lower"].values
    cv = close.values
    desired = np.zeros(len(close), dtype=int)
    state = 0
    for i in range(len(close)):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            desired[i] = 0
            continue
        if cv[i] > upper[i]:
            state = 1
        elif cv[i] < lower[i]:
            state = -1
        desired[i] = state
    return desired


TEMPLATES: dict[str, Template] = {
    "sma_cross": Template(
        "sma_cross", "SMA crossover",
        [Param("fast", "Fast SMA", 20, 5, 100, 1), Param("slow", "Slow SMA", 50, 10, 250, 1)],
        _sma_cross, ["fast", "slow"],
    ),
    "ema_cross": Template(
        "ema_cross", "EMA crossover",
        [Param("fast", "Fast EMA", 12, 3, 60, 1), Param("slow", "Slow EMA", 26, 10, 200, 1)],
        _ema_cross, ["fast", "slow"],
    ),
    "rsi_reversion": Template(
        "rsi_reversion", "RSI mean-reversion",
        [Param("period", "RSI period", 14, 2, 50, 1), Param("low", "Oversold", 30, 5, 45, 1),
         Param("high", "Overbought", 70, 55, 95, 1)],
        _rsi_reversion, ["period", "low", "high"],
    ),
    "macd_cross": Template(
        "macd_cross", "MACD crossover", [], _macd_cross, [],
    ),
    "bollinger": Template(
        "bollinger", "Bollinger reversion/breakout",
        [Param("window", "Window", 20, 5, 100, 1), Param("n_std", "Std dev", 2, 1, 4, 0.5),
         Param("breakout", "Breakout (0/1)", 0, 0, 1, 1)],
        _bollinger, ["window", "n_std"],
    ),
    "donchian": Template(
        "donchian", "Donchian breakout",
        [Param("window", "Channel window", 20, 5, 120, 1)],
        _donchian, ["window"],
    ),
}


# ── custom strategies (user-registered, sandboxed signal code) ──────────────────
# A custom strategy's code gets close/high/low (pandas Series) + params (dict) + pd/np/math and
# must set `result` = a per-bar position array in {-1,0,1}. Validated by the agent_code AST
# allowlist (no imports/dunders/eval/exec/file/net) before it can run.
CUSTOM: dict[str, Template] = {}


def _custom_fn(code: str):
    import math as _math

    from app.services import agent_code as ac

    tree = ac._validate(code)  # raises ValueError on disallowed constructs

    def fn(close, high, low, params):
        ns = {
            "__builtins__": ac._SAFE_BUILTINS,
            "pd": pd, "np": np, "math": _math,
            "close": close, "high": high, "low": low, "params": dict(params),
            "result": None,
        }
        exec(compile(tree, "<strategy>", "exec"), ns)  # noqa: S102 - sandboxed + AST-allowlisted
        raw = ns.get("result")
        arr = np.asarray(raw if raw is not None else np.zeros(len(close)), dtype=float)
        if arr.shape[0] != len(close):
            arr = np.zeros(len(close), dtype=float)
        return np.clip(np.nan_to_num(arr), -1, 1)

    return fn


def register_custom(name: str, label: str, params_defs: list[dict], code: str) -> None:
    """Validate + register a custom strategy so simulate()/run_lab() can run it."""
    ps = [
        Param(
            p["key"], p.get("label", p["key"]), float(p.get("default", 0) or 0),
            float(p.get("min", 0) or 0), float(p.get("max", 0) or 0), float(p.get("step", 1) or 1),
        )
        for p in params_defs
    ]
    CUSTOM[name] = Template(name, label, ps, _custom_fn(code), [p["key"] for p in params_defs])


def unregister_custom(name: str) -> None:
    CUSTOM.pop(name, None)


def _template(name: str) -> Template | None:
    return TEMPLATES.get(name) or CUSTOM.get(name)


def list_strategies() -> list[dict]:
    return [
        {
            "key": t.key,
            "label": t.label,
            "params": [vars(p) for p in t.params],
            "sweepable": t.sweepable,
        }
        for t in TEMPLATES.values()
    ]


# ── simulator ────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    side: str  # long | short
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    pnl: float
    ret_pct: float
    bars: int
    reason: str  # signal | stop | target | eod


def _desired_with_direction(desired: np.ndarray, direction: str) -> np.ndarray:
    if direction == "long_only":
        return np.where(desired > 0, 1, 0)
    if direction == "short_only":
        return np.where(desired < 0, -1, 0)
    return desired


def simulate(
    bars: pd.DataFrame,
    strategy: str,
    params: dict,
    *,
    direction: str = "long_only",
    sl_pct: float = 0.0,
    tp_pct: float = 0.0,
    initial: float = 100_000.0,
    commission_bps: float = 5.0,
    size_pct: float = 1.0,
    leverage: float = 1.0,
    intraday: bool = False,
) -> dict:
    tmpl = _template(strategy)
    if tmpl is None:
        raise ValueError(f"unknown strategy '{strategy}'")
    full = {p.key: params.get(p.key, p.default) for p in tmpl.params}
    raw = tmpl.fn(bars["close"], bars["high"], bars["low"], full)
    desired = _desired_with_direction(np.asarray(raw, dtype=int), direction)

    close = bars["close"].values
    high = bars["high"].values
    low = bars["low"].values
    n = len(bars)

    def tstr(i: int) -> str:
        ts = bars.index[i]
        return str(int(ts.timestamp())) if intraday else ts.strftime("%Y-%m-%d")

    comm = commission_bps / 10_000.0
    cash = initial
    pos = 0            # +1 long, -1 short
    entry_px = 0.0
    units = 0.0
    entry_i = 0
    entry_cost = 0.0
    trades: list[Trade] = []
    equity_curve: list[dict] = []
    markers: list[dict] = []

    def open_pos(side: int, i: int) -> None:
        nonlocal pos, entry_px, units, entry_i, entry_cost, cash
        notional = max(cash, 0.0) * size_pct * leverage
        if notional <= 0:
            return
        entry_px = close[i]
        units = notional / entry_px
        entry_cost = units * entry_px * comm
        cash -= entry_cost
        pos = side
        entry_i = i
        markers.append({
            "time": tstr(i), "price": round(float(entry_px), 6),
            "kind": "longEntry" if side > 0 else "shortEntry",
        })

    def close_pos(exit_px: float, i: int, reason: str) -> None:
        nonlocal pos, cash
        realized = (exit_px - entry_px) * units * pos
        exit_cost = units * exit_px * comm
        cash += realized - exit_cost
        pnl = realized - entry_cost - exit_cost
        ret = pnl / (units * entry_px) * 100 if units and entry_px else 0.0
        trades.append(Trade(
            side="long" if pos > 0 else "short",
            entry_time=tstr(entry_i), entry_price=round(float(entry_px), 6),
            exit_time=tstr(i), exit_price=round(float(exit_px), 6),
            pnl=round(float(pnl), 2), ret_pct=round(float(ret), 3),
            bars=i - entry_i, reason=reason,
        ))
        markers.append({
            "time": tstr(i), "price": round(float(exit_px), 6),
            "kind": "exit", "win": bool(pnl >= 0),
        })
        pos = 0

    for i in range(n):
        # 1) intrabar stop / target on an open position
        if pos != 0 and (sl_pct or tp_pct):
            if pos > 0:
                stop = entry_px * (1 - sl_pct) if sl_pct else None
                take = entry_px * (1 + tp_pct) if tp_pct else None
                if stop is not None and low[i] <= stop:
                    close_pos(stop, i, "stop")
                elif take is not None and high[i] >= take:
                    close_pos(take, i, "target")
            else:
                stop = entry_px * (1 + sl_pct) if sl_pct else None
                take = entry_px * (1 - tp_pct) if tp_pct else None
                if stop is not None and high[i] >= stop:
                    close_pos(stop, i, "stop")
                elif take is not None and low[i] <= take:
                    close_pos(take, i, "target")

        # 2) signal-driven transitions at this bar's close
        want = desired[i]
        if pos == 0 and want != 0:
            open_pos(int(want), i)
        elif pos > 0 and want <= 0:
            close_pos(close[i], i, "signal")
            if want < 0:
                open_pos(-1, i)
        elif pos < 0 and want >= 0:
            close_pos(close[i], i, "signal")
            if want > 0:
                open_pos(1, i)

        # 3) mark equity
        unreal = (close[i] - entry_px) * units * pos if pos != 0 else 0.0
        equity_curve.append({"time": tstr(i), "value": round(float(cash + unreal), 2)})

    if pos != 0:  # close any open position at the last bar
        close_pos(close[n - 1], n - 1, "eod")
        equity_curve[-1]["value"] = round(float(cash), 2)

    return _metrics(trades, equity_curve, markers, initial, bars, intraday)


def _metrics(
    trades: list[Trade], equity_curve: list[dict], markers: list[dict],
    initial: float, bars: pd.DataFrame, intraday: bool,
) -> dict:
    eq = np.array([p["value"] for p in equity_curve]) if equity_curve else np.array([initial])
    final = float(eq[-1])
    pnls = np.array([t.pnl for t in trades]) if trades else np.array([])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0

    # equity returns for Sharpe
    rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0.0])
    ppy = 252 * (390 if intraday else 1)  # rough annualization factor
    sharpe = float(rets.mean() / rets.std() * np.sqrt(ppy)) if rets.std() > 0 else 0.0

    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = float(dd.min() * 100) if len(dd) else 0.0

    # histogram of trade returns (%) for the distribution chart
    hist = []
    if trades:
        r = np.array([t.ret_pct for t in trades])
        counts, edges = np.histogram(r, bins=min(15, max(5, len(r) // 2)))
        hist = [
            {"lo": round(float(edges[i]), 2), "hi": round(float(edges[i + 1]), 2), "n": int(counts[i])}
            for i in range(len(counts))
        ]

    avg_bars = float(np.mean([t.bars for t in trades])) if trades else 0.0

    # Buy-and-hold baseline: the single most important sanity check for a single-symbol strategy —
    # did the timing actually beat just owning the asset over the same window? Equity = initial
    # scaled by the close, so the curve is comparable to the strategy's on the same chart.
    close_s = bars["close"].to_numpy(dtype=float)
    bh_curve: list[dict] = []
    buy_hold_pct = 0.0
    if len(close_s) and close_s[0] > 0:
        base = close_s[0]
        bh_curve = [
            {"time": equity_curve[i]["time"], "value": round(float(initial * close_s[i] / base), 2)}
            for i in range(min(len(close_s), len(equity_curve)))
        ]
        buy_hold_pct = (float(close_s[-1]) / base - 1) * 100
    net_pnl_pct = (final / initial - 1) * 100

    return {
        "trades": [vars(t) for t in trades],
        "equity_curve": equity_curve,
        "benchmark_curve": bh_curve,
        "markers": markers,
        "stats": {
            "net_pnl": round(final - initial, 2),
            "net_pnl_pct": round(net_pnl_pct, 2),
            "buy_hold_pct": round(buy_hold_pct, 2),
            "vs_buy_hold": round(net_pnl_pct - buy_hold_pct, 2),  # strategy − buy & hold, ppts
            "final_equity": round(final, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (None if not gross_win else 999.0),
            "max_drawdown": round(max_dd, 2),
            "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
            "total_trades": len(trades),
            "avg_bars": round(avg_bars, 1),
            "expectancy": round(float(pnls.mean()), 2) if len(pnls) else 0.0,
            "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
            "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
            "sharpe": round(sharpe, 2),
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
        },
        "histogram": hist,
    }


def sweep(
    bars: pd.DataFrame, strategy: str, base_params: dict, x_key: str, x_vals: list[float],
    y_key: str | None, y_vals: list[float], metric: str = "net_pnl_pct", **sim_kw,
) -> dict:
    """Grid of backtests over one or two parameters → matrix of a chosen metric."""
    grid = []
    for yv in (y_vals or [None]):
        row = []
        for xv in x_vals:
            params = dict(base_params)
            params[x_key] = xv
            if y_key and yv is not None:
                params[y_key] = yv
            try:
                res = simulate(bars, strategy, params, **sim_kw)
                row.append(round(float(res["stats"].get(metric) or 0.0), 2))
            except Exception:  # noqa: BLE001 - a bad cell shouldn't kill the sweep
                row.append(None)
        grid.append(row)
    return {
        "x_key": x_key, "x_vals": x_vals,
        "y_key": y_key, "y_vals": y_vals if y_key else [],
        "metric": metric, "grid": grid,
    }


def run_lab(
    dm,
    *,
    symbol: str,
    asset: str = "equity",
    timeframe: str = "1d",
    strategy: str = "sma_cross",
    params: dict | None = None,
    direction: str = "long_only",
    sl_pct: float = 0.0,
    tp_pct: float = 0.0,
    initial: float = 100_000.0,
    commission_bps: float = 5.0,
    size_pct: float = 1.0,
    leverage: float = 1.0,
    years: int = 3,
) -> dict:
    """Load bars and run one Strategy-Lab backtest. Shared by the REST route and the agent.

    Raises ValueError on a bad strategy or insufficient data (the router maps it to a 4xx; the
    agent turns it into an error observation). Market import is lazy so this module stays a pure
    pandas/numpy presentation layer at import time.
    """
    from datetime import date

    from app.services import market as mkt

    if _template(strategy) is None:
        raise ValueError(f"unknown strategy '{strategy}'")
    intraday = timeframe != "1d"
    if intraday:
        _, frame = mkt.fetch_bars_intraday(symbol, asset, timeframe)
    else:
        end = date.today()
        start = end.replace(year=end.year - max(1, int(years)))
        _, frame = mkt.fetch_bars(dm, symbol, asset, start, end)
    if frame.empty or len(frame) < 30:
        raise ValueError(f"insufficient data for {symbol} ({timeframe})")

    out = simulate(
        frame, strategy, params or {},
        direction=direction, sl_pct=sl_pct, tp_pct=tp_pct,
        initial=initial, commission_bps=commission_bps,
        size_pct=size_pct, leverage=leverage, intraday=intraday,
    )
    out["candles"] = mkt.to_candles(frame, intraday=intraday)["candles"]
    out["symbol"] = symbol.upper()
    out["timeframe"] = timeframe
    return out
