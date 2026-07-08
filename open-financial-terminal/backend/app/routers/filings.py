"""Public filings endpoints — SEC EDGAR feed, insider transactions, institutional holders."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import (
    get_cusip_store,
    get_edgar_client,
    get_filings_store,
    get_holdings_store,
    get_insider_store,
)
from app.services import filings as fl

router = APIRouter(prefix="/api", tags=["filings"])


@router.get("/filings")
def filings(
    symbol: str,
    category: str | None = None,
    edgar=Depends(get_edgar_client),
    filings_store=Depends(get_filings_store),
) -> dict:
    return fl.feed(edgar, filings_store, symbol, category)


@router.get("/filings/insider")
def insider(
    symbol: str,
    edgar=Depends(get_edgar_client),
    insider_store=Depends(get_insider_store),
) -> dict:
    return fl.insider(edgar, insider_store, symbol)


@router.get("/filings/holders")
def holders(
    symbol: str,
    holdings_store=Depends(get_holdings_store),
    cusip_store=Depends(get_cusip_store),
) -> dict:
    return fl.holders(holdings_store, cusip_store, symbol)
