"""New-listing detection endpoint — SEC EDGAR full-text search (8-A12B / 424B4)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.config import get_terminal_settings
from app.deps import get_edgar_client
from app.services import listings as ls

router = APIRouter(prefix="/api", tags=["listings"])


@router.get("/listings/new")
def new_listings(
    days: int = Query(14, ge=1, le=90, description="lookback window in days"),
    limit: int = Query(200, ge=1, le=1000),
    with_ticker_only: bool = Query(True, description="keep only issuers that have a ticker"),
    edgar=Depends(get_edgar_client),
) -> dict:
    """Securities newly listed (8-A12B) or freshly IPO-priced (424B4) in the last ``days``."""
    return ls.new_listings(
        edgar, get_terminal_settings().data_dir, days=days, limit=limit,
        with_ticker_only=with_ticker_only,
    )
