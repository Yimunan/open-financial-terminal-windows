"""Yield-curve container and basic term-structure analytics.

The curve is a wide ``(dates × tenor)`` panel of par yields in **percent** (the shape
``data.rates.RatesStore`` / FRED produce). Everything here is tenor-count agnostic — it works
with whatever tenors are present (the 4-tenor yfinance fallback or the full 11-tenor FRED
curve) and orders columns by maturity so slope/curvature are well defined.

All functions are pure and look-ahead-free: a value at date *t* uses only the curve at *t*
(carry/roll-down explicitly shift forward returns onto the decision date, like
``factors.evaluation.forward_returns``).
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from qhfi.data.lake import lake_root
from qhfi.data.rates import RatesStore

# Common Treasury constant-maturity tenor labels → year fractions.
TENOR_YEARS: dict[str, float] = {
    "1M": 1 / 12, "2M": 2 / 12, "3M": 0.25, "4M": 4 / 12, "6M": 0.5,
    "1Y": 1.0, "2Y": 2.0, "3Y": 3.0, "5Y": 5.0, "7Y": 7.0,
    "10Y": 10.0, "20Y": 20.0, "30Y": 30.0,
}

_TENOR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([MY])\s*$", re.IGNORECASE)


def tenor_years(label: str) -> float:
    """Map a tenor label (e.g. '3M', '5Y') to its maturity in years."""
    if label in TENOR_YEARS:
        return TENOR_YEARS[label]
    m = _TENOR_RE.match(label)
    if not m:
        raise ValueError(f"unrecognized tenor label {label!r}")
    n, unit = float(m.group(1)), m.group(2).upper()
    return n / 12.0 if unit == "M" else n


def order_tenors(curve: pd.DataFrame) -> pd.DataFrame:
    """Return the curve with columns sorted by ascending maturity."""
    cols = sorted(curve.columns, key=tenor_years)
    return curve[cols]


def load_treasury_curve(store: RatesStore | None = None, ffill: bool = True) -> pd.DataFrame:
    """Load the Treasury curve from the rates lake, maturity-ordered and (optionally) forward
    filled across the union of trading days so every tenor is defined on every row."""
    store = store or RatesStore(lake_root())
    curve = order_tenors(store.load("treasury_curve").sort_index())
    curve.columns.name = "tenor"
    return curve.ffill() if ffill else curve


def curve_metrics(curve: pd.DataFrame) -> pd.DataFrame:
    """Per-date level / slope / curvature of the curve (in percent / percentage points).

    * **level**     — mean yield across tenors (parallel height of the curve).
    * **slope**     — long minus short yield (longest − shortest available tenor); >0 normal,
      <0 inverted.
    * **curvature** — the classic butterfly ``2·belly − short − long`` using the tenor nearest
      the geometric-mean maturity as the belly.
    """
    curve = order_tenors(curve)
    short, long = curve.columns[0], curve.columns[-1]
    ys = np.array([tenor_years(c) for c in curve.columns])
    belly = curve.columns[int(np.argmin(np.abs(np.log(ys) - np.log(ys).mean())))]
    return pd.DataFrame(
        {
            "level": curve.mean(axis=1),
            "slope": curve[long] - curve[short],
            "curvature": 2 * curve[belly] - curve[short] - curve[long],
        }
    )


def carry_rolldown(curve: pd.DataFrame, tenor: str, horizon_days: int = 21) -> pd.DataFrame:
    """Approximate carry + roll-down for holding the ``tenor`` point over ``horizon_days``.

    Roll-down: as the bond ages it 'rolls' to a shorter maturity ``tenor - horizon`` on the
    *current* curve; the yield pickup ``y(tenor) − y(tenor - h)`` becomes a price gain of
    ``≈ (y(tenor) − y(tenor-h)) · duration``. Carry is the yield earned over the period. Both
    are computed from today's curve only (a standard ex-ante carry/roll estimate). Returns a
    frame with ``carry``, ``rolldown`` and ``carry_roll`` (their sum) per date, in percent.
    """
    curve = order_tenors(curve)
    t = tenor_years(tenor)
    h = horizon_days / 252.0
    rolled = max(t - h, tenor_years(curve.columns[0]))
    ys = np.array([tenor_years(c) for c in curve.columns])

    def interp(row: pd.Series) -> float:
        return float(np.interp(rolled, ys, row.to_numpy(dtype=float)))

    y_t = curve[tenor]
    y_rolled = curve.apply(interp, axis=1)
    carry = y_t * h                                  # yield earned over the horizon (%)
    rolldown = (y_t - y_rolled) * t                  # price gain ≈ Δy · duration (%)
    return pd.DataFrame({"carry": carry, "rolldown": rolldown, "carry_roll": carry + rolldown})
