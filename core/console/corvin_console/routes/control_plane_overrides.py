from core.security.csrf import require_csrf
"""Control Plane Routes — Override Authority (Phase 9b Stream 3, ADR-2029).

Provides REST endpoints for operator override requests and approvals:

    POST   /v1/console/control-plane/overrides
    GET    /v1/console/control-plane/overrides
    GET    /v1/console/control-plane/overrides/{id}
    POST   /v1/console/control-plane/overrides/{id}/approve
    POST   /v1/console/control-plane/overrides/{id}/deny
    POST   /v1/console/control-plane/overrides/{id}/interrupt
    GET    /v1/console/control-plane/overrides/audit

Override Authority — operator-initiated overrides with admin approval gates,
full audit trail, tenant isolation, and TTL-based expiration.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette import status as http_status

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session, consent_required
from ..error_handling import safe_error_response, safe_override_error
from core.control_plane.override_authority import (
    OverrideAuthority,
    OverrideType,
    PermissionError,
)

router = APIRouter(prefix="/v1/console/control-plane/overrides", tags=["control-plane"])

# Singleton instance — in production, inject via dependency
_authority: Optional[OverrideAuthority] = None


def get_authority() -> OverrideAuthority:
    """Get or initialize singleton OverrideAuthority (with core audit chain).

    Wires to the immutable core audit chain (ADR-0232/0233).
    """
    global _authority
    if _authority is None:
        # ✅ Initialize with real core audit chain writer
        # (tenant_id defaults to "_default", can be overridden per request)
        _authority = OverrideAuthority(tenant_id="_default")
    return _authority


# Request/Response models
class OverrideRequestModel(BaseModel):
    """Request to create an override."""

    override_type: str  # force_enable, force_disable, emergency_stop, bypass_gate, force_restart
    target_id: str
    reason: str


class ApprovalDecisionModel(BaseModel):
    """Decision to approve/deny."""

    reason: str


class OverrideDetailModel(BaseModel):
    """Override detail."""

    override_id: str
    override_type: str
    target_id: str
    reason: str
    requestor_id: str
    approval_status: str
    created_at: str
    approved_at: Optional[str] = None
    approver_id: Optional[str] = None


@require_csrf
@router.post("")
async def create_override(
    body: OverrideRequestModel,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """Create an override request.

    Args:
        body: Override request (override_type, target_id, reason)
        rec: Authenticated session record

    Returns:
        Created override details with ID and status
    """
    authority = get_authority()

    # Validate override type
    try:
        override_type = OverrideType(body.override_type)
    except ValueError:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid override_type: {body.override_type}",
        )

    try:
        result = await authority.request_override(
            override_type=override_type,
            target_id=body.target_id,
            reason=body.reason,
            requestor_id=rec.sid,
            tenant_id=rec.tenant_id,
        )
    except ValueError as exc:
        safe_msg = safe_error_response(exc, "Failed to create override request")
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=safe_msg)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.request",
        target_kind="override",
        target_id=result["override_id"],
    )

    return result


@router.get("")
async def list_overrides(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """List pending overrides for tenant.

    Args:
        rec: Authenticated session record

    Returns:
        List of pending overrides
    """
    authority = get_authority()

    pending = authority.list_pending_approvals(tenant_id=rec.tenant_id)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.list",
        target_kind="system",
        target_id="overrides",
    )

    return {"overrides": pending, "count": len(pending)}


@router.get("/{override_id}")
async def get_override_detail(
    override_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """Get override details.

    Args:
        override_id: Override ID
        rec: Authenticated session record

    Returns:
        Override detail
    """
    authority = get_authority()

    try:
        detail = authority.get_approval_detail(override_id, rec.tenant_id)
    except ValueError as exc:
        safe_msg = safe_error_response(exc, "Override request not found or access denied")
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail=safe_msg)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.view",
        target_kind="override",
        target_id=override_id,
    )

    return detail


@require_csrf
@router.post("/{override_id}/approve")
async def approve_override(
    override_id: str,
    body: ApprovalDecisionModel,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """Approve an override (admin only, authorization checked).

    Args:
        override_id: Override ID to approve
        body: Approval decision
        rec: Authenticated session record

    Returns:
        Approval status
    """
    authority = get_authority()

    # ✅ CRITICAL FIX: Check approver authority BEFORE attempting approval
    # Never unconditionally add_approver — that's privilege escalation!
    if not authority.is_approver(rec.sid):
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="override.approve_unauthorized",
            target_kind="override",
            target_id=override_id,
        )
        raise HTTPException(
            http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can approve overrides"
        )

    try:
        result = await authority.approve_override(override_id, rec.sid, rec.tenant_id)
    except PermissionError as exc:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="override.approve_denied",
            target_kind="override",
            target_id=override_id,
        )
        safe_msg = safe_error_response(exc, "Not authorized to approve this override")
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail=safe_msg)
    except ValueError as exc:
        safe_msg = safe_error_response(exc, "Failed to approve override")
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=safe_msg)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.approved",
        target_kind="override",
        target_id=override_id,
    )

    return result


@require_csrf
@router.post("/{override_id}/deny")
async def deny_override(
    override_id: str,
    body: ApprovalDecisionModel,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """Deny an override (admin only, authorization checked).

    Args:
        override_id: Override ID to deny
        body: Denial reason
        rec: Authenticated session record

    Returns:
        Denial status
    """
    authority = get_authority()

    # ✅ CRITICAL FIX: Check approver authority BEFORE attempting denial
    # Never unconditionally add_approver — that's privilege escalation!
    if not authority.is_approver(rec.sid):
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="override.deny_unauthorized",
            target_kind="override",
            target_id=override_id,
        )
        raise HTTPException(
            http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can deny overrides"
        )

    try:
        result = await authority.deny_override(
            override_id, rec.sid, body.reason, rec.tenant_id
        )
    except PermissionError as exc:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="override.deny_denied",
            target_kind="override",
            target_id=override_id,
        )
        safe_msg = safe_error_response(exc, "Not authorized to deny this override")
        raise HTTPException(http_status.HTTP_403_FORBIDDEN, detail=safe_msg)
    except ValueError as exc:
        safe_msg = safe_error_response(exc, "Failed to deny override")
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=safe_msg)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.denied",
        target_kind="override",
        target_id=override_id,
    )

    return result


@require_csrf
@router.post("/{override_id}/interrupt")
async def interrupt_override(
    override_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """Interrupt/cancel a pending override.

    Args:
        override_id: Override ID to interrupt
        rec: Authenticated session record

    Returns:
        Interruption status
    """
    authority = get_authority()

    try:
        result = await authority.interrupt_override(override_id, rec.tenant_id)
    except ValueError as exc:
        safe_msg = safe_error_response(exc, "Failed to interrupt override")
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, detail=safe_msg)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.interrupted",
        target_kind="override",
        target_id=override_id,
    )

    return result


@router.get("/audit")
async def get_audit_log(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = ...,
    _: Annotated[None, Depends(consent_required("control_plane_override_operations"))] = None,
) -> dict[str, Any]:
    """Get override audit trail (read-only).

    Args:
        rec: Authenticated session record

    Returns:
        Audit events for tenant
    """
    authority = get_authority()

    events = await authority.get_audit_log(rec.tenant_id)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="override.audit_view",
        target_kind="system",
        target_id="override_audit",
    )

    return {
        "tenant_id": rec.tenant_id,
        "events": events,
        "count": len(events),
    }
