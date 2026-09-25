"""
Phase 5: Console API Routes for Remediation Management (FastAPI)

REST API endpoints for:
- Listing pending approval requests
- Approving/rejecting remediations
- Viewing remediation history
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
import logging

# Import remediation components (Phase 5)
try:
    from core.remediation.approval_workflow import get_approval_gate
    from core.remediation.orchestrator import RemediationOrchestrator
except ImportError:
    get_approval_gate = None
    RemediationOrchestrator = None


logger = logging.getLogger(__name__)

# Create FastAPI router (compatible with console app)
remediation_router = APIRouter(
    prefix='/v1/console/remediation',
    tags=['remediation']
)


# Request/Response models
class ApprovalDecisionRequest(BaseModel):
    """Request payload for approval/rejection"""
    approved_by: str
    reason: Optional[str] = None


class RemediationResponse(BaseModel):
    """Generic remediation response"""
    request_id: str
    state: str
    timestamp: str


@remediation_router.get('/pending')
async def list_pending_approvals():
    """List all pending approval requests"""
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    try:
        approval_gate = get_approval_gate()
        pending_requests = [
            req for req in approval_gate.pending_requests.values()
            if req.state.value == "pending"
        ]

        return {
            "pending_approvals": [
                {
                    "request_id": req.request_id,
                    "drift_id": req.drift_id,
                    "drift_type": req.drift_type,
                    "instance_id": req.instance_id,
                    "state": req.state.value,
                    "requested_at": req.requested_at,
                    "expires_at": req.expires_at,
                }
                for req in pending_requests
            ],
            "count": len(pending_requests),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.exception(f"Error listing pending approvals: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@remediation_router.post('/approve/{request_id}')
async def approve_remediation(request_id: str, data: ApprovalDecisionRequest):
    """Operator approves a remediation request"""
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    try:
        approval_gate = get_approval_gate()
        if request_id not in approval_gate.pending_requests:
            raise HTTPException(
                status_code=404,
                detail=f"Approval request {request_id} not found"
            )

        req = approval_gate.pending_requests[request_id]
        req.state.value = "approved"
        req.approved_by = data.approved_by
        req.approval_reason = data.reason

        return {
            "request_id": request_id,
            "state": "approved",
            "decision_at": datetime.utcnow().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error approving remediation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@remediation_router.post('/reject/{request_id}')
async def reject_remediation(request_id: str, data: ApprovalDecisionRequest):
    """Operator rejects a remediation request"""
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    try:
        approval_gate = get_approval_gate()
        if request_id not in approval_gate.pending_requests:
            raise HTTPException(
                status_code=404,
                detail=f"Approval request {request_id} not found"
            )

        req = approval_gate.pending_requests[request_id]
        req.state.value = "rejected"
        req.rejected_by = data.approved_by
        req.rejection_reason = data.reason

        return {
            "request_id": request_id,
            "state": "rejected",
            "decision_at": datetime.utcnow().isoformat(),
            "timestamp": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error rejecting remediation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@remediation_router.get('/history')
async def get_remediation_history(limit: int = 100, offset: int = 0):
    """Get remediation history (paginated)"""
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    try:
        approval_gate = get_approval_gate()
        all_requests = list(approval_gate.pending_requests.values())

        # Pagination
        total = len(all_requests)
        paginated = all_requests[offset:offset + limit]

        return {
            "history": [
                {
                    "request_id": req.request_id,
                    "drift_type": req.drift_type,
                    "instance_id": req.instance_id,
                    "state": req.state.value,
                    "requested_at": req.requested_at,
                    "expires_at": req.expires_at,
                }
                for req in paginated
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        logger.exception(f"Error fetching remediation history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@remediation_router.get('/status/{request_id}')
async def get_remediation_status(request_id: str):
    """Check status of a specific remediation request"""
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    try:
        approval_gate = get_approval_gate()
        if request_id not in approval_gate.pending_requests:
            raise HTTPException(
                status_code=404,
                detail=f"Approval request {request_id} not found"
            )

        req = approval_gate.pending_requests[request_id]
        return {
            "request_id": request_id,
            "state": req.state.value,
            "drift_type": req.drift_type,
            "instance_id": req.instance_id,
            "requested_at": req.requested_at,
            "expires_at": req.expires_at,
            "timestamp": datetime.utcnow().isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error fetching remediation status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
