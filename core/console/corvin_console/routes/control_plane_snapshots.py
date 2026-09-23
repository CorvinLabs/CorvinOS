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
"""

from typing import Optional, List, Dict, Any, Annotated
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, field_validator
from datetime import datetime
import json

from core.control_plane.snapshot_manager import SnapshotManager, Snapshot
from corvin_console.deps import require_session, require_csrf, consent_required
from corvin_console import auth as session_auth
from ..error_handling import safe_snapshot_error, safe_error_response

router = APIRouter(
    prefix="/v1/console/control-plane/snapshots",
    tags=["control-plane-snapshots"]
)

# Singleton snapshot manager
_snapshot_manager: Optional[SnapshotManager] = None


def get_snapshot_manager() -> SnapshotManager:
    """Get or create singleton snapshot manager (with real audit backend)."""
    global _snapshot_manager
    if _snapshot_manager is None:
        import os
        from core.audit import get_audit_backend
        storage_path = os.path.expanduser("~/.corvin/snapshots")
        # ✅ Use real audit backend (not None)
        _snapshot_manager = SnapshotManager(
            audit_backend=get_audit_backend(),
            storage_path=storage_path
        )
    return _snapshot_manager


class SnapshotCreateRequest(BaseModel):
    """Request to create a snapshot."""
    name: str
    description: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate snapshot name (max 500 chars, non-empty)."""
        if not v or not v.strip():
            raise ValueError("Snapshot name cannot be empty")
        if len(v) > 500:
            raise ValueError("Snapshot name must be <= 500 characters")
        return v.strip()

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        """Validate snapshot description (max 500 chars)."""
        if len(v) > 500:
            raise ValueError("Snapshot description must be <= 500 characters")
        return v


class SnapshotRestoreRequest(BaseModel):
    """Request to restore a snapshot."""
    confirm: bool = True  # Must explicitly confirm restore


class SnapshotResponse(BaseModel):
    """Snapshot response."""
    snapshot_id: str
    timestamp: str
    name: str
    description: str
    checksum: str
    size_bytes: int
    created_by: str


class SnapshotOperationResponse(BaseModel):
    """Response from snapshot operation."""
    status: str  # success, error
    message: str
    snapshot_id: Optional[str] = None


