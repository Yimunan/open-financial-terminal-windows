"""Kalman filter for *dynamic linear regression* — the engine behind spread trading.

Models one series as a time-varying linear function of one or more others,

    y_t = alpha_t + sum_j beta_{j,t} x_{j,t} + v_t,    v_t ~ N(0, obs_var)     (observation)
    theta_t = theta_{t-1} + w_t,                        w_t ~ N(0, Vw)          (random-walk state)

i.e. an *online* OLS whose intercept and slopes drift over time. The standard Bayesian-regression
Kalman recursion (Chan, *Algorithmic Trading*) runs causally — each row uses data only through
that date, so the outputs are backtest-safe with no look-ahead.

The **forecast error** ``e_t = y_t - (theta_{t|t-1} · [1, x_t])`` is the model-implied *spread*;
its predicted variance ``Q_t`` gives a self-normalizing z-score ``z_t = e_t/sqrt(Q_t)`` that
spread strategies trade for mean reversion. ``Vw`` is set from a single ``delta`` knob
(``Vw = delta/(1-delta) * I``): smaller delta → stiffer (slower-drifting) coefficients.

* :func:`kalman_hedge` — one regressor (pairs hedge ratio).
* :func:`kalman_regression` — many regressors (e.g. a butterfly: belly on its two wings).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _run_kalman(
    y: np.ndarray, design: np.ndarray, delta: float, obs_var: float, prior_var: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Core recursion. ``design`` is the (n, k) observation matrix (rows ``[1, x_t...]``);
    returns the per-row filtered state ``(n, k)``, the prior forecast error ``(n,)`` and its
    variance ``(n,)``. Causal: row t's error uses the state estimated through t-1."""
    n, k = design.shape
    vw = delta / (1.0 - delta) * np.eye(k)
    theta = np.zeros(k)
    cov = np.eye(k) * prior_var

    states = np.empty((n, k))
    spread = np.empty(n)
    var = np.empty(n)
    for t in range(n):
        h = design[t]
        cov_pred = cov + vw                              # predicted state covariance
        e = float(y[t] - h @ theta)                      # forecast error (uses prior state)
        q = float(h @ cov_pred @ h) + obs_var            # forecast-error variance
        gain = cov_pred @ h / q                          # Kalman gain
        theta = theta + gain * e                         # state update
        cov = cov_pred - np.outer(gain, h @ cov_pred)    # covariance update
        states[t] = theta
        spread[t] = e
        var[t] = q
    return states, spread, var


def _aligned(y: pd.Series, regressors: dict[str, pd.Series]) -> pd.DataFrame:
    cols = {"__y__": y.astype(float)}
    cols.update({k: v.astype(float) for k, v in regressors.items()})
    return pd.concat(cols, axis=1).dropna()


def kalman_regression(
    y: pd.Series,
    regressors: dict[str, pd.Series],
    delta: float = 1e-4,
    obs_var: float = 1e-3,
    prior_var: float = 1.0,
) -> pd.DataFrame:
    """Online multivariate regression of ``y`` on the named ``regressors`` via a Kalman filter.

    Returns a per-date frame (indexed by the dates all series share) with ``alpha``, one
    ``beta_<name>`` per regressor (the filtered coefficients through date t), ``spread`` (the
    causal forecast error e_t), ``spread_var`` (Q_t) and ``z`` (e_t/sqrt(Q_t))."""
    names = list(regressors)
    df = _aligned(y, regressors)
    out_cols = ["alpha", *(f"beta_{n}" for n in names), "spread", "spread_var", "z"]
    if df.empty:
        return pd.DataFrame(columns=out_cols)

    design = np.column_stack([np.ones(len(df)), df[names].to_numpy()])   # [1, x1, x2, ...]
    states, spread, var = _run_kalman(df["__y__"].to_numpy(), design, delta, obs_var, prior_var)

    out = pd.DataFrame(index=df.index)
    out["alpha"] = states[:, 0]
    for j, n in enumerate(names, start=1):
        out[f"beta_{n}"] = states[:, j]
    out["spread"] = spread
    out["spread_var"] = var
    out["z"] = out["spread"] / np.sqrt(out["spread_var"])
    return out


def kalman_hedge(
    y: pd.Series,
    x: pd.Series,
    delta: float = 1e-4,
    obs_var: float = 1e-3,
    prior_var: float = 1.0,
) -> pd.DataFrame:
    """Single-regressor dynamic regression (pairs hedge ratio) of ``y`` on ``x``.

    Returns columns ``alpha``, ``beta``, ``spread`` (forecast error e_t), ``spread_var`` (Q_t)
    and ``z``. Causal — a position taken on ``z_t`` uses no future information."""
    out = kalman_regression(y, {"x": x}, delta, obs_var, prior_var)
    return out.rename(columns={"beta_x": "beta"})
