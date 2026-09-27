"""Audit Trail Integration for Escalation Events (ADR-0232, ADR-2084 K=3)."""

from datetime import datetime
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

# Escalation event type
ESCALATION_TRIGGERED = "escalation_triggered"


def emit_escalation_to_audit(event: Dict, audit_backend) -> bool:
    """
    Emit escalation event to hash-chained audit trail (ADR-0232 immutable).

    Args:
        event: Escalation event {model_old, model_new, reason, ...}
        audit_backend: Audit chain writer (must support hash-chaining)

    Returns:
        True if successfully written, False if failed (fail-closed)
    """
    if not audit_backend:
        logger.warning("Audit backend not available, escalation not recorded")
        return False

    try:
        # Get previous hash (for chaining)
        prev_hash = audit_backend.get_last_hash()

        # Augment event with audit metadata
        audit_event = {
            **event,
            "event_type": ESCALATION_TRIGGERED,
            "timestamp": datetime.utcnow().isoformat(),
            "prev_hash": prev_hash,
            # hash will be computed by backend
        }

        # Write to chain (atomic, fail-closed)
        audit_backend.write_event(audit_event)

        logger.info(f"Escalation recorded: {event['task_id']} ({event['model_old']}→{event['model_new']})")
        return True

    except Exception as e:
        logger.error(f"Failed to write escalation to audit trail: {e}", exc_info=True)
        return False


def get_escalation_audit_schema() -> Dict:
    """Return schema for escalation events (for validation)."""
    return {
        "event_type": ESCALATION_TRIGGERED,
        "task_id": str,
        "model_old": str,
        "model_new": str,
        "reason": str,
        "elapsed_ms": float,
        "p99_projection": float,
        "timestamp": str,
        "tenant_id": str,
    }
