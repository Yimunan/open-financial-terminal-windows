"""Risk attribution — Barra factor + position decomposition of the tracked book.

Resolves the portfolio from either the user's Holdings (SQLite) or the paper-trading account, then
runs qhfi's BarraRiskModel attribution (services.risk_attribution). Equity-only; crypto positions
come back under ``skipped``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.deps import get_broker, get_sim_broker, get_store
from app.services import risk_attribution as ra
from app.store import TerminalStore

router = APIRouter(prefix="/api", tags=["risk"])


class RiskAttributionRequest(BaseModel):
    source: str = "holdings"            # "holdings" | "paper"
    window_days: int = ra._DEFAULT_WINDOW
    base_universe: str = ra._DEFAULT_BASE
    account: int | None = None          # sim account id for source="paper" (None → primary broker)


def _positions_for(source: str, store: TerminalStore, account: int | None = None) -> list[dict]:
    """A ``[{symbol, asset, quantity}]`` book from the chosen source.

    For ``source="paper"``: ``account`` selects a specific sim book; ``None`` keeps the historical
    behavior of reading the *primary* broker (Alpaca when keyed, else sim account 1)."""
    if source == "paper":
        broker = get_sim_broker(account) if account is not None else get_broker()
        return [
            # Position carries no asset class; crypto ids are slash-quoted (BTC/USDT).
            {"symbol": sym, "asset": "crypto" if "/" in sym else "equity", "quantity": float(pos.quantity)}
            for sym, pos in broker.get_positions().items()
        ]
    return [
        {"symbol": h["symbol"], "asset": h.get("asset", "equity"), "quantity": float(h["quantity"])}
        for h in store.list_holdings()
    ]


@router.post("/risk/attribution")
def risk_attribution(
    req: RiskAttributionRequest, store: TerminalStore = Depends(get_store)
) -> dict:
    source = req.source if req.source in ("holdings", "paper") else "holdings"
    positions = _positions_for(source, store)
    if not positions:
        return {"insufficient": True, "reason": f"no {source} positions", "source": source, "n": 0}
    return ra.compute_attribution(
        positions, source=source, base_universe=req.base_universe, window_days=req.window_days
    )


@router.post("/risk/return-attribution")
def return_attribution(
    req: RiskAttributionRequest, store: TerminalStore = Depends(get_store)
) -> dict:
    """Realized-P&L attribution: decompose the book's return over the window into factor + specific."""
    source = req.source if req.source in ("holdings", "paper") else "holdings"
    positions = _positions_for(source, store)
    if not positions:
        return {"insufficient": True, "reason": f"no {source} positions", "source": source, "n": 0}
    return ra.compute_return_attribution(
        positions, source=source, base_universe=req.base_universe, window_days=req.window_days
    )


@router.post("/risk/brinson")
def brinson_attribution(
    req: RiskAttributionRequest, store: TerminalStore = Depends(get_store)
) -> dict:
    """Brinson–Fachler sector attribution: book's active return vs an equal-weight benchmark of the
    fit universe, split into allocation / selection / interaction."""
    source = req.source if req.source in ("holdings", "paper") else "holdings"
    positions = _positions_for(source, store)
    if not positions:
        return {"insufficient": True, "reason": f"no {source} positions", "source": source, "n": 0}
    return ra.compute_brinson_attribution(
        positions, source=source, base_universe=req.base_universe, window_days=req.window_days
    )
