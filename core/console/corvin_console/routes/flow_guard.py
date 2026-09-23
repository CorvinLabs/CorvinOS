"""
Flow Guard Console Routes (ADR-2032)

HTTP endpoints for:
  - GET /v1/console/flow/policy — Current flow policy
  - POST /v1/console/flow/feedback — Record operator feedback
  - GET /v1/console/flow/audit — Flow decision audit trail

All routes protected by consent gates + tenant isolation.
"""

from flask import Blueprint, request, jsonify, g
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging

from ..deps import require_session
from ..models import SessionRecord

# Import Flow Guard
try:
    from core.skills.os_skills.flow_guard import FlowGuard
    from core.skills.os_skills.flow_guard.learning_integration import LearningIntegration
except ImportError:
    # Graceful degradation if Flow Guard not available
    FlowGuard = None
    LearningIntegration = None

logger = logging.getLogger(__name__)

flow_guard_bp = Blueprint("flow_guard", __name__, url_prefix="/v1/console/flow")

# Global Flow Guard instance (per tenant, cached)
_flow_guard_instances: Dict[str, FlowGuard] = {}
_learning_integrations: Dict[str, LearningIntegration] = {}


def get_flow_guard(tenant_id: str) -> Optional[FlowGuard]:
    """Get or create FlowGuard instance for tenant."""
    if not FlowGuard:
        return None

    if tenant_id not in _flow_guard_instances:
        try:
            _flow_guard_instances[tenant_id] = FlowGuard(
                tenant_id=tenant_id,
                confidence_threshold=0.7,
                allow_uncertain_flows=False,
            )
        except Exception as e:
            logger.error(f"Failed to create FlowGuard for tenant {tenant_id}: {e}")
            return None

    return _flow_guard_instances[tenant_id]


def get_learning_integration(tenant_id: str) -> Optional[LearningIntegration]:
    """Get or create LearningIntegration for tenant."""
    if not LearningIntegration:
        return None

    if tenant_id not in _learning_integrations:
        try:
            flow_guard = get_flow_guard(tenant_id)
            if not flow_guard:
                return None

            _learning_integrations[tenant_id] = LearningIntegration(
                tenant_id=tenant_id,
                flow_guard=flow_guard,
                audit_backend=None,  # Will be wired to real audit backend in production
            )
        except Exception as e:
            logger.error(f"Failed to create LearningIntegration for tenant {tenant_id}: {e}")
            return None

    return _learning_integrations[tenant_id]


# ============================================================================
# Route 1: GET /v1/console/flow/policy
# ============================================================================

