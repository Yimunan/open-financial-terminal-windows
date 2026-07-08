"""Background data-refresh controls — status, per-job config, and a manual trigger.

Nested under ``/api/settings`` to match the existing market-data settings group; surfaced in the
Settings → Data Refresh panel. The runner itself lives in :mod:`app.services.data_refresh`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import get_data_refresh_runner

router = APIRouter(prefix="/api/settings/data-refresh", tags=["data-refresh"])


class JobConfigIn(BaseModel):
    enabled: bool | None = None
    interval_minutes: float | None = None


class RefreshConfigIn(BaseModel):
    master_enabled: bool | None = None
    market_hours_only: bool | None = None
    jobs: dict[str, JobConfigIn] | None = None


@router.get("/status")
def status(runner=Depends(get_data_refresh_runner)) -> dict:
    return runner.status()


@router.put("/config")
def config(body: RefreshConfigIn, runner=Depends(get_data_refresh_runner)) -> dict:
    if body.master_enabled is not None:
        runner.set_master_enabled(body.master_enabled)
    if body.market_hours_only is not None:
        runner.set_market_hours_only(body.market_hours_only)
    for name, jc in (body.jobs or {}).items():
        try:
            if jc.enabled is not None:
                runner.set_enabled(name, jc.enabled)
            if jc.interval_minutes is not None:
                runner.set_interval_s(name, int(round(jc.interval_minutes * 60)))
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
    return runner.status()


@router.post("/{job}/run")
def run_now(job: str, runner=Depends(get_data_refresh_runner)) -> dict:
    """Trigger one job immediately. Sync route → runs in the threadpool, off the event loop."""
    try:
        return runner.trigger_now(job)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
