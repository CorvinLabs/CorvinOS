"""Read-only run API for every Forge (ADR-2253 D4): list, detail, events.

Session-gated and tenant-bound: a run belongs to the tenant that started it, and any other tenant (or a guessed
id) gets 404. There are no mutating routes. The record is shown through an ALLOWLIST (``forge_runs.PUBLIC_RUN_FIELDS``):
the resume payload - which holds the request and generated drafts - never leaves the store.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth as session_auth
from .. import forge_runs
from ..deps import require_session

router = APIRouter(tags=["forge-runs"])

_KINDS = ("skill", "tool", "plugin", "layer")
_STATUSES = ("running", "success", "failed")


def _check_filter(value: Optional[str], allowed: tuple, name: str) -> Optional[str]:
    if value is not None and value not in allowed:
        raise HTTPException(status_code=422, detail=f"{name} must be one of {list(allowed)}")
    return value


@router.get("/runs")
async def list_runs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    kind: Annotated[Optional[str], Query()] = None,
    status: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Dict[str, Any]:
    runs = forge_runs.list_runs(rec.tenant_id, kind=_check_filter(kind, _KINDS, "kind"),
                                status=_check_filter(status, _STATUSES, "status"), limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> Dict[str, Any]:
    run = forge_runs.peek_run(run_id, rec.tenant_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return forge_runs.public_view(run_id, run)


@router.get("/runs/{run_id}/events")
async def get_run_events(
    run_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> Dict[str, Any]:
    if forge_runs.peek_run(run_id, rec.tenant_id) is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    events = forge_runs.read_events(rec.tenant_id, run_id, after=after, limit=limit)
    return {"run_id": run_id, "events": events, "next_after": events[-1]["seq"] if events else after}
