"""Factor performance monitoring — IC / decay / quantile / turnover diagnostics for the
terminal's factors, plus persisted monitor sets with snapshot history.

Reuses ``factors.build_signed`` (factor key → signed score panel for price/alpha/value/quality)
and qhfi's ``factors.evaluation`` diagnostics. Everything aligns a factor score at date *t* with
the return realized after *t*, so there's no look-ahead.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
from qhfi.core.types import DateRange
from qhfi.data.manager import DataManager
from qhfi.factors import evaluation as ev
from qhfi.factors import heatmap

from app.services import factors as fac
from app.services.market import fetch_bars
from app.services.universe import get_universe

_DECAY_HORIZONS = (1, 2, 3, 5, 10, 21)
_DECILE = 0.1
_MAX_FACTOR_SYMBOLS = 60  # cap the per-symbol sweep for custom formula factors (matches sandbox)


# ── factor-library resolution (built-in CATALOG + custom formulas + engine/linked) ────
def _custom_index(store: Any) -> dict[str, dict]:
    """name → custom-factor record (sandboxed formula code) from the SQLite registry."""
    if store is None:
        return {}
    try:
        return {r["name"]: r for r in store.list_custom_factors()}
    except Exception:  # noqa: BLE001 - a store hiccup shouldn't sink factor lookups
        return {}


def _engine_names(store: Any) -> list[str]:
    """Names registered in the live qhfi factor registry (built-ins + linked drop-ins)."""
    if store is None:
        return []
    try:
        from app.services import registry as reg
        return [e["name"] for e in reg.engine_factors(store)]
    except Exception:  # noqa: BLE001
        return []


def _labels(store: Any = None) -> dict[str, str]:
    """Display labels for every resolvable factor (catalog + custom + engine)."""
    labels = {f["key"]: f["label"] for f in fac.available_factors()}
    for name, rec in _custom_index(store).items():
        labels[name] = rec.get("label") or name
    if store is not None:
        try:
            from app.services import registry as reg
            for e in reg.engine_factors(store):
                labels.setdefault(e["name"], e.get("label") or e["name"])
        except Exception:  # noqa: BLE001
            pass
    return labels


def _default_keys(store: Any) -> list[str]:
    """Default leaderboard set: cheap built-in trial factors + the user's custom factors."""
    return fac.trial_keys() + list(_custom_index(store))


def known_factor_keys(store: Any) -> set[str]:
    """Every factor key the module can resolve — for agent validation."""
    return set(fac.CATALOG) | set(_custom_index(store)) | set(_engine_names(store))


def _custom_scores(dm: DataManager, rec: dict, universe: Any, prices: pd.DataFrame) -> pd.DataFrame:
    """Run a custom sandboxed factor formula per instrument → a signed cross-sectional score panel.
    Reuses ``sandbox._factor_formula`` (per-symbol OHLCV → score Series)."""
    from app.services import sandbox  # local import: avoids factors↔sandbox↔registry import cycle

    code = (rec.get("code") or "").strip()
    if not code:
        raise ValueError(f"custom factor '{rec.get('name')}' has no code")
    fn = sandbox._factor_formula(code)
    cols: dict[str, pd.Series] = {}
    for ins in universe.instruments[:_MAX_FACTOR_SYMBOLS]:
        try:
            _, bars = fetch_bars(dm, ins.id, ins.asset_class.value)
            if bars.empty:
                continue
            res = fn(bars, {})
            if res is None:
                continue
            s = res if isinstance(res, pd.Series) else pd.Series(res, index=bars.index)
            cols[ins.id] = pd.to_numeric(s, errors="coerce")
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the factor
            continue
    if len(cols) < 3:
        raise ValueError(f"custom factor '{rec.get('name')}' produced too few usable series")
    panel = pd.DataFrame(cols).reindex(prices.index)
    if str(rec.get("direction", "high=long")) in ("low=long", "cheap=long"):
        panel = -panel
    return panel


