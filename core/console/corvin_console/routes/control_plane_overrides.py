"""
Control Plane Routes — Override Authority Stream 3.

GET    /v1/console/control-plane/approvals
GET    /v1/console/control-plane/approvals/<id>
POST   /v1/console/control-plane/approvals/<id>/approve
POST   /v1/console/control-plane/approvals/<id>/deny
POST   /v1/console/control-plane/approvals/<id>/interrupt
GET    /v1/console/control-plane/approvals/audit-log

ADR-2029: User-Centric CorvinOS Control Plane — Stream 3
"""

from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from datetime import datetime

from core.control_plane.override_authority import OverrideAuthority, OverrideType, OverrideRequest

router = APIRouter(
    prefix="/v1/console/control-plane/approvals",
    tags=["control-plane-approvals"]
)

# Singleton override authority
_override_authority: Optional[OverrideAuthority] = None


def get_override_authority() -> OverrideAuthority:
    """Get or create singleton override authority."""
    global _override_authority
    if _override_authority is None:
        # In production, inject real audit_backend
        _override_authority = OverrideAuthority(audit_backend=None)
    return _override_authority


class ApprovalRequestModel(BaseModel):
    """Request to create an approval."""
    override_type: str  # force_enable, force_disable, emergency_stop, bypass_gate, force_restart
    target_id: str
    reason: str


class ApprovalDecisionModel(BaseModel):
    """Decision to approve/deny."""
    reason: str


class ApprovalResponseModel(BaseModel):
    """Approval response."""
    status: str  # success, error, forbidden
    message: str
    override_id: Optional[str] = None


class ApprovalDetailModel(BaseModel):
    """Approval details."""
    override_id: str
    override_type: str
    target_id: str
    reason: str
    requestor_id: str
    approval_status: str  # pending, approved, rejected, expired
    created_at: str
    approved_at: Optional[str] = None
    approver_id: Optional[str] = None


@router.post("", response_model=ApprovalResponseModel)
async def create_approval_request(
    req: ApprovalRequestModel,
    tenant_id: str = Query(default="default")
) -> ApprovalResponseModel:
    """
    Request an override (with justification).

    Args:
        req: Override request
        tenant_id: Tenant scope

    Returns:
        Approval request status
    """
    authority = get_override_authority()

    # Validate override type
    valid_types = {"force_enable", "force_disable", "emergency_stop", "bypass_gate", "force_restart"}
    if req.override_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid override_type: {req.override_type}")

    # Request override
    result = await authority.request_override(
        override_type=OverrideType(req.override_type),
        target_id=req.target_id,
        reason=req.reason,
        requestor_id="console-user",
        tenant_id=tenant_id
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return ApprovalResponseModel(
        status=result["status"],
        message=result["message"],
        override_id=result.get("override_id")
    )


@router.get("", response_model=List[ApprovalDetailModel])
async def list_pending_approvals(
    status_filter: Optional[str] = Query(default=None),
    tenant_id: str = Query(default="default")
) -> List[ApprovalDetailModel]:
    """
    List pending approvals.

    Args:
        status_filter: Filter by status (pending, approved, rejected, expired)
        tenant_id: Tenant scope

    Returns:
        List of approvals
    """
    authority = get_override_authority()
    approvals = await authority.list_pending_approvals(tenant_id=tenant_id)

    if status_filter:
        approvals = [a for a in approvals if a["approval_status"] == status_filter]

    return [ApprovalDetailModel(**a) for a in approvals]


@router.get("/{override_id}", response_model=ApprovalDetailModel)
async def get_approval_detail(
    override_id: str,
    tenant_id: str = Query(default="default")
) -> ApprovalDetailModel:
    """
    Get approval details.

    Args:
        override_id: Override ID
        tenant_id: Tenant scope

    Returns:
        Approval details
    """
    authority = get_override_authority()
    approval = await authority.get_approval_detail(override_id, tenant_id=tenant_id)

    if approval is None:
        raise HTTPException(status_code=404, detail=f"Approval {override_id} not found")

    return ApprovalDetailModel(**approval)


@router.post("/{override_id}/approve", response_model=ApprovalResponseModel)
async def approve_override(
    override_id: str,
    decision: ApprovalDecisionModel,
    tenant_id: str = Query(default="default")
) -> ApprovalResponseModel:
    """
    Approve an override (admin only).

    Args:
        override_id: Override to approve
        decision: Approval decision (reason required)
        tenant_id: Tenant scope

    Returns:
        Approval status
    """
    authority = get_override_authority()

    # Check approver permissions (in Phase 9b.3, admin-only)
    approver_id = "console-admin"  # In production, extract from auth context
    is_admin = True  # In production, check role

    if not is_admin:
        raise HTTPException(status_code=403, detail="Only admins can approve overrides")

    result = await authority.approve_override(
        override_id=override_id,
        approver_id=approver_id,
        reason=decision.reason,
        tenant_id=tenant_id
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return ApprovalResponseModel(
        status=result["status"],
        message=result["message"]
    )


@router.post("/{override_id}/deny", response_model=ApprovalResponseModel)
async def deny_override(
    override_id: str,
    decision: ApprovalDecisionModel,
    tenant_id: str = Query(default="default")
) -> ApprovalResponseModel:
    """
    Deny an override (admin only).

    Args:
        override_id: Override to deny
        decision: Denial reason
        tenant_id: Tenant scope

    Returns:
        Denial status
    """
    authority = get_override_authority()

    # Check approver permissions
    approver_id = "console-admin"  # In production, extract from auth context
    is_admin = True  # In production, check role

    if not is_admin:
        raise HTTPException(status_code=403, detail="Only admins can deny overrides")

    result = await authority.deny_override(
        override_id=override_id,
        approver_id=approver_id,
        reason=decision.reason,
        tenant_id=tenant_id
    )

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return ApprovalResponseModel(
        status=result["status"],
        message=result["message"]
    )


@router.post("/{override_id}/interrupt", response_model=ApprovalResponseModel)
async def interrupt_override(
    override_id: str,
    tenant_id: str = Query(default="default")
) -> ApprovalResponseModel:
    """
    Interrupt a pending operation.

    Args:
        override_id: Override to interrupt
        tenant_id: Tenant scope

    Returns:
        Interrupt status
    """
    authority = get_override_authority()
    result = await authority.interrupt_override(override_id, tenant_id=tenant_id)

    if result["status"] == "error":
        raise HTTPException(status_code=400, detail=result["message"])

    return ApprovalResponseModel(
        status=result["status"],
        message=result["message"]
    )


@router.get("/audit-log", tags=["audit"])
async def get_approval_audit_log(
    tenant_id: str = Query(default="default")
) -> Dict[str, Any]:
    """
    Get approval audit trail (read-only).

    Args:
        tenant_id: Tenant scope

    Returns:
        Audit events
    """
    authority = get_override_authority()
    audit_log = await authority.get_audit_log(tenant_id=tenant_id)

    return {
        "tenant_id": tenant_id,
        "events": audit_log,
        "count": len(audit_log)
    }
