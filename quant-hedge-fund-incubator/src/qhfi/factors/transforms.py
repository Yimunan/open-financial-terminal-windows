"""Cross-sectional factor transforms.

Every function operates **row-wise** on a wide (dates × instrument_id) panel: each date is
standardized/neutralized independently across the instruments available that day. This is
the standard hygiene applied to a raw factor before it becomes a tradable signal.

All functions are pure, look-ahead-free (no information crosses dates), and NaN-tolerant
(instruments missing on a given day are ignored in that row's statistics).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import Panel


def winsorize(panel: Panel, lower: float = 0.01, upper: float = 0.99) -> Panel:
    """Clip each row to its [lower, upper] cross-sectional quantiles to tame outliers."""
    lo = panel.quantile(lower, axis=1)
    hi = panel.quantile(upper, axis=1)
    return panel.clip(lower=lo, upper=hi, axis=0)


def zscore(panel: Panel) -> Panel:
    """Cross-sectional z-score per row: (x - mean) / std across instruments that day."""
    mean = panel.mean(axis=1)
    std = panel.std(axis=1, ddof=0).replace(0.0, np.nan)
    return panel.sub(mean, axis=0).div(std, axis=0)


def rank(panel: Panel, normalize: bool = True) -> Panel:
    """Cross-sectional rank per row. If ``normalize``, map to roughly [-0.5, 0.5] so the
    output is scale-free and centered (robust alternative to z-score)."""
    r = panel.rank(axis=1)
    if not normalize:
        return r
    counts = panel.notna().sum(axis=1).replace(0, np.nan)
    return r.sub(1, axis=0).div(counts - 1, axis=0) - 0.5


def neutralize(panel: Panel, groups: dict[str, str]) -> Panel:
    """Group-neutralize (e.g. sector): subtract each row's per-group mean from its members,
    so the factor expresses only *within-group* differences.

    ``groups`` maps instrument_id → group label. Instruments absent from the map keep their
    raw (row-demeaned only within their own missing-group bucket).
    """
    grp = pd.Series({c: groups.get(c, "__none__") for c in panel.columns})
    out = panel.copy()
    for label in grp.unique():
        cols = grp.index[grp == label]
        block = panel[cols]
        out[cols] = block.sub(block.mean(axis=1), axis=0)
    return out


def beta_neutralize(panel: Panel, market_returns: pd.Series, betas: Panel) -> Panel:
    """Remove the market-beta component from a factor row-by-row.

    Subtracts the cross-sectional OLS fit of the factor on ``betas`` each day so the residual
    is beta-neutral. ``betas`` is a (dates × instrument) panel of rolling betas.
    """
    raise NotImplementedError("TODO: per-row OLS of factor on beta, return residual")


def combine(factors: dict[str, Panel], weights: dict[str, float] | None = None) -> Panel:
    """Blend several already-standardized factor panels into one composite score.

    Equal-weight by default; otherwise a weighted sum (weights need not sum to 1 — the
    composite is typically re-standardized downstream).
    """
    if not factors:
        raise ValueError("no factors to combine")
    w = weights or {k: 1.0 for k in factors}
    aligned = [panel * w[name] for name, panel in factors.items()]
    total = aligned[0].copy()
    for extra in aligned[1:]:
        total = total.add(extra, fill_value=0.0)
    return total
