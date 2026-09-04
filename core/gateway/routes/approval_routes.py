"""FastAPI routes for L5 k=2 OperatorApprovalGate — Approval Management API.

GET /v1/approvals/{skill_id} — List pending approvals for a skill
GET /v1/approvals/{approval_id}/status — Get status of a specific approval
POST /v1/approvals/{approval_id}/approve — Operator approves a pending request
POST /v1/approvals/{approval_id}/reject — Operator rejects a pending request
POST /v1/approvals/{approval_id}/revoke — Operator revokes a previously-approved change

All endpoints are audit-logged (fail-closed pattern).
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

approval_router = APIRouter(prefix="/v1/approvals", tags=["approvals"])


# ============================================================================
# Response Models
# ============================================================================


class ApprovalReasonCodeEnum(str, Enum):
    """Scrubbed reason codes (no PII, no raw data)."""
    RANDOM_NOISE = "random_noise"
    CONSISTENT_PATTERN = "consistent_pattern"
    REGIME_SHIFT = "regime_shift"
    UNKNOWN = "unknown"


class ApprovalDecisionEnum(str, Enum):
    """Operator approval outcome."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


class ScrubbedDriftAlertResponse(BaseModel):
    """Scrubbed drift alert (no raw training data)."""
    skill_id: str
    metric_name: str
    magnitude: float = Field(..., description="Absolute value of smoothed delta")
    confidence: float = Field(..., ge=0.0, le=1.0, description="EMA confidence [0.0-1.0]")
    reason_code: ApprovalReasonCodeEnum
    timestamp: str = Field(..., description="ISO 8601 timestamp")


class ApprovalStatusResponse(BaseModel):
    """Status of a single approval record."""
    approval_id: str
    skill_id: str
    metric_name: str
    scrubbed_alert: ScrubbedDriftAlertResponse
    decision: ApprovalDecisionEnum
    operator_id: str
    operator_timestamp: str = Field(..., description="ISO 8601 when operator acted")
    prev_config_hash: str
    next_config_hash: str
    ttl_expires: str = Field(..., description="ISO 8601 when approval expires")
    audit_event_id: str
    revoke_timestamp: Optional[str] = None
    revoke_reason: Optional[str] = None


class PendingApprovalsListResponse(BaseModel):
    """List of pending approvals (optionally filtered by skill_id)."""
    count: int
    approvals: List[ApprovalStatusResponse]


class ApprovalActionRequest(BaseModel):
    """Request to approve/reject/revoke an approval."""
    operator_id: str = Field(..., min_length=3, max_length=50, description="Who is acting (e.g., 'user:alice')")
    reason: Optional[str] = Field(None, max_length=500, description="Optional reason for rejection/revoke")


class ApprovalActionResponse(BaseModel):
    """Response to approval action."""
    success: bool
    message: str
    approval_id: str
    decision: Optional[ApprovalDecisionEnum] = None


# ============================================================================
# Gateway Singleton (Lazy-Loaded)
# ============================================================================

# Lazy seam: imported on first use (avoid circular imports)
_approval_gate = None


def _get_approval_gate():
    """Get OperatorApprovalGate singleton from app context."""
    global _approval_gate
    if _approval_gate is None:
        # In production, this is wired from app.state.approval_gate during app startup
        # For now, return None to be safe
        logger.warning("[Approval Routes] OperatorApprovalGate not initialized in app state")
        return None
    return _approval_gate


def set_approval_gate(gate):
    """Wire OperatorApprovalGate into routes (called from app.py startup)."""
    global _approval_gate
    _approval_gate = gate
    logger.info("[Approval Routes] OperatorApprovalGate initialized")


# ============================================================================
# Routes
# ============================================================================


