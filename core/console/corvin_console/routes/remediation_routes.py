"""
Phase 5: Console API Routes for Remediation Management (FastAPI)

REST API endpoints for:
- Listing pending approval requests
- Approving/rejecting remediations
- Viewing remediation history

Every route needs a console session; approve/reject additionally need the CSRF
token and an owner/admin tier. The deciding operator is taken from the session
(``console:<sid_fingerprint>``), never from the request body — a body-supplied
``approved_by`` let any caller approve a remediation under any name.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Annotated, Optional, List
import logging

from .. import auth as session_auth
from ..deps import require_csrf, require_session

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
    prefix='/remediation',  # mounted under /v1/console by app.router
    tags=['remediation']
)


# Request/Response models
class ApprovalDecisionRequest(BaseModel):
    """Request payload for approval/rejection. The decider comes from the session."""
    reason: Optional[str] = None


Session = Annotated[session_auth.SessionRecord, Depends(require_session)]
Mutation = Annotated[session_auth.SessionRecord, Depends(require_csrf)]


def _decider(rec: session_auth.SessionRecord) -> str:
    if rec.tier not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="owner or admin required")
    return f"console:{rec.sid_fingerprint}"


class RemediationResponse(BaseModel):
    """Generic remediation response"""
    request_id: str
    state: str
    timestamp: str


@remediation_router.get('/pending')
async def list_pending_approvals(rec: Session):
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
async def approve_remediation(request_id: str, data: ApprovalDecisionRequest, rec: Mutation):
    """Operator approved a remediation request (decider = the session's operator)"""
    decider = _decider(rec)
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    approval_gate = get_approval_gate()
    if request_id not in approval_gate.pending_requests:
        raise HTTPException(
            status_code=404,
            detail=f"Approval request {request_id} not found"
        )
    try:
        req = approval_gate.approve_request(request_id, decider, data.reason)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception(f"Error on remediation approve: {e}")
        raise HTTPException(status_code=500, detail="remediation decision failed")

    return {
        "request_id": request_id,
        "state": req.state.value,
        "decided_by": decider,
        "decision_at": req.decision_at,
        "timestamp": datetime.utcnow().isoformat(),
    }


@remediation_router.post('/reject/{request_id}')
async def reject_remediation(request_id: str, data: ApprovalDecisionRequest, rec: Mutation):
    """Operator rejected a remediation request (decider = the session's operator)"""
    decider = _decider(rec)
    if not get_approval_gate:
        raise HTTPException(
            status_code=503,
            detail="Remediation module not available"
        )

    approval_gate = get_approval_gate()
    if request_id not in approval_gate.pending_requests:
        raise HTTPException(
            status_code=404,
            detail=f"Approval request {request_id} not found"
        )
    try:
        req = approval_gate.reject_request(request_id, decider, data.reason)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.exception(f"Error on remediation reject: {e}")
        raise HTTPException(status_code=500, detail="remediation decision failed")

    return {
        "request_id": request_id,
        "state": req.state.value,
        "decided_by": decider,
        "decision_at": req.decision_at,
        "timestamp": datetime.utcnow().isoformat(),
    }


@remediation_router.get('/history')
async def get_remediation_history(rec: Session, limit: int = 100, offset: int = 0):
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
async def get_remediation_status(request_id: str, rec: Session):
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
