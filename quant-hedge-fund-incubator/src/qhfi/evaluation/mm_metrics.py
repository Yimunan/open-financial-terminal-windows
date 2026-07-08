"""Market-making diagnostics — the metrics that tell a quoting book apart from a directional one.

A market-maker's P&L decomposes into *spread earned* minus *adverse selection* minus *fees*,
with inventory ideally mean-reverting to zero. The standard headline metric is the **markout
curve**: the signed mid move at horizons ``h`` after each fill — negative markout is adverse
selection (you got filled right before the price moved against you). These consume the same
``BacktestResult`` the scorecard does (``trades`` + ``positions``), plus an optional mid path
for markout.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.backtest.engine import BacktestResult


def spread_captured_bps(trades: pd.DataFrame) -> float:
    """Volume-weighted realized half-spread earned vs the mid at fill, in bps.

    Per passive fill the edge is ``sign(units)·(ref_mid − fill_price)`` — positive when we
    bought below / sold above the mid. ``ref_price`` carries the mid at quote-post time.
    """
    if trades.empty:
        return 0.0
    units = trades["units"].to_numpy()
    ref = trades["ref_price"].to_numpy()
    fill = trades["fill_price"].to_numpy()
    edge_bps = np.sign(units) * (ref - fill) / np.where(ref != 0, ref, np.nan) * 1e4
    w = np.abs(units)
    return float(np.nansum(edge_bps * w) / w.sum()) if w.sum() else 0.0


def fill_ratio(result: BacktestResult) -> float:
    """Fills per quote posted — how often a resting quote actually traded."""
    n_quotes = result.meta.get("n_quotes", 0)
    n_fills = len(result.trades)
    return float(n_fills / n_quotes) if n_quotes else 0.0


def inventory_stats(positions: pd.DataFrame, instrument: str | None = None) -> dict[str, float]:
    """Inventory distribution + mean-reversion (AR(1) half-life in snapshots).

    A healthy market-maker holds inventory near zero and reverts quickly; a rising/persistent
    inventory means the skew isn't clearing the book. ``instrument`` defaults to the first column.
    """
    col = instrument or (positions.columns[0] if len(positions.columns) else None)
    if col is None or positions.empty:
        return {"inv_mean": 0.0, "inv_std": 0.0, "inv_max_abs": 0.0, "inv_half_life": float("inf")}
    q = positions[col].to_numpy(dtype=float)
    half_life = float("inf")
    if len(q) > 2 and np.std(q) > 0:
        phi = float(np.polyfit(q[:-1], q[1:], 1)[0])
        if 0.0 < phi < 1.0:
            half_life = float(-np.log(2) / np.log(phi))
    return {
        "inv_mean": float(np.mean(q)), "inv_std": float(np.std(q)),
        "inv_max_abs": float(np.max(np.abs(q))), "inv_half_life": half_life,
    }


def markout_bps(trades: pd.DataFrame, mid: pd.Series, horizons=(1, 5, 30)) -> dict[int, float]:
    """Mean signed mid move at each horizon ``h`` after a fill, in bps (the adverse-selection curve).

    ``markout_h = sign(units)·(mid_{t+h} − fill_price)`` — positive favourable, negative adverse.
    ``mid`` is a snapshot-indexed mid series; horizons count snapshots forward.
    """
    out: dict[int, float] = {}
    if trades.empty or mid.empty:
        return {h: 0.0 for h in horizons}
    mid = mid.sort_index()
    pos = mid.index.get_indexer(pd.DatetimeIndex(trades["date"]))
    units = trades["units"].to_numpy()
    fill = trades["fill_price"].to_numpy()
    n = len(mid)
    for h in horizons:
        vals = []
        for i, p in enumerate(pos):
            if p < 0 or p + h >= n:
                continue
            fwd = float(mid.iloc[p + h])
            vals.append(np.sign(units[i]) * (fwd - fill[i]) / fill[i] * 1e4)
        out[h] = float(np.mean(vals)) if vals else 0.0
    return out


def mm_summary(
    result: BacktestResult,
    mid: pd.Series | None = None,
    instrument: str | None = None,
    horizons=(1, 5, 30),
    periods_per_year: int = 365 * 24 * 60,
) -> dict[str, float]:
    """One call → the full market-making panel: P&L Sharpe, spread captured, fill ratio,
    inventory distribution + half-life, markout curve, and net edge (spread − fees) in bps."""
    from qhfi.evaluation import metrics

    trades = result.trades
    out: dict[str, float] = {
        "sharpe": metrics.sharpe(result.returns, periods_per_year=periods_per_year),
        "total_return": float(result.equity_curve.iloc[-1] / result.equity_curve.iloc[0] - 1)
        if len(result.equity_curve) else 0.0,
        "n_fills": float(len(trades)),
        "n_quotes": float(result.meta.get("n_quotes", 0)),
        "fill_ratio": fill_ratio(result),
        "spread_captured_bps": spread_captured_bps(trades),
        "commission_total": float(result.commission.sum()),
    }
    out.update(inventory_stats(result.positions, instrument))

    # Net edge: gross spread captured minus commission, per unit of traded notional, in bps.
    if not trades.empty:
        traded_notional = float((trades["units"].abs() * trades["fill_price"]).sum())
        comm_bps = float(result.commission.sum() / traded_notional * 1e4) if traded_notional else 0.0
        out["fee_bps"] = comm_bps
        out["net_edge_bps"] = out["spread_captured_bps"] - comm_bps
    else:
        out["fee_bps"] = out["net_edge_bps"] = 0.0

    if mid is not None:
        for h, v in markout_bps(trades, mid, horizons).items():
            out[f"markout_{h}_bps"] = v
    return out