@approval_router.get("/{skill_id}", response_model=PendingApprovalsListResponse)
async def list_pending_approvals(
    skill_id: str,
    tenant_id: str = Query("_default", description="Tenant ID (default: _default)"),
) -> PendingApprovalsListResponse:
    """
    List pending approvals for a skill.

    GET /v1/approvals/{skill_id}?tenant_id=_default

    Returns:
        List of OperatorApprovalRecord objects (pending/approved/rejected/revoked)

    Audit:
        Logged to audit backend with event_type=approval_list_queried
    """
    gate = _get_approval_gate()
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OperatorApprovalGate not initialized",
        )

    # Verify tenant match (fail-closed)
    if gate.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant mismatch: {tenant_id} != {gate.tenant_id}",
        )

    try:
        # Get pending approvals for this skill
        pending = gate.get_pending_approvals(skill_id=skill_id)

        # Convert to response format
        approvals = [_approval_record_to_response(r) for r in pending]

        # Audit: log query (non-blocking)
        try:
            gate.audit_backend.write_event({
                "tenant_id": tenant_id,
                "event_type": "approval_list_queried",
                "skill_id": skill_id,
                "count": len(approvals),
            })
        except Exception as e:
            logger.warning(f"[Approval Routes] Audit failed for list query: {e}")

        return PendingApprovalsListResponse(
            count=len(approvals),
            approvals=approvals,
        )
    except Exception as e:
        logger.error(f"[Approval Routes] Failed to list approvals for {skill_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@approval_router.get("/{skill_id}/{approval_id}/status", response_model=ApprovalStatusResponse)
async def get_approval_status(
    skill_id: str,
    approval_id: str,
    tenant_id: str = Query("_default", description="Tenant ID (default: _default)"),
) -> ApprovalStatusResponse:
    """
    Get status of a specific approval.

    GET /v1/approvals/{skill_id}/{approval_id}/status?tenant_id=_default

    Returns:
        OperatorApprovalRecord with full decision history

    Audit:
        Logged with event_type=approval_status_queried
    """
    gate = _get_approval_gate()
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OperatorApprovalGate not initialized",
        )

    # Verify tenant match
    if gate.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant mismatch: {tenant_id} != {gate.tenant_id}",
        )

    try:
        record = gate.get_approval_status(approval_id)

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Approval {approval_id} not found",
            )

        # Verify skill_id matches (prevent unrelated queries)
        if record.scrubbed_alert.skill_id != skill_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Approval {approval_id} does not match skill {skill_id}",
            )

        # Audit: log query
        try:
            gate.audit_backend.write_event({
                "tenant_id": tenant_id,
                "event_type": "approval_status_queried",
                "approval_id": approval_id,
                "skill_id": skill_id,
                "decision": record.decision.value,
            })
        except Exception as e:
            logger.warning(f"[Approval Routes] Audit failed for status query: {e}")

        return _approval_record_to_response(record)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Approval Routes] Failed to get status for {approval_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@approval_router.post("/{skill_id}/{approval_id}/approve", response_model=ApprovalActionResponse)
async def approve_request(
    skill_id: str,
    approval_id: str,
    request: ApprovalActionRequest,
    tenant_id: str = Query("_default", description="Tenant ID (default: _default)"),
) -> ApprovalActionResponse:
    """
    Operator approves a pending approval request.

    POST /v1/approvals/{skill_id}/{approval_id}/approve
    {
        "operator_id": "user:alice"
    }

    Returns:
        Success/failure status

    Audit:
        Logged with event_type=skill_approval_granted
    """
    gate = _get_approval_gate()
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OperatorApprovalGate not initialized",
        )

    # Verify tenant match
    if gate.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant mismatch: {tenant_id} != {gate.tenant_id}",
        )

    try:
        # Validate operator_id format
        if not request.operator_id or len(request.operator_id) < 3:
            raise ValueError("operator_id must be at least 3 characters")

        # Request approval from gate (audit-logged by gate)
        success = gate.operator_approve(
            approval_id=approval_id,
            operator_id=request.operator_id,
        )

        if not success:
            # Not found or expired
            record = gate.get_approval_status(approval_id)
            if record is None:
                return ApprovalActionResponse(
                    success=False,
                    message="Approval not found or expired",
                    approval_id=approval_id,
                )
            else:
                return ApprovalActionResponse(
                    success=False,
                    message=f"Approval is {record.decision.value}, cannot approve",
                    approval_id=approval_id,
                )

        # Get updated record
        record = gate.get_approval_status(approval_id)

        return ApprovalActionResponse(
            success=True,
            message="Approval granted",
            approval_id=approval_id,
            decision=ApprovalDecisionEnum(record.decision.value) if record else None,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"[Approval Routes] Failed to approve {approval_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@approval_router.post("/{skill_id}/{approval_id}/reject", response_model=ApprovalActionResponse)
async def reject_request(
    skill_id: str,
    approval_id: str,
    request: ApprovalActionRequest,
    tenant_id: str = Query("_default", description="Tenant ID (default: _default)"),
) -> ApprovalActionResponse:
    """
    Operator rejects a pending approval request.

    POST /v1/approvals/{skill_id}/{approval_id}/reject
    {
        "operator_id": "user:alice",
        "reason": "Magnitude too high"
    }

    Returns:
        Success/failure status

    Audit:
        Logged with event_type=skill_approval_denied
    """
    gate = _get_approval_gate()
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OperatorApprovalGate not initialized",
        )

    # Verify tenant match
    if gate.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant mismatch: {tenant_id} != {gate.tenant_id}",
        )

    try:
        # Validate operator_id format
        if not request.operator_id or len(request.operator_id) < 3:
            raise ValueError("operator_id must be at least 3 characters")

        # Request rejection from gate (audit-logged by gate)
        success = gate.operator_reject(
            approval_id=approval_id,
            operator_id=request.operator_id,
            reason=request.reason or "",
        )

        if not success:
            # Not found
            return ApprovalActionResponse(
                success=False,
                message="Approval not found",
                approval_id=approval_id,
            )

        record = gate.get_approval_status(approval_id)

        return ApprovalActionResponse(
            success=True,
            message="Approval rejected",
            approval_id=approval_id,
            decision=ApprovalDecisionEnum(record.decision.value) if record else None,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"[Approval Routes] Failed to reject {approval_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@approval_router.post("/{skill_id}/{approval_id}/revoke", response_model=ApprovalActionResponse)
async def revoke_approval(
    skill_id: str,
    approval_id: str,
    request: ApprovalActionRequest,
    tenant_id: str = Query("_default", description="Tenant ID (default: _default)"),
) -> ApprovalActionResponse:
    """
    Operator revokes a previously-approved change.

    POST /v1/approvals/{skill_id}/{approval_id}/revoke
    {
        "operator_id": "user:alice",
        "reason": "Caused latency regression"
    }

    Returns:
        Success/failure status

    Audit:
        Logged with event_type=skill_approval_revoked
    """
    gate = _get_approval_gate()
    if gate is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OperatorApprovalGate not initialized",
        )

    # Verify tenant match
    if gate.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant mismatch: {tenant_id} != {gate.tenant_id}",
        )

    try:
        # Validate operator_id format
        if not request.operator_id or len(request.operator_id) < 3:
            raise ValueError("operator_id must be at least 3 characters")

        # Request revoke from gate (audit-logged by gate)
        success = gate.operator_revoke(
            approval_id=approval_id,
            operator_id=request.operator_id,
            reason=request.reason or "",
        )

        if not success:
            # Not found or not currently approved
            return ApprovalActionResponse(
                success=False,
                message="Approval not found or not in approved state",
                approval_id=approval_id,
            )

        record = gate.get_approval_status(approval_id)

        # Trigger optimizer callback for rollback if available
        if hasattr(gate, '_optimizer_with_gate') and gate._optimizer_with_gate:
            try:
                gate._optimizer_with_gate.handle_revoke(approval_id)
            except Exception as e:
                logger.error(f"[Approval Routes] Optimizer revoke callback failed: {e}")

        return ApprovalActionResponse(
            success=True,
            message="Approval revoked",
            approval_id=approval_id,
            decision=ApprovalDecisionEnum(record.decision.value) if record else None,
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"[Approval Routes] Failed to revoke {approval_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# ============================================================================
# Helper Functions
# ============================================================================

def _approval_record_to_response(record) -> ApprovalStatusResponse:
    """Convert OperatorApprovalRecord to HTTP response."""
    return ApprovalStatusResponse(
        approval_id=record.approval_id,
        skill_id=record.scrubbed_alert.skill_id,
        metric_name=record.scrubbed_alert.metric_name,
        scrubbed_alert=ScrubbedDriftAlertResponse(
            skill_id=record.scrubbed_alert.skill_id,
            metric_name=record.scrubbed_alert.metric_name,
            magnitude=record.scrubbed_alert.magnitude,
            confidence=record.scrubbed_alert.confidence,
            reason_code=ApprovalReasonCodeEnum(record.scrubbed_alert.reason_code.value),
            timestamp=record.scrubbed_alert.timestamp,
        ),
        decision=ApprovalDecisionEnum(record.decision.value),
        operator_id=record.operator_id,
        operator_timestamp=record.operator_timestamp,
        prev_config_hash=record.prev_config_hash,
        next_config_hash=record.next_config_hash,
        ttl_expires=record.ttl_expires,
        audit_event_id=record.audit_event_id,
        revoke_timestamp=record.revoke_timestamp,
        revoke_reason=record.revoke_reason,
    )
