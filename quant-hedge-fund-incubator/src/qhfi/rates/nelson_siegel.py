"""Nelson-Siegel parametric yield-curve model.

The Nelson-Siegel (1987) form fits the whole curve with three economically meaningful factors
and one decay parameter ``lam`` (the maturity, in years, where the curvature loading peaks)::

    y(τ) = β0 + β1 · L1(τ) + β2 · L2(τ)
    L1(τ) = (1 - exp(-τ/lam)) / (τ/lam)                 # short-rate / slope loading
    L2(τ) = L1(τ) - exp(-τ/lam)                         # curvature loading

β0 is the long-run **level**, β1 the **slope** (short-end premium, β0+β1 ≈ the instantaneous
short rate), β2 the **curvature**. With ``lam`` fixed, the loadings are constant across dates,
so each day's β is a plain (look-ahead-free) OLS of that day's yields on the three loadings —
fast, stable, and well determined even with only 4 tenors.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.rates.curve import tenor_years

# A conventional decay: curvature loading peaks near the 2-3y belly.
DEFAULT_LAM = 2.0


def _loadings(years: np.ndarray, lam: float) -> np.ndarray:
    """Design matrix columns [1, L1, L2] for the given maturities (years)."""
    x = years / lam
    # limit of (1-e^-x)/x as x→0 is 1; guard the short end.
    with np.errstate(divide="ignore", invalid="ignore"):
        l1 = np.where(x == 0, 1.0, (1.0 - np.exp(-x)) / x)
    l2 = l1 - np.exp(-x)
    return np.column_stack([np.ones_like(years), l1, l2])


class NelsonSiegel:
    """Fixed-``lam`` Nelson-Siegel fitter over a ``(dates × tenor)`` curve."""

    def __init__(self, lam: float = DEFAULT_LAM) -> None:
        self.lam = lam

    def fit(self, curve: pd.DataFrame) -> NelsonSiegel:
        self.tenors_ = list(curve.columns)
        self.years_ = np.array([tenor_years(t) for t in self.tenors_])
        self.design_ = _loadings(self.years_, self.lam)
        return self

    def factors(self, curve: pd.DataFrame) -> pd.DataFrame:
        """Per-date (β0=level, β1=slope, β2=curvature) by OLS on each row's yields."""
        y = curve[self.tenors_].to_numpy(dtype=float)
        betas, *_ = np.linalg.lstsq(self.design_, y.T, rcond=None)     # (3, n_dates)
        return pd.DataFrame(betas.T, index=curve.index, columns=["level", "slope", "curvature"])

    def fitted(self, betas: pd.DataFrame) -> pd.DataFrame:
        """Reconstruct the curve implied by a frame of betas (the model's fitted yields)."""
        y = betas[["level", "slope", "curvature"]].to_numpy(dtype=float) @ self.design_.T
        return pd.DataFrame(y, index=betas.index, columns=self.tenors_)

    def rmse(self, curve: pd.DataFrame) -> float:
        """Root-mean-square fit error (percent) of the model across all tenors and dates."""
        betas = self.factors(curve)
        resid = curve[self.tenors_].to_numpy(dtype=float) - self.fitted(betas).to_numpy(dtype=float)
        return float(np.sqrt(np.nanmean(resid**2)))


def nelson_siegel_factors(curve: pd.DataFrame, lam: float = DEFAULT_LAM) -> pd.DataFrame:
    """Convenience: fit and return the per-date NS (level, slope, curvature) factors."""
    return NelsonSiegel(lam).fit(curve).factors(curve)
