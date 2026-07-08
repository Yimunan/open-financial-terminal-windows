"""Portfolio construction — turn a combined alpha *score* into a tradable target-weight
schedule with the controls the research flagged as first-order.

Pipeline (per date, on a standardized/neutralized score panel):
  1. **smooth** the score (EWMA) — the primary turnover lever; slows position churn.
  2. **dollar-neutralize** — subtract the cross-sectional mean (long-short, net≈0).
  3. **scale to gross** — weights ∝ score, |w|.sum = gross.
  4. **position cap** — clip per-name to a fraction of gross (concentration limit).
  5. **vol target** — scale the whole book by target_vol / trailing realized vol (causal),
     so the strategy runs at a stable risk level.

This is the continuous-weight alternative to FactorStrategy's quantile buckets, and it's
where turnover and concentration are governed (cf. the deep-research synthesis).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from qhfi.core.types import Panel, TargetWeights


@dataclass
class ConstructionConfig:
    gross: float = 1.0
    max_position: float = 0.05          # per-name cap as a fraction of gross
    smoothing_halflife: int | None = 10  # EWMA halflife on the score; None = off
    target_vol: float | None = 0.10      # annualized; None = off
    vol_lookback: int = 60
    max_leverage: float = 3.0


class PortfolioConstructor:
    def __init__(self, config: ConstructionConfig | None = None) -> None:
        self.cfg = config or ConstructionConfig()

    def build(self, score: Panel, returns: Panel) -> TargetWeights:
        c = self.cfg
        s = score.copy()
        if c.smoothing_halflife:
            s = s.ewm(halflife=c.smoothing_halflife).mean()

        # dollar-neutral, scaled to gross
        s = s.sub(s.mean(axis=1), axis=0)
        denom = s.abs().sum(axis=1).replace(0.0, np.nan)
        w = s.div(denom, axis=0) * c.gross

        # per-name concentration cap, then renormalize gross
        cap = c.max_position * c.gross
        if cap > 0:
            # hard limit — do NOT renormalize (that re-inflates capped names over the cap);
            # gross sits ≤ target when caps bind, and the vol-target sets realized risk.
            w = w.clip(-cap, cap)

        # volatility targeting (causal: scale today by vol estimated through yesterday)
        if c.target_vol:
            base_ret = (w.shift(1) * returns).sum(axis=1)
            realized = base_ret.rolling(c.vol_lookback).std(ddof=0) * np.sqrt(252)
            lev = (c.target_vol / realized.replace(0.0, np.nan)).clip(upper=c.max_leverage)
            w = w.mul(lev.shift(1), axis=0)

        return w.fillna(0.0)
