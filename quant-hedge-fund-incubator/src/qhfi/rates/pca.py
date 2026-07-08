"""Principal-component analysis of the yield curve — the canonical rates model.

PCA on daily curve *changes* (the stationary series) recovers the three textbook factors that
explain ~99% of Treasury-curve variation:

* **PC1 — level**     : all tenors move together (a parallel shift).
* **PC2 — slope**     : short and long ends move oppositely (steepen / flatten).
* **PC3 — curvature** : the belly moves against the wings (butterfly).

Loadings are sign-normalized to that economic convention (level loadings positive; a positive
slope score = steeper; a positive curvature score = belly rich). ``transform`` projects the
demeaned *level* curve onto those loadings to give the factor **levels** used as model features.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

_NAMES = ["level", "slope", "curvature"]


class CurvePCA:
    """Fit on a ``(dates × tenor)`` curve; expose loadings, explained variance, and factors."""

    def __init__(self, n_components: int = 3) -> None:
        self.n_components = n_components

    def fit(self, curve: pd.DataFrame) -> CurvePCA:
        from sklearn.decomposition import PCA

        self.tenors_ = list(curve.columns)
        k = min(self.n_components, len(self.tenors_))
        changes = curve.diff().dropna(how="any")
        pca = PCA(n_components=k)
        pca.fit(changes.to_numpy(dtype=float))

        comp = pca.components_.copy()                       # (k, n_tenors)
        comp *= self._signs(comp)[:, None]                 # economic sign convention
        self.components_ = comp
        self.explained_variance_ratio_ = pca.explained_variance_ratio_
        self.mean_level_ = curve.mean(axis=0).to_numpy(dtype=float)
        self.names_ = [_NAMES[i] if i < len(_NAMES) else f"pc{i + 1}" for i in range(k)]
        return self

    @staticmethod
    def _signs(comp: np.ndarray) -> np.ndarray:
        """Flip components so level loadings are positive, slope increases with maturity, and
        curvature is positive in the belly."""
        signs = np.ones(comp.shape[0])
        n = comp.shape[1]
        if comp.shape[0] >= 1 and comp[0].mean() < 0:                  # level → positive
            signs[0] = -1.0
        if comp.shape[0] >= 2 and (comp[1, -1] - comp[1, 0]) < 0:      # slope → increasing
            signs[1] = -1.0
        if comp.shape[0] >= 3:                                         # curvature → belly up
            belly = comp[2, n // 2] - 0.5 * (comp[2, 0] + comp[2, -1])
            if belly < 0:
                signs[2] = -1.0
        return signs

    def loadings(self) -> pd.DataFrame:
        """Factor loadings as a ``(tenor × factor)`` frame."""
        return pd.DataFrame(self.components_.T, index=self.tenors_, columns=self.names_)

    def transform(self, curve: pd.DataFrame) -> pd.DataFrame:
        """Project the demeaned level curve onto the loadings → factor **levels** time series."""
        x = curve[self.tenors_].to_numpy(dtype=float) - self.mean_level_
        scores = x @ self.components_.T
        return pd.DataFrame(scores, index=curve.index, columns=self.names_)

    def fit_transform(self, curve: pd.DataFrame) -> pd.DataFrame:
        return self.fit(curve).transform(curve)

    def explained(self) -> pd.Series:
        """Fraction of curve-change variance each factor explains."""
        return pd.Series(self.explained_variance_ratio_, index=self.names_)
