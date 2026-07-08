"""Backtest service — turn a qhfi factor into a portfolio and run it through qhfi's engine.

Pipeline: factor signed scores → monthly cross-sectional weights (long-only or dollar-neutral
long-short) → BacktestEngine.run → equity curve + qhfi metrics. Reuses the same engine the
quant fund uses, so terminal backtests and qhfi backtests agree.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
from qhfi.backtest.engine import BacktestEngine, BacktestResult, SlippageModel
from qhfi.core.types import AssetClass, DateRange, Panel, TargetWeights, Universe
from qhfi.data.fundamentals import FundamentalsStore
from qhfi.data.manager import DataManager
from qhfi.data.providers.fundamentals_yfinance import YFinanceFundamentalsProvider
from qhfi.evaluation import metrics as M
from qhfi.evaluation.deflated_sharpe import deflated_from_trials, probabilistic_sharpe_ratio

from app import indicators
from app.services import factors as fac
from app.services.universe import get_universe

#: rebalance cadence → pandas period code for `_weights_from_scores`.
_REBAL_FREQ = {"monthly": "M", "quarterly": "Q", "annual": "Y", "yearly": "Y"}


def _clampf(v: object, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(v)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _weights_from_scores(scores: Panel, mode: str, top_pct: float, freq: str = "M") -> TargetWeights:
    """Cross-sectional weights from signed factor scores (higher = long), rebalanced on the last
    *trading day* of each period and forward-filled onto the daily grid.

    ``freq`` is a pandas period code — ``M`` (monthly, default), ``Q`` (quarterly), ``Y`` (annual).
    """
    # Last trading day present in the index for each calendar period.
    periods = scores.index.tz_localize(None).to_period(freq)  # tz-naive avoids a pandas warning
    rebal_dates = scores.groupby(periods).tail(1).index
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for t in rebal_dates:
        valid = scores.loc[t].dropna()
        if valid.empty:
            continue
        k = max(1, int(len(valid) * top_pct))
        ranked = valid.sort_values()
        longs = list(ranked.index[-k:])
        # Full row: names not selected this month are explicit ZEROS. With a sparse row,
        # the ffill below would carry a dropped name's old weight forward forever — the
        # book never exits anything and gross leverage silently compounds (observed:
        # 17 names / 284% gross on a 6-name long-only strategy).
        w: dict[str, float] = dict.fromkeys(scores.columns, 0.0)
        if mode == "long_only":
            for c in longs:
                w[c] = 1.0 / k
        else:  # long_short, dollar-neutral
            shorts = list(ranked.index[:k])
            for c in longs:
                w[c] = 0.5 / k
            for c in shorts:
                w[c] = -0.5 / k
        rows[t] = w
    if not rows:
        return pd.DataFrame(0.0, index=scores.index, columns=scores.columns)
    wdf = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=scores.columns)
    wdf.index = pd.DatetimeIndex(wdf.index)
    wdf = wdf.sort_index()
    return wdf.reindex(scores.index).ffill().fillna(0.0)


def _periods_per_year(universe: Universe) -> int:
    return 365 if all(i.asset_class == AssetClass.CRYPTO for i in universe.instruments) else 252


def _benchmark_stats(
    cand_ret: pd.Series, bench_ret: pd.Series, summary: dict, ppy: int
) -> dict | None:
    """Single-factor market-model analytics of the strategy against its benchmark.

    Regresses the strategy's daily returns on the benchmark's (rf = 0, matching the module's
    Sharpe convention): beta = cov/var, Jensen's alpha = mean(s) - beta*mean(b) annualized,
    plus active-return diagnostics (information ratio, tracking error) and fit (corr, R²).
    Returns ``None`` when the overlap is too short or the benchmark has no variance — the same
    guard the per-symbol BTC-beta in metrics.py uses.
    """
    joined = pd.concat([cand_ret.rename("s"), bench_ret.rename("b")], axis=1).dropna()
    if len(joined) < 20 or joined["b"].var() <= 0:
        return None
    s, b = joined["s"], joined["b"]
    beta = float(s.cov(b) / b.var())
    alpha_ann = float(s.mean() - beta * b.mean()) * ppy  # additive annualization, like Sharpe's
    active = s - b
    te_daily = float(active.std(ddof=0))
    te_ann = te_daily * (ppy ** 0.5)
    ir = (float(active.mean()) / te_daily * (ppy ** 0.5)) if te_daily > 0 else None
    corr = float(s.corr(b))
    bench = M.summary(b, periods_per_year=ppy)
    return {
        "beta": round(beta, 3),
        "alpha": round(alpha_ann * 100, 2),               # annualized Jensen's alpha, %
        "information_ratio": None if ir is None else round(ir, 2),
        "tracking_error": round(te_ann * 100, 2),          # annualized, %
        "correlation": round(corr, 3),
        "r_squared": round(corr * corr, 3),
        "bench_cagr": round(bench["cagr"] * 100, 2),
        "bench_sharpe": round(bench["sharpe"], 2),
        "excess_cagr": round((summary["cagr"] - bench["cagr"]) * 100, 2),  # strategy − benchmark
    }


def _monthly_rebalance_pairs(
    scores: Panel, win_start_d: date, win_end_d: date
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Consecutive month-end rebalance dates ``(a, b, a_naive)`` within the window, where forward
    returns run a→b. Shared by the IC and quantile-spread factor analytics so they agree on the
    period grid. ``a_naive`` is the tz-stripped prediction date (for labelling/window filtering)."""
    s = scores.dropna(how="all")
    if s.empty:
        return []
    months = s.index.tz_localize(None).to_period("M")
    rebal = list(s.groupby(months).tail(1).index)  # last trading day per month
    lo, hi = pd.Timestamp(win_start_d), pd.Timestamp(win_end_d)
    pairs = []
    for a, b in zip(rebal[:-1], rebal[1:]):
        a_naive = a.tz_localize(None) if a.tzinfo else a
        if lo <= a_naive <= hi:
            pairs.append((a, b, a_naive))
    return pairs


