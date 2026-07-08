"""Portfolio & risk service — correlation, per-symbol risk metrics, holdings P&L.

Risk metrics reuse qhfi.evaluation.metrics so the terminal and the backtester report the
same numbers. Returns are daily close-to-close over a trailing window.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from qhfi.data.manager import DataManager
from qhfi.evaluation import metrics as M

from app.services.market import fetch_bars


def _returns_frame(dm: DataManager, items: list[dict], days: int = 365) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=days)
    series: dict[str, pd.Series] = {}
    for it in items:
        try:
            _, bars = fetch_bars(dm, it["symbol"], it.get("asset", "equity"), start, end)
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the whole matrix
            continue
        if not bars.empty:
            series[it["symbol"].upper()] = bars["close"].pct_change()
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="all")


def risk(dm: DataManager, items: list[dict], days: int = 365) -> dict:
    rets = _returns_frame(dm, items, days=days)
    if rets.empty:
        return {"symbols": [], "correlation": [], "metrics": []}

    corr = rets.corr().round(3)
    symbols = list(corr.columns)
    matrix = [[None if pd.isna(v) else float(v) for v in corr[c].tolist()] for c in symbols]

    per_symbol = []
    for sym in symbols:
        r = rets[sym].dropna()
        if len(r) < 20:
            continue
        s = M.summary(r)
        per_symbol.append({
            "symbol": sym,
            "ann_vol": round(s["ann_vol"] * 100, 2),
            "sharpe": round(s["sharpe"], 2),
            "sortino": round(s["sortino"], 2),
            "max_drawdown": round(s["max_drawdown"] * 100, 2),
            "cagr": round(s["cagr"] * 100, 2),
        })

    return {"symbols": symbols, "correlation": matrix, "metrics": per_symbol}


def portfolio_risk(
    dm: DataManager, positions: list[dict], days: int = 365, benchmark: str = "SPY"
) -> dict:
    """Portfolio-LEVEL risk for a weighted book (vs the per-symbol matrix in risk()).

    Weights come from current market value (quantity x last close), normalised so the gross
    notional sums to 1; shorts carry negative weight. Aggregate metrics reuse qhfi's summary so
    they match the backtester. VaR/CVaR are 1-day historical at 95%. Beta is vs `benchmark`.
    """
    end = date.today()
    start = end - timedelta(days=days)

    series: dict[str, pd.Series] = {}
    last_close: dict[str, float] = {}
    qty: dict[str, float] = {}
    for p in positions:
        sym = p["symbol"].upper()
        qty[sym] = qty.get(sym, 0.0) + float(p["quantity"])
        if sym in series:
            continue
        try:
            _, bars = fetch_bars(dm, p["symbol"], p.get("asset", "equity"), start, end)
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the book
            continue
        if not bars.empty:
            series[sym] = bars["close"].pct_change()
            last_close[sym] = float(bars["close"].iloc[-1])

    syms = [s for s in qty if s in last_close and s in series]
    values = {s: qty[s] * last_close[s] for s in syms}
    gross_notional = sum(abs(v) for v in values.values())
    if len(syms) < 2 or gross_notional == 0:
        return {"n": len(syms), "insufficient": True}

    weights = {s: values[s] / gross_notional for s in syms}  # signed; sum|w| = 1
    rets = pd.DataFrame({s: series[s] for s in syms}).dropna()  # aligned, drops pct_change NaN row
    if len(rets) < 20:
        return {"n": len(syms), "insufficient": True}

    w = pd.Series(weights)
    port = rets[syms].mul(w, axis=1).sum(axis=1)

    s = M.summary(port)
    var95 = float(-port.quantile(0.05))
    tail = port[port <= -var95]
    cvar95 = float(-tail.mean()) if len(tail) else var95

    total_value = sum(values.values())  # net (signed) market value
    net = sum(weights.values())
    long_w = sum(v for v in weights.values() if v > 0)
    short_w = sum(v for v in weights.values() if v < 0)
    concentration = sum(v * v for v in weights.values())  # Herfindahl (gross-normalised)

    beta: float | None = None
    try:
        _, bbars = fetch_bars(dm, benchmark, "equity", start, end)
        if not bbars.empty:
            joined = pd.concat(
                [port.rename("p"), bbars["close"].pct_change().rename("b")], axis=1, sort=False
            ).dropna()
            if len(joined) >= 20 and joined["b"].var() > 0:
                beta = float(joined["p"].cov(joined["b"]) / joined["b"].var())
    except Exception:  # noqa: BLE001 - benchmark is best-effort
        beta = None

    asof = port.index[-1]
    return {
        "as_of": str(getattr(asof, "date", lambda: asof)()),
        "n": len(syms),
        "total_value": round(total_value, 2),
        "ann_vol": round(s["ann_vol"] * 100, 2),
        "sharpe": round(s["sharpe"], 2),
        "sortino": round(s["sortino"], 2),
        "max_drawdown": round(s["max_drawdown"] * 100, 2),
        "cagr": round(s["cagr"] * 100, 2),
        "var_95": round(var95 * 100, 2),
        "cvar_95": round(cvar95 * 100, 2),
        "var_95_usd": round(var95 * gross_notional, 2),
        "cvar_95_usd": round(cvar95 * gross_notional, 2),
        "gross": round(sum(abs(v) for v in weights.values()), 4),
        "net": round(net, 4),
        "long": round(long_w, 4),
        "short": round(short_w, 4),
        "n_long": sum(1 for v in weights.values() if v > 0),
        "n_short": sum(1 for v in weights.values() if v < 0),
        "concentration": round(concentration, 4),
        "beta": None if beta is None else round(beta, 2),
        "benchmark": benchmark.upper(),
    }


def composition_over_time(
    dm: DataManager, holdings: list[dict], start: date, end: date, max_points: int = 240
) -> dict:
    """Reconstruct how each holding's share of the portfolio drifts across [start, end].

    Holdings are the CURRENT book (fixed quantities — there is no transaction history), valued
    back through time at each day's close: ``value_i(t) = qty_i x close_i(t)`` and
    ``weight_i(t) = value_i(t) / sum_j |value_j(t)|``. So this reveals composition drift driven by
    relative price moves, not by trades. Series are downsampled to <= ``max_points`` for a light
    payload; only the span where every name has a price is kept so the bands always sum cleanly.
    """
    closes: dict[str, pd.Series] = {}
    qty: dict[str, float] = {}
    asset_of: dict[str, str] = {}
    for h in holdings:
        sym = h["symbol"].upper()
        qty[sym] = qty.get(sym, 0.0) + float(h["quantity"])
        asset_of[sym] = h.get("asset", "equity")
    for sym, a in asset_of.items():
        try:
            _, bars = fetch_bars(dm, sym, a, start, end)
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the book
            continue
        if not bars.empty and "close" in bars:
            s = bars["close"].dropna()
            if not s.empty:
                closes[sym] = s

    syms = [s for s in qty if s in closes]
    if not syms:
        return {"insufficient": True, "n": 0}

    px = pd.DataFrame({s: closes[s] for s in syms}).sort_index()
    px = px.ffill().dropna(how="any")  # common span where every name has a price
    if len(px) < 2:
        return {"insufficient": True, "n": len(syms)}

    values = px.mul(pd.Series(qty), axis=1)               # value_i(t)
    gross = values.abs().sum(axis=1).replace(0.0, pd.NA)
    weights = values.div(gross, axis=0).fillna(0.0)       # share of gross notional
    total = values.sum(axis=1)                            # net market value

    n = len(px)
    step = max(1, n // max_points)
    pos = list(range(0, n, step))
    if pos[-1] != n - 1:
        pos.append(n - 1)
    times = [px.index[p].strftime("%Y-%m-%d") for p in pos]

    series = []
    for s in syms:
        w, v = weights[s], values[s]
        series.append({
            "symbol": s,
            "weights": [round(float(w.iloc[p]) * 100, 3) for p in pos],
            "start_weight": round(float(w.iloc[0]) * 100, 2),
            "end_weight": round(float(w.iloc[-1]) * 100, 2),
            "end_value": round(float(v.iloc[-1]), 2),
        })
    # Heaviest current holding first → stable stacking order + legend.
    series.sort(key=lambda x: x["end_weight"], reverse=True)

    return {
        "n": len(syms),
        "start": times[0],
        "end": times[-1],
        "times": times,
        "symbols": [s["symbol"] for s in series],
        "series": series,
        "total_value": [round(float(total.iloc[p]), 2) for p in pos],
    }


def holdings_pnl(dm: DataManager, holdings: list[dict]) -> dict:
    rows, total_value, total_cost = [], 0.0, 0.0
    for h in holdings:
        try:
            _, bars = fetch_bars(dm, h["symbol"], h.get("asset", "equity"))
            price = float(bars["close"].iloc[-1]) if not bars.empty else None
        except Exception:  # noqa: BLE001
            price = None
        qty, cost_basis = float(h["quantity"]), float(h["cost_basis"])
        value = price * qty if price is not None else None
        cost = cost_basis * qty
        pnl = (value - cost) if value is not None else None
        if value is not None:
            total_value += value
            total_cost += cost
        rows.append({
            "symbol": h["symbol"], "asset": h.get("asset", "equity"),
            "quantity": qty, "cost_basis": cost_basis, "price": price,
            "value": None if value is None else round(value, 2),
            "pnl": None if pnl is None else round(pnl, 2),
            "pnl_pct": None if not cost or pnl is None else round(pnl / cost * 100, 2),
        })
    return {
        "holdings": rows,
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_value - total_cost, 2),
        "total_pnl_pct": round((total_value - total_cost) / total_cost * 100, 2) if total_cost else None,
    }
