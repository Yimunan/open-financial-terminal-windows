"""FICC board — curated rates / FX / commodity sections for the unified Futures/FICC widget.

Metadata only: the client fetches each row's quote via ``/api/quote`` (native asset class). Kept
server-side like the Market Board so the catalog retunes without a frontend build.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.services import ficc as ficc_svc

router = APIRouter(prefix="/api", tags=["ficc"])


@router.get("/ficc/board")
def board() -> dict:
    return {"sections": ficc_svc.ficc_board()}
