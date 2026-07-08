"""Market Board — curated multi-asset symbol sections for the dashboard board widget.

Metadata only: the client fetches each row's live/delayed quote via ``/api/quote``. Keeping the
catalog server-side (like universes) means the symbol list can be retuned without a frontend build.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.services import board as bd

router = APIRouter(prefix="/api", tags=["board"])


@router.get("/board")
def board() -> dict:
    return {"sections": bd.board_catalog()}
