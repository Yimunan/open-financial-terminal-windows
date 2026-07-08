"""Kalman-filter layer — online state-space estimation for trading signals.

* :mod:`qhfi.kalman.filter` — a dynamic linear regression (time-varying intercept + hedge
  ratio) run as a causal Kalman filter; the spread/z-score it emits is the input to pairs
  trading.

The filter ships as ``strategy.library.kalman_pairs.KalmanPairsStrategy`` (→ TargetWeights,
backtestable).
"""

from qhfi.kalman.filter import kalman_hedge, kalman_regression

__all__ = ["kalman_hedge", "kalman_regression"]
