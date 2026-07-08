"""Safe loading of LLM-generated strategy code.

Generated source is executed in a restricted namespace that exposes only the Strategy
contract, pandas, and numpy — no builtins like ``open``/``__import__`` for arbitrary I/O,
no network. The loaded class is validated to be a ``Strategy`` subclass before use, and it
only ever runs against the backtest engine (never execution).

NOTE: this is a guardrail, not a true security boundary. Treat generated code as untrusted;
for stronger isolation run codegen materialization in a separate process/container.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qhfi.strategy.base import Strategy, StrategyParams


def load_strategy_source(source: str) -> type[Strategy]:
    """Exec ``source`` in a restricted namespace and return the defined Strategy subclass.

    Steps (TODO):
      * compile + exec with a curated globals dict {Strategy, StrategyParams, pd, np}
        and ``__builtins__`` reduced to a safe whitelist (no open/eval/exec/import).
      * find the single subclass of Strategy defined; reject if zero or many.
      * smoke-test: instantiate, call generate_weights on a tiny synthetic panel, assert
        the result is a finite DataFrame aligned to the universe.
    """
    safe_globals = {
        "Strategy": Strategy,
        "StrategyParams": StrategyParams,
        "pd": pd,
        "np": np,
        "__builtins__": {},  # TODO: replace with a minimal safe whitelist
    }
    raise NotImplementedError("TODO: exec in safe_globals, locate + validate Strategy subclass")
