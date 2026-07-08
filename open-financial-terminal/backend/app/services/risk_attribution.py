"""Risk attribution service — Barra factor decomposition of a tracked portfolio.

Fits qhfi's :class:`BarraRiskModel` on a broad equity cross-section (a base universe ∪ the
portfolio's own names, so nothing is dropped) and attributes the book's forecast risk to factors
and to positions (Euler / component contribution). Equity-only: the cross-sectional factor model
has no crypto factors, so crypto positions are returned under ``skipped`` rather than attributed.

The fit (WLS per date + EWMA covariance, ~0.5-2s) depends only on universe + window + as-of day,
so it is memoized; the per-weights attribution on top of a fitted model is cheap. The pure
attribution math lives in :func:`_attribute` so it can be unit-tested on a synthetic model without
touching the data lake.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import pandas as pd
from qhfi.barra.model import BarraRiskModel
from qhfi.core.types import AssetClass, DateRange, EquityMeta, Instrument, Universe
from qhfi.factors.market import MarketPanels

from app.deps import get_data_manager, make_instrument
from app.services import fundamentals as fa
from app.services.market import fetch_bars
from app.services.universe import get_universe, list_universes

_DEFAULT_BASE = "equity_sectors"
_DEFAULT_WINDOW = 504          # ~2y of trading days; EWMA halflives are 252/126
_MIN_CROSS_SECTION = 10        # BarraRiskModel.min_names — needs a broad cross-section to fit
_MIN_HISTORY_ROWS = 400        # coverage floor (~315: beta-252 → resid_vol-63) + buffer
_BACKFILL_YEARS = 5            # how much history to force-fetch for a thin name

#: Held names we've already tried to auto-deepen this process — so a genuinely-short name (recent
#: IPO that can't reach the floor) isn't re-fetched + re-fit on every request.
_BACKFILL_ATTEMPTED: set[str] = set()


def _f(x: object) -> float:
    return round(float(x), 6)


def _pct(x: object) -> float:
    """Annualized-vol fraction → percent, matching services.portfolio."""
    return round(float(x) * 100, 2)


# ── universe construction ─────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _sector_index() -> dict[str, str]:
    """id → GICS sector across every config universe (cheap, cached) for extra-name lookup."""
    out: dict[str, str] = {}
    for uname in list_universes():
        try:
            uni = get_universe(uname)
        except Exception:  # noqa: BLE001 - a malformed yaml shouldn't break attribution
            continue
        for ins in uni.instruments:
            if ins.sector and ins.id not in out:
                out[ins.id] = ins.sector
    return out


def _sector_for(symbol: str) -> str | None:
    """GICS sector for a portfolio name: config universes first, then a live yfinance fallback."""
    s = _sector_index().get(symbol.upper())
    if s:
        return s
    try:
        return fa.snapshot(symbol).get("sector")
    except Exception:  # noqa: BLE001 - sector is best-effort; falls into the '__none__' bucket
        return None


def _superset_universe(base_universe: str, names: tuple[str, ...]) -> Universe:
    """Base pool ∪ the portfolio's equity names, so a held name outside the pool is still modeled."""
    base = get_universe(base_universe)
    have = set(base.ids)
    extra: list[Instrument] = []
    for sym in names:
        u = sym.upper()
        if u in have:
            continue
        have.add(u)
        extra.append(
            Instrument(id=u, asset_class=AssetClass.EQUITY, equity=EquityMeta(gics_sector=_sector_for(u)))
        )
    if not extra:
        return base
    return Universe(name=f"{base.name}+pf", instruments=list(base.instruments) + extra)


def _trim(panels: MarketPanels, window_days: int) -> MarketPanels:
    """Keep the trailing ``window_days`` rows so the fit is recent (and fast) without losing the
    rolling-exposure warmup that the early rows still provide."""
    if window_days and len(panels.close) > window_days:
        sl = slice(-window_days, None)
        return MarketPanels(
            open=panels.open.iloc[sl], high=panels.high.iloc[sl], low=panels.low.iloc[sl],
            close=panels.close.iloc[sl], volume=panels.volume.iloc[sl],
        )
    return panels


