"""Factor evaluation — does a raw factor actually predict forward returns?

These diagnostics are computed *before* a factor becomes a strategy, and feed the
research workflow (a weak IC / fast-decaying factor should never reach a backtest). All
metrics align a factor score at date *t* with the return realized *after* *t*, so there is
no look-ahead.

Conventions: factor and prices are wide (dates × instrument_id) panels sharing columns.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from qhfi.core.types import Panel


def forward_returns(prices: Panel, horizon: int = 1) -> Panel:
    """Return realized over the next ``horizon`` days, aligned to the *decision* date t:
    fwd_t = price_{t+horizon} / price_t - 1."""
    return prices.shift(-horizon) / prices - 1.0


def information_coefficient(
    factor: Panel, prices: Panel, horizon: int = 1, method: str = "spearman"
) -> pd.Series:
    """Daily cross-sectional IC: correlation between the factor row at t and forward returns.

    ``spearman`` (rank IC, the default) is robust to outliers and monotonic-but-nonlinear
    relationships; ``pearson`` measures linear strength. Returns a per-date IC series.
    """
    fwd = forward_returns(prices, horizon)
    f = factor.reindex_like(fwd)
    if method == "spearman":
        f = f.rank(axis=1)
        fwd = fwd.rank(axis=1)
    elif method != "pearson":
        raise ValueError(f"method must be 'spearman' or 'pearson', got {method!r}")
    return f.corrwith(fwd, axis=1)


@dataclass
class ICSummary:
    mean_ic: float
    ic_std: float
    ic_ir: float          # information ratio = mean / std (risk-adjusted predictiveness)
    t_stat: float         # significance of mean IC ≠ 0
    hit_rate: float       # fraction of days with same-sign IC
    n: int


def ic_summary(ic: pd.Series) -> ICSummary:
    ic = ic.dropna()
    n = len(ic)
    if n == 0:
        return ICSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)
    mean, std = float(ic.mean()), float(ic.std(ddof=0))
    ir = mean / std if std else 0.0
    t = ir * np.sqrt(n)
    hit = float((np.sign(ic) == np.sign(mean)).mean()) if mean != 0 else 0.0
    return ICSummary(mean, std, ir, float(t), hit, n)


def quantile_returns(factor: Panel, prices: Panel, q: int = 5, horizon: int = 1) -> pd.Series:
    """Mean forward return per factor quantile bucket (1 = lowest score … q = highest).

    A monotonic increase across buckets — and a positive top-minus-bottom spread — is the
    classic evidence that a factor is tradable. Returns a Series indexed by bucket.
    """
    fwd = forward_returns(prices, horizon)
    f = factor.reindex_like(fwd)

    buckets: dict[int, list[float]] = {b: [] for b in range(1, q + 1)}
    for date, row in f.iterrows():
        row = row.dropna()
        if len(row) < q:
            continue
        labels = pd.qcut(row.rank(method="first"), q, labels=range(1, q + 1))
        fr = fwd.loc[date]
        for b in range(1, q + 1):
            members = labels.index[labels == b]
            vals = fr[members].dropna()
            if len(vals):
                buckets[b].append(float(vals.mean()))
    return pd.Series({b: float(np.mean(v)) if v else np.nan for b, v in buckets.items()})


def spread(qret: pd.Series) -> float:
    """Top-minus-bottom quantile mean-return spread (the long/short factor return per period)."""
    return float(qret.iloc[-1] - qret.iloc[0])


def ic_decay(factor: Panel, prices: Panel, horizons: tuple[int, ...] = (1, 2, 3, 5, 10, 21)) -> pd.Series:
    """Mean IC as the forward horizon lengthens — how fast the signal's edge decays.

    A slow decay means a longer holding period is viable (lower turnover); a sharp drop to
    zero implies the edge is short-lived. Returns a Series indexed by horizon.
    """
    return pd.Series(
        {h: ic_summary(information_coefficient(factor, prices, horizon=h)).mean_ic for h in horizons}
    )


def autocorrelation(factor: Panel, lag: int = 1) -> float:
    """Mean cross-sectional rank autocorrelation of the factor at the given lag — a proxy
    for turnover (high autocorr → stable ranks → low turnover)."""
    r = factor.rank(axis=1)
    return float(r.corrwith(r.shift(lag), axis=1).mean())
