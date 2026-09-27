"""
Control Plane Routes — Subsystem Management Stream 2.

PATCH  /v1/console/control-plane/subsystems/<id>/start
PATCH  /v1/console/control-plane/subsystems/<id>/pause
PATCH  /v1/console/control-plane/subsystems/<id>/resume
PATCH  /v1/console/control-plane/subsystems/<id>/stop
GET    /v1/console/control-plane/subsystems/<id>
GET    /v1/console/control-plane/subsystems
GET    /v1/console/control-plane/subsystems/<id>/logs
GET    /v1/console/control-plane/subsystems/audit-log

ADR-2029: User-Centric CorvinOS Control Plane — Stream 2

DEFUSED 2026-09-27 (adversarial review): this router was mounted but served an in-memory SIMULATION: start/pause/resume/stop flipped a dict entry and reported success while no subsystem was touched, ``/logs`` returned a fabricated line with a hard-coded 2026-09-22 timestamp, and the audit log was an in-memory list. It also had no router-level session guard. Every route now requires a console session (CSRF on mutations) and answers 501 ``not_implemented``. The backing module under ``corvin_console/control_plane/`` is kept, unrouted.
"""


from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from corvin_console.deps import require_session_csrf_on_mutation

router = APIRouter(
    prefix="/control-plane/subsystems",
    tags=["control-plane"],
    dependencies=[Depends(require_session_csrf_on_mutation)],
)

_REASON = 'not implemented on this build'


def _not_implemented() -> HTTPException:
    return HTTPException(status_code=501, detail={"status": "not_implemented", "reason": _REASON})


@router.get("/audit-log")
async def get_audit_log() -> Dict[str, Any]:
    raise _not_implemented()


@router.patch("/{subsystem_id}/start")
async def start_subsystem(subsystem_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.patch("/{subsystem_id}/pause")
async def pause_subsystem(subsystem_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.patch("/{subsystem_id}/resume")
async def resume_subsystem(subsystem_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.patch("/{subsystem_id}/stop")
async def stop_subsystem(subsystem_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.get("/{subsystem_id}")
async def get_subsystem(subsystem_id: str) -> Dict[str, Any]:
    raise _not_implemented()


@router.get("")
async def list_subsystems() -> Dict[str, Any]:
    raise _not_implemented()


@router.get("/{subsystem_id}/logs")
async def get_subsystem_logs(subsystem_id: str) -> Dict[str, Any]:
    raise _not_implemented()
