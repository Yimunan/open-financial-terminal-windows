"""Portfolio — watchlist/holdings CRUD (SQLite) and risk analytics (qhfi metrics)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qhfi.data.manager import DataManager

from app.deps import get_data_manager, get_store
from app.services import portfolio as pf
from app.services import portfolio_book as pbk
from app.store import TerminalStore

router = APIRouter(prefix="/api", tags=["portfolio"])


class WatchItem(BaseModel):
    symbol: str
    asset: str = "equity"


class Holding(BaseModel):
    symbol: str
    asset: str = "equity"
    quantity: float
    cost_basis: float


# ── portfolio books (named multi-book holdings) ──────────────────────────────────
class BookBody(BaseModel):
    name: str


class ActiveBookBody(BaseModel):
    id: int


class AllocationRow(BaseModel):
    symbol: str
    asset: str = "equity"
    weight: float = 0.0


class BookFromAllocationsBody(BaseModel):
    name: str
    allocations: list[AllocationRow] = []
    capital: float = 1_000_000.0


def _books_payload(store: TerminalStore) -> dict:
    return {"books": store.list_portfolio_books(), "active": store.active_portfolio_book()}


@router.get("/portfolio-books")
def get_portfolio_books(store: TerminalStore = Depends(get_store)) -> dict:
    return _books_payload(store)


@router.post("/portfolio-books")
def create_portfolio_book(body: BookBody, store: TerminalStore = Depends(get_store)) -> dict:
    bid = store.create_portfolio_book(body.name)
    store.set_active_portfolio_book(bid)  # new book becomes active
    return {**_books_payload(store), "created": bid}


@router.post("/portfolio-books/from-allocations")
def book_from_allocations(
    body: BookFromAllocationsBody,
    store: TerminalStore = Depends(get_store),
    dm: DataManager = Depends(get_data_manager),
) -> dict:
    """Export a target-weight allocation (from Portfolio Builder) into the Portfolio module as a
    brand-new book. Weights are valued into share counts at current prices, then seeded as holdings
    in the new book (which becomes active). Unpriceable names are skipped, not faked."""
    result = pbk.allocate(dm, [a.model_dump() for a in body.allocations], body.capital)
    bid = store.create_portfolio_book(body.name)
    store.set_active_portfolio_book(bid)  # new book becomes active so holdings land in it
    seeded = 0
    for r in result["rows"]:
        # need a real price and a non-zero share count to be a holding
        if r["price"] is None or not r["shares"]:
            continue
        store.upsert_holding(r["symbol"], r["asset"], r["shares"], r["price"], book_id=bid)
        seeded += 1
    return {
        **_books_payload(store),
        "created": bid,
        "seeded": seeded,
        "priced": result["priced"],
        "rows": len(result["rows"]),
    }


@router.put("/portfolio-books/active")
def set_active_portfolio_book(body: ActiveBookBody, store: TerminalStore = Depends(get_store)) -> dict:
    store.set_active_portfolio_book(body.id)
    return _books_payload(store)


@router.put("/portfolio-books/{book_id}")
def rename_portfolio_book(book_id: int, body: BookBody, store: TerminalStore = Depends(get_store)) -> dict:
    store.rename_portfolio_book(book_id, body.name)
    return _books_payload(store)


@router.delete("/portfolio-books/{book_id}")
def delete_portfolio_book(book_id: int, store: TerminalStore = Depends(get_store)) -> dict:
    try:
        store.delete_portfolio_book(book_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _books_payload(store)


# ── watchlist ──────────────────────────────────────────────────────────────────
@router.get("/watchlist")
def get_watchlist(store: TerminalStore = Depends(get_store)) -> dict:
    return {"items": store.list_watchlist()}


@router.post("/watchlist")
def add_watchlist(item: WatchItem, store: TerminalStore = Depends(get_store)) -> dict:
    store.add_watch(item.symbol, item.asset)
    return {"items": store.list_watchlist()}


@router.delete("/watchlist/{symbol}")
def remove_watchlist(symbol: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_watch(symbol)
    return {"items": store.list_watchlist()}


class ReorderBody(BaseModel):
    order: list[str]  # full list of symbols in the desired order


@router.post("/watchlist/reorder")
def reorder_watchlist(body: ReorderBody, store: TerminalStore = Depends(get_store)) -> dict:
    store.reorder_watchlist(body.order)
    return {"items": store.list_watchlist()}


# ── holdings ─────────────────────────────────────────────────────────────────────
@router.get("/holdings")
def get_holdings(
    store: TerminalStore = Depends(get_store), dm: DataManager = Depends(get_data_manager)
) -> dict:
    return pf.holdings_pnl(dm, store.list_holdings())


@router.put("/holdings")
def upsert_holding(h: Holding, store: TerminalStore = Depends(get_store)) -> dict:
    store.upsert_holding(h.symbol, h.asset, h.quantity, h.cost_basis)
    return {"ok": True}


@router.delete("/holdings/{symbol}")
def remove_holding(symbol: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_holding(symbol)
    return {"ok": True}


def _parse_date(s: str | None) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else None
    except (TypeError, ValueError):
        return None


class CompositionRequest(BaseModel):
    start: str | None = None   # ISO date; window falls back to `years` back from `end`/today
    end: str | None = None
    years: int = 1


@router.post("/portfolio/composition")
def portfolio_composition(
    req: CompositionRequest,
    store: TerminalStore = Depends(get_store),
    dm: DataManager = Depends(get_data_manager),
) -> dict:
    """How each current holding's share of the book drifts over a user-defined window."""
    today = date.today()
    end = min(_parse_date(req.end) or today, today)
    start = _parse_date(req.start) or (end - timedelta(days=int(max(req.years, 1) * 365.25)))
    if start >= end:
        return {"insufficient": True, "n": 0, "error": "window start must be before end"}
    return pf.composition_over_time(dm, store.list_holdings(), start, end)


# ── risk ───────────────────────────────────────────────────────────────────────
class RiskRequest(BaseModel):
    items: list[WatchItem]
    days: int = 365  # trailing window for the return series / correlation


@router.post("/risk")
def risk(req: RiskRequest, dm: DataManager = Depends(get_data_manager)) -> dict:
    return pf.risk(dm, [i.model_dump() for i in req.items], days=req.days)


class Position(BaseModel):
    symbol: str
    asset: str = "equity"
    quantity: float


class PortfolioRiskRequest(BaseModel):
    positions: list[Position]
    days: int = 365              # trailing window for returns / VaR / beta
    benchmark: str = "SPY"       # symbol for portfolio beta


@router.post("/risk/portfolio")
def risk_portfolio(req: PortfolioRiskRequest, dm: DataManager = Depends(get_data_manager)) -> dict:
    return pf.portfolio_risk(
        dm, [p.model_dump() for p in req.positions], days=req.days, benchmark=req.benchmark
    )