def _safe_panels(dm, universe: Universe) -> tuple[MarketPanels, Universe]:
    """Build panels, tolerating a corrupt/partial parquet: a single bad file shouldn't sink the
    whole cross-section. The bulk read is the fast path; only on failure do we probe name-by-name
    and drop the unreadable ones (returning the surviving universe so dummies/fit stay aligned)."""
    try:
        return MarketPanels.from_store(dm.store, universe), universe
    except Exception:  # noqa: BLE001 - fall back to a tolerant per-name load
        good = []
        for ins in universe.instruments:
            try:
                MarketPanels.from_store(dm.store, Universe(name="_probe", instruments=[ins]))
                good.append(ins)
            except Exception:  # noqa: BLE001 - skip the unreadable name
                continue
        if not good:
            raise
        surviving = Universe(name=universe.name, instruments=good)
        return MarketPanels.from_store(dm.store, surviving), surviving


@lru_cache(maxsize=8)
def _fit_cached(base_universe: str, extra_key: tuple[str, ...], window_days: int, as_of: str) -> BarraRiskModel:
    """Memoized fit — keyed by universe + portfolio names + window + day; reused across weights."""
    dm = get_data_manager()
    panels, universe = _safe_panels(dm, _superset_universe(base_universe, extra_key))
    return BarraRiskModel.from_panels(_trim(panels, window_days), universe)


class _Insufficient(Exception):
    """Raised when a book can't be attributed; carries the JSON payload to return."""

    def __init__(self, reason: str, skipped: list[dict]) -> None:
        super().__init__(reason)
        self.payload = {"insufficient": True, "reason": reason, "skipped": skipped, "n": 0}


def _ensure_history(dm, names: list[str]) -> list[str]:
    """Force-deepen any of ``names`` whose lake history is too thin/corrupt for the Barra exposure
    chain (≥~315 trading days). Attempted at most once per name per process (so a genuinely-short
    name doesn't re-fetch every request). Returns the names actually deepened to the floor."""
    span = DateRange(start=date.today() - timedelta(days=365 * _BACKFILL_YEARS), end=date.today())
    deepened: list[str] = []
    for sym in names:
        if sym in _BACKFILL_ATTEMPTED:
            continue
        _BACKFILL_ATTEMPTED.add(sym)
        ins = make_instrument(sym, "equity")
        try:
            dm.update(Universe(name=f"_bf_{sym}", instruments=[ins]), span, force=True)
            cov = dm.coverage(ins)
            if cov and cov[2] >= _MIN_HISTORY_ROWS:
                deepened.append(sym)
        except Exception:  # noqa: BLE001 - provider hiccup / unfixable short name → leave it skipped
            continue
    return deepened


def _fit_book(
    positions: list[dict], base_universe: str, window_days: int
) -> tuple[BarraRiskModel, pd.Series, list[dict], str]:
    """Resolve positions → gross-normalized equity weights, fit (cached) the Barra model, and return
    the covered-weight Series. Shared by the risk and realized-return attributions. Raises
    :class:`_Insufficient` (with a ready payload) when the book can't be attributed."""
    dm = get_data_manager()
    skipped: list[dict] = []

    qty: dict[str, float] = {}
    for p in positions:
        sym = p["symbol"].upper()
        if (p.get("asset") or "equity").lower() != "equity":
            skipped.append({"symbol": sym, "reason": "non-equity (no factor model)"})
            continue
        qty[sym] = qty.get(sym, 0.0) + float(p["quantity"])

    last_close: dict[str, float] = {}
    for s in list(qty):
        try:
            _, bars = fetch_bars(dm, s, "equity")
        except Exception:  # noqa: BLE001 - one bad symbol shouldn't sink the book
            bars = None
        if bars is not None and not bars.empty:
            last_close[s] = float(bars["close"].iloc[-1])
        else:
            skipped.append({"symbol": s, "reason": "no price data"})
            qty.pop(s)

    values = {s: qty[s] * last_close[s] for s in qty}
    gross = sum(abs(v) for v in values.values())
    if not values or gross == 0:
        raise _Insufficient("no equity positions to attribute", skipped)

    weights = {s: values[s] / gross for s in values}      # signed; sum|w| = 1
    as_of = str(date.today())
    names_key = tuple(sorted(weights))

    def _fit() -> BarraRiskModel:
        try:
            return _fit_cached(base_universe, names_key, int(window_days), as_of)
        except Exception as e:  # noqa: BLE001 - thin cross-section / missing lake data → graceful
            raise _Insufficient(f"could not fit risk model: {e}", skipped) from e

    model = _fit()
    covered = [s for s in weights if s in model.exposures_.index]
    thin = [s for s in weights if s not in covered]
    # Auto-deepen: a held name dropped here is almost always thin lake history — force-fetch it and
    # refit once (cache busted) so coverage self-heals instead of needing a manual backfill.
    if thin and _ensure_history(dm, thin):
        _fit_cached.cache_clear()
        model = _fit()
        covered = [s for s in weights if s in model.exposures_.index]

    for s in (s for s in weights if s not in covered):
        skipped.append({"symbol": s, "reason": "no model coverage"})
    if not covered:
        raise _Insufficient("none of the holdings are covered by the risk model", skipped)

    return model, pd.Series({s: weights[s] for s in covered}), skipped, as_of


