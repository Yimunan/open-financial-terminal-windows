"""Fundamentals & news endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from qhfi.research.client import LLMClient

from app.config import get_news_sources
from app.deps import get_llm_client
from app.services import fundamentals as fa
from app.services import news_router as nr

router = APIRouter(prefix="/api", tags=["fundamentals"])


@router.get("/fundamentals")
def fundamentals(symbol: str) -> dict:
    return {"snapshot": fa.snapshot(symbol), "financials": fa.financials(symbol)}


@router.get("/news")
def news(
    symbol: str,
    sentiment: bool = False,
    rank: bool = True,
    llm: LLMClient = Depends(get_llm_client),
) -> dict:
    """Routed + ranked headlines. `rank` re-orders by the composite score; else newest-first.

    The headline cap (Settings → News → Max headlines) is applied LAST: we score/rank a larger pool
    so an important-but-slightly-older story can still surface, then truncate to `max_items`.
    """
    cfg = get_news_sources()
    max_items = int(cfg.get("max_items", 30))
    # Consider more than we'll keep (so ranking isn't limited to the newest N), but bound the
    # per-headline LLM sentiment cost.
    pool = nr.news(symbol, limit=None)[: max(max_items * 2, 60)]
    if (sentiment or rank) and pool:
        nr.apply_scores(llm, symbol, pool)  # cached; only new headlines hit the LLM
    if rank and pool:
        pool = nr.rank_news(pool, symbol, params=cfg.get("ranking"))  # user-tuned weights
    return {"symbol": symbol.upper(), "items": pool[:max_items]}


@router.get("/news/topics")
def news_topics() -> dict:
    """Selectable news topics (built-in Market/Macro + enabled user topics) for the launcher."""
    return {"topics": nr.available_topics()}


@router.get("/news/topic")
def news_topic(
    category: str,
    sentiment: bool = False,
    rank: bool = True,
    llm: LLMClient = Depends(get_llm_client),
) -> dict:
    """Symbol-agnostic topic news (built-in or a user keyword topic) for the Topic News widget.

    Mirrors :func:`news` but pulls topic feeds instead of per-symbol sources. Sentiment is scored
    against the category's subject (not a ticker) and ranking passes an empty symbol, so the
    symbol-match signal naturally contributes 0 — the rest of the pipeline is identical.
    """
    if category not in {t["key"] for t in nr.available_topics()}:
        raise HTTPException(status_code=400, detail=f"unknown news category: {category}")
    cfg = get_news_sources()
    max_items = int(cfg.get("max_items", 30))
    pool = nr.topic_news(category, limit=None)[: max(max_items * 2, 60)]
    if (sentiment or rank) and pool:
        nr.apply_scores(llm, nr.topic_subject(category), pool)  # cached; only new headlines hit LLM
    if rank and pool:
        pool = nr.rank_news(pool, "", params=cfg.get("ranking"))  # empty symbol → match term = 0
    return {"category": category, "items": pool[:max_items]}
