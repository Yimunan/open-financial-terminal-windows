"""Fill modeling — *when* and *at what price* a target trade actually executes.

Separating this from the cost model lets the backtest answer "what price did I get?"
distinctly from "what commission did I pay?". Slippage here moves the **execution price**
adversely (so it also changes the marked PnL of the new position), which is more realistic
than folding it into a flat cost subtracted from returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FillTiming(str, Enum):
    CLOSE = "close"          # execute at the same bar's close (conservative, close-to-close)
    NEXT_OPEN = "next_open"  # execute at the next bar's open (needs an open panel)


@dataclass
class SlippageModel:
    """Adverse price impact in basis points of the reference price.

    ``bps`` is applied against you: buys fill higher, sells fill lower. A future market-impact
    term (∝ trade size / ADV) would extend ``price_impact``.
    """

    bps: float = 5.0

    def fill_price(self, ref_price: float, side: int) -> float:
        """``side`` = +1 for a buy, -1 for a sell. Returns the adverse fill price."""
        return ref_price * (1.0 + side * self.bps / 1e4)