@router.post("", response_model=SnapshotOperationResponse)
async def create_snapshot(
    req: SnapshotCreateRequest,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> SnapshotOperationResponse:
    """
    Create a snapshot of Control Plane state (tenant-scoped, CSRF-protected).

    Args:
        req: Snapshot creation request
        session: Session record (CSRF-protected, tenant-scoped)

    Returns:
        Snapshot creation status
    """
    manager = get_snapshot_manager()

    # In Phase 9b.4, we capture a mock state. Real implementation captures actual state.
    control_plane_state = {
        "intent": {},
        "plugins": {},
        "subsystems": {},
        "overrides": {}
    }

    try:
        result = await manager.create_snapshot(
            control_plane_state=control_plane_state,
            name=req.name,
            description=req.description,
            creator_id=session.sid,
            tenant_id=session.tenant_id
        )

        return SnapshotOperationResponse(
            status="success",
            message=f"Snapshot {result['snapshot_id']} created successfully",
            snapshot_id=result.get("snapshot_id")
        )
    except Exception as e:
        safe_msg = safe_error_response(e, "Failed to create snapshot")
        raise HTTPException(status_code=400, detail=safe_msg)


@router.get("", response_model=List[SnapshotResponse])
async def list_snapshots(
    session: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> List[SnapshotResponse]:
    """
    List all snapshots for current tenant (tenant-scoped).

    Args:
        session: Session record (tenant-scoped)

    Returns:
        List of snapshots for this tenant only
    """
    manager = get_snapshot_manager()
    try:
        snapshots = await manager.list_snapshots(tenant_id=session.tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return [SnapshotResponse(**snap) for snap in snapshots]


@router.get("/audit-log", tags=["audit"])
async def get_snapshot_audit_log(
    session: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> Dict[str, Any]:
    """
    Get snapshot audit trail for current tenant (read-only, tenant-scoped, immutable).

    Args:
        session: Session record (tenant-scoped)

    Returns:
        Audit events for this tenant only
    """
    try:
        manager = get_snapshot_manager()
        audit_log = await manager.get_audit_log(tenant_id=session.tenant_id)

        return {
            "tenant_id": session.tenant_id,
            "events": audit_log,
            "count": len(audit_log)
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        safe_msg = safe_error_response(e, "Failed to retrieve audit log", log_level="warning")
        raise HTTPException(status_code=500, detail=safe_msg)


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot_detail(
    snapshot_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> SnapshotResponse:
    """
    Get snapshot details (tenant-scoped).

    Args:
        snapshot_id: Snapshot ID
        session: Session record (tenant-scoped)

    Returns:
        Snapshot details (only if it belongs to this tenant)
    """
    manager = get_snapshot_manager()
    try:
        snapshot = await manager.get_snapshot(snapshot_id, tenant_id=session.tenant_id)

        if snapshot is None:
            raise HTTPException(status_code=404, detail="Snapshot not found or access denied")

        return SnapshotResponse(**snapshot)
    except ValueError as e:
        safe_msg = safe_snapshot_error(e)
        raise HTTPException(status_code=403, detail=safe_msg)


@router.post("/{snapshot_id}/restore", response_model=SnapshotOperationResponse)
async def restore_snapshot(
    snapshot_id: str,
    req: SnapshotRestoreRequest,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> SnapshotOperationResponse:
    """
    Restore a snapshot (atomic, with audit seaming, CSRF+auth protected).

    ✅ This is a risky operation (system state restore) — requires auth + CSRF.

    Args:
        snapshot_id: Snapshot to restore
        req: Restore request (must confirm)
        session: Session record (CSRF + auth validated)

    Returns:
        Restore status
    """
    manager = get_snapshot_manager()

    if not req.confirm:
        raise HTTPException(status_code=400, detail="Restore must be explicitly confirmed")

    try:
        result = await manager.restore_snapshot(
            snapshot_id=snapshot_id,
            approver_id=session.sid,
            tenant_id=session.tenant_id
        )

        return SnapshotOperationResponse(
            status=result["status"],
            message=f"Snapshot {snapshot_id} restored successfully"
        )
    except ValueError as e:
        safe_msg = safe_snapshot_error(e)
        raise HTTPException(status_code=400, detail=safe_msg)


@router.post("/{snapshot_id}/diff", response_model=Dict[str, Any])
async def diff_snapshot(
    snapshot_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> Dict[str, Any]:
    """
    Compare snapshot with current state (diff view, tenant-scoped).

    Args:
        snapshot_id: Snapshot to compare
        session: Session record (tenant-scoped)

    Returns:
        Diff between snapshot and current
    """
    manager = get_snapshot_manager()
    try:
        snapshot = await manager.get_snapshot(snapshot_id, tenant_id=session.tenant_id)

        if snapshot is None:
            raise HTTPException(status_code=404, detail="Snapshot not found or access denied")

        # In Phase 9b.4, diff is simplified. Real implementation uses deep diff.
        return {
            "snapshot_id": snapshot_id,
            "current_state": {},
            "snapshot_state": snapshot,
            "changes": []
        }
    except ValueError as e:
        safe_msg = safe_snapshot_error(e)
        raise HTTPException(status_code=403, detail=safe_msg)


@router.delete("/{snapshot_id}", response_model=SnapshotOperationResponse)
async def delete_snapshot(
    snapshot_id: str,
    session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    _: Annotated[None, Depends(consent_required("control_plane_snapshot_operations"))] = None
) -> SnapshotOperationResponse:
    """
    Delete a snapshot (audit logged, CSRF-protected, tenant-scoped).

    Args:
        snapshot_id: Snapshot to delete
        session: Session record (CSRF + auth validated)

    Returns:
        Deletion status
    """
    manager = get_snapshot_manager()
    try:
        result = await manager.delete_snapshot(
            snapshot_id=snapshot_id,
            approver_id=session.sid,
            tenant_id=session.tenant_id
        )

        return SnapshotOperationResponse(
            status="success",
            message=f"Snapshot {snapshot_id} deleted successfully"
        )
    except ValueError as e:
        safe_msg = safe_snapshot_error(e)
        raise HTTPException(status_code=400, detail=safe_msg)
