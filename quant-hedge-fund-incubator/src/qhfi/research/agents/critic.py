"""CriticAgent — qualitative robustness review layered on top of the numeric scorecard.

Reads a ``BacktestResult`` summary + ``ScorecardResult`` and flags overfitting tells:
too-good Sharpe, dependence on a few dates, regime concentration, suspiciously low
turnover with high return, look-ahead smells. Returns a structured verdict that the
registry stores alongside the numeric gate. The numeric scorecard remains authoritative;
the critic can only *block*, never *approve*.
"""

from __future__ import annotations

from pydantic import BaseModel

from qhfi.evaluation.scorecard import ScorecardResult
from qhfi.research.client import LLMClient


class CriticVerdict(BaseModel):
    block: bool
    concerns: list[str]
    suggested_tests: list[str]


class CriticAgent:
    SYSTEM = (
        "You are a skeptical risk reviewer. Given strategy metrics and OOS results, identify "
        "overfitting and look-ahead risks. Default to caution. You may BLOCK promotion but "
        "cannot approve; approval is the numeric scorecard's job."
    )

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()

    def review(self, scorecard: ScorecardResult) -> CriticVerdict:
        raise NotImplementedError("TODO: client.structured(SYSTEM, scorecard) → CriticVerdict")