def _engine_scores(store: Any, key: str, prices: pd.DataFrame, universe: Any) -> pd.DataFrame | None:
    """Signed panel from a qhfi-registry factor (built-in or linked drop-in). None if unavailable
    or it needs multi-field panels we don't supply here (caller treats None as 'skip')."""
    try:
        from app.services import registry as reg
        reg.load_dir_modules(reg.get_paths(store)["factors_dir"])
        from qhfi.factors import registry as freg
        if key not in set(freg.all_names()):
            return None
        return freg.get(key)().signed(prices, universe)
    except Exception:  # noqa: BLE001 - engine factor that can't build here is skipped, not fatal
        return None


def _signed_scores(
    dm: DataManager, fstore: Any, fprov: Any, store: Any, universe: Any, prices: pd.DataFrame, key: str,
) -> pd.DataFrame:
    """Unified score resolver: built-in CATALOG → ``build_signed``; custom formula → sandbox run;
    else qhfi engine/linked registry. Raises ValueError for an unknown key."""
    if key in fac.CATALOG:
        return fac.build_signed(dm, fstore, fprov, universe, prices, key)
    custom = _custom_index(store)
    if key in custom:
        return _custom_scores(dm, custom[key], universe, prices)
    eng = _engine_scores(store, key, prices, universe)
    if eng is not None:
        return eng
    raise ValueError(f"unknown factor '{key}'")


def _load_prices(dm: DataManager, universe_name: str, lookback_days: int):
    """Universe + close panel over [today - lookback - 200d warm-up, today]."""
    universe = get_universe(universe_name)
    end = date.today()
    span = DateRange(start=end - timedelta(days=int(lookback_days) + 200), end=end)
    dm.update(universe, span)
    prices = dm.get_panel(universe, "close", span)
    if prices.empty or prices.shape[1] < 3:
        raise ValueError(f"insufficient data for universe '{universe_name}'")
    return universe, prices


def _points(series: pd.Series, digits: int = 4) -> list[dict]:
    return [
        {"time": t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t), "value": round(float(v), digits)}
        for t, v in series.items()
        if v == v  # skip NaN
    ]


def _window(prices: pd.DataFrame) -> tuple[str, str]:
    fmt = lambda t: t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t)
    return fmt(prices.index[0]), fmt(prices.index[-1])


# ── attribution / risk / health helpers (the 3-layer drill-down) ──────────────────────
def _ls_returns(scores: pd.DataFrame, prices: pd.DataFrame, top: float = _DECILE) -> pd.Series:
    """Gross daily long-short decile return series: long the top ``top`` fraction by signed score,
    short the bottom, equal-weight, held one day. No costs — this is the diagnostic (not tradeable)
    view; the costed P&L lives in the Backtest module."""
    fwd1 = prices.pct_change(fill_method=None).shift(-1).reindex(index=scores.index, columns=scores.columns)
    rank = scores.rank(axis=1, pct=True)
    longs = fwd1.where(rank >= 1.0 - top).mean(axis=1)
    shorts = fwd1.where(rank <= top).mean(axis=1)
    return (longs - shorts).dropna()


def _monotonicity(qret: pd.Series) -> float | None:
    """Spearman corr between quantile bucket index and mean forward return (≈1 = healthy monotonic)."""
    if qret is None or len(qret) < 3:
        return None
    buckets = pd.Series(range(len(qret)), index=qret.index, dtype=float)
    v = buckets.corr(qret.astype(float), method="spearman")
    return round(float(v), 3) if v == v else None


def _beta(ls: pd.Series, bench_ret: pd.Series) -> dict:
    """CAPM beta + market correlation + annualized alpha of the LS series vs a benchmark return series."""
    j = pd.concat([ls.rename("ls"), bench_ret.rename("b")], axis=1, sort=False).dropna()
    if len(j) < 20 or float(j["b"].var()) == 0.0:
        return {"beta": None, "market_corr": None, "alpha_annual": None}
    beta = float(j["ls"].cov(j["b"]) / j["b"].var())
    corr = float(j["ls"].corr(j["b"]))
    alpha_annual = float((j["ls"].mean() - beta * j["b"].mean()) * 252 * 100)
    return {"beta": round(beta, 3), "market_corr": round(corr, 3), "alpha_annual": round(alpha_annual, 2)}


