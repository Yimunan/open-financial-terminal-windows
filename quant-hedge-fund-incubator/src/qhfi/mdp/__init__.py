"""Markov Decision Process layer — dynamic, state-dependent investment decisions.

* :mod:`qhfi.mdp.core`       — a generic finite-MDP solver (value / policy iteration).
* :mod:`qhfi.mdp.regime`     — Gaussian-mixture market regimes (Markov state) + transition matrix.
* :mod:`qhfi.mdp.allocation` — regime-switching dynamic allocation solved as an MDP.

The fitted policy ships as ``strategy.library.mdp_strategy.MDPStrategy`` (→ TargetWeights,
backtestable) and versions in the ``ModelRepository`` under ``ModelDomain.ALLOCATION``.
"""

from qhfi.mdp.allocation import RegimeAllocationMDP
from qhfi.mdp.core import MDP, policy_iteration, value_iteration
from qhfi.mdp.regime import RegimeModel, regime_return_stats, transition_matrix

__all__ = [
    "MDP",
    "value_iteration",
    "policy_iteration",
    "RegimeModel",
    "transition_matrix",
    "regime_return_stats",
    "RegimeAllocationMDP",
]
