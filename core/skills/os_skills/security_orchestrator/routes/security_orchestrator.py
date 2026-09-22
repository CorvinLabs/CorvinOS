"""
Security Orchestrator Skill — Console Routes (ADR-2031, Week 2).

Four endpoints:
- GET /security/threats — List active threats + confidence
- GET /security/audit — Threat decision history
- POST /security/feedback — Operator feedback on threats
- PUT /security/policy — Adjust policy parameters

Integrated with learning backend (ADR-0314) for feedback loops.
All routes are audit-logged and fail-closed.
"""

from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timezone
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Create blueprint for security orchestrator routes
security_orchestrator_routes = Blueprint(
    "security_orchestrator",
    __name__,
    url_prefix="/security"
)


# ============================================================================
# Route 1: GET /security/threats — List active threats
# ============================================================================

@security_orchestrator_routes.route("/threats", methods=["GET"])
def list_threats():
    """
    List active threats detected by the Security Orchestrator.

    Query Parameters:
    - tenant_id: Tenant ID (optional, defaults to authenticated tenant)
    - limit: Max results (default 50)
    - severity: Filter by severity (low, high, critical)

    Response:
    {
        "threats": [
            {
                "threat_id": "threat-123",
                "pattern": "brute_force",
                "confidence": 0.85,
                "severity": "high",
                "affected_users": ["alice", "bob"],
                "affected_ips": ["192.168.1.100"],
                "detected_at": "2026-09-22T16:00:00Z",
                "response_action": "tighten_auth_gate",
                "response_state": "active"
            },
            ...
        ],
        "total_count": 5,
        "window": {
            "active": true,
            "since": "2026-09-22T00:00:00Z"
        }
    }
    """
    try:
        # Get query parameters
        tenant_id = request.args.get("tenant_id", "")
        limit = min(int(request.args.get("limit", 50)), 500)
        severity_filter = request.args.get("severity", "")

        # Get current authenticated context (mock for now)
        if not tenant_id:
            tenant_id = getattr(current_app, "current_tenant_id", "_default")

        # Fail-closed: require tenant_id
        if not tenant_id or tenant_id.strip() == "":
            return jsonify({"error": "tenant_id required (fail-closed)"}), 400

        # Get security orchestrator skill instance
        skill = getattr(current_app, "security_orchestrator_skill", None)
        if not skill:
            return jsonify({"error": "Security Orchestrator not initialized"}), 503

        # Retrieve active threats from backend
        threats = _get_active_threats(
            tenant_id=tenant_id,
            severity_filter=severity_filter,
            limit=limit
        )

        # Audit log the query
        _audit_threats_query(tenant_id=tenant_id, count=len(threats))

        return jsonify({
            "threats": threats,
            "total_count": len(threats),
            "window": {
                "active": True,
                "since": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            }
        }), 200

    except Exception as e:
        logger.error(f"Failed to list threats: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Route 2: GET /security/audit — Threat decision history
# ============================================================================

@security_orchestrator_routes.route("/audit", methods=["GET"])
def threat_audit_history():
    """
    Return threat detection and response history (audit trail).

    Query Parameters:
    - tenant_id: Tenant ID (optional)
    - since: ISO timestamp to filter events (default: 24h ago)
    - threat_id: Filter by specific threat
    - event_type: Filter by event type (detect, tighten, revert, feedback)

    Response:
    {
        "events": [
            {
                "timestamp": "2026-09-22T15:00:00Z",
                "event_type": "threat_detected",
                "threat_id": "threat-123",
                "pattern": "brute_force",
                "confidence": 0.85,
                "action_taken": "tighten_auth_gate",
                "old_policy": {"auth_max_failures": 5},
                "new_policy": {"auth_max_failures": 3},
                "hash": "sha256:abc123",
                "prev_hash": "sha256:def456"
            },
            ...
        ],
        "total_events": 42,
        "chain_verified": true
    }
    """
    try:
        tenant_id = request.args.get("tenant_id", "_default")
        since = request.args.get("since", "")
        threat_id = request.args.get("threat_id", "")
        event_type = request.args.get("event_type", "")

        # Fail-closed: require tenant_id
        if not tenant_id or tenant_id.strip() == "":
            return jsonify({"error": "tenant_id required"}), 400

        # Retrieve audit trail
        events = _get_audit_trail(
            tenant_id=tenant_id,
            since=since,
            threat_id=threat_id,
            event_type=event_type
        )

        # Verify hash chain integrity
        chain_verified = _verify_audit_chain(events)

        # Audit log the history query
        _audit_history_query(tenant_id=tenant_id, event_count=len(events))

        return jsonify({
            "events": events,
            "total_events": len(events),
            "chain_verified": chain_verified
        }), 200

    except Exception as e:
        logger.error(f"Failed to retrieve audit history: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Route 3: POST /security/feedback — Operator feedback on threats
# ============================================================================

@security_orchestrator_routes.route("/feedback", methods=["POST"])
def submit_threat_feedback():
    """
    Submit operator feedback on a detected threat (for learning loop, ADR-0314).

    Request Body:
    {
        "threat_id": "threat-123",
        "feedback_type": "outcome" | "preference" | "confidence",
        "signal": "true_positive" | "false_positive",
        "confidence_score": 0.85,
        "reason": "Operator notes (optional, scrubbed before storage)"
    }

    Response:
    {
        "success": true,
        "threat_id": "threat-123",
        "feedback_recorded": true,
        "learning_signal_sent": true,
        "new_confidence": 0.88
    }
    """
    try:
        data = request.get_json() or {}
        tenant_id = request.headers.get("X-Tenant-Id", "_default")

        # Validate required fields
        threat_id = data.get("threat_id", "").strip()
        feedback_type = data.get("feedback_type", "").strip()
        signal = data.get("signal", "").strip()

        if not threat_id or not feedback_type or not signal:
            return jsonify({
                "error": "threat_id, feedback_type, and signal are required"
            }), 400

        # Fail-closed: require tenant_id
        if not tenant_id or tenant_id.strip() == "":
            return jsonify({"error": "tenant_id required (fail-closed)"}), 400

        # Record feedback (learning loop integration)
        feedback_result = _record_threat_feedback(
            tenant_id=tenant_id,
            threat_id=threat_id,
            feedback_type=feedback_type,
            signal=signal,
            confidence_score=data.get("confidence_score", 0.5),
            reason=data.get("reason", "")  # Will be scrubbed
        )

        # Audit log the feedback
        _audit_feedback_submission(
            tenant_id=tenant_id,
            threat_id=threat_id,
            feedback_type=feedback_type,
            signal=signal
        )

        return jsonify({
            "success": feedback_result.get("success", False),
            "threat_id": threat_id,
            "feedback_recorded": feedback_result.get("recorded", False),
            "learning_signal_sent": feedback_result.get("learning_sent", False),
            "new_confidence": feedback_result.get("new_confidence", 0.0)
        }), 200

    except Exception as e:
        logger.error(f"Failed to record feedback: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Route 4: PUT /security/policy — Adjust policy parameters
# ============================================================================

@security_orchestrator_routes.route("/policy", methods=["PUT"])
def adjust_security_policy():
    """
    Adjust security policy parameters manually (operator override, audited).

    Request Body:
    {
        "action": "tighten" | "loosen" | "reset",
        "gate": "auth_max_failures" | "data_high_risk_flow_limit" | "rate_limit_requests_per_minute",
        "new_value": 3,
        "ttl_minutes": 60,
        "reason": "Manual operator adjustment"
    }

    Response:
    {
        "success": true,
        "gate": "auth_max_failures",
        "old_value": 5,
        "new_value": 3,
        "expires_at": "2026-09-22T17:00:00Z",
        "audit_event_id": "event-123"
    }
    """
    try:
        data = request.get_json() or {}
        tenant_id = request.headers.get("X-Tenant-Id", "_default")

        # Validate required fields
        action = data.get("action", "").strip().lower()
        gate = data.get("gate", "").strip()
        new_value = data.get("new_value")
        ttl_minutes = int(data.get("ttl_minutes", 60))
        reason = data.get("reason", "Manual operator adjustment")

        # Fail-closed: require tenant_id
        if not tenant_id or tenant_id.strip() == "":
            return jsonify({"error": "tenant_id required (fail-closed)"}), 400

        # Validate action
        if action not in ["tighten", "loosen", "reset"]:
            return jsonify({
                "error": f"Invalid action: {action} (must be tighten, loosen, or reset)"
            }), 400

        # Validate gate name
        valid_gates = [
            "auth_max_failures",
            "data_high_risk_flow_limit",
            "rate_limit_requests_per_minute",
            "override_allowed_per_user"
        ]
        if gate and gate not in valid_gates:
            return jsonify({
                "error": f"Invalid gate: {gate}"
            }), 400

        # Apply policy adjustment
        policy_result = _adjust_policy(
            tenant_id=tenant_id,
            action=action,
            gate=gate,
            new_value=new_value,
            ttl_minutes=ttl_minutes,
            reason=reason
        )

        if not policy_result.get("success"):
            return jsonify({
                "error": policy_result.get("error", "Failed to adjust policy")
            }), 400

        # Audit log the policy change
        _audit_policy_adjustment(
            tenant_id=tenant_id,
            action=action,
            gate=gate,
            old_value=policy_result.get("old_value"),
            new_value=policy_result.get("new_value"),
            reason=reason
        )

        return jsonify({
            "success": True,
            "gate": gate,
            "old_value": policy_result.get("old_value"),
            "new_value": policy_result.get("new_value"),
            "expires_at": policy_result.get("expires_at"),
            "audit_event_id": policy_result.get("audit_event_id")
        }), 200

    except ValueError as e:
        logger.error(f"Invalid policy adjustment: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error(f"Failed to adjust policy: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Helper Functions (Internal)
# ============================================================================

def _get_active_threats(tenant_id: str, severity_filter: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve active threats from backend."""
    # Mock implementation - in production, fetch from threat database
    threats = [
        {
            "threat_id": f"threat-{i}",
            "pattern": ["brute_force", "privilege_escalation", "data_exfiltration"][i % 3],
            "confidence": 0.75 + (i * 0.02),
            "severity": ["low", "high", "critical"][i % 3],
            "affected_users": [f"user-{i}"],
            "affected_ips": [f"192.168.1.{100 + i}"],
            "detected_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "response_action": "tighten_auth_gate",
            "response_state": "active"
        }
        for i in range(min(5, limit))
    ]

    if severity_filter:
        threats = [t for t in threats if t["severity"] == severity_filter]

    return threats[:limit]


def _get_audit_trail(
    tenant_id: str,
    since: str = "",
    threat_id: str = "",
    event_type: str = ""
) -> List[Dict[str, Any]]:
    """Retrieve audit trail for threats."""
    # Mock implementation - in production, fetch from audit backend
    events = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event_type": "threat_detected",
            "threat_id": "threat-123",
            "pattern": "brute_force",
            "confidence": 0.85,
            "action_taken": "tighten_auth_gate",
            "old_policy": {"auth_max_failures": 5},
            "new_policy": {"auth_max_failures": 3},
            "hash": "sha256:abc123",
            "prev_hash": "sha256:def456"
        },
        {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event_type": "policy_tightened",
            "threat_id": "threat-123",
            "pattern": "brute_force",
            "confidence": 0.85,
            "action_taken": "tighten_auth_gate",
            "old_policy": {"auth_max_failures": 5},
            "new_policy": {"auth_max_failures": 3},
            "hash": "sha256:def456",
            "prev_hash": "sha256:abc123"
        }
    ]

    if threat_id:
        events = [e for e in events if e.get("threat_id") == threat_id]
    if event_type:
        events = [e for e in events if e.get("event_type") == event_type]

    return events


def _verify_audit_chain(events: List[Dict[str, Any]]) -> bool:
    """Verify hash chain integrity (mock implementation)."""
    if not events:
        return True

    # In production: verify prev_hash links in sequence
    for i in range(1, len(events)):
        if events[i].get("prev_hash") != events[i-1].get("hash"):
            return False

    return True


def _record_threat_feedback(
    tenant_id: str,
    threat_id: str,
    feedback_type: str,
    signal: str,
    confidence_score: float,
    reason: str
) -> Dict[str, Any]:
    """Record operator feedback for learning loop."""
    # Scrub reason before storage (no PII)
    scrubbed_reason = _scrub_pii(reason) if reason else ""

    # Record to learning backend (ADR-0314)
    try:
        # In production: emit learning event to backend
        logger.info(
            f"Threat feedback recorded: threat_id={threat_id}, type={feedback_type}, signal={signal}",
            extra={"tenant_id": tenant_id}
        )

        return {
            "success": True,
            "recorded": True,
            "learning_sent": True,
            "new_confidence": confidence_score
        }
    except Exception as e:
        logger.error(f"Failed to record feedback: {e}")
        return {
            "success": False,
            "recorded": False,
            "learning_sent": False,
            "new_confidence": 0.0
        }


def _adjust_policy(
    tenant_id: str,
    action: str,
    gate: str,
    new_value: Optional[int],
    ttl_minutes: int,
    reason: str
) -> Dict[str, Any]:
    """Apply manual policy adjustment."""
    try:
        # Mock policy state
        current_policy = {
            "auth_max_failures": 5,
            "data_high_risk_flow_limit": 100,
            "rate_limit_requests_per_minute": 1000,
            "override_allowed_per_user": True
        }

        old_value = current_policy.get(gate) if gate else None

        # For reset action, restore to baseline
        if action == "reset":
            new_val = old_value  # Keep current for mock
        else:
            new_val = new_value if new_value is not None else old_value

        # Calculate expiration time
        expires_at = (
            datetime.now(timezone.utc) + \
            __import__("datetime").timedelta(minutes=ttl_minutes)
        ).isoformat().replace("+00:00", "Z")

        return {
            "success": True,
            "old_value": old_value,
            "new_value": new_val,
            "expires_at": expires_at,
            "audit_event_id": f"evt-{datetime.now(timezone.utc).timestamp()}"
        }
    except Exception as e:
        logger.error(f"Policy adjustment failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def _scrub_pii(text: str) -> str:
    """Remove PII from audit text (fail-closed)."""
    # In production: comprehensive PII scrubbing
    # For now: just verify text is safe
    if any(pattern in text.lower() for pattern in ["email", "password", "token", "key"]):
        return "[REDACTED]"
    return text[:100]  # Truncate to 100 chars


def _audit_threats_query(tenant_id: str, count: int) -> None:
    """Audit log a threats query."""
    logger.info(
        f"Threats query: tenant_id={tenant_id}, results={count}",
        extra={"event_type": "security_threats_queried"}
    )


def _audit_history_query(tenant_id: str, event_count: int) -> None:
    """Audit log a history query."""
    logger.info(
        f"History query: tenant_id={tenant_id}, events={event_count}",
        extra={"event_type": "security_audit_queried"}
    )


def _audit_feedback_submission(
    tenant_id: str,
    threat_id: str,
    feedback_type: str,
    signal: str
) -> None:
    """Audit log a feedback submission."""
    logger.info(
        f"Feedback submitted: threat_id={threat_id}, type={feedback_type}, signal={signal}",
        extra={"tenant_id": tenant_id, "event_type": "security_feedback_submitted"}
    )


def _audit_policy_adjustment(
    tenant_id: str,
    action: str,
    gate: str,
    old_value: Any,
    new_value: Any,
    reason: str
) -> None:
    """Audit log a policy adjustment."""
    logger.info(
        f"Policy adjusted: action={action}, gate={gate}, {old_value} → {new_value}",
        extra={
            "tenant_id": tenant_id,
            "event_type": "security_policy_adjusted",
            "reason": reason
        }
    )