def _regime_rows(ic: pd.Series, ls: pd.Series, bench_ret: pd.Series) -> list[dict]:
    """Split mean IC and annualized LS return by volatility (High/Low vs median 21d realized vol)
    and trend (Bull/Bear by 63d trailing benchmark return). Empty if no benchmark."""
    if bench_ret is None or bench_ret.dropna().empty:
        return []
    vol = bench_ret.rolling(21).std()
    trail = bench_ret.rolling(63).sum()  # 63d trailing benchmark return (sum of daily ≈ cumulative)

    def stat(mask: pd.Series, name: str) -> dict:
        dates = mask[mask.fillna(False)].index
        ic_sel = ic.reindex(dates).dropna()
        ls_sel = ls.reindex(dates).dropna()
        ic_m = round(float(ic_sel.mean()), 4) if len(ic_sel) else None
        ls_m = round(float(ls_sel.mean()) * 252 * 100, 2) if len(ls_sel) else None
        return {"regime": name, "ic": ic_m, "ls_return": ls_m, "n": int(len(dates))}

    med = vol.median()
    return [
        stat(vol > med, "High vol"),
        stat(vol <= med, "Low vol"),
        stat(trail >= 0, "Bull"),
        stat(trail < 0, "Bear"),
    ]


def _factor_correlations(
    dm: DataManager, fstore: Any, fprov: Any, universe: Any, prices: pd.DataFrame,
    factor: str, scores: pd.DataFrame, store: Any = None,
) -> list[dict]:
    """Pooled cross-correlation of this factor vs the other default factors (cheap built-ins +
    the user's custom factors), sorted by |corr| descending. Reuses ``heatmap.factor_correlation``."""
    signals: dict[str, pd.DataFrame] = {factor: scores}
    for k in _default_keys(store):
        if k == factor:
            continue
        try:
            signals[k] = _signed_scores(dm, fstore, fprov, store, universe, prices, k)
        except Exception:  # noqa: BLE001 - skip any factor that can't be built
            pass
    if len(signals) < 2:
        return []
    try:
        row = heatmap.factor_correlation(signals, method="spearman").loc[factor].drop(labels=[factor], errors="ignore")
    except Exception:  # noqa: BLE001
        return []
    labels = _labels(store)
    return sorted(
        ({"factor": k, "label": labels.get(k, k), "corr": round(float(v), 3)} for k, v in row.items() if v == v),
        key=lambda r: abs(r["corr"]),
        reverse=True,
    )


def _benchmark_returns(dm: DataManager, prices: pd.DataFrame, symbol: str = "SPY") -> pd.Series | None:
    """Daily returns of the benchmark index aligned to the universe's trading days. None on failure
    (the drill must not hard-fail offline)."""
    try:
        _, bars = fetch_bars(dm, symbol, "equity", prices.index[0].date(), prices.index[-1].date())
        if bars.empty:
            return None
        ret = bars["close"].pct_change()
        ret.index = pd.to_datetime(ret.index)
        return ret.reindex(prices.index)
    except Exception:  # noqa: BLE001
        return None


# ── leaderboard ─────────────────────────────────────────────────────────────────────
def scorecard(
    dm: DataManager, fstore: Any, fprov: Any, universe_name: str,
    factors: list[str] | None = None, horizon: int = 5, q: int = 5, lookback_days: int = 504,
    store: Any = None,
) -> dict:
    """Rank factors over a universe/window by their IC diagnostics. Defaults to the cheap built-in
    trial factors PLUS the user's custom factors; pass ``factors`` to choose an explicit set."""
    universe, prices = _load_prices(dm, universe_name, lookback_days)
    keys = factors or _default_keys(store)
    labels = _labels(store)
    rows, errors = [], []
    for k in keys:
        try:
            scores = _signed_scores(dm, fstore, fprov, store, universe, prices, k)
            summ = ev.ic_summary(ev.information_coefficient(scores, prices, horizon))
            q_spread = ev.spread(ev.quantile_returns(scores, prices, q, horizon))
            autocorr = ev.autocorrelation(scores, 1)
            rows.append({
                "factor": k, "label": labels.get(k, k),
                "mean_ic": round(summ.mean_ic, 4), "ic_ir": round(summ.ic_ir, 3),
                "t_stat": round(summ.t_stat, 2), "hit_rate": round(summ.hit_rate * 100, 1),
                "q_spread": round(q_spread * 100, 3), "autocorr": round(autocorr, 3), "n": summ.n,
            })
        except Exception as e:  # noqa: BLE001 - a factor needing offline fundamentals shouldn't sink the board
            errors.append({"factor": k, "error": f"{type(e).__name__}: {e}"})
    rows.sort(key=lambda r: (r["ic_ir"] if r["ic_ir"] == r["ic_ir"] else -1e9), reverse=True)
    w0, w1 = _window(prices)
    return {
        "universe": universe_name, "horizon": horizon, "q": q,
        "n_instruments": int(prices.shape[1]), "window_start": w0, "window_end": w1,
        "rows": rows, "errors": errors,
    }


