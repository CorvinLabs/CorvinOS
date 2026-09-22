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

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from corvin_console.control_plane.subsystem_manager import SubsystemManager, SubsystemState

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
    tenant_id: str = Query(default="default")
) -> SubsystemOperationResponse:
    """
    Start a subsystem.

    Args:
        subsystem_id: Subsystem to start
        tenant_id: Tenant scope

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    result = await manager.start_subsystem(
        subsystem_id=subsystem_id,
        tenant_id=tenant_id,
        operator_id="console-user"
    )

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
    tenant_id: str = Query(default="default")
) -> SubsystemOperationResponse:
    """
    Pause a subsystem (graceful shutdown, 30s timeout).

    Args:
        subsystem_id: Subsystem to pause
        req: Request with timeout_s
        tenant_id: Tenant scope

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    result = await manager.pause_subsystem(
        subsystem_id=subsystem_id,
        timeout_s=req.timeout_s or 30,
        tenant_id=tenant_id,
        operator_id="console-user"
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SubsystemOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.patch("/{subsystem_id}/resume")
async def resume_subsystem(
    subsystem_id: str,
    tenant_id: str = Query(default="default")
) -> SubsystemOperationResponse:
    """
    Resume a paused subsystem.

    Args:
        subsystem_id: Subsystem to resume
        tenant_id: Tenant scope

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    result = await manager.resume_subsystem(
        subsystem_id=subsystem_id,
        tenant_id=tenant_id,
        operator_id="console-user"
    )

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
    tenant_id: str = Query(default="default")
) -> SubsystemOperationResponse:
    """
    Stop a subsystem (graceful or force).

    Args:
        subsystem_id: Subsystem to stop
        req: Request with force and timeout_s
        tenant_id: Tenant scope

    Returns:
        Operation result
    """
    manager = get_subsystem_manager()
    result = await manager.stop_subsystem(
        subsystem_id=subsystem_id,
        force=req.force or False,
        timeout_s=req.timeout_s or 30,
        tenant_id=tenant_id,
        operator_id="console-user"
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SubsystemOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.get("/{subsystem_id}")
async def get_subsystem_status(
    subsystem_id: str,
    tenant_id: str = Query(default="default")
) -> SubsystemStatusResponse:
    """
    Get subsystem status.

    Args:
        subsystem_id: Subsystem to get
        tenant_id: Tenant scope

    Returns:
        Subsystem status
    """
    manager = get_subsystem_manager()
    status = await manager.get_subsystem_status(subsystem_id)

    if status is None:
        raise HTTPException(status_code=404, detail=f"Subsystem {subsystem_id} not found")

    return SubsystemStatusResponse(**status)


@router.get("")
async def list_subsystems(
    tenant_id: str = Query(default="default")
) -> List[SubsystemStatusResponse]:
    """
    List all subsystems.

    Args:
        tenant_id: Tenant scope

    Returns:
        List of subsystem statuses
    """
    manager = get_subsystem_manager()
    subsystems = await manager.list_subsystems()

    return [SubsystemStatusResponse(**sub) for sub in subsystems]


@router.get("/{subsystem_id}/logs")
async def get_subsystem_logs(
    subsystem_id: str,
    lines: int = Query(default=100, ge=1, le=1000),
    tenant_id: str = Query(default="default")
) -> Dict[str, Any]:
    """
    Get subsystem logs (last N lines).

    Args:
        subsystem_id: Subsystem to get logs for
        lines: Number of lines to return
        tenant_id: Tenant scope

    Returns:
        Log lines
    """
    manager = get_subsystem_manager()
    status = await manager.get_subsystem_status(subsystem_id)

    if status is None:
        raise HTTPException(status_code=404, detail=f"Subsystem {subsystem_id} not found")

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
    tenant_id: str = Query(default="default")
) -> Dict[str, Any]:
    """
    Get subsystem audit trail (read-only).

    Args:
        tenant_id: Tenant scope

    Returns:
        Audit events
    """
    manager = get_subsystem_manager()
    audit_log = manager.get_audit_log()

    # Filter by tenant_id
    filtered = [evt for evt in audit_log if evt.get("tenant_id") == tenant_id]

    return {
        "tenant_id": tenant_id,
        "events": filtered,
        "count": len(filtered)
    }
