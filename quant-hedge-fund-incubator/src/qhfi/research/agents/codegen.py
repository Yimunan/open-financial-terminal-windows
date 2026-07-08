"""CodegenAgent — drafts a ``Strategy`` subclass from a hypothesis.

Pipeline: prompt the model with the Strategy contract + the Momentum reference + the
hypothesis → receive Python source → load it in the **sandbox** (restricted namespace, no
network/fs at import) → run a dry backtest on a small panel. Only source that imports
cleanly and produces finite weights is written to ``strategy/library/`` and registered as
IMPLEMENTED. The agent never touches execution; generated code only ever runs in backtest.
"""

from __future__ import annotations

from qhfi.research.agents.ideation import Hypothesis
from qhfi.research.client import LLMClient
from qhfi.research.sandbox import load_strategy_source


class CodegenAgent:
    SYSTEM = (
        "Write a single Python class subclassing qhfi.strategy.base.Strategy. Implement "
        "generate_weights(self, prices, universe) with NO look-ahead (shift signals by >=1 "
        "bar). Use only pandas/numpy. Return weights as a DataFrame (dates × instrument id). "
        "Output only code, no prose."
    )

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient()

    def draft(self, hypothesis: Hypothesis) -> str:
        """Return generated strategy source code for ``hypothesis``."""
        raise NotImplementedError("TODO: client.complete(SYSTEM, hypothesis+contract) → source")

    def materialize(self, source: str):
        """Sandbox-load the source and return the Strategy subclass if it passes checks."""
        return load_strategy_source(source)
