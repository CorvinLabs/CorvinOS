"""
Control Plane Routes — Snapshots & Rollback Stream 4.

POST   /v1/console/control-plane/snapshots
GET    /v1/console/control-plane/snapshots
GET    /v1/console/control-plane/snapshots/<id>
POST   /v1/console/control-plane/snapshots/<id>/restore
POST   /v1/console/control-plane/snapshots/<id>/diff
DELETE /v1/console/control-plane/snapshots/<id>
GET    /v1/console/control-plane/snapshots/audit-log

ADR-2029: User-Centric CorvinOS Control Plane — Stream 4

DEFUSED 2026-09-27 (adversarial review): this router was mounted but served snapshot capture recorded a hard-coded EMPTY system state, and restore reported success while restoring nothing. It also had no router-level session guard. Every route now requires a console session (CSRF on mutations) and answers 501 ``not_implemented``. The backing module under ``corvin_console/control_plane/`` is kept, unrouted.
"""


from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from corvin_console.deps import require_session_csrf_on_mutation

router = APIRouter(
    prefix="/control-plane/snapshots",
    tags=["control-plane"],
    dependencies=[Depends(require_session_csrf_on_mutation)],
)

_REASON = 'not implemented on this build'


def _not_implemented() -> HTTPException:
    return HTTPException(status_code=501, detail={"status": "not_implemented", "reason": _REASON})


@router.post("")
async def create_snapshot() -> Dict[str, Any]:
    raise _not_implemented()


@router.get("")
async def list_snapshots() -> Dict[str, Any]:
    raise _not_implemented()


@router.get("/audit-log")
async def get_audit_log() -> Dict[str, Any]:
    raise _not_implemented()


@router.get("/{snapshot_id}")
async def get_snapshot(snapshot_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.post("/{snapshot_id}/restore")
async def restore_snapshot(snapshot_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.post("/{snapshot_id}/diff")
async def diff_snapshots(snapshot_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.delete("/{snapshot_id}")
async def delete_snapshot(snapshot_id: str) -> Dict[str, Any]:
    raise _not_implemented()