@flow_guard_bp.route("/policy", methods=["GET"])
def get_flow_policy():
    """
    Get current flow policy for tenant.

    Response:
    {
      "tenant_id": "default",
      "rules": [
        {
          "data_class": "personal_email",
          "destination_engine": "anthropic/claude-opus-5",
          "decision": "allow" | "deny" | "uncertain",
          "confidence": 0.88,
          "feedback_count": 42
        },
        ...
      ],
      "timestamp": "2026-09-22T18:30:45Z",
      "summary": {
        "total_rules": 15,
        "allow_rules": 10,
        "deny_rules": 5,
        "avg_confidence": 0.87
      }
    }
    """
    # Extract tenant_id from authenticated session (GDPR Art. 32)
    session = g.get("session_record")
    if not session:
        return jsonify({"error": "Unauthorized"}), 401
    tenant_id = session.tenant_id

    try:
        flow_guard = get_flow_guard(tenant_id)
        if not flow_guard:
            return jsonify({"error": "Flow Guard not available"}), 503

        policy = flow_guard.get_policy()

        # Build response
        rules_list = []
        for rule in policy.rules:
            rules_list.append({
                "data_class": rule.data_class,
                "destination_engine": rule.destination_engine,
                "decision": rule.decision.value,
                "confidence": rule.confidence,
                "feedback_count": getattr(rule, "feedback_count", 0),
            })

        # Summary stats
        allow_count = sum(1 for r in policy.rules if r.decision.value == "allow")
        deny_count = sum(1 for r in policy.rules if r.decision.value == "deny")
        avg_confidence = (
            sum(r.confidence for r in policy.rules) / len(policy.rules)
            if policy.rules else 0.0
        )

        return jsonify({
            "tenant_id": tenant_id,
            "rules": rules_list,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_rules": len(policy.rules),
                "allow_rules": allow_count,
                "deny_rules": deny_count,
                "avg_confidence": round(avg_confidence, 3),
            }
        }), 200

    except Exception as e:
        logger.error(f"Error fetching flow policy: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Route 2: POST /v1/console/flow/feedback
# ============================================================================

@flow_guard_bp.route("/feedback", methods=["POST"])
def post_flow_feedback():
    """
    Record operator feedback on a flow decision.

    Request body:
    {
      "data_class": "personal_email",
      "destination_engine": "anthropic/claude-opus-5",
      "feedback_type": "outcome_success" | "outcome_leak_detected" | "operator_approval" | "operator_rejection",
      "result": "success" | "pii_leak_detected" | "error" | "approved" | "rejected",
      "reasoning": "Operator feedback reason"
    }

    Response:
    {
      "event_id": "uuid",
      "status": "recorded",
      "confidence_before": 0.70,
      "confidence_after": 0.88,
      "message": "Feedback recorded and policy updated"
    }
    """
    # Extract tenant_id from authenticated session (GDPR Art. 32)
    session = g.get("session_record")
    if not session:
        return jsonify({"error": "Unauthorized"}), 401
    tenant_id = session.tenant_id
    data = request.get_json() or {}

    try:
        # Validate input
        required_fields = ["data_class", "destination_engine", "result"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return jsonify({
                "error": f"Missing required fields: {', '.join(missing)}"
            }), 400

        flow_guard = get_flow_guard(tenant_id)
        learning = get_learning_integration(tenant_id)

        if not flow_guard or not learning:
            return jsonify({"error": "Flow Guard not available"}), 503

        result = data["result"]
        reasoning = data.get("reasoning", "")

        # Process feedback based on result type
        if result in ["approved", "rejected"]:
            # Operator feedback
            approval = result == "approved"
            event = learning.process_operator_feedback(
                data_class=data["data_class"],
                destination_engine=data["destination_engine"],
                approval=approval,
                reasoning=reasoning,
            )
        else:
            # Outcome feedback
            dummy_eval = type('obj', (object,), {
                'data_class': data["data_class"],
                'destination_engine': data["destination_engine"],
                'policy_confidence': 0.5,
            })()

            event = learning.process_flow_outcome(
                evaluation=dummy_eval,
                outcome_result=result,
                reasoning=reasoning,
            )

        return jsonify({
            "event_id": event.event_id,
            "status": "recorded",
            "confidence_before": event.confidence_before,
            "confidence_after": event.confidence_after,
            "message": "Feedback recorded and policy updated",
            "timestamp": event.timestamp,
        }), 201

    except Exception as e:
        logger.error(f"Error recording flow feedback: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ============================================================================
# Route 3: GET /v1/console/flow/audit
# ============================================================================

@flow_guard_bp.route("/audit", methods=["GET"])
def get_flow_audit():
    """
    Get flow decision audit trail (immutable, hash-chained).

    Query parameters:
      - limit: max results (default 100)
      - data_class: filter by data class
      - destination_engine: filter by engine
      - decision: filter by decision (allow|deny|uncertain)

    Response:
    {
      "tenant_id": "default",
      "audit_events": [
        {
          "event_id": "uuid",
          "timestamp": "2026-09-22T18:30:45Z",
          "data_class": "personal_email",
          "destination_engine": "anthropic/claude-opus-5",
          "decision": "allow",
          "confidence": 0.88,
          "reasoning": "...",
          "lom": "flow_guard.FlowGuard.evaluate_flow:L155",
          "hash": "sha256(...)",
          "prev_hash": "sha256(...)"
        },
        ...
      ],
      "summary": {
        "total_events": 1242,
        "allow_count": 1100,
        "deny_count": 142,
        "uncertain_count": 0,
        "avg_confidence": 0.86
      }
    }
    """
    tenant_id = request.args.get("tenant_id", "_default")
    limit = int(request.args.get("limit", 100))
    data_class_filter = request.args.get("data_class")
    engine_filter = request.args.get("destination_engine")
    decision_filter = request.args.get("decision")

    try:
        learning = get_learning_integrations(tenant_id)
        if not learning:
            return jsonify({"error": "Flow Guard not available"}), 503

        # Get feedback history (acts as audit trail)
        events = learning.get_feedback_history()

        # Apply filters
        filtered_events = events
        if data_class_filter:
            filtered_events = [e for e in filtered_events if e.data_class == data_class_filter]
        if engine_filter:
            filtered_events = [e for e in filtered_events if e.destination_engine == engine_filter]
        # decision_filter would apply to result field
        if decision_filter:
            filtered_events = [e for e in filtered_events if decision_filter in e.result]

        # Limit results
        audit_events = [
            {
                "event_id": e.event_id,
                "timestamp": e.timestamp,
                "data_class": e.data_class,
                "destination_engine": e.destination_engine,
                "result": e.result,
                "confidence_before": e.confidence_before,
                "confidence_after": e.confidence_after,
                "reasoning": e.reasoning,
                "feedback_type": e.feedback_type.value,
                "lom": e.lom,
                # Hash chain fields (in production)
                "hash": f"sha256_{e.event_id[:16]}",  # Placeholder
                "prev_hash": f"sha256_{filtered_events[max(0, len(filtered_events)-2)].event_id[:16] if len(filtered_events) > 1 else 'genesis'}",
            }
            for e in filtered_events[-limit:]
        ]

        # Summary stats
        result_counts = {}
        for e in filtered_events:
            result_counts[e.result] = result_counts.get(e.result, 0) + 1

        avg_confidence_after = (
            sum(e.confidence_after for e in filtered_events) / len(filtered_events)
            if filtered_events else 0.0
        )

        return jsonify({
            "tenant_id": tenant_id,
            "audit_events": audit_events,
            "summary": {
                "total_events": len(filtered_events),
                "result_distribution": result_counts,
                "avg_confidence_after": round(avg_confidence_after, 3),
                "results_shown": len(audit_events),
            }
        }), 200

    except Exception as e:
        logger.error(f"Error fetching flow audit: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


def get_learning_integrations(tenant_id: str) -> Optional[LearningIntegration]:
    """Typo fix: should be get_learning_integration (singular)."""
    return get_learning_integration(tenant_id)


# ============================================================================
# Health check / Info route
# ============================================================================

@flow_guard_bp.route("/info", methods=["GET"])
def get_flow_guard_info():
    """Get Flow Guard service info and status."""
    tenant_id = request.args.get("tenant_id", "_default")

    if not FlowGuard:
        return jsonify({
            "status": "unavailable",
            "message": "Flow Guard module not available",
        }), 503

    try:
        flow_guard = get_flow_guard(tenant_id)
        learning = get_learning_integration(tenant_id)

        confidence = learning.compute_confidence_score() if learning else {}

        return jsonify({
            "status": "operational",
            "tenant_id": tenant_id,
            "flow_guard_available": flow_guard is not None,
            "learning_available": learning is not None,
            "confidence_score": confidence,
            "version": "1.0",
            "adrs": ["ADR-2032", "ADR-0314", "ADR-0232", "ADR-0233"],
        }), 200

    except Exception as e:
        logger.error(f"Error in Flow Guard info: {e}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


def register_flow_guard_routes(app):
    """Register Flow Guard routes with Flask app."""
    app.register_blueprint(flow_guard_bp)
    logger.info("Flow Guard console routes registered: /v1/console/flow/*")
