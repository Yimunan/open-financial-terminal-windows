"""Named workspaces: persisted Dockview layout JSON (terminal-owned state)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import get_store
from app.store import TerminalStore

router = APIRouter(prefix="/api", tags=["workspace"])


class WorkspaceIn(BaseModel):
    layout: dict


@router.get("/workspaces")
def list_workspaces(store: TerminalStore = Depends(get_store)) -> dict:
    return {"workspaces": store.list_workspaces()}


@router.get("/workspaces/{name}")
def get_workspace(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    ws = store.get_workspace(name)
    if ws is None:
        raise HTTPException(404, f"no workspace named '{name}'")
    return ws


@router.put("/workspaces/{name}")
def save_workspace(name: str, body: WorkspaceIn, store: TerminalStore = Depends(get_store)) -> dict:
    store.save_workspace(name, body.layout)
    return {"ok": True, "name": name}


@router.delete("/workspaces/{name}")
def delete_workspace(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_workspace(name)
    return {"ok": True}


# ── workspace templates (reusable layout snapshots) ─────────────────────────────
@router.get("/templates")
def list_templates(store: TerminalStore = Depends(get_store)) -> dict:
    return {"templates": store.list_templates()}


@router.get("/templates/{name}")
def get_template(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    tpl = store.get_template(name)
    if tpl is None:
        raise HTTPException(404, f"no template named '{name}'")
    return tpl


@router.put("/templates/{name}")
def save_template(name: str, body: WorkspaceIn, store: TerminalStore = Depends(get_store)) -> dict:
    store.save_template(name, body.layout)
    return {"ok": True, "name": name}


@router.delete("/templates/{name}")
def delete_template(name: str, store: TerminalStore = Depends(get_store)) -> dict:
    store.remove_template(name)
    return {"ok": True}
