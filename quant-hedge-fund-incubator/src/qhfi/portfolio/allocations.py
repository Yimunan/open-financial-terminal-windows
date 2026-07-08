"""Factor-free portfolio allocators — weights from returns/covariance alone (no alpha views).

All are **long-only**, fully invested (weights ≥ 0, sum to 1), so they're directly
comparable to the 1/N long-only benchmark:

* ``equal_weight`` — 1/N (the benchmark; no estimation, famously hard to beat OOS).
* ``inverse_vol`` — w ∝ 1/σ (risk-parity proxy; needs only individual vols).
* ``min_variance_long_only`` — global min-variance on Ledoit-Wolf covariance, clipped ≥0.
* ``max_sharpe_long_only`` — tangency on historical mean + shrinkage cov (the noisiest;
  great in-sample, fragile out-of-sample — the estimation-error showcase).

Each takes a returns window (DataFrame, T×N) and returns a weight vector aligned to columns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.portfolio import optimize as mv
from qhfi.portfolio.covariance import ledoit_wolf


def equal_weight(returns: pd.DataFrame) -> np.ndarray:
    n = returns.shape[1]
    return np.full(n, 1.0 / n)


def inverse_vol(returns: pd.DataFrame) -> np.ndarray:
    sd = returns.std(ddof=0).to_numpy()
    w = np.where(sd > 0, 1.0 / sd, 0.0)
    return w / w.sum() if w.sum() > 0 else equal_weight(returns)


def min_variance_long_only(returns: pd.DataFrame) -> np.ndarray:
    sigma, _ = ledoit_wolf(returns)
    w = np.clip(mv.min_variance(sigma), 0.0, None)
    return w / w.sum() if w.sum() > 0 else equal_weight(returns)


def max_sharpe_long_only(returns: pd.DataFrame) -> np.ndarray:
    mu = returns.mean().to_numpy()
    sigma, _ = ledoit_wolf(returns)
    w = mv.max_sharpe(mu, sigma, gross=1.0, long_only=True)  # clips ≥0, |w|.sum == 1
    return w if w.sum() > 0 else equal_weight(returns)


ALLOCATORS = {
    "1/N (benchmark)": equal_weight,
    "inverse-vol": inverse_vol,
    "min-variance": min_variance_long_only,
    "max-sharpe": max_sharpe_long_only,
}