# ── risk attribution ───────────────────────────────────────────────────────────────
def _attribute(
    model: BarraRiskModel,
    weights: pd.Series,
    *,
    source: str,
    as_of: str,
    skipped: list[dict],
    base_universe: str,
    window_days: int,
) -> dict:
    """Pure decomposition of a fitted model + weight vector → JSON-safe dict (testable offline)."""
    rd = model.risk_decomposition(weights)
    rc = model.risk_contributions(weights)
    frc = model.factor_risk_contributions(weights)

    style = set(getattr(model, "style_names_", []))
    factors = [
        {
            "factor": name,
            "kind": "style" if name in style else "industry",
            "exposure": _f(row["exposure"]),
            "var_contribution": _f(row["var_contribution"]),
            "pct_total": _f(row["pct_total"]),
        }
        for name, row in frc.iterrows()
    ]
    factors.sort(key=lambda d: abs(d["pct_total"]), reverse=True)

    positions = [
        {
            "symbol": str(sym),
            "weight": _f(row["weight"]),
            "mctr": _pct(row["mctr"]),
            "cctr": _pct(row["cctr"]),
            "pct": _f(row["pct"]),
        }
        for sym, row in rc.iterrows()
    ]
    positions.sort(key=lambda d: abs(d["pct"]), reverse=True)

    return {
        "as_of": as_of,
        "source": source,
        "base_universe": base_universe,
        "window_days": window_days,
        "n": int(len(positions)),
        "skipped": skipped,
        "total_vol": _pct(rd["total_vol"]),
        "factor_vol": _pct(rd["factor_vol"]),
        "specific_vol": _pct(rd["specific_vol"]),
        "pct_factor": _f(rd["pct_factor"]),
        "factors": factors,
        "positions": positions,
    }


def compute_attribution(
    positions: list[dict],
    *,
    source: str = "holdings",
    base_universe: str = _DEFAULT_BASE,
    window_days: int = _DEFAULT_WINDOW,
) -> dict:
    """Attribute a book's forecast risk to Barra factors + positions.

    ``positions``: ``[{symbol, asset, quantity}]``. Weights come from market value (qty × last
    close), gross-normalized (shorts negative); only the equity names with price + model coverage
    are attributed, the rest are listed under ``skipped`` with a reason.
    """
    try:
        model, w, skipped, as_of = _fit_book(positions, base_universe, int(window_days))
    except _Insufficient as e:
        return {**e.payload, "source": source}
    return _attribute(model, w, source=source, as_of=as_of, skipped=skipped,
                      base_universe=base_universe, window_days=int(window_days))


