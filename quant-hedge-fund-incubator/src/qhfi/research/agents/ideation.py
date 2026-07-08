"""IdeationAgent — turns a theme + universe into testable, structured hypotheses.

Output is structured (not prose) so each hypothesis flows straight into the registry as an
IDEA record and can be picked up by the CodegenAgent. The agent proposes; nothing trades.
"""

from __future__ import annotations

from pydantic import BaseModel

from qhfi.research.client import LLMClient


class Hypothesis(BaseModel):
    title: str
    rationale: str
    signal_description: str          # how to compute the signal from daily bars
    universe_hint: str               # asset class / liquidity filter
    expected_edge: str               # why it should work; known risks/regimes


class IdeationAgent:
    SYSTEM = (
        "You are a quantitative researcher. Propose daily-frequency, cross-sectional or "
        "time-series strategy hypotheses that are computable from adjusted daily OHLCV "
        "alone. Be specific and skeptical; note the regime where each edge fails."
    )

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()

    def ideate(self, theme: str, n: int = 5) -> list[Hypothesis]:
        """Ask the local model for ``n`` structured hypotheses on ``theme``."""
        raise NotImplementedError("TODO: client.structured(...) with a list[Hypothesis] schema")