# ── single-factor drill-down ──────────────────────────────────────────────────────────
def factor_detail(
    dm: DataManager, fstore: Any, fprov: Any, universe_name: str, factor: str,
    horizon: int = 5, q: int = 5, lookback_days: int = 504, roll_window: int = 63,
    store: Any = None,
) -> dict:
    universe, prices = _load_prices(dm, universe_name, lookback_days)
    scores = _signed_scores(dm, fstore, fprov, store, universe, prices, factor)

    ic = ev.information_coefficient(scores, prices, horizon)
    summ = ev.ic_summary(ic)
    roll = ic.rolling(roll_window, min_periods=max(5, roll_window // 3)).mean()
    decay = ev.ic_decay(scores, prices, _DECAY_HORIZONS)
    qret = ev.quantile_returns(scores, prices, q, horizon)

    # Turnover proxy over time: 1 - per-date cross-sectional rank autocorrelation, monthly.
    r = scores.rank(axis=1)
    rank_ac = r.corrwith(r.shift(1), axis=1)
    turnover_m = (1.0 - rank_ac).clip(lower=0).resample("ME").mean()

    # ── 3-layer diagnostics: Returns / Risk / Health ──
    ls = _ls_returns(scores, prices)
    ls_equity = (1.0 + ls).cumprod()
    ls_dd = (ls_equity / ls_equity.cummax() - 1.0) * 100 if len(ls_equity) else ls_equity
    ls_total = float((ls_equity.iloc[-1] - 1.0) * 100) if len(ls_equity) else 0.0
    ls_sharpe = float(ls.mean() / ls.std() * (252 ** 0.5)) if len(ls) and ls.std() else 0.0
    bench_ret = _benchmark_returns(dm, prices)

    w0, w1 = _window(prices)
    return {
        "universe": universe_name, "factor": factor, "label": _labels(store).get(factor, factor),
        "horizon": horizon, "q": q, "n_instruments": int(prices.shape[1]),
        "window_start": w0, "window_end": w1, "roll_window": roll_window,
        "metrics": {
            "mean_ic": round(summ.mean_ic, 4), "ic_ir": round(summ.ic_ir, 3),
            "t_stat": round(summ.t_stat, 2), "hit_rate": round(summ.hit_rate * 100, 1),
            "autocorr": round(ev.autocorrelation(scores, 1), 3), "n": summ.n,
        },
        "ic_series": _points(roll, 4),
        "ic_decay": [{"horizon": int(h), "value": round(float(v), 4)} for h, v in decay.items() if v == v],
        "quantile_returns": [{"bucket": int(b), "value": round(float(rv) * 100, 3)} for b, rv in qret.items() if rv == rv],
        "turnover_series": _points(turnover_m * 100, 2),
        # Returns layer — is it generating alpha?
        "returns": {
            "ls_curve": _points((ls_equity - 1.0) * 100, 2),
            "ls_drawdown": _points(ls_dd, 2),
            "ls_total_return": round(ls_total, 2),
            "ls_sharpe": round(ls_sharpe, 2),
            "quantile_monotonicity": _monotonicity(qret),
        },
        # Risk layer — what unintended risk does it carry?
        "risk": {
            "factor_correlations": _factor_correlations(dm, fstore, fprov, universe, prices, factor, scores, store),
            **_beta(ls, bench_ret if bench_ret is not None else pd.Series(dtype=float)),
            "benchmark": "SPY",
        },
        # Health layer — is the edge stable or decaying / regime-dependent?
        "health": {
            "regimes": _regime_rows(ic, ls, bench_ret),
        },
    }


# ── factor correlation heatmap ────────────────────────────────────────────────────────
def correlation_matrix(
    dm: DataManager, fstore: Any, fprov: Any, universe_name: str,
    factors: list[str] | None = None, lookback_days: int = 504, store: Any = None,
) -> dict:
    """Pooled (date × instrument) Spearman cross-correlation among factors — the redundancy /
    diversification view. Defaults to the cheap built-in trial factors + the user's custom factors.
    Reuses ``heatmap.factor_correlation``."""
    universe, prices = _load_prices(dm, universe_name, lookback_days)
    keys = factors or _default_keys(store)
    labels = _labels(store)
    signals: dict[str, pd.DataFrame] = {}
    errors: list[dict] = []
    for k in keys:
        try:
            sig = _signed_scores(dm, fstore, fprov, store, universe, prices, k)
            # An all-NaN signal (e.g. alpha factors when the store has no OHLCV panels) would
            # empty the pooled dropna() and NaN the whole matrix — skip it like a build failure.
            if not sig.notna().any().any():
                errors.append({"factor": k, "error": "empty signal: no data for its inputs"})
                continue
            signals[k] = sig
        except Exception as e:  # noqa: BLE001 - a factor that can't be built is skipped, not fatal
            errors.append({"factor": k, "error": f"{type(e).__name__}: {e}"})
    if len(signals) < 2:
        raise ValueError("need at least two factors for a correlation heatmap")
    cmat = heatmap.factor_correlation(signals, method="spearman")
    order = list(cmat.columns)
    matrix = [
        [None if cmat.iat[i, j] != cmat.iat[i, j] else round(float(cmat.iat[i, j]), 3) for j in range(len(order))]
        for i in range(len(order))
    ]
    w0, w1 = _window(prices)
    return {
        "universe": universe_name, "method": "spearman",
        "n_instruments": int(prices.shape[1]), "window_start": w0, "window_end": w1,
        "factors": order, "labels": [labels.get(k, k) for k in order],
        "matrix": matrix, "errors": errors,
    }


# ── persisted monitor sets ──────────────────────────────────────────────────────────
def list_monitors(store: Any) -> dict:
    return {"monitors": store.list_factor_monitors()}


def save_monitor(store: Any, name: str, record: dict) -> None:
    store.save_factor_monitor(name, {
        "name": name,
        "universe": record.get("universe", "dow30"),
        "factors": record.get("factors") or [],
        "horizon": int(record.get("horizon", 5) or 5),
        "q": int(record.get("q", 5) or 5),
        "lookback_days": int(record.get("lookback_days", 504) or 504),
        "notes": record.get("notes", ""),
    })


def remove_monitor(store: Any, name: str) -> None:
    store.remove_factor_monitor(name)


def run_monitor(dm: DataManager, fstore: Any, fprov: Any, store: Any, name: str) -> dict:
    rec = next((m for m in store.list_factor_monitors() if m["name"] == name), None)
    if rec is None:
        raise ValueError(f"unknown monitor '{name}'")
    board = scorecard(
        dm, fstore, fprov, rec["universe"], rec.get("factors") or None,
        rec.get("horizon", 5), rec.get("q", 5), rec.get("lookback_days", 504), store=store,
    )
    snap_id = store.add_monitor_snapshot(name, board)
    return {**board, "monitor": name, "snapshot_id": snap_id}


def monitor_history(store: Any, name: str) -> dict:
    """Per-factor time series of mean_ic / ic_ir across snapshots (oldest → newest)."""
    snaps = store.list_monitor_snapshots(name)  # newest first
    factors: dict[str, dict] = {}
    for snap in reversed(snaps):
        ts = snap["ts"]
        for row in snap["data"].get("rows", []):
            f = row["factor"]
            entry = factors.setdefault(f, {"label": row.get("label", f), "mean_ic": [], "ic_ir": []})
            entry["mean_ic"].append({"time": ts, "value": row["mean_ic"]})
            entry["ic_ir"].append({"time": ts, "value": row["ic_ir"]})
    return {"monitor": name, "n_snapshots": len(snaps), "factors": factors}
