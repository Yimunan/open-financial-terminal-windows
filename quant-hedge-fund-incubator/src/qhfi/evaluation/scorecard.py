"""Promotion scorecard — the explicit, recorded gate between lifecycle states.

A strategy advances (e.g. BACKTESTED → VALIDATED → PAPER) only by clearing thresholds
defined here. Thresholds are *config*: ``Scorecard.from_config()`` loads them from the
``scorecard:`` block of ``config/settings.yaml`` (via ``core.config.scorecard_thresholds``)
so the bar can be tuned without code changes; the dataclass values below are the matching
defaults. The ``CriticAgent`` reads the same scorecard to add a qualitative
overfitting/robustness opinion, but the numeric gate is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from qhfi.backtest.engine import BacktestResult
from qhfi.evaluation import metrics


@dataclass
class Thresholds:
    min_sharpe: float = 1.0
    min_calmar: float = 0.5
    max_drawdown: float = 0.25      # as a positive magnitude
    max_ann_turnover: float = 50.0  # one-way, annualized
    min_oos_sharpe_ratio: float = 0.5  # OOS sharpe / IS sharpe — overfit guard


@dataclass
class ScorecardResult:
    passed: bool
    metrics: dict[str, float]
    checks: dict[str, bool]
    notes: list[str] = field(default_factory=list)


class Scorecard:
    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.t = thresholds or Thresholds()

    @classmethod
    def from_config(cls, path: str | None = None) -> Scorecard:
        """Build with thresholds loaded from ``config/settings.yaml`` (the tunable gate)."""
        from qhfi.core.config import scorecard_thresholds

        return cls(scorecard_thresholds(path) if path else scorecard_thresholds())

    def grade(
        self,
        result: BacktestResult,
        oos_returns: pd.Series | None = None,
        periods_per_year: int = 252,
    ) -> ScorecardResult:
        m = metrics.summary(result.returns, periods_per_year)
        ann_turnover = float(result.turnover.fillna(0).sum() / max(len(result.turnover), 1) * periods_per_year)
        m["ann_turnover"] = ann_turnover

        checks = {
            "sharpe": m["sharpe"] >= self.t.min_sharpe,
            "calmar": m["calmar"] >= self.t.min_calmar,
            "drawdown": abs(m["max_drawdown"]) <= self.t.max_drawdown,
            "turnover": ann_turnover <= self.t.max_ann_turnover,
        }

        notes: list[str] = []
        if oos_returns is None or len(oos_returns) == 0:
            notes.append("no OOS returns supplied — run walk_forward before VALIDATED")
        elif m["sharpe"] <= 0:
            # OOS/IS ratio is meaningless with a non-positive in-sample Sharpe; the `sharpe`
            # check already fails the card, so don't fabricate a robustness verdict.
            m["oos_sharpe"] = metrics.sharpe(oos_returns, periods_per_year=periods_per_year)
            notes.append("in-sample Sharpe ≤ 0; OOS robustness ratio undefined")
        else:
            oos_sharpe = metrics.sharpe(oos_returns, periods_per_year=periods_per_year)
            ratio = oos_sharpe / m["sharpe"]
            m["oos_sharpe"] = oos_sharpe
            checks["oos_robustness"] = ratio >= self.t.min_oos_sharpe_ratio
            if not checks["oos_robustness"]:
                notes.append(f"OOS/IS sharpe ratio {ratio:.2f} below {self.t.min_oos_sharpe_ratio}")

        return ScorecardResult(passed=all(checks.values()), metrics=m, checks=checks, notes=notes)


class MarketMakingScorecard:
    """Quoting-strategy gate — wraps ``Scorecard`` and folds in the market-making panel
    (spread captured, fill ratio, inventory half-life, markout / adverse selection) from
    ``evaluation.mm_metrics``. The base risk/return checks still apply; MM adds a positive
    net-edge and a bounded-inventory gate on top.
    """

    def __init__(self, thresholds: Thresholds | None = None,
                 min_net_edge_bps: float = 0.0, max_inv_half_life: float = 1e9) -> None:
        self.base = Scorecard(thresholds)
        self.min_net_edge_bps = min_net_edge_bps
        self.max_inv_half_life = max_inv_half_life

    def grade(
        self,
        result: BacktestResult,
        mid: pd.Series | None = None,
        instrument: str | None = None,
        periods_per_year: int = 365 * 24 * 60,
    ) -> ScorecardResult:
        from qhfi.evaluation import mm_metrics

        card = self.base.grade(result, periods_per_year=periods_per_year)
        mm = mm_metrics.mm_summary(result, mid=mid, instrument=instrument,
                                   periods_per_year=periods_per_year)
        card.metrics.update(mm)
        card.checks["net_edge"] = mm["net_edge_bps"] >= self.min_net_edge_bps
        card.checks["inventory_reverts"] = mm["inv_half_life"] <= self.max_inv_half_life
        card.passed = all(card.checks.values())
        if mm["net_edge_bps"] < self.min_net_edge_bps:
            card.notes.append(f"net edge {mm['net_edge_bps']:.2f}bps below {self.min_net_edge_bps}bps")
        return card
