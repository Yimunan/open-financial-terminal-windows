"""Pre-trade risk gates and a portfolio kill-switch.

Every order the paper loop produces passes through the gates before reaching a broker. In
backtest the same limits can be enforced on the weight schedule. Gates return a decision
plus a reason so rejections are auditable in the registry.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from qhfi.core.types import TargetWeights


@dataclass
class RiskLimits:
    max_gross: float = 1.5          # abs weights sum
    max_net: float = 1.0            # signed weights sum
    max_position: float = 0.20      # per-instrument abs weight
    max_drawdown_kill: float = 0.20 # halt new risk if book DD exceeds this


@dataclass
class GateDecision:
    approved: bool
    reason: str = "ok"


class RiskGate:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def check_weights(self, target: TargetWeights | pd.Series) -> GateDecision:
        """Validate a target-weight row/frame against gross/net/position limits.

        Accepts a single weight row (Series) or a frame (the last row is checked). The first
        limit breached wins, with a human-readable reason for the audit log.
        """
        row = target.iloc[-1] if isinstance(target, pd.DataFrame) else target
        w = row.dropna()
        lim = self.limits
        gross = float(w.abs().sum())
        if gross > lim.max_gross + 1e-9:
            return GateDecision(False, f"gross {gross:.3f} > max_gross {lim.max_gross}")
        net = float(w.sum())
        if abs(net) > lim.max_net + 1e-9:
            return GateDecision(False, f"net {net:.3f} exceeds ±{lim.max_net}")
        if len(w):
            worst = w.abs().idxmax()
            pos = float(w.abs().loc[worst])
            if pos > lim.max_position + 1e-9:
                return GateDecision(False, f"position {worst} {pos:.3f} > max_position {lim.max_position}")
        return GateDecision(True)

    def check_drawdown(self, equity_curve: pd.Series) -> GateDecision:
        """Trip the kill-switch if current drawdown breaches the limit.

        ``equity_curve`` is an equity-*level* series; current DD = last/peak − 1.
        """
        curve = equity_curve.dropna()
        if curve.empty:
            return GateDecision(True)
        dd = float(curve.iloc[-1] / curve.cummax().iloc[-1] - 1.0)
        if dd < -self.limits.max_drawdown_kill:
            return GateDecision(False, f"drawdown {dd:.3f} breaches kill {-self.limits.max_drawdown_kill}")
        return GateDecision(True)
