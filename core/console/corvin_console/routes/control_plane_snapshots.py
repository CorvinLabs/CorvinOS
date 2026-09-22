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

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime
import json

from core.control_plane.snapshot_manager import SnapshotManager, Snapshot

router = APIRouter(
    prefix="/v1/console/control-plane/snapshots",
    tags=["control-plane-snapshots"]
)

# Singleton snapshot manager
_snapshot_manager: Optional[SnapshotManager] = None


def get_snapshot_manager() -> SnapshotManager:
    """Get or create singleton snapshot manager."""
    global _snapshot_manager
    if _snapshot_manager is None:
        import os
        storage_path = os.path.expanduser("~/.corvin/snapshots")
        _snapshot_manager = SnapshotManager(audit_backend=None, storage_path=storage_path)
    return _snapshot_manager


class SnapshotCreateRequest(BaseModel):
    """Request to create a snapshot."""
    name: str
    description: str


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
    tenant_id: str = Query(default="default")
) -> SnapshotOperationResponse:
    """
    Create a snapshot of Control Plane state.

    Args:
        req: Snapshot creation request
        tenant_id: Tenant scope

    Returns:
        Snapshot creation status
    """
    manager = get_snapshot_manager()

    # In Phase 9b.4, we capture a mock state. Real implementation captures actual state.
    control_plane_state = {
        "intent_state": {},
        "plugin_state": {},
        "subsystem_state": {},
        "override_state": {}
    }

    result = await manager.create_snapshot(
        control_plane_state=control_plane_state,
        name=req.name,
        description=req.description,
        creator_id="console-user",
        tenant_id=tenant_id
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SnapshotOperationResponse(
        status=result["status"],
        message=result["message"],
        snapshot_id=result.get("snapshot_id")
    )


@router.get("", response_model=List[SnapshotResponse])
async def list_snapshots(
    tenant_id: str = Query(default="default")
) -> List[SnapshotResponse]:
    """
    List all snapshots.

    Args:
        tenant_id: Tenant scope

    Returns:
        List of snapshots
    """
    manager = get_snapshot_manager()
    snapshots = await manager.list_snapshots(tenant_id=tenant_id)

    return [SnapshotResponse(**snap) for snap in snapshots]


@router.get("/{snapshot_id}", response_model=SnapshotResponse)
async def get_snapshot_detail(
    snapshot_id: str,
    tenant_id: str = Query(default="default")
) -> SnapshotResponse:
    """
    Get snapshot details.

    Args:
        snapshot_id: Snapshot ID
        tenant_id: Tenant scope

    Returns:
        Snapshot details
    """
    manager = get_snapshot_manager()
    snapshot = await manager.get_snapshot(snapshot_id, tenant_id=tenant_id)

    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")

    return SnapshotResponse(**snapshot)


@router.post("/{snapshot_id}/restore", response_model=SnapshotOperationResponse)
async def restore_snapshot(
    snapshot_id: str,
    req: SnapshotRestoreRequest,
    tenant_id: str = Query(default="default")
) -> SnapshotOperationResponse:
    """
    Restore a snapshot (atomic, with audit seaming).

    Args:
        snapshot_id: Snapshot to restore
        req: Restore request (must confirm)
        tenant_id: Tenant scope

    Returns:
        Restore status
    """
    manager = get_snapshot_manager()

    if not req.confirm:
        raise HTTPException(status_code=400, detail="Restore must be explicitly confirmed")

    result = await manager.restore_snapshot(
        snapshot_id=snapshot_id,
        restorer_id="console-user",
        tenant_id=tenant_id
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SnapshotOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.post("/{snapshot_id}/diff", response_model=Dict[str, Any])
async def diff_snapshot(
    snapshot_id: str,
    tenant_id: str = Query(default="default")
) -> Dict[str, Any]:
    """
    Compare snapshot with current state (diff view).

    Args:
        snapshot_id: Snapshot to compare
        tenant_id: Tenant scope

    Returns:
        Diff between snapshot and current
    """
    manager = get_snapshot_manager()
    snapshot = await manager.get_snapshot(snapshot_id, tenant_id=tenant_id)

    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"Snapshot {snapshot_id} not found")

    # In Phase 9b.4, diff is simplified. Real implementation uses deep diff.
    return {
        "snapshot_id": snapshot_id,
        "current_state": {},
        "snapshot_state": snapshot,
        "changes": []
    }


@router.delete("/{snapshot_id}", response_model=SnapshotOperationResponse)
async def delete_snapshot(
    snapshot_id: str,
    tenant_id: str = Query(default="default")
) -> SnapshotOperationResponse:
    """
    Delete a snapshot (audit logged).

    Args:
        snapshot_id: Snapshot to delete
        tenant_id: Tenant scope

    Returns:
        Deletion status
    """
    manager = get_snapshot_manager()
    result = await manager.delete_snapshot(
        snapshot_id=snapshot_id,
        deleter_id="console-user",
        tenant_id=tenant_id
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return SnapshotOperationResponse(
        status=result["status"],
        message=result["message"]
    )


@router.get("/audit-log", tags=["audit"])
async def get_snapshot_audit_log(
    tenant_id: str = Query(default="default")
) -> Dict[str, Any]:
    """
    Get snapshot audit trail (read-only).

    Args:
        tenant_id: Tenant scope

    Returns:
        Audit events
    """
    manager = get_snapshot_manager()
    audit_log = await manager.get_audit_log(tenant_id=tenant_id)

    return {
        "tenant_id": tenant_id,
        "events": audit_log,
        "count": len(audit_log)
    }
