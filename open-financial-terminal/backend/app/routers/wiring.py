"""Module wiring introspection.

Enumerates the live FastAPI routes and their router tags so the front-end Map widget can *scan*
the backend it's actually talking to — reconciling its (hand-declared) data layer against what's
really mounted: assigning each route its true service (router tag), flagging stale references, and
surfacing backend modules nothing is wired to. Pure reflection over `app.routes`; no drift.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.routing import APIRoute, APIWebSocketRoute

router = APIRouter(prefix="/api", tags=["wiring"])


@router.get("/wiring")
async def wiring(request: Request) -> dict:
    routes: list[dict] = []
    service_counts: dict[str, int] = {}

    for r in request.app.routes:
        if isinstance(r, APIRoute):
            kind = "rest"
            methods = sorted(m for m in (r.methods or set()) if m not in ("HEAD", "OPTIONS"))
            tags = [str(t) for t in (r.tags or [])]
        elif isinstance(r, APIWebSocketRoute):
            kind = "ws"
            methods = ["WS"]
            tags = [str(t) for t in (getattr(r, "tags", None) or [])]
        else:
            continue  # mounts, static, etc. — not part of the API surface

        path = getattr(r, "path", "")
        if not path.startswith("/api"):
            continue

        routes.append({
            "path": path,
            "methods": methods,
            "tags": tags,
            "kind": kind,
            "name": getattr(r, "name", ""),
        })
        for t in (tags or ["untagged"]):
            service_counts[t] = service_counts.get(t, 0) + 1

    services = [{"tag": t, "count": c} for t, c in sorted(service_counts.items())]
    return {"routes": routes, "services": services}
