"""Barra-style cross-sectional fundamental factor RISK model.

* :mod:`qhfi.barra.exposures` — standardized style factors + GICS industry dummies (the design
  matrix ``X``); price/volume-only, full coverage, ADV as the cap proxy.
* :mod:`qhfi.barra.model`     — ``BarraRiskModel``: per-date WLS factor returns, EWMA factor
  covariance + specific risk, the asset covariance ``Σ = X F Xᵀ + diag(Δ)``, risk decomposition,
  and the ``bias_statistic`` calibration check.

The fitted model versions in the ``ModelRepository`` under ``ModelDomain.RISK`` and powers
``strategy.library.barra_minvar.BarraMinVarStrategy`` (a risk-model min-variance book).
"""

from qhfi.barra.exposures import STYLE_FACTORS, industry_dummies, style_exposures
from qhfi.barra.model import BarraRiskModel, bias_statistic

__all__ = [
    "STYLE_FACTORS",
    "style_exposures",
    "industry_dummies",
    "BarraRiskModel",
    "bias_statistic",
]
