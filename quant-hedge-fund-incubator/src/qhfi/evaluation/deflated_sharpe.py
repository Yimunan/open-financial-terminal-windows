"""Deflated Sharpe Ratio (Bailey & López de Prado, 2014) — the overfitting control the
research flagged as essential: an uncontrolled backtest is worthless.

When you search ``N`` candidate strategies, the expected *maximum* Sharpe is > 0 even if
every true Sharpe is 0, so the significance bar must rise with the breadth of the search.

* ``probabilistic_sharpe_ratio`` — P(true SR > benchmark), correcting for sample length,
  skew, and kurtosis (non-Normal returns).
* ``expected_max_sharpe`` — the SR you'd expect as the best of ``N`` trials by luck alone
  (the "False Strategy" threshold).
* ``deflated_sharpe_ratio`` — PSR evaluated against that threshold.

All Sharpes here are **per-period** (not annualized); keep candidate and trial Sharpes on the
same per-period basis. No scipy dependency (erf + Acklam inverse-normal).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

_EULER = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation, ~1e-9 accurate)."""
    if not 0.0 < p < 1.0:
        return math.inf if p >= 1 else -math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def per_period_sharpe(returns) -> float:
    r = pd.Series(returns).dropna()
    sd = r.std(ddof=1)
    return float(r.mean() / sd) if sd else 0.0


def probabilistic_sharpe_ratio(returns, sr_benchmark: float = 0.0) -> float:
    """P(true per-period SR > ``sr_benchmark``), adjusted for T, skew, kurtosis."""
    r = pd.Series(returns).dropna()
    n = len(r)
    if n < 3:
        return float("nan")
    sr = per_period_sharpe(r)
    skew = float(r.skew())
    kurt = float(r.kurt()) + 3.0                      # pandas .kurt is excess → non-excess
    denom = math.sqrt(max(1e-12, 1 - skew * sr + ((kurt - 1) / 4.0) * sr ** 2))
    return _norm_cdf((sr - sr_benchmark) * math.sqrt(n - 1) / denom)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected best per-period Sharpe across ``n_trials`` independent trials with true SR=0
    (variance of trial Sharpes = ``sr_variance``). The False-Strategy threshold."""
    if n_trials < 2 or sr_variance <= 0:
        return 0.0
    z1 = _norm_ppf(1 - 1.0 / n_trials)
    z2 = _norm_ppf(1 - 1.0 / (n_trials * math.e))
    return math.sqrt(sr_variance) * ((1 - _EULER) * z1 + _EULER * z2)


def deflated_sharpe_ratio(returns, n_trials: int, sr_variance: float) -> float:
    """PSR against the expected-max-Sharpe threshold for ``n_trials`` searched candidates."""
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(returns, sr_benchmark=sr0)


def deflated_from_trials(candidate_returns, trial_returns: list) -> float:
    """Convenience: deflate a candidate against the set of all trials searched (each a
    returns series). n_trials and SR variance are taken from the trials."""
    srs = [per_period_sharpe(t) for t in trial_returns]
    var = float(np.var(srs, ddof=1)) if len(srs) > 1 else 0.0
    return deflated_sharpe_ratio(candidate_returns, n_trials=len(srs), sr_variance=var)