# ── realized return attribution ─────────────────────────────────────────────────────
def _attribute_returns(
    model: BarraRiskModel,
    weights: pd.Series,
    *,
    source: str,
    as_of: str,
    skipped: list[dict],
    base_universe: str,
    window_days: int,
    max_points: int = 180,
) -> dict:
    """Decompose the book's *realized* return over the fit window into per-factor P&L + specific.

    Uses the model's stored exposure history: each day ``r_p = (Xᵀw)·f + w·u`` holds exactly, so the
    per-date columns (factor returns × exposure, plus specific) sum to the book's return. We cumulate
    (additive) over the window and return per-factor totals + a downsampled cumulative series.
    """
    attr = model.return_attribution(weights)            # T × (factors + 'specific' + 'total')
    cum = attr.cumsum()
    last = cum.iloc[-1]
    factor_names = list(model.factor_names_)
    style = set(getattr(model, "style_names_", []))

    contributions = [
        {"factor": name, "kind": "style" if name in style else "industry",
         "contribution": _pct(last[name])}
        for name in factor_names
    ]
    contributions.sort(key=lambda d: abs(d["contribution"]), reverse=True)

    idx = cum.index
    step = max(1, len(idx) // max_points)
    pts = list(range(0, len(idx), step))
    if pts and pts[-1] != len(idx) - 1:
        pts.append(len(idx) - 1)
    factor_cum = cum[factor_names].sum(axis=1)
    series = {
        "times": [str(idx[p].date()) for p in pts],
        "total": [_pct(cum["total"].iloc[p]) for p in pts],
        "factor": [_pct(factor_cum.iloc[p]) for p in pts],
        "specific": [_pct(cum["specific"].iloc[p]) for p in pts],
    }

    return {
        "as_of": as_of,
        "source": source,
        "base_universe": base_universe,
        "window_days": window_days,
        "n": int(len(weights)),
        "skipped": skipped,
        "total_return": _pct(last["total"]),
        "factor_return": _pct(float(last[factor_names].sum())),
        "specific_return": _pct(last["specific"]),
        "contributions": contributions,
        "series": series,
    }


def compute_return_attribution(
    positions: list[dict],
    *,
    source: str = "holdings",
    base_universe: str = _DEFAULT_BASE,
    window_days: int = _DEFAULT_WINDOW,
) -> dict:
    """Attribute the book's realized return over the fit window to factor bets + stock selection.

    Holds today's weights fixed back over the window (the standard 'what did my current tilts earn'
    view), decomposing realized P&L into each factor's contribution plus a specific (selection) term.
    """
    try:
        model, w, skipped, as_of = _fit_book(positions, base_universe, int(window_days))
    except _Insufficient as e:
        return {**e.payload, "source": source}
    return _attribute_returns(model, w, source=source, as_of=as_of, skipped=skipped,
                              base_universe=base_universe, window_days=int(window_days))


# ── Brinson sector attribution ──────────────────────────────────────────────────────
def _window_returns(model: BarraRiskModel) -> pd.Series:
    """Per-name cumulative realized return over the fit window, reconstructed from the model
    (``Σ_t Xₜ·fₜ + uₜ``) — so a window-level (not single-day) Brinson has meaningful magnitudes."""
    cum = pd.Series(0.0, index=list(model.exposures_.index))
    factor_names = list(model.factor_names_)
    for t, x in (model.exposures_history_ or {}).items():
        f = model.factor_returns_.loc[t].reindex(factor_names).fillna(0.0).to_numpy()
        u = model.specific_returns_.loc[t].reindex(x.index).fillna(0.0)
        r_t = pd.Series(x.to_numpy() @ f, index=x.index) + u
        cum = cum.add(r_t.reindex(cum.index).fillna(0.0), fill_value=0.0)
    return cum


def _attribute_brinson(
    model: BarraRiskModel,
    weights: pd.Series,
    *,
    source: str,
    as_of: str,
    skipped: list[dict],
    base_universe: str,
    window_days: int,
) -> dict:
    """Brinson–Fachler active-return attribution by GICS sector vs an equal-weight benchmark of the
    model universe (window-cumulative returns). allocation + selection + interaction = active return."""
    universe = list(model.exposures_.index)
    bench = pd.Series(1.0 / len(universe), index=universe)     # equal-weight the model pool
    bdf = model.brinson_attribution(weights, bench, asset_returns=_window_returns(model))

    sectors = [
        {
            "sector": str(sec),
            "w_port": _pct(row["w_port"]),
            "w_bench": _pct(row["w_bench"]),
            "r_port": _pct(row["r_port"]),
            "r_bench": _pct(row["r_bench"]),
            "allocation": _pct(row["allocation"]),
            "selection": _pct(row["selection"]),
            "interaction": _pct(row["interaction"]),
            "total": _pct(row["total"]),
        }
        for sec, row in bdf.iterrows()
    ]
    sectors.sort(key=lambda d: abs(d["total"]), reverse=True)

    return {
        "as_of": as_of,
        "source": source,
        "base_universe": base_universe,
        "window_days": window_days,
        "benchmark": f"{base_universe} (equal-weight)",
        "n": int(len(weights)),
        "skipped": skipped,
        "active_return": _pct(float(bdf["total"].sum())),
        "allocation": _pct(float(bdf["allocation"].sum())),
        "selection": _pct(float(bdf["selection"].sum())),
        "interaction": _pct(float(bdf["interaction"].sum())),
        "sectors": sectors,
    }


def compute_brinson_attribution(
    positions: list[dict],
    *,
    source: str = "holdings",
    base_universe: str = _DEFAULT_BASE,
    window_days: int = _DEFAULT_WINDOW,
) -> dict:
    """Brinson sector attribution of the book's active return vs an equal-weight benchmark of the
    fit universe, over the window. Single-period decomposition (no multi-period return linking yet)."""
    try:
        model, w, skipped, as_of = _fit_book(positions, base_universe, int(window_days))
    except _Insufficient as e:
        return {**e.payload, "source": source}
    return _attribute_brinson(model, w, source=source, as_of=as_of, skipped=skipped,
                              base_universe=base_universe, window_days=int(window_days))
