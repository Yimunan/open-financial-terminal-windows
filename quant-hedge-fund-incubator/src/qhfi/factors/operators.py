"""Alpha101 operator vocabulary — the building blocks of formulaic alphas.

All operate on **wide panels** (index = dates, columns = instruments). Two families:

* **cross-sectional** (row-wise, ``axis=1``): ``rank``, ``scale``, ``signedpower`` — compare
  instruments against each other on a given day.
* **time-series** (column-wise rolling): ``delay``, ``delta``, ``ts_sum``, ``ts_std``,
  ``ts_min/max``, ``ts_argmax/argmin``, ``ts_rank``, ``product``, ``decay_linear``,
  ``correlation``, ``covariance`` — look back over ``d`` days per instrument.

Reference: Kakushadze (2015), "101 Formulaic Alphas". Sector neutralization (the paper's
``IndNeutralize``) is provided by ``factors.transforms.neutralize`` + ``Universe.groups``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.core.types import Panel

# ── cross-sectional ────────────────────────────────────────────────────────────
def rank(df: Panel) -> Panel:
    """Cross-sectional percentile rank per row, in (0, 1]."""
    return df.rank(axis=1, pct=True)


def scale(df: Panel, a: float = 1.0) -> Panel:
    """Rescale each row so the sum of absolute values is ``a``."""
    return df.div(df.abs().sum(axis=1), axis=0) * a


def signedpower(df: Panel, a: float) -> Panel:
    return np.sign(df) * df.abs() ** a


# ── element-wise helpers ───────────────────────────────────────────────────────
def delay(df: Panel, d: int) -> Panel:
    return df.shift(d)


def delta(df: Panel, d: int) -> Panel:
    return df - df.shift(d)


# ── time-series (rolling) ──────────────────────────────────────────────────────
def ts_sum(df: Panel, d: int) -> Panel:
    return df.rolling(d).sum()


def ts_mean(df: Panel, d: int) -> Panel:
    return df.rolling(d).mean()


def ts_std(df: Panel, d: int) -> Panel:
    return df.rolling(d).std(ddof=0)


def ts_min(df: Panel, d: int) -> Panel:
    return df.rolling(d).min()


def ts_max(df: Panel, d: int) -> Panel:
    return df.rolling(d).max()


def ts_argmax(df: Panel, d: int) -> Panel:
    """Position (0..d-1) of the max within the trailing window."""
    return df.rolling(d).apply(np.argmax, raw=True)


def ts_argmin(df: Panel, d: int) -> Panel:
    return df.rolling(d).apply(np.argmin, raw=True)


def ts_rank(df: Panel, d: int) -> Panel:
    """Percentile rank (in (0,1]) of the latest value within the trailing window."""
    def _last_rank(a: np.ndarray) -> float:
        return (a.argsort().argsort()[-1] + 1) / len(a)
    return df.rolling(d).apply(_last_rank, raw=True)


def product(df: Panel, d: int) -> Panel:
    return df.rolling(d).apply(np.prod, raw=True)


def decay_linear(df: Panel, d: int) -> Panel:
    """Linearly-decaying weighted moving average (weights d, d-1, … 1, normalized)."""
    w = np.arange(1, d + 1, dtype=float)
    w /= w.sum()
    return df.rolling(d).apply(lambda a: float(np.dot(a, w)), raw=True)


def correlation(x: Panel, y: Panel, d: int) -> Panel:
    """Element-wise (per-instrument) rolling Pearson correlation over ``d`` days."""
    mx, my = x.rolling(d).mean(), y.rolling(d).mean()
    cov = (x * y).rolling(d).mean() - mx * my
    sx, sy = x.rolling(d).std(ddof=0), y.rolling(d).std(ddof=0)
    return (cov / (sx * sy)).replace([np.inf, -np.inf], np.nan)


def covariance(x: Panel, y: Panel, d: int) -> Panel:
    mx, my = x.rolling(d).mean(), y.rolling(d).mean()
    return (x * y).rolling(d).mean() - mx * my
