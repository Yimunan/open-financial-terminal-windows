"""Equity options: chain expirations + a normalized chain snapshot (calls|puts, IV, greeks).

Standalone chain subsystem (services/options.py) — the source is chosen in Settings → Market Data →
Options; greeks are source-provided or computed locally (Black-Scholes) for sources that lack them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import get_options_enabled, get_options_source
from app.services import options as opt

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/expirations")
def expirations(underlying: str = Query(..., min_length=1)) -> dict:
    """Available expirations for an underlying (filtered to the configured window)."""
    if not get_options_enabled():
        raise HTTPException(400, f"options source is off ('{get_options_source()}')")
    return opt.expirations(underlying)


@router.get("/chain")
def chain(underlying: str = Query(..., min_length=1), expiry: str = Query(..., min_length=8)) -> dict:
    """The calls|puts chain for (underlying, expiry) with bid/ask/IV/OI and greeks."""
    if not get_options_enabled():
        raise HTTPException(400, f"options source is off ('{get_options_source()}')")
    data = opt.chain(underlying, expiry)
    if not data.get("calls") and not data.get("puts"):
        raise HTTPException(404, data.get("note") or f"no options chain for {underlying} {expiry}")
    return data
