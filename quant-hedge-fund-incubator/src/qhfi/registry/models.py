"""Lifecycle model for the incubator.

Every strategy is a record that moves through states only via recorded transitions, each
justified by an artifact (hypothesis text, code version, backtest id, scorecard). The
allowed transitions encode the incubator's process; ``advance`` enforces them.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class LifecycleState(str, Enum):
    IDEA = "idea"
    RESEARCH = "research"
    IMPLEMENTED = "implemented"
    BACKTESTED = "backtested"
    VALIDATED = "validated"     # passed OOS scorecard — eligible for paper
    PAPER = "paper"
    LIVE = "live"               # gated future state; not reachable in this scope
    REJECTED = "rejected"
    RETIRED = "retired"


# Allowed forward transitions (REJECTED/RETIRED reachable from any active state).
_ALLOWED: dict[LifecycleState, set[LifecycleState]] = {
    LifecycleState.IDEA: {LifecycleState.RESEARCH, LifecycleState.IMPLEMENTED},
    LifecycleState.RESEARCH: {LifecycleState.IMPLEMENTED},
    LifecycleState.IMPLEMENTED: {LifecycleState.BACKTESTED},
    LifecycleState.BACKTESTED: {LifecycleState.VALIDATED},
    LifecycleState.VALIDATED: {LifecycleState.PAPER},
    LifecycleState.PAPER: {LifecycleState.LIVE},  # blocked: live is out of scope
}


class Transition(BaseModel):
    at: datetime
    frm: LifecycleState
    to: LifecycleState
    artifact: str = Field(..., description="what justified it: hypothesis/code/backtest/scorecard id")


class StrategyRecord(BaseModel):
    name: str
    state: LifecycleState = LifecycleState.IDEA
    params: dict = {}
    universe: str | None = None
    history: list[Transition] = []
    origin: str = "human"        # "human" | "ideation-agent" | "codegen-agent"

    def can_advance(self, to: LifecycleState) -> bool:
        if to in (LifecycleState.REJECTED, LifecycleState.RETIRED):
            return True
        return to in _ALLOWED.get(self.state, set())

    def advance(self, to: LifecycleState, artifact: str, now: datetime) -> None:
        if not self.can_advance(to):
            raise ValueError(f"illegal transition {self.state} → {to}")
        if to == LifecycleState.LIVE:
            raise PermissionError("LIVE is out of scope: no live-money execution in this build")
        self.history.append(Transition(at=now, frm=self.state, to=to, artifact=artifact))
        self.state = to
