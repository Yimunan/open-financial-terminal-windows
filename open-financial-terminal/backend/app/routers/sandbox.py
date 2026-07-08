"""Sandbox endpoints — run + save module code (factor/strategy/portfolio) in sandboxed or
trusted mode. See `services/sandbox.py` for the trust model.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from qhfi.data.manager import DataManager

from app.deps import get_data_manager, get_store
from app.services import sandbox as sb
from app.store import TerminalStore

router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


class RunIn(BaseModel):
    mode: str           # factor | strategy | portfolio
    trust: str = "sandboxed"  # sandboxed | trusted
    code: str = ""
    context: dict = {}


class SaveIn(BaseModel):
    mode: str
    trust: str = "sandboxed"
    name: str
    code: str = ""
    meta: dict = {}
    allocations: list[dict] | None = None


@router.get("/templates")
def templates() -> dict:
    return sb.templates()


@router.post("/run")
def run(
    body: RunIn,
    dm: DataManager = Depends(get_data_manager),
    store: TerminalStore = Depends(get_store),
) -> dict:
    deps = SimpleNamespace(dm=dm, store=store)
    try:
        return sb.run(deps, mode=body.mode, trust=body.trust, code=body.code, context=body.context)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:  # noqa: BLE001 - surface any run error to the editor, never 500
        raise HTTPException(400, f"{type(e).__name__}: {e}") from None


@router.post("/save")
def save(body: SaveIn, store: TerminalStore = Depends(get_store)) -> dict:
    deps = SimpleNamespace(dm=None, store=store)
    try:
        return sb.save(
            deps, mode=body.mode, trust=body.trust, name=body.name,
            code=body.code, meta=body.meta, allocations=body.allocations,
        )
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(400, str(e)) from None
