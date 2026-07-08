"""Importing this package registers all built-in strategies."""

from qhfi.strategy.library import (  # noqa: F401
    barra_minvar,
    butterfly,
    factor_strategy,
    kalman_pairs,
    mdp_strategy,
    model_strategy,
    momentum,
)
