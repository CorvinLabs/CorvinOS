"""
Phase 5: Console API Routes for Remediation Management

REST API endpoints for:
- Listing pending approval requests
- Approving/rejecting remediations
- Viewing remediation history
"""

from flask import Blueprint, request, jsonify
from datetime import datetime
from typing import Optional
import logging

# Import remediation components (Phase 5)
try:
    from core.remediation.approval_workflow import get_approval_gate
    from core.remediation.orchestrator import RemediationOrchestrator
except ImportError:
    get_approval_gate = None
    RemediationOrchestrator = None


logger = logging.getLogger(__name__)

# Create Flask blueprint
remediation_bp = Blueprint(
    'remediation',
    __name__,
    url_prefix='/v1/console/remediation'
)


class RemediationAPIError(Exception):
    """Custom exception for remediation API errors"""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


@remediation_bp.route('/pending', methods=['GET'])
def list_pending_approvals():
    """
    GET /v1/console/remediation/pending

    List all pending approval requests.

    Returns:
        {
            "pending_approvals": [
                {
                    "request_id": "apr-...",
                    "drift_id": "drift-...",
                    "drift_type": "CODE_VERSION_DRIFT",
                    "instance_id": "prod-01",
                    "risk_assessment": {...},
                    "state": "pending",
                    "requested_at": "2026-09-26T...",
                    "expires_at": "2026-09-27T...",
                }
            ],
            "count": 1,
            "timestamp": "2026-09-26T..."
        }
    """
    if not get_approval_gate:
        return jsonify({
            "error": "Remediation module not available",
            "timestamp": datetime.utcnow().isoformat()
        }), 503

    try:
        approval_gate = get_approval_gate()
        pending_requests = [
            req for req in approval_gate.pending_requests.values()
            if req.state.value == "pending"
        ]

        return jsonify({
            "pending_approvals": [
                {
                    "request_id": req.request_id,
                    "drift_id": req.drift_id,
                    "drift_type": req.drift_type,
                    "instance_id": req.instance_id,
                    "risk_assessment": req.risk_assessment,
                    "state": req.state.value,
                    "requested_at": req.requested_at,
                    "expires_at": req.expires_at,
                    "requested_by": req.requested_by,
                }
                for req in pending_requests
            ],
            "count": len(pending_requests),
            "timestamp": datetime.utcnow().isoformat(),
        }), 200

    except Exception as e:
        logger.exception(f"Error listing pending approvals: {e}")
        return jsonify({
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500


@remediation_bp.route('/approve/<request_id>', methods=['POST'])
def approve_remediation(request_id: str):
    """
    POST /v1/console/remediation/approve/<request_id>

    Operator approves a remediation request.

    Payload:
        {
            "approved_by": "user@example.com",
            "reason": "Looks good, proceeding" (optional)
        }

    Returns:
        {
            "request_id": "apr-...",
            "state": "approved",
            "decision_at": "2026-09-26T...",
            "timestamp": "2026-09-26T..."
        }
    """
    if not get_approval_gate:
        return jsonify({
            "error": "Remediation module not available",
            "timestamp": datetime.utcnow().isoformat()
        }), 503

    try:
        data = request.get_json() or {}
        approved_by = data.get("approved_by")
        reason = data.get("reason")

        if not approved_by:
            return jsonify({
                "error": "Missing 'approved_by' field",
                "timestamp": datetime.utcnow().isoformat()
            }), 400

        approval_gate = get_approval_gate()
        approved_request = approval_gate.approve_request(
            request_id=request_id,
            approved_by=approved_by,
            reason=reason
        )

        return jsonify({
            "request_id": approved_request.request_id,
            "drift_id": approved_request.drift_id,
            "drift_type": approved_request.drift_type,
            "state": approved_request.state.value,
            "approved_by": approved_request.approved_by,
            "decision_at": approved_request.decision_at,
            "decision_reason": approved_request.decision_reason,
            "timestamp": datetime.utcnow().isoformat(),
        }), 200

    except Exception as e:
        logger.exception(f"Error approving remediation: {e}")
        return jsonify({
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500


@remediation_bp.route('/reject/<request_id>', methods=['POST'])
def reject_remediation(request_id: str):
    """
    POST /v1/console/remediation/reject/<request_id>

    Operator rejects a remediation request.

    Payload:
        {
            "rejected_by": "user@example.com",
            "reason": "Not the right time" (optional)
        }

    Returns:
        {
            "request_id": "apr-...",
            "state": "rejected",
            "decision_at": "2026-09-26T...",
            "timestamp": "2026-09-26T..."
        }
    """
    if not get_approval_gate:
        return jsonify({
            "error": "Remediation module not available",
            "timestamp": datetime.utcnow().isoformat()
        }), 503

    try:
        data = request.get_json() or {}
        rejected_by = data.get("rejected_by")
        reason = data.get("reason")

        if not rejected_by:
            return jsonify({
                "error": "Missing 'rejected_by' field",
                "timestamp": datetime.utcnow().isoformat()
            }), 400

        approval_gate = get_approval_gate()
        rejected_request = approval_gate.reject_request(
            request_id=request_id,
            rejected_by=rejected_by,
            reason=reason
        )

        return jsonify({
            "request_id": rejected_request.request_id,
            "drift_id": rejected_request.drift_id,
            "drift_type": rejected_request.drift_type,
            "state": rejected_request.state.value,
            "rejected_by": rejected_request.rejected_by,
            "decision_at": rejected_request.decision_at,
            "decision_reason": rejected_request.decision_reason,
            "timestamp": datetime.utcnow().isoformat(),
        }), 200

    except Exception as e:
        logger.exception(f"Error rejecting remediation: {e}")
        return jsonify({
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500


@remediation_bp.route('/history', methods=['GET'])
def remediation_history():
    """
    GET /v1/console/remediation/history?limit=50&offset=0

    Retrieve remediation audit trail.

    Query Parameters:
        limit: Max number of records (default: 50)
        offset: Pagination offset (default: 0)
        state: Filter by state (e.g., "approved", "rejected")
        drift_type: Filter by drift type

    Returns:
        {
            "history": [
                {
                    "request_id": "apr-...",
                    "drift_id": "drift-...",
                    "drift_type": "CODE_VERSION_DRIFT",
                    "state": "approved",
                    "requested_at": "2026-09-26T...",
                    "decision_at": "2026-09-26T...",
                    "approved_by": "user@example.com",
                    "decision_reason": "Approved"
                }
            ],
            "total": 100,
            "limit": 50,
            "offset": 0,
            "timestamp": "2026-09-26T..."
        }
    """
    if not get_approval_gate:
        return jsonify({
            "error": "Remediation module not available",
            "timestamp": datetime.utcnow().isoformat()
        }), 503

    try:
        limit = request.args.get('limit', default=50, type=int)
        offset = request.args.get('offset', default=0, type=int)
        state_filter = request.args.get('state')
        drift_type_filter = request.args.get('drift_type')

        approval_gate = get_approval_gate()
        all_requests = list(approval_gate.pending_requests.values())

        # Filter by state (if provided)
        if state_filter:
            all_requests = [
                req for req in all_requests
                if req.state.value == state_filter
            ]

        # Filter by drift_type (if provided)
        if drift_type_filter:
            all_requests = [
                req for req in all_requests
                if req.drift_type == drift_type_filter
            ]

        # Sort by requested_at (newest first)
        all_requests.sort(
            key=lambda r: r.requested_at,
            reverse=True
        )

        # Paginate
        total = len(all_requests)
        paginated = all_requests[offset:offset + limit]

        return jsonify({
            "history": [
                {
                    "request_id": req.request_id,
                    "drift_id": req.drift_id,
                    "drift_type": req.drift_type,
                    "instance_id": req.instance_id,
                    "state": req.state.value,
                    "requested_at": req.requested_at,
                    "requested_by": req.requested_by,
                    "decision_at": req.decision_at,
                    "approved_by": req.approved_by,
                    "rejected_by": req.rejected_by,
                    "decision_reason": req.decision_reason,
                }
                for req in paginated
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
            "timestamp": datetime.utcnow().isoformat(),
        }), 200

    except Exception as e:
        logger.exception(f"Error retrieving remediation history: {e}")
        return jsonify({
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500


@remediation_bp.route('/status/<request_id>', methods=['GET'])
def remediation_status(request_id: str):
    """
    GET /v1/console/remediation/status/<request_id>

    Get status of a specific remediation request.

    Returns:
        {
            "request_id": "apr-...",
            "drift_id": "drift-...",
            "drift_type": "CODE_VERSION_DRIFT",
            "state": "pending|approved|rejected|expired",
            "requested_at": "2026-09-26T...",
            "expires_at": "2026-09-27T...",
            "decision_at": "2026-09-26T..." (if decided),
            "timestamp": "2026-09-26T..."
        }
    """
    if not get_approval_gate:
        return jsonify({
            "error": "Remediation module not available",
            "timestamp": datetime.utcnow().isoformat()
        }), 503

    try:
        approval_gate = get_approval_gate()
        request_obj = approval_gate._get_request(request_id)

        if not request_obj:
            return jsonify({
                "error": f"Request not found: {request_id}",
                "timestamp": datetime.utcnow().isoformat()
            }), 404

        return jsonify({
            "request_id": request_obj.request_id,
            "drift_id": request_obj.drift_id,
            "drift_type": request_obj.drift_type,
            "instance_id": request_obj.instance_id,
            "state": request_obj.state.value,
            "requested_at": request_obj.requested_at,
            "requested_by": request_obj.requested_by,
            "expires_at": request_obj.expires_at,
            "decision_at": request_obj.decision_at,
            "approved_by": request_obj.approved_by,
            "rejected_by": request_obj.rejected_by,
            "decision_reason": request_obj.decision_reason,
            "timestamp": datetime.utcnow().isoformat(),
        }), 200

    except Exception as e:
        logger.exception(f"Error retrieving remediation status: {e}")
        return jsonify({
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }), 500


@remediation_bp.errorhandler(400)
def bad_request(error):
    """Handle 400 Bad Request"""
    return jsonify({
        "error": "Bad request",
        "details": str(error),
        "timestamp": datetime.utcnow().isoformat()
    }), 400


@remediation_bp.errorhandler(404)
def not_found(error):
    """Handle 404 Not Found"""
    return jsonify({
        "error": "Not found",
        "details": str(error),
        "timestamp": datetime.utcnow().isoformat()
    }), 404


@remediation_bp.errorhandler(500)
def internal_error(error):
    """Handle 500 Internal Server Error"""
    logger.exception(f"Internal server error: {error}")
    return jsonify({
        "error": "Internal server error",
        "timestamp": datetime.utcnow().isoformat()
    }), 500