def _bootstrap_sharpe(
    cand_ret: pd.Series, ppy: int, n_boot: int = 1000, block: int = 5, seed: int = 12345
) -> dict | None:
    """Empirical confidence interval for the Sharpe via a circular block bootstrap.

    PSR/DSR are analytic; this resamples the realised returns in consecutive blocks (preserving
    short-horizon autocorrelation) ``n_boot`` times and reports the 5th/50th/95th percentile of
    the bootstrap Sharpe plus P(Sharpe > 0). A wide band means the headline Sharpe is one noisy
    draw. Seeded so the same backtest returns the same band (deterministic API).
    """
    r = cand_ret.dropna().to_numpy(dtype=float)
    n = len(r)
    if n < 60:
        return None
    block = max(1, min(block, n // 10))
    n_blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    sq = ppy ** 0.5
    offsets = np.arange(block)
    sharpes = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)  # circular: wrap with modulo
        idx = ((starts[:, None] + offsets[None, :]).ravel() % n)[:n]
        sample = r[idx]
        sd = sample.std()
        sharpes[i] = (sample.mean() / sd * sq) if sd > 0 else 0.0
    p5, p50, p95 = (float(x) for x in np.percentile(sharpes, [5, 50, 95]))
    return {
        "p5": round(p5, 2),
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "prob_positive": round(float((sharpes > 0).mean()) * 100, 1),
        "n_boot": n_boot,
        "block": block,
    }


def _rolling_beta_series(cand_ret: pd.Series, bench_ret: pd.Series, window: int = 63) -> list[dict]:
    """Trailing-window beta of the strategy to its benchmark over time — does market exposure
    drift? A dollar-neutral book should hover near 0; a long book near 1. ``window`` shrinks for
    short runs so a few quarters of history still yield a curve."""
    joined = pd.concat([cand_ret.rename("s"), bench_ret.rename("b")], axis=1).dropna()
    if len(joined) < 40:
        return []
    window = max(21, min(window, len(joined) // 3))
    cov = joined["s"].rolling(window).cov(joined["b"])
    var = joined["b"].rolling(window).var()
    beta = cov / var.where(var > 0)
    return [
        {"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 3)}
        for t, v in beta.items()
        if v == v  # skip the NaN warm-up + any zero-variance window
    ]


def _information_coefficient(
    scores: Panel, prices: Panel, win_start_d: date, win_end_d: date
) -> dict | None:
    """Monthly Information Coefficient — the cross-sectional rank (Spearman) correlation between a
    factor's signed scores and the *forward* one-month return.

    This is the factor-research view that the portfolio Sharpe can't give: does the signal actually
    rank winners ahead of losers, period by period? Reports mean IC, its volatility, the annualized
    ICIR (mean/std·√12), the hit rate (% of months with IC > 0), and the IC time series. Dated at
    the prediction time and filtered to the defined window.
    """
    if scores.dropna(how="all").shape[1] < 5:
        return None
    ics: list[float] = []
    times: list[pd.Timestamp] = []
    for a, b, a_naive in _monthly_rebalance_pairs(scores, win_start_d, win_end_d):
        try:
            fwd = prices.loc[b] / prices.loc[a] - 1.0  # next-month forward return per name
        except KeyError:
            continue
        df = pd.concat([scores.loc[a].rename("s"), fwd.rename("f")], axis=1).dropna()
        if len(df) < 5:
            continue
        ic = df["s"].corr(df["f"], method="spearman")
        if ic == ic:  # skip NaN (e.g. a constant score column)
            ics.append(float(ic))
            times.append(a_naive)
    if len(ics) < 3:
        return None
    ser = pd.Series(ics)
    mean_ic, std_ic = float(ser.mean()), float(ser.std(ddof=0))
    icir = (mean_ic / std_ic * (12 ** 0.5)) if std_ic > 0 else None
    return {
        "mean_ic": round(mean_ic, 3),
        "ic_std": round(std_ic, 3),
        "icir": None if icir is None else round(icir, 2),  # annualized
        "hit_rate": round(float((ser > 0).mean()) * 100, 1),
        "n_periods": len(ics),
        "series": [{"time": t.strftime("%Y-%m-%d"), "value": round(v, 3)} for t, v in zip(times, ics)],
    }


def _ic_decay(
    scores: Panel, prices: Panel, win_start_d: date, win_end_d: date,
    horizons: tuple[int, ...] = (1, 3, 6),
) -> list[dict] | None:
    """Information Coefficient at several forward horizons — how fast does the signal decay?

    For each horizon h (months), correlate the score at month-end ``i`` with the cumulative return
    to month-end ``i+h``. A signal whose IC fades from 1m→6m wants frequent rebalancing; one that
    holds rewards patience. Mean IC + annualized ICIR per horizon (overlapping windows, so ICIR is
    indicative). Dated/filtered at the prediction time, like the 1-month IC.
    """
    s = scores.dropna(how="all")
    if s.empty or s.shape[1] < 5:
        return None
    months = s.index.tz_localize(None).to_period("M")
    rebal = list(s.groupby(months).tail(1).index)
    lo, hi = pd.Timestamp(win_start_d), pd.Timestamp(win_end_d)
    out: list[dict] = []
    for h in horizons:
        ics: list[float] = []
        for i in range(len(rebal) - h):
            a, b = rebal[i], rebal[i + h]
            a_naive = a.tz_localize(None) if a.tzinfo else a
            if not (lo <= a_naive <= hi):
                continue
            try:
                fwd = prices.loc[b] / prices.loc[a] - 1.0
            except KeyError:
                continue
            df = pd.concat([scores.loc[a].rename("s"), fwd.rename("f")], axis=1).dropna()
            if len(df) < 5:
                continue
            ic = df["s"].corr(df["f"], method="spearman")
            if ic == ic:
                ics.append(float(ic))
        if len(ics) >= 3:
            ser = pd.Series(ics)
            m, sd = float(ser.mean()), float(ser.std(ddof=0))
            out.append({
                "horizon": h,
                "mean_ic": round(m, 3),
                "icir": None if sd <= 0 else round(m / sd * (12 ** 0.5), 2),
                "n": len(ics),
            })
    return out or None


def _quantile_spread(
    scores: Panel, prices: Panel, win_start_d: date, win_end_d: date, n_buckets: int = 5
) -> dict | None:
    """Forward returns by factor-score quantile — the economic *shape* the IC's magnitude hides.

    Each month, sort names into ``n_buckets`` equal score buckets (0 = lowest score) and record
    each bucket's mean forward one-month return; average across months and annualize. A real factor
    is roughly monotone (top bucket > … > bottom). Reports the per-bucket annualized return, the
    long-short spread (top − bottom), and the monotonicity (Spearman of bucket rank vs return).
    Returns ``None`` when the universe is too narrow to fill the buckets (needs ≥2 names each).
    """
    pairs = _monthly_rebalance_pairs(scores, win_start_d, win_end_d)
    if len(pairs) < 3:
        return None
    min_names = n_buckets * 2
    bucket_rets: list[list[float]] = [[] for _ in range(n_buckets)]
    used = 0
    for a, b, _ in pairs:
        sc = scores.loc[a].dropna()
        if len(sc) < min_names:
            continue
        try:
            fwd = prices.loc[b] / prices.loc[a] - 1.0
        except KeyError:
            continue
        df = pd.concat([sc.rename("s"), fwd.rename("f")], axis=1).dropna()
        if len(df) < min_names:
            continue
        # rank-then-qcut so equal-sized buckets even with score ties; 0 = lowest score
        q = pd.qcut(df["s"].rank(method="first"), n_buckets, labels=False)
        means = df["f"].groupby(q).mean()
        for bucket in range(n_buckets):
            if bucket in means.index:
                bucket_rets[bucket].append(float(means[bucket]))
        used += 1
    if used < 3:
        return None
    buckets: list[float | None] = []
    for arr in bucket_rets:
        if not arr:
            buckets.append(None)
            continue
        mr = sum(arr) / len(arr)
        buckets.append(round(((1 + mr) ** 12 - 1) * 100, 2))  # annualized from monthly mean
    valid = [(i, v) for i, v in enumerate(buckets) if v is not None]
    if len(valid) < 3:
        return None
    spread = (
        round(buckets[-1] - buckets[0], 2)
        if buckets[-1] is not None and buckets[0] is not None
        else None
    )
    bi = pd.Series([i for i, _ in valid])
    bv = pd.Series([v for _, v in valid])
    mono = bi.corr(bv, method="spearman")
    return {
        "n_buckets": n_buckets,
        "buckets": buckets,                                  # annualized %, low-score → high-score
        "spread": spread,                                    # top − bottom bucket, annualized %
        "monotonicity": None if mono != mono else round(float(mono), 2),  # 1 = perfectly monotone
        "n_periods": used,
    }


def _cost_sensitivity(
    weights: TargetWeights, prices: Panel, universe: Universe,
    win_start_d: date, win_end_d: date, ppy: int, initial_equity: float,
    bps_grid: tuple[float, ...] = (0, 5, 10, 20, 40),
) -> list[dict] | None:
    """Re-run the engine over the same target weights at a ladder of slippage levels (bps) to show
    how net Sharpe / CAGR decay as trading frictions rise — does the edge survive realistic costs?

    Cheap: reuses the already-loaded weights + prices; only the slippage assumption changes. A
    high-turnover strategy whose Sharpe collapses by 20bps is fragile regardless of its headline.
    """
    win_start = pd.Timestamp(win_start_d)
    win_end = pd.Timestamp(win_end_d) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    points: list[dict] = []
    for bps in bps_grid:
        try:
            res = BacktestEngine(
                slippage=SlippageModel(bps=float(bps)), initial_equity=initial_equity
            ).run(weights, prices, universe)
        except Exception:  # noqa: BLE001 - a single cost point failing shouldn't drop the curve
            continue
        r = _clip_window(res.returns, win_start, win_end).dropna()
        if r.empty:
            continue
        s = M.summary(r, periods_per_year=ppy)
        points.append({
            "bps": float(bps),
            "sharpe": round(s["sharpe"], 2),
            "cagr": round(s["cagr"] * 100, 2),
            "total_costs": round(float(_clip_window(res.costs, win_start, win_end).sum()), 2),
        })
    return points or None


def _sector_exposure(last_w: pd.Series, universe: Universe) -> list[dict] | None:
    """Net long/short weight by GICS sector at the last rebalance — does the book unintentionally
    concentrate or tilt into one sector? Sums signed weights per sector (longs − shorts). Returns
    ``None`` when the universe carries no sector tags (e.g. crypto/fx), where the view is moot."""
    if last_w is None or len(last_w) == 0:
        return None
    smap = {ins.id.upper(): (ins.sector or "Unknown") for ins in universe.instruments}
    agg: dict[str, float] = {}
    for sym, w in last_w.items():
        sec = smap.get(str(sym).upper(), "Unknown")
        agg[sec] = agg.get(sec, 0.0) + float(w)
    if set(agg) <= {"Unknown"}:  # no real sector info to show
        return None
    rows = [{"sector": s, "net": round(v * 100, 2)} for s, v in agg.items() if abs(v) > 1e-6]
    rows.sort(key=lambda r: r["net"], reverse=True)
    return rows or None


def _benchmark_symbol(universe: Universe) -> tuple[str, str] | None:
    """The universe's investable benchmark: SPY for any equity universe (cap-weighted S&P 500),
    BTC for an all-crypto universe. ``None`` → no listed proxy, use the equal-weight mean."""
    classes = {i.asset_class for i in universe.instruments}
    if classes == {AssetClass.CRYPTO}:
        return ("BTC/USDT", "crypto")
    if AssetClass.EQUITY in classes:
        return ("SPY", "equity")
    return None


def index_benchmark_returns(
    dm: DataManager, universe: Universe, grid: pd.DatetimeIndex
) -> tuple[pd.Series | None, str]:
    """Best-effort daily returns of the universe's investable benchmark, reindexed onto ``grid``.

    Returns ``(returns, label)``; on any failure (no proxy, fetch error, too little overlap with
    the grid) returns ``(None, "equal-weight")`` so ``shape_result`` falls back to the
    equal-weight universe mean. The fetch is cached by the data manager, so repeat runs are cheap.
    """
    spec = _benchmark_symbol(universe)
    if spec is None or len(grid) == 0:
        return None, "equal-weight"
    symbol, asset = spec
    try:
        from app.services.market import fetch_bars

        _, bars = fetch_bars(dm, symbol, asset, grid[0].date(), grid[-1].date())
        if bars.empty or "close" not in bars or bars["close"].dropna().empty:
            return None, "equal-weight"
        ret = bars["close"].pct_change().reindex(grid)
        if int(ret.notna().sum()) < max(20, len(grid) // 2):  # too sparse to trust
            return None, "equal-weight"
        return ret, symbol
    except Exception:  # noqa: BLE001 - the benchmark is decorative; never fail a backtest on it
        return None, "equal-weight"


def _subperiod_stability(cand_ret: pd.Series, ppy: int, k: int = 4) -> dict | None:
    """Split the in-window daily returns into ``k`` consecutive equal-length sub-periods and
    report each one's annualized Sharpe + total return + max drawdown.

    Reveals regime dependence — whether the edge is steady or concentrated in one lucky stretch
    — which the full-sample headline Sharpe hides. No look-ahead is introduced: the factors are
    fixed formulas (not fit), so segmenting the realised return stream is a fair stability read,
    orthogonal to the multiple-trials penalty (Deflated Sharpe) and the benchmark market model.
    ``consistency`` is the share of sub-periods with a positive return.
    """
    cand = cand_ret.dropna()
    n = len(cand)
    if n < 40:  # too short to split meaningfully
        return None
    k = min(k, max(2, n // 15))  # keep ~15+ observations per segment
    bounds = [round(i * n / k) for i in range(k + 1)]
    periods: list[dict] = []
    positive = 0
    for i in range(k):
        seg = cand.iloc[bounds[i]:bounds[i + 1]]
        if len(seg) < 5:
            continue
        s = M.summary(seg, periods_per_year=ppy)
        total = float((1 + seg).prod() - 1.0)
        periods.append({
            "label": f"{seg.index[0].strftime('%y-%m')}→{seg.index[-1].strftime('%y-%m')}",
            "start": seg.index[0].strftime("%Y-%m-%d"),
            "end": seg.index[-1].strftime("%Y-%m-%d"),
            "sharpe": round(s["sharpe"], 2),
            "ret": round(total * 100, 2),
            "max_drawdown": round(s["max_drawdown"] * 100, 2),
        })
        if total > 0:
            positive += 1
    if len(periods) < 2:
        return None
    sharpes = [p["sharpe"] for p in periods]
    return {
        "periods": periods,
        "n_periods": len(periods),
        "positive_periods": positive,
        "consistency": round(positive / len(periods), 2),  # share with positive return
        "sharpe_min": min(sharpes),
        "sharpe_max": max(sharpes),
    }


def _distribution_stats(cand_ret: pd.Series) -> dict | None:
    """Shape and tail risk of the daily return distribution — what Sharpe/Sortino can't show.

    Sharpe assumes returns are roughly normal; real strategies are skewed and fat-tailed. This
    reports skew, excess kurtosis (normal = 0), the historical 95% daily VaR and its expected
    shortfall (CVaR = mean loss beyond VaR), best/worst day, the share of up days, the tail ratio
    (right tail / left tail), and the average-win / average-loss ratio. All percentages in %.
    """
    r = cand_ret.dropna()
    if len(r) < 20:
        return None
    q05 = float(r.quantile(0.05))
    q95 = float(r.quantile(0.95))
    tail = r[r <= q05]
    wins, losses = r[r > 0], r[r < 0]
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    return {
        "skew": round(float(r.skew()), 2),
        "kurtosis": round(float(r.kurt()), 2),          # excess kurtosis (normal = 0)
        "var95": round(q05 * 100, 2),                    # 95% daily VaR, % (a loss → negative)
        "cvar95": round(float(tail.mean()) * 100, 2) if len(tail) else None,  # expected shortfall
        "best_day": round(float(r.max()) * 100, 2),
        "worst_day": round(float(r.min()) * 100, 2),
        "pct_positive": round(float((r > 0).mean()) * 100, 1),
        "tail_ratio": round(q95 / abs(q05), 2) if q05 != 0 else None,  # >1 = fatter right tail
        "win_loss": round(float(wins.mean()) / abs(avg_loss), 2) if len(wins) and avg_loss != 0 else None,
    }


def _drawdown_table(eq: pd.Series, top: int = 5) -> list[dict]:
    """Decompose an equity curve into peak-to-trough drawdown episodes, worst first.

    A new episode opens when equity falls below the running peak and closes when equity recovers
    to that peak (an episode still open at the end is ``ongoing`` with no recovery date). For each
    episode: ``depth`` (trough/peak − 1, %), and three calendar-day durations — decline
    (peak→trough), recovery (trough→peak), and total underwater (peak→recovery, or peak→last bar
    while ongoing). The drawdown *curve* shows shape; this answers "how deep, how long, recovered?"
    """
    eq = eq.dropna()
    if len(eq) < 2:
        return []
    peak = float(eq.iloc[0])
    peak_date = eq.index[0]
    trough = peak
    trough_date = peak_date
    in_dd = False
    last_date = eq.index[-1]
    episodes: list[dict] = []

    def _days(a, b) -> int:
        return max(0, int((b - a).days))

    for t, raw in eq.items():
        v = float(raw)
        if v >= peak:
            if in_dd:  # equity reclaimed the prior peak → episode recovered
                episodes.append({
                    "depth": trough / peak - 1.0,
                    "peak_date": peak_date, "trough_date": trough_date, "recovery_date": t,
                    "decline_days": _days(peak_date, trough_date),
                    "recovery_days": _days(trough_date, t),
                    "underwater_days": _days(peak_date, t),
                    "ongoing": False,
                })
                in_dd = False
            peak, peak_date, trough, trough_date = v, t, v, t
        else:  # below the running peak → underwater
            if not in_dd:
                in_dd, trough, trough_date = True, v, t
            elif v < trough:
                trough, trough_date = v, t
    if in_dd:  # never recovered by the end of the window
        episodes.append({
            "depth": trough / peak - 1.0,
            "peak_date": peak_date, "trough_date": trough_date, "recovery_date": None,
            "decline_days": _days(peak_date, trough_date),
            "recovery_days": None,
            "underwater_days": _days(peak_date, last_date),
            "ongoing": True,
        })

    episodes.sort(key=lambda e: e["depth"])  # most negative (deepest) first
    out = []
    for rank, e in enumerate(episodes[:top], start=1):
        out.append({
            "rank": rank,
            "depth": round(e["depth"] * 100, 2),
            "peak_date": e["peak_date"].strftime("%Y-%m-%d"),
            "trough_date": e["trough_date"].strftime("%Y-%m-%d"),
            "recovery_date": e["recovery_date"].strftime("%Y-%m-%d") if e["recovery_date"] else None,
            "decline_days": e["decline_days"],
            "recovery_days": e["recovery_days"],
            "underwater_days": e["underwater_days"],
            "ongoing": e["ongoing"],
        })
    return out


def _run_strategy(
    dm: DataManager,
    fstore: FundamentalsStore,
    fprov: YFinanceFundamentalsProvider,
    universe: Universe,
    prices: Panel,
    factor_key: str,
    mode: str,
    top_pct: float,
    initial_equity: float,
    freq: str = "M",
) -> BacktestResult:
    scores = fac.build_signed(dm, fstore, fprov, universe, prices, factor_key)
    weights = _weights_from_scores(scores, mode, top_pct, freq)
    return BacktestEngine(initial_equity=initial_equity).run(weights, prices, universe)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s.strip()[:10])
    except ValueError:
        return None


def _clip_window(s: pd.Series, win_start: pd.Timestamp, win_end: pd.Timestamp) -> pd.Series:
    """Clip an engine output series to the defined window. Engine series carry a tz-aware
    (UTC) DatetimeIndex; coerce the bounds to the index tz per series."""
    idx = s.index
    lo, hi = win_start, win_end
    if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
        lo = lo.tz_localize(idx.tz) if lo.tzinfo is None else lo.tz_convert(idx.tz)
        hi = hi.tz_localize(idx.tz) if hi.tzinfo is None else hi.tz_convert(idx.tz)
    return s.loc[(idx >= lo) & (idx <= hi)]


def _points(s: pd.Series, digits: int = 2) -> list[dict]:
    """Serialize a (date-indexed) series to lightweight-charts line data, skipping NaN warm-up rows."""
    return [
        {"time": t.strftime("%Y-%m-%d"), "value": round(float(v), digits)}
        for t, v in s.items()
        if v == v
    ]


def _timing_exposure(
    dm: DataManager, universe: Universe, prices: Panel, baseline_returns: pd.Series, timing: dict
) -> tuple[pd.Series, dict] | None:
    """A per-date market-exposure multiplier (≈[0,1]) for a timing overlay; ``None`` if the universe
    has no investable benchmark to time off (then the run stays fully invested).

    * **trend**  — benchmark close vs its SMA: 1.0 when above, ``floor`` when below.
    * **regime** — qhfi ``RegimeAllocationMDP`` fitted on (benchmark, baseline-book) returns → the
      optimal risky fraction per regime, clipped to ``[0, max_leverage]`` (same flow as ``MDPStrategy``).

    Causal: the signal is lagged one day before it gates the book; warm-up = fully invested.
    """
    spec = _benchmark_symbol(universe)
    if spec is None:
        return None
    symbol, asset = spec
    try:
        from app.services.market import fetch_bars

        _, bars = fetch_bars(dm, symbol, asset, prices.index[0].date(), prices.index[-1].date())
    except Exception:  # noqa: BLE001 - timing is an overlay; never fail the backtest on the benchmark
        return None
    if bars.empty or "close" not in bars or bars["close"].dropna().empty:
        return None
    close = bars["close"].dropna()
    kind = timing.get("kind")

    if kind == "trend":
        ma = int(_clampf(timing.get("ma", 200), 10, 400, 200))
        floor = _clampf(timing.get("floor", 0.0), 0.0, 1.0, 0.0)
        above = close > indicators.sma(close, ma)
        exposure = pd.Series(np.where(above, 1.0, floor), index=close.index)
        diag = {"kind": "trend", "params": {"ma": ma, "floor": round(floor, 2)}}
    elif kind == "regime":
        from qhfi.mdp.allocation import RegimeAllocationMDP

        n_regimes = int(_clampf(timing.get("n_regimes", 3), 2, 5, 3))
        lookback = int(_clampf(timing.get("lookback", 63), 20, 252, 63))
        max_lev = _clampf(timing.get("max_leverage", 1.0), 0.5, 1.5, 1.0)
        grid = tuple(np.round(np.arange(0.0, max_lev + 1e-9, 0.25), 6))
        bench_ret = close.pct_change()
        market = bench_ret.dropna()
        if len(market) < max(lookback + 20, 60):  # too short to fit regimes
            return None
        book = baseline_returns.reindex(market.index).fillna(0.0)
        mdp = RegimeAllocationMDP(n_regimes=n_regimes, lookback=lookback, action_grid=grid).fit(market, book)
        labels = mdp.label(market)
        exposure = labels.map(lambda r: mdp.optimal_fraction(int(r))).clip(0.0, max_lev)
        pol = mdp.policy_table()
        diag = {
            "kind": "regime",
            "params": {"n_regimes": n_regimes, "lookback": lookback, "max_leverage": round(max_lev, 2)},
            "labels": [{"time": t.strftime("%Y-%m-%d"), "value": int(v)} for t, v in labels.items() if v == v],
            "policy": [
                {
                    "regime": int(i),
                    "ann_mean": round(float(row.ann_mean) * 100, 2),
                    "ann_vol": round(float(row.ann_vol) * 100, 2),
                    "risky_fraction": round(float(row.risky_fraction), 2),
                }
                for i, row in pol.iterrows()
            ],
        }
    else:
        return None

    return exposure.shift(1).fillna(1.0), diag


def _timing_block(
    diag: dict, exposure: pd.Series, base_result: BacktestResult, timed_result: BacktestResult,
    win_start_d: date, win_end_d: date, initial_equity: float, ppy: int,
) -> dict:
    """Dashboard diagnostics for a timed run: the exposure path, the un-timed baseline equity curve,
    and the timed−baseline metric deltas (so the UI can show what the overlay added)."""
    win_start = pd.Timestamp(win_start_d)
    win_end = pd.Timestamp(win_end_d) + pd.Timedelta(hours=23, minutes=59, seconds=59)

    def win(s):
        return _clip_window(s, win_start, win_end)

    b_raw = win(base_result.equity_curve.dropna())
    base_eq = (b_raw / float(b_raw.iloc[0]) * initial_equity) if len(b_raw) else b_raw
    base_sum = M.summary(win(base_result.returns).dropna(), periods_per_year=ppy)
    timed_sum = M.summary(win(timed_result.returns).dropna(), periods_per_year=ppy)
    exp_win = win(exposure.reindex(timed_result.returns.index).ffill().fillna(1.0)) * 100.0  # % invested
    return {
        **diag,
        "exposure_curve": _points(exp_win, 1),
        "baseline_equity_curve": _points(base_eq, 2),
        "delta": {
            "sharpe": round(timed_sum["sharpe"] - base_sum["sharpe"], 2),
            "cagr": round((timed_sum["cagr"] - base_sum["cagr"]) * 100, 2),
            "max_drawdown": round((timed_sum["max_drawdown"] - base_sum["max_drawdown"]) * 100, 2),
        },
    }


def run_backtest(
    dm: DataManager,
    fstore: FundamentalsStore,
    fprov: YFinanceFundamentalsProvider,
    universe_name: str,
    factor_key: str,
    mode: str = "long_short",
    top_pct: float = 0.2,
    years: int = 3,
    initial_equity: float = 100_000.0,
    deflate: bool = True,
    start: str | None = None,
    end: str | None = None,
    rebalance: str = "monthly",
    timing: dict | None = None,
) -> dict:
    if factor_key not in fac.CATALOG:
        raise ValueError(f"unknown factor '{factor_key}'")
    universe = get_universe(universe_name)

    # Defined time window: explicit start/end win; otherwise `years` back from today.
    today = date.today()
    win_end_d = min(_parse_date(end) or today, today)
    win_start_d = _parse_date(start) or (win_end_d - timedelta(days=int(years * 365.25)))
    if win_start_d >= win_end_d:
        return {"error": "window start must be before end", "universe": universe_name}
    data_start = win_start_d - timedelta(days=200)  # warm-up so factors have lookback before the window
    span = DateRange(start=data_start, end=win_end_d)

    dm.update(universe, span)
    prices = dm.get_panel(universe, "close", span)
    if prices.empty or prices.shape[1] < 3:
        return {"error": "insufficient data", "universe": universe_name}

    # Candidate run — built inline (not via _run_strategy) so the signed scores are available
    # for the Information Coefficient as well as the weights.
    freq = _REBAL_FREQ.get(str(rebalance).lower(), "M")
    scores = fac.build_signed(dm, fstore, fprov, universe, prices, factor_key)
    base_weights = _weights_from_scores(scores, mode, top_pct, freq)

    # Optional market-timing overlay: backtest the un-timed book first, scale its weights per-date
    # by the timing exposure, then re-run. The baseline result is kept for the side-by-side compare.
    timing_diag = None
    if isinstance(timing, dict) and timing.get("kind") in ("trend", "regime"):
        base_result = BacktestEngine(initial_equity=initial_equity).run(base_weights, prices, universe)
        pair = _timing_exposure(dm, universe, prices, base_result.returns, timing)
        if pair is not None:
            exposure, diag = pair
            exposure = exposure.reindex(base_weights.index).ffill().fillna(1.0)
            weights = base_weights.mul(exposure, axis=0).fillna(0.0)
            result = BacktestEngine(initial_equity=initial_equity).run(weights, prices, universe)
            timing_diag = _timing_block(
                diag, exposure, base_result, result, win_start_d, win_end_d,
                initial_equity, _periods_per_year(universe),
            )
        else:  # no benchmark / too short → stay fully invested
            weights, result = base_weights, base_result
    else:
        weights = base_weights
        result = BacktestEngine(initial_equity=initial_equity).run(weights, prices, universe)

    ic = _information_coefficient(scores, prices, win_start_d, win_end_d)
    ic_decay = _ic_decay(scores, prices, win_start_d, win_end_d)
    quantile = _quantile_spread(scores, prices, win_start_d, win_end_d)
    cost_curve = _cost_sensitivity(
        weights, prices, universe, win_start_d, win_end_d, _periods_per_year(universe), initial_equity
    )

    win_start = pd.Timestamp(win_start_d)
    win_end = pd.Timestamp(win_end_d) + pd.Timedelta(hours=23, minutes=59, seconds=59)
    cand_ret = _clip_window(result.returns, win_start, win_end).dropna()
    if cand_ret.empty:
        return {"error": "no returns in the selected window", "universe": universe_name}

    # Deflated Sharpe: deflate the candidate against the search of all cheap catalog factors,
    # so a high in-sample Sharpe found by trying many factors is penalised for selection bias.
    dsr, n_trials = None, 0
    if deflate:
        trials = []
        for k in fac.trial_keys():
            try:
                r = _run_strategy(dm, fstore, fprov, universe, prices, k, mode, top_pct, initial_equity, freq)
                trials.append(_clip_window(r.returns, win_start, win_end).dropna())
            except Exception:  # noqa: BLE001 - a single trial factor failing shouldn't abort
                continue
        if factor_key not in fac.trial_keys():
            trials.append(cand_ret)
        if len(trials) >= 2:
            dsr = deflated_from_trials(cand_ret, trials)
            n_trials = len(trials)

    bench, bench_label = index_benchmark_returns(dm, universe, prices.index)
    return shape_result(
        result, prices, universe, universe_name, win_start_d, win_end_d, initial_equity,
        label_extra={"factor": factor_key, "mode": mode, "rebalance": rebalance}, dsr=dsr, n_trials=n_trials,
        bench=bench, bench_label=bench_label, ic=ic, ic_decay=ic_decay,
        quantile=quantile, cost_curve=cost_curve, timing=timing_diag,
    )


def shape_result(
    result: BacktestResult,
    prices: Panel,
    universe: Universe,
    universe_name: str,
    win_start_d: date,
    win_end_d: date,
    initial_equity: float,
    *,
    label_extra: dict,
    dsr: float | None = None,
    n_trials: int = 0,
    bench: pd.Series | None = None,
    bench_label: str = "equal-weight",
    ic: dict | None = None,
    ic_decay: list[dict] | None = None,
    quantile: dict | None = None,
    cost_curve: list[dict] | None = None,
    timing: dict | None = None,
) -> dict:
    """Turn a raw ``BacktestResult`` into the terminal dashboard payload (curves + metrics).

    Shared by the factor backtest and the qhfi-strategy engine run so both produce the same
    shape. ``label_extra`` supplies the run's identity keys (``factor``/``mode`` or
    ``strategy``/``mode``); ``dsr``/``n_trials`` are the optional Deflated-Sharpe results.
    """
    win_start = pd.Timestamp(win_start_d)
    win_end = pd.Timestamp(win_end_d) + pd.Timedelta(hours=23, minutes=59, seconds=59)

    def win(s):
        return _clip_window(s, win_start, win_end)

    ppy = _periods_per_year(universe)
    cand_ret = win(result.returns).dropna()
    summary = M.summary(cand_ret, periods_per_year=ppy)
    psr = probabilistic_sharpe_ratio(cand_ret)  # P(true SR > 0), per-period

    # Equity rebased to `initial_equity` at the window start, so the curve + PnL are window-relative.
    eq_raw = win(result.equity_curve.dropna())
    eq = (eq_raw / float(eq_raw.iloc[0]) * initial_equity) if len(eq_raw) else eq_raw
    # Benchmark returns on the strategy's window grid: a real investable index when the caller
    # supplied one (SPY/BTC), else the equal-weight mean of the universe's own constituents.
    if bench is not None:
        bench_ret = win(bench).reindex(eq.index).fillna(0.0)
    else:
        bench_ret = win(prices.pct_change().mean(axis=1)).reindex(eq.index).fillna(0.0)
        bench_label = "equal-weight"
    bench_curve = (1 + bench_ret).cumprod() * initial_equity
    pnl_series = eq - initial_equity
    final_eq = float(eq.iloc[-1]) if len(eq) else initial_equity

    benchmark = _benchmark_stats(cand_ret, bench_ret, summary, ppy)
    rolling_beta = _rolling_beta_series(cand_ret, bench_ret)
    sharpe_ci = _bootstrap_sharpe(cand_ret, ppy)
    stability = _subperiod_stability(cand_ret, ppy)

    def points(s: pd.Series, digits: int = 2) -> list[dict]:
        return [
            {"time": t.strftime("%Y-%m-%d"), "value": round(float(v), digits)}
            for t, v in s.items()
            if v == v  # skip NaN warm-up rows
        ]

    # ── dashboard series (all derived from the engine's audit outputs) ─────────
    drawdown = (eq / eq.cummax() - 1.0) * 100
    drawdowns = _drawdown_table(eq)
    distribution = _distribution_stats(cand_ret)

    roll = max(21, min(63, len(cand_ret) // 4))  # ~quarterly window, shrink for short runs
    roll_mean = cand_ret.rolling(roll).mean()
    roll_std = cand_ret.rolling(roll).std()
    rolling_sharpe = (roll_mean / roll_std) * (ppy ** 0.5)

    monthly = (1 + cand_ret).resample("ME").prod() - 1.0
    monthly_returns = [
        {"year": int(t.year), "month": int(t.month), "ret": round(float(v) * 100, 2)}
        for t, v in monthly.items()
        if v == v
    ]

    turnover_win = win(result.turnover)
    turnover_m = turnover_win.resample("ME").sum() * 100  # one-way % traded per month
    costs_win = win(result.costs)
    costs_cum = costs_win.cumsum()

    weights_win = win(result.weights).dropna(how="all")
    last_w = weights_win.iloc[-1].dropna() if len(weights_win) else pd.Series(dtype=float)
    last_w = last_w[last_w.abs() > 1e-6]
    top_weights = [
        {"symbol": str(sym), "weight": round(float(w) * 100, 2)}
        for sym, w in last_w.reindex(last_w.abs().sort_values(ascending=False).index).head(12).items()
    ]

    return {
        "universe": universe_name,
        **label_extra,
        "n_instruments": int(prices.shape[1]),
        "window_start": cand_ret.index[0].strftime("%Y-%m-%d"),
        "window_end": cand_ret.index[-1].strftime("%Y-%m-%d"),
        "pnl": round(final_eq - initial_equity, 2),
        "pnl_pct": round((final_eq / initial_equity - 1.0) * 100, 2),
        "metrics": {
            "cagr": round(summary["cagr"] * 100, 2),
            "ann_vol": round(summary["ann_vol"] * 100, 2),
            "sharpe": round(summary["sharpe"], 2),
            "sortino": round(summary["sortino"], 2),
            "max_drawdown": round(summary["max_drawdown"] * 100, 2),
            "calmar": round(summary["calmar"], 2),
        },
        "robustness": {
            "psr": None if psr != psr else round(float(psr) * 100, 1),   # P(SR>0), %
            "dsr": None if dsr is None else round(float(dsr) * 100, 1),  # deflated PSR, %
            "n_trials": n_trials,
        },
        "benchmark": benchmark,
        "benchmark_label": bench_label,
        "rolling_beta": rolling_beta,
        "sharpe_ci": sharpe_ci,
        "ic": ic,
        "ic_decay": ic_decay,
        "quantile_spread": quantile,
        "cost_sensitivity": cost_curve,
        "stability": stability,
        "avg_turnover": round(float(turnover_win.mean()) * 100, 2) if len(turnover_win) else 0.0,
        "total_costs": round(float(costs_win.sum()), 2),
        "final_equity": round(final_eq, 2),
        "equity_curve": points(eq),
        "benchmark_curve": points(bench_curve),
        "pnl_curve": points(pnl_series),
        "drawdown_curve": points(drawdown),
        "drawdowns": drawdowns,
        "distribution": distribution,
        "rolling_sharpe": points(rolling_sharpe),
        "rolling_window": roll,
        "monthly_returns": monthly_returns,
        "turnover_monthly": points(turnover_m),
        "costs_cum": points(costs_cum),
        "gross_exposure": points(result.gross_exposure.reindex(eq.index) * 100),
        "net_exposure": points(result.net_exposure.reindex(eq.index) * 100),
        "top_weights": top_weights,
        "sector_exposure": _sector_exposure(last_w, universe),
        "timing": timing,
    }
