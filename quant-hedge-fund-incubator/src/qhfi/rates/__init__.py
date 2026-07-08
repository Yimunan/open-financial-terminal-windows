"""Rates models — term-structure analytics over the Treasury yield curve.

The yield curve is a (dates × tenors) panel, not the (dates × instruments) cross-section the
equity factor/strategy layer assumes — so rates gets its own small modeling layer:

* :mod:`qhfi.rates.curve`        — load the curve, tenor↔year mapping, level/slope/curvature
                                    metrics, and carry / roll-down.
* :mod:`qhfi.rates.pca`          — PCA of curve changes → the canonical level/slope/curvature
                                    factors (3 PCs explain ~99% of curve variance).
* :mod:`qhfi.rates.nelson_siegel`— the Nelson-Siegel parametric curve fit (β0/β1/β2).
* :mod:`qhfi.rates.forecast`     — forecast forward curve moves from those factors and version
                                    the fitted model in the taxonomy-aware ModelRepository.
"""

from qhfi.rates.curve import (
    TENOR_YEARS,
    carry_rolldown,
    curve_metrics,
    load_treasury_curve,
    tenor_years,
)
from qhfi.rates.nelson_siegel import NelsonSiegel, nelson_siegel_factors
from qhfi.rates.pca import CurvePCA

__all__ = [
    "TENOR_YEARS",
    "tenor_years",
    "load_treasury_curve",
    "curve_metrics",
    "carry_rolldown",
    "CurvePCA",
    "NelsonSiegel",
    "nelson_siegel_factors",
]
