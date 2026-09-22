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
"""

from typing import Optional, List, Dict, Any, Annotated
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from corvin_console.control_plane.subsystem_manager import SubsystemManager, SubsystemState
from corvin_console.deps import require_session, require_csrf
from corvin_console import auth as session_auth

router = APIRouter(
    prefix="/v1/console/control-plane/subsystems",
    tags=["control-plane-subsystems"]
)

# Singleton subsystem manager
_subsystem_manager: Optional[SubsystemManager] = None


def get_subsystem_manager() -> SubsystemManager:
    """Get or create singleton subsystem manager."""
    global _subsystem_manager
    if _subsystem_manager is None:
        _subsystem_manager = SubsystemManager()
    return _subsystem_manager


class SubsystemOperationRequest(BaseModel):
    """Request to control a subsystem."""
    force: Optional[bool] = False  # For stop operations
    timeout_s: Optional[int] = 30  # Graceful shutdown timeout


class SubsystemOperationResponse(BaseModel):
    """Response from subsystem operation."""
    status: str  # success, warning, error
    message: str


class SubsystemStatusResponse(BaseModel):
    """Subsystem status response."""
    subsystem_id: str
    state: str  # running, paused, stopped
    started_at: Optional[str]
    paused_at: Optional[str]
    stopped_at: Optional[str]


@router.patch("/{subsystem_id}/start")
async def start_subsystem(
    subsystem_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)]
) -> SubsystemOperationResponse:
    """
    Start a subsystem (tenant-scoped, CSRF-protected).

    Args:
        subsystem_id: Subsystem to start
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    try:
        result = await manager.start_subsystem(
            subsystem_id=subsystem_id,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SubsystemOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.patch("/{subsystem_id}/pause")
async def pause_subsystem(
    subsystem_id: str,
    req: SubsystemOperationRequest,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)]
) -> SubsystemOperationResponse:
    """
    Pause a subsystem (tenant-scoped, CSRF-protected, graceful shutdown with timeout bounds).

    Args:
        subsystem_id: Subsystem to pause
        req: Request with timeout_s (1-3600 seconds, fail-closed)
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    try:
        # Use provided timeout or default to 30
        timeout_s = req.timeout_s or 30
        result = await manager.pause_subsystem(
            subsystem_id=subsystem_id,
            timeout_s=timeout_s,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SubsystemOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.patch("/{subsystem_id}/resume")
async def resume_subsystem(
    subsystem_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)]
) -> SubsystemOperationResponse:
    """
    Resume a paused subsystem (tenant-scoped, CSRF-protected).

    Args:
        subsystem_id: Subsystem to resume
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    try:
        result = await manager.resume_subsystem(
            subsystem_id=subsystem_id,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SubsystemOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.patch("/{subsystem_id}/stop")
async def stop_subsystem(
    subsystem_id: str,
    req: SubsystemOperationRequest,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)]
) -> SubsystemOperationResponse:
    """
    Stop a subsystem (tenant-scoped, CSRF-protected, graceful or force).

    Args:
        subsystem_id: Subsystem to stop
        req: Request with force and timeout_s (1-3600 seconds if graceful, fail-closed)
        session: Session record (extracted from CSRF-protected cookie)

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    try:
        # Use provided timeout or default to 30
        timeout_s = req.timeout_s or 30
        result = await manager.stop_subsystem(
            subsystem_id=subsystem_id,
            force=req.force or False,
            timeout_s=timeout_s,
            tenant_id=session.tenant_id,
            operator_id=session.sid
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SubsystemOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.get("/{subsystem_id}")
async def get_subsystem_status(
    subsystem_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_session)]
) -> SubsystemStatusResponse:
    """
    Get subsystem status (tenant-scoped).

    Args:
        subsystem_id: Subsystem to get
        session: Session record (extracted from session cookie)

    Returns:
        Subsystem status (only if it belongs to this tenant)
    """
    manager = get_subsystem_manager()
    try:
        status = await manager.get_subsystem_status(
            subsystem_id=subsystem_id,
            tenant_id=session.tenant_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if status is None:
        raise HTTPException(status_code=404, detail=f"Subsystem {subsystem_id} not found for tenant {session.tenant_id}")

    return SubsystemStatusResponse(**status)


@router.get("")
async def list_subsystems(
    session: Annotated[session_auth.SessionRecord, Depends(require_session)]
) -> List[SubsystemStatusResponse]:
    """
    List all subsystems for the current tenant (tenant-scoped).

    Args:
        session: Session record (extracted from session cookie)

    Returns:
        List of subsystem statuses for this tenant only
    """
    manager = get_subsystem_manager()
    try:
        subsystems = await manager.list_subsystems(tenant_id=session.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return [SubsystemStatusResponse(**sub) for sub in subsystems]


@router.get("/{subsystem_id}/logs")
async def get_subsystem_logs(
    subsystem_id: str,
    lines: int = Query(default=100, ge=1, le=1000),
    session: Annotated[session_auth.SessionRecord, Depends(require_session)] = None
) -> Dict[str, Any]:
    """
    Get subsystem logs (last N lines, tenant-scoped).

    Args:
        subsystem_id: Subsystem to get logs for
        lines: Number of lines to return
        session: Session record (extracted from session cookie)

    Returns:
        Log lines (only if subsystem belongs to this tenant)
    """
    manager = get_subsystem_manager()
    try:
        status = await manager.get_subsystem_status(
            subsystem_id=subsystem_id,
            tenant_id=session.tenant_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if status is None:
        raise HTTPException(status_code=404, detail=f"Subsystem {subsystem_id} not found for tenant {session.tenant_id}")

    # In Phase 9b.2, logs are mock. Real implementation connects to actual subsystem output.
    return {
        "subsystem_id": subsystem_id,
        "lines": lines,
        "logs": [
            f"[2026-09-22T12:34:56Z] Subsystem {subsystem_id} is {status['state']}"
        ]
    }


@router.get("/audit-log", tags=["audit"])
async def get_subsystem_audit_log(
    session: Annotated[session_auth.SessionRecord, Depends(require_session)]
) -> Dict[str, Any]:
    """
    Get subsystem audit trail for a tenant (read-only, immutable, tenant-scoped).

    Args:
        session: Session record (extracted from session cookie)

    Returns:
        Audit events for this tenant only
    """
    manager = get_subsystem_manager()
    try:
        audit_log = manager.get_audit_log(tenant_id=session.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "tenant_id": session.tenant_id,
        "events": audit_log,
        "count": len(audit_log)
    }
