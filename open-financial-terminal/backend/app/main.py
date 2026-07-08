"""Open Financial Terminal v2 — FastAPI application entrypoint.

Run locally:  uvicorn app.main:app --reload --port 8050
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_terminal_settings
from app.routers import (
    agent,
    algo,
    assistant,
    backtest,
    board,
    chart,
    committee,
    data_refresh,
    factor_monitor,
    ficc,
    filings,
    fundamentals,
    health,
    intents,
    lab,
    listings,
    macro,
    market,
    market_making,
    metrics,
    options,
    paper,
    portfolio,
    rates,
    registry,
    research,
    risk,
    sandbox,
    screener,
    settings,
    stream,
    wiring,
    workspace,
)
from app.services.realtime import get_hub


@asynccontextmanager
async def lifespan(_: FastAPI):
    import threading

    from app.deps import get_store

    get_store()  # init store + seed demos + register persisted custom strategies into the lab

    def _warm_listings() -> None:
        # Populate the new-listings snapshot so the search box finds fresh IPOs even before the
        # widget is opened. Background thread (one EDGAR call) so it never delays startup.
        try:
            from app.config import get_terminal_settings
            from app.deps import get_edgar_client
            from app.services import listings as ls

            ls.new_listings(get_edgar_client(), get_terminal_settings().data_dir, days=30)
        except Exception:  # noqa: BLE001 - best-effort warm; search still works once the widget runs
            pass

    threading.Thread(target=_warm_listings, daemon=True).start()

    from app.deps import get_runner

    get_runner().start()  # resume armed algos + start the scheduling tick loop

    from app.deps import get_data_refresh_runner

    get_data_refresh_runner().start()  # background lake refresh (bars/news/rates/macro/filings)

    # First-run: if the lake is empty, pull a baseline (dow30 + crypto_majors) so the terminal isn't
    # blank on a fresh install. No-op + non-blocking once bootstrapped or on a populated lake.
    from app.services.bootstrap import maybe_bootstrap

    maybe_bootstrap()
    yield
    await get_data_refresh_runner().stop()  # cancel the refresh loop
    await get_runner().stop()  # cancel the tick loop before tearing down sockets
    await get_hub().close()  # tear down exchange websockets cleanly on shutdown


app = FastAPI(
    title="Open Financial Terminal",
    version="2.0.0",
    description="Local-first, LLM-native financial terminal over the qhfi engine.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_terminal_settings().cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(market.router)
app.include_router(options.router)
app.include_router(board.router)
app.include_router(screener.router)
app.include_router(portfolio.router)
app.include_router(fundamentals.router)
app.include_router(metrics.router)
app.include_router(macro.router)
app.include_router(rates.router)
app.include_router(ficc.router)
app.include_router(filings.router)
app.include_router(listings.router)
app.include_router(assistant.router)
app.include_router(committee.router)
app.include_router(backtest.router)
app.include_router(chart.router)
app.include_router(factor_monitor.router)
app.include_router(lab.router)
app.include_router(market_making.router)
app.include_router(paper.router)
app.include_router(agent.router)
app.include_router(algo.router)
app.include_router(registry.router)
app.include_router(research.router)
app.include_router(risk.router)
app.include_router(sandbox.router)
app.include_router(settings.router)
app.include_router(workspace.router)
app.include_router(stream.router)
app.include_router(intents.router)
app.include_router(data_refresh.router)
app.include_router(wiring.router)


# ── Frontend (SPA) ─────────────────────────────────────────────────────────────────
# Desktop builds run as a single origin: the backend ALSO serves the built Vite bundle so a
# native webview can point straight at http://127.0.0.1:<port>. Because that origin then *is*
# the backend, the SPA's relative `/api` calls and its `location.host` WebSockets resolve with
# no CORS and no URL rewriting. In the two-process dev stack (Vite on :5173) there's no dist, so
# we fall back to the JSON root. The mount goes LAST so all `/api/*` routers + /docs match first.

def _frontend_dist() -> Path | None:
    """Locate the built frontend in both a PyInstaller bundle and the dev source tree."""
    cands = [
        Path(getattr(sys, "_MEIPASS", "")) / "frontend_dist",        # PyInstaller --add-data
        Path(__file__).resolve().parent.parent.parent / "frontend" / "dist",  # repo tree
    ]
    return next((p for p in cands if (p / "index.html").is_file()), None)


_dist = _frontend_dist()
if _dist is not None:
    # html=True serves index.html at "/" (single-page Dockview app — no client router, so no
    # deep-link catch-all needed) and the hashed assets under /assets.
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
else:
    @app.get("/")
    def root() -> dict:
        return {"name": "Open Financial Terminal", "docs": "/docs", "health": "/api/health"}
