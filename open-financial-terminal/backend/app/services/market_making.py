"""Market-making backtest — run qhfi's quoting strategies inside the terminal.

The quoting market-maker needs an L2 order-book time series, which the qhfi lake does not yet
hold historically (the depth recorders accumulate it going forward). So for an immediately
runnable, viewable backtest we **synthesize an L2 book around real crypto 1-minute bars**: the
mid follows the actual close path (real market data, fetched via ccxt), and a modelled spread +
depth + bar-direction imbalance wrap each bar into a snapshot. This exercises the full quoting
pipeline (OBI/microprice signal → inventory skew → limit-order matching → fills) over genuine
price action; only the depth is synthetic, and the response says so.

Strategies (all qhfi `QuotingStrategy`): the **SymmetricMM** baseline, **LinearInventoryMM**
(bps spread + inventory skew + OBI tilt), **AvellanedaStoikovMM** (textbook), and **AlphaQuoterMM**
— a calibrated OBI alpha overlay (the per-OBI forward-return is fit from the book) that either
*dodges* adverse selection (passive) or *crosses to capture* the move (taker). The `/compare`
path runs them all on the same book so the terminal shows the full progression.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from qhfi.backtest.costs import BpsCostModel
from qhfi.backtest.eventdriven.engine import MarketMakingEngine
from qhfi.core.types import AssetClass, Instrument, Universe
from qhfi.data.microstructure import book_features, forward_return_on_obi
from qhfi.evaluation.mm_metrics import mm_summary
from qhfi.strategy.library.mm.alpha_quoter import AlphaQuoterMM, AlphaQuoterMMParams
from qhfi.strategy.library.mm.avellaneda_stoikov import ASParams, AvellanedaStoikovMM
from qhfi.strategy.library.mm.linear_inventory import LinearInventoryMM, LinearInventoryMMParams
from qhfi.strategy.library.mm.symmetric import SymmetricMM, SymmetricMMParams

# Display order + labels for the strategy selector / comparison table.
STRATEGIES = [
    ("symmetric", "SymmetricMM (baseline)"),
    ("linear", "LinearInventoryMM"),
    ("avellaneda", "AvellanedaStoikovMM"),
    ("alpha", "AlphaQuoterMM (passive)"),
    ("alpha_taker", "AlphaQuoterMM (taker)"),
]
_LABELS = dict(STRATEGIES)


def _synthetic_book(frame: pd.DataFrame, levels: int, spread_bps: float,
                    depth: float, imbalance_gain: float) -> pd.DataFrame:
    """Wrap real OHLC bars into a long-format L2 book (the OrderBookStore schema)."""
    close = frame["close"].to_numpy(dtype=float)
    open_ = frame["open"].to_numpy(dtype=float)
    ts_ms = np.array([int(ts.timestamp() * 1000) for ts in frame.index], dtype="int64")
    ret = np.divide(close - open_, open_, out=np.zeros_like(close), where=open_ > 0)
    tilt = np.clip(imbalance_gain * ret * 1e2, -0.9, 0.9)
    rows = []
    for i in range(len(close)):
        mid = close[i]
        half = mid * spread_bps / 2.0 / 1e4
        bid_mult, ask_mult = 1.0 + tilt[i], 1.0 - tilt[i]
        for lv in range(levels):
            step = half * (1 + 2 * lv)
            rows.append((ts_ms[i], "bid", lv, mid - step, depth * bid_mult * (1.0 - 0.1 * lv)))
            rows.append((ts_ms[i], "ask", lv, mid + step, depth * ask_mult * (1.0 - 0.1 * lv)))
    return pd.DataFrame(rows, columns=["snapshot_ts", "side", "level", "price", "amount"])


def _curve(series: pd.Series) -> list[dict]:
    return [{"time": int(ts.timestamp()), "value": round(float(v), 6)}
            for ts, v in series.items() if v == v]


def _build(name: str, *, alpha_bps: float, half_spread_bps: float, skew_bps: float,
           gamma: float, kappa: float, obi_alpha: float, q_max: float, quote_size: float,
           sigma_window: int):
    if name == "symmetric":
        return SymmetricMM(SymmetricMMParams(half_spread_bps=half_spread_bps, q_max=q_max,
                                             quote_size=quote_size))
    if name == "linear":
        return LinearInventoryMM(LinearInventoryMMParams(
            half_spread_bps=half_spread_bps, skew_bps=skew_bps, obi_alpha=obi_alpha,
            q_max=q_max, quote_size=quote_size))
    if name == "avellaneda":
        return AvellanedaStoikovMM(ASParams(gamma=gamma, kappa=kappa, obi_alpha=obi_alpha,
                                            q_max=q_max, quote_size=quote_size,
                                            sigma_window=sigma_window))
    if name == "alpha":
        return AlphaQuoterMM(AlphaQuoterMMParams(
            half_spread_bps=half_spread_bps, skew_bps=skew_bps, alpha_bps=alpha_bps,
            alpha_gain=0.5, q_max=q_max, quote_size=quote_size))
    if name == "alpha_taker":
        return AlphaQuoterMM(AlphaQuoterMMParams(
            half_spread_bps=half_spread_bps, skew_bps=skew_bps, alpha_bps=alpha_bps,
            alpha_gain=1.0, q_max=q_max, quote_size=quote_size,
            take_threshold_bps=1.0, taker_fee_bps=2.0))
    raise ValueError(f"unknown strategy '{name}' (use {[s for s, _ in STRATEGIES]})")


def _stats(summ: dict, final: float, initial_equity: float) -> dict:
    return {
        "final_equity": round(final, 2),
        "net_pnl": round(final - initial_equity, 2),
        "net_pnl_pct": round((final / initial_equity - 1) * 100, 4),
        "spread_captured_bps": round(summ["spread_captured_bps"], 3),
        "adv_sel_bps": round(summ.get("markout_1_bps", float("nan")), 3),
        "net_edge_bps": round(summ["net_edge_bps"], 3),
        "fill_ratio": round(summ["fill_ratio"], 4),
        "n_fills": int(summ["n_fills"]),
        "inv_max_abs": round(summ["inv_max_abs"], 2),
        "inv_half_life": round(summ["inv_half_life"], 1)
        if np.isfinite(summ["inv_half_life"]) else None,
    }


def _prepare(symbol: str, timeframe: str, spread_bps: float, levels: int, depth: float,
             imbalance_gain: float, max_snapshots: int):
    from app.services import market as mkt

    if "/" not in symbol:
        raise ValueError("market making runs on crypto pairs, e.g. BTC/USDT")
    _, frame = mkt.fetch_bars_intraday(symbol, "crypto", timeframe)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    if frame.empty or len(frame) < 50:
        raise ValueError(f"insufficient intraday data for {symbol} ({timeframe})")
    if len(frame) > max_snapshots:
        frame = frame.iloc[-max_snapshots:]

    book = _synthetic_book(frame, levels, spread_bps, depth, imbalance_gain)
    uni = Universe(name="mm", instruments=[
        Instrument(id=symbol, asset_class=AssetClass.CRYPTO, exchange="synthetic", lot_size=1e-12)])
    feat = book_features(book, levels=levels)
    alpha_bps, alpha_r2 = forward_return_on_obi(feat, horizon=1)
    return frame, book, uni, feat, alpha_bps, alpha_r2


def _run_one(name: str, *, book, uni, symbol, feat, alpha_bps, levels, maker_bps,
             initial_equity, **kw) -> dict:
    strat = _build(name, alpha_bps=alpha_bps, **kw)
    engine = MarketMakingEngine(cost_model=BpsCostModel(maker_bps),
                                taker_cost_model=BpsCostModel(2.0), initial_equity=initial_equity,
                                queue_model=False, levels=levels)
    result = engine.run_quoting(strat, {symbol: book}, uni)
    mid = feat["mid"].reindex(result.equity_curve.index)
    summ = mm_summary(result, mid=mid, instrument=symbol)
    final = float(result.equity_curve.iloc[-1]) if len(result.equity_curve) else initial_equity
    markout = sorted(({"h": int(k.split("_")[1]), "bps": round(summ[k], 4)}
                      for k in summ if k.startswith("markout_")), key=lambda d: d["h"])
    return {
        "key": name,
        "name": _LABELS[name],
        "equity_curve": _curve(result.equity_curve),
        "inventory_curve": _curve(result.positions[symbol]),
        "markout": markout,
        "stats": _stats(summ, final, initial_equity),
    }


def run_mm_backtest(*, symbol="BTC/USDT", timeframe="1m", strategy="alpha_taker",
                    gamma=0.3, kappa=1.5, q_max=20.0, obi_alpha=0.5, quote_size=1.0,
                    sigma_window=100, half_spread_bps=5.0, skew_bps=10.0, spread_bps=5.0,
                    levels=5, depth=5.0, imbalance_gain=1.0, maker_bps=1.0,
                    initial_equity=100_000.0, max_snapshots=4000) -> dict:
    """Single-strategy backtest over real bars + synthetic depth → curves + MM metrics."""
    frame, book, uni, feat, alpha_bps, alpha_r2 = _prepare(
        symbol, timeframe, spread_bps, levels, depth, imbalance_gain, max_snapshots)
    kw = dict(half_spread_bps=half_spread_bps, skew_bps=skew_bps, gamma=gamma, kappa=kappa,
              obi_alpha=obi_alpha, q_max=q_max, quote_size=quote_size, sigma_window=sigma_window)
    one = _run_one(strategy, book=book, uni=uni, symbol=symbol, feat=feat, alpha_bps=alpha_bps,
                   levels=levels, maker_bps=maker_bps, initial_equity=initial_equity, **kw)
    mid = feat["mid"]
    base = float(mid.dropna().iloc[0]) if mid.notna().any() else 1.0
    return {
        "symbol": symbol.upper(), "timeframe": timeframe, "strategy": strategy,
        "strategy_name": _LABELS.get(strategy, strategy), "initial_equity": initial_equity,
        "equity_curve": one["equity_curve"], "inventory_curve": one["inventory_curve"],
        "benchmark_curve": _curve(initial_equity * mid / base) if base else [],
        "mid_curve": _curve(mid), "markout": one["markout"], "stats": one["stats"],
        "alpha_bps": round(alpha_bps, 3), "alpha_r2": round(alpha_r2, 3),
        "meta": _meta(frame, spread_bps, levels),
    }


def compare_mm_backtest(*, symbol="BTC/USDT", timeframe="1m", q_max=20.0, obi_alpha=0.5,
                        quote_size=1.0, sigma_window=100, gamma=0.3, kappa=1.5,
                        half_spread_bps=5.0, skew_bps=10.0, spread_bps=5.0, levels=5,
                        depth=5.0, imbalance_gain=1.0, maker_bps=1.0,
                        initial_equity=100_000.0, max_snapshots=4000) -> dict:
    """Run ALL quoting strategies on one book → the full comparison the terminal renders."""
    frame, book, uni, feat, alpha_bps, alpha_r2 = _prepare(
        symbol, timeframe, spread_bps, levels, depth, imbalance_gain, max_snapshots)
    kw = dict(half_spread_bps=half_spread_bps, skew_bps=skew_bps, gamma=gamma, kappa=kappa,
              obi_alpha=obi_alpha, q_max=q_max, quote_size=quote_size, sigma_window=sigma_window)
    runs = [_run_one(name, book=book, uni=uni, symbol=symbol, feat=feat, alpha_bps=alpha_bps,
                     levels=levels, maker_bps=maker_bps, initial_equity=initial_equity, **kw)
            for name, _ in STRATEGIES]
    mid = feat["mid"]
    base = float(mid.dropna().iloc[0]) if mid.notna().any() else 1.0
    return {
        "symbol": symbol.upper(), "timeframe": timeframe, "initial_equity": initial_equity,
        "benchmark_curve": _curve(initial_equity * mid / base) if base else [],
        "strategies": runs, "alpha_bps": round(alpha_bps, 3), "alpha_r2": round(alpha_r2, 3),
        "meta": _meta(frame, spread_bps, levels),
    }


def _meta(frame: pd.DataFrame, spread_bps: float, levels: int) -> dict:
    return {
        "synthetic_depth": True,
        "note": "Real 1m price path with synthetic L2 depth/spread. The OBI signal is a "
                "bar-direction proxy, so the alpha edge here is illustrative — record live books "
                "(scripts/pull_orderbook_stream.py) for a true-depth, true-signal backtest.",
        "snapshots": len(frame), "spread_bps": spread_bps, "levels": levels,
    }
