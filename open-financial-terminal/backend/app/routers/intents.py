"""Server-side resolver for cross-module `send` hand-offs.

Most hand-offs resolve entirely client-side (the registry `accepts` map turns a payload into the
target widget's params). This endpoint exists for the cases that need engine knowledge to
materialize a concrete config — e.g. turning a screen result into a ready-to-run backtest config.
It returns a `{target, config}` the frontend can feed to `openWidget` / a run endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.schemas.intents import SendPayload

router = APIRouter(prefix="/api", tags=["intents"])


class ResolveRequest(BaseModel):
    payload: SendPayload


@router.post("/intents/resolve")
def resolve(req: ResolveRequest) -> dict:
    """Materialize a `send` payload into a concrete target config. Pure/deterministic today; the
    seam is here so payloads that need qhfi (universe expansion, default windows) can grow into it
    without changing the contract."""
    p = req.payload
    if p.kind == "screen_result":
        return {
            "target": "backtest",
            "config": {
                "btMode": "factor",
                "factor": p.factor,
                "universe": p.universe,
                "mode": "long_short",
                "symbols": p.symbols,
            },
        }
    if p.kind == "backtest_result":
        return {
            "target": "strategies",
            "config": {"name": p.strategyKey or "backtest", "params": p.params},
        }
    if p.kind == "symbols":
        return {"target": "watchlist", "config": {"symbols": p.symbols, "asset": p.asset}}
    raise HTTPException(400, f"cannot resolve payload kind '{getattr(p, 'kind', '?')}'")
