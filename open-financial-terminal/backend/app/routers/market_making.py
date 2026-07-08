"""Market making — qhfi quoting-strategy backtests over a single crypto pair."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import market_making as mm

router = APIRouter(prefix="/api/mm", tags=["market-making"])


class MMRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1m"          # crypto intraday: 1m | 5m | 15m | 1h
    strategy: str = "alpha_taker"  # symmetric | linear | avellaneda | alpha | alpha_taker
    gamma: float = 0.3             # AS risk aversion (skew + spread width)
    kappa: float = 1.5             # AS order-arrival decay
    half_spread_bps: float = 5.0   # base quote half-spread (bps) for the bps-parametrized quoters
    skew_bps: float = 10.0         # inventory skew at ±q_max (bps)
    q_max: float = 20.0            # inventory limit (units) → one-sided quoting
    obi_alpha: float = 0.5         # order-book-imbalance tilt on fair value
    quote_size: float = 1.0
    sigma_window: int = 100
    spread_bps: float = 5.0        # modelled touch spread of the synthetic book
    levels: int = 5
    depth: float = 5.0
    imbalance_gain: float = 1.0
    maker_bps: float = 1.0
    initial_equity: float = 100_000.0
    max_snapshots: int = 4000


@router.post("/backtest")
def mm_backtest(req: MMRequest) -> dict:
    """Run one quoting strategy over real bars + synthetic depth → curves + MM metrics."""
    try:
        return mm.run_mm_backtest(**req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/compare")
def mm_compare(req: MMRequest) -> dict:
    """Run ALL quoting strategies on the same book → the full comparison (the headline view)."""
    body = req.model_dump()
    body.pop("strategy", None)     # compare ignores a single-strategy selection
    try:
        return mm.compare_mm_backtest(**body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
