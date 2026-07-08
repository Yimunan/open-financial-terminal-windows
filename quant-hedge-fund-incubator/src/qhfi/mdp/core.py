"""A generic finite Markov Decision Process and its dynamic-programming solvers.

An MDP is ``(states, actions, transition, reward, gamma)``. Here the transition is **action-
independent** — ``P[s, s']`` is the probability of moving to ``s'`` from ``s`` regardless of the
action — which is the right model when the state is an *exogenous* market regime and the action
only sets exposure (it does not move the market). The reward ``R[s, a]`` is the immediate payoff
of taking action ``a`` in state ``s``.

Both solvers find the policy maximizing expected discounted reward
``V(s) = maxₐ [ R[s,a] + γ · Σ_{s'} P[s,s'] V(s') ]`` (the Bellman optimality equation). Pure
numpy, no qhfi imports — reusable for any finite MDP (allocation today, execution later).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MDP:
    """A finite MDP with an action-independent transition kernel.

    ``transition``: (S × S) row-stochastic regime transition matrix ``P[s, s']``.
    ``reward``:     (S × A) immediate reward ``R[s, a]``.
    ``actions``:    length-A array of the action *values* (e.g. risky fractions).
    ``gamma``:      discount factor in [0, 1).
    """

    transition: np.ndarray
    reward: np.ndarray
    actions: np.ndarray
    gamma: float = 0.95

    def __post_init__(self) -> None:
        self.transition = np.asarray(self.transition, dtype=float)
        self.reward = np.asarray(self.reward, dtype=float)
        self.actions = np.asarray(self.actions, dtype=float)
        s, a = self.reward.shape
        if self.transition.shape != (s, s):
            raise ValueError(f"transition must be ({s},{s}), got {self.transition.shape}")
        if self.actions.shape != (a,):
            raise ValueError(f"actions must be ({a},), got {self.actions.shape}")
        if not 0.0 <= self.gamma < 1.0:
            raise ValueError(f"gamma must be in [0, 1), got {self.gamma}")

    @property
    def n_states(self) -> int:
        return self.reward.shape[0]

    @property
    def n_actions(self) -> int:
        return self.reward.shape[1]


def _q_values(mdp: MDP, v: np.ndarray) -> np.ndarray:
    """Action-value ``Q[s,a] = R[s,a] + γ · (P V)[s]`` (broadcast over actions)."""
    continuation = mdp.gamma * (mdp.transition @ v)        # (S,)
    return mdp.reward + continuation[:, None]              # (S, A)


def value_iteration(
    mdp: MDP, tol: float = 1e-10, max_iter: int = 10_000
) -> tuple[np.ndarray, np.ndarray]:
    """Solve the MDP by value iteration. Returns ``(V, policy)`` where ``policy[s]`` is the
    index into ``mdp.actions`` of the optimal action in state ``s``."""
    v = np.zeros(mdp.n_states)
    for _ in range(max_iter):
        q = _q_values(mdp, v)
        v_new = q.max(axis=1)
        if np.max(np.abs(v_new - v)) < tol:
            v = v_new
            break
        v = v_new
    policy = _q_values(mdp, v).argmax(axis=1)
    return v, policy


def policy_iteration(mdp: MDP, max_iter: int = 1_000) -> tuple[np.ndarray, np.ndarray]:
    """Solve the MDP by policy iteration (exact policy evaluation). Returns ``(V, policy)``.
    Used to cross-check :func:`value_iteration`."""
    s = mdp.n_states
    policy = np.zeros(s, dtype=int)
    eye = np.eye(s)
    for _ in range(max_iter):
        r_pi = mdp.reward[np.arange(s), policy]                       # (S,)
        v = np.linalg.solve(eye - mdp.gamma * mdp.transition, r_pi)   # exact evaluation
        new_policy = _q_values(mdp, v).argmax(axis=1)
        if np.array_equal(new_policy, policy):
            policy = new_policy
            break
        policy = new_policy
    r_pi = mdp.reward[np.arange(s), policy]
    v = np.linalg.solve(eye - mdp.gamma * mdp.transition, r_pi)
    return v, policy
