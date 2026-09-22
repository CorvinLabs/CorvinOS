"""
Skills Execution + Audit Integration — Emit audit events for all skill operations

GDPR Art. 30 compliance: Every skill execution, feedback, and config change must be
recorded in the immutable audit trail.

Wraps SkillExecutor to emit audit_backend events (skill_executed, feedback, config_updated).
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime
import time

logger = logging.getLogger(__name__)


def emit_skill_audit_event(
    event_type: str,
    skill_id: str,
    tenant_id: str,
    input_hash: Optional[str] = None,
    output_hash: Optional[str] = None,
    latency_ms: int = 0,
    line_of_moral_responsibility: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None
) -> None:
    """
    Emit an immutable audit event for skill operations (GDPR Art. 30).

    Args:
        event_type: 'skill_executed', 'skill_feedback', or 'skill_config_updated'
        skill_id: Skill identifier (e.g., 'os.delegation_router')
        tenant_id: Tenant (must match, fail-closed)
        input_hash: SHA256 of input (never store raw input)
        output_hash: SHA256 of output (never store raw output)
        latency_ms: Execution latency in milliseconds
        line_of_moral_responsibility: Stack frame reference (e.g., 'adapter.py:237')
        details: Optional extra details (version, config_delta, feedback_signal, etc.)
        error: Optional error message (if execution failed)
    """
    try:
        from core.compliance.audit_backend import log_audit_event

        if not tenant_id or not skill_id:
            logger.error(f"Skill audit emit failed: missing tenant_id or skill_id (fail-closed)")
            return  # Fail-silent on audit (operation still proceeds, but logged)

        # Build audit event payload
        payload = {
            "event_type": event_type,
            "skill_id": skill_id,
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Add hashed inputs/outputs (never raw content)
        if input_hash:
            payload["input_hash"] = input_hash
        if output_hash:
            payload["output_hash"] = output_hash

        # Add latency + moral responsibility
        if latency_ms > 0:
            payload["latency_ms"] = latency_ms
        if line_of_moral_responsibility:
            payload["lom"] = line_of_moral_responsibility

        # Add optional details
        if details:
            payload.update(details)

        # Add error (if any)
        if error:
            payload["error"] = error

        # Emit to audit backend (hash-chained, immutable)
        log_audit_event(
            event_type=event_type,
            payload=payload,
            tenant_id=tenant_id
        )

        logger.info(f"Skill audit emitted: event={event_type}, skill={skill_id}, tenant={tenant_id}")

    except Exception as e:
        logger.error(f"Failed to emit skill audit event: {e} (operation continues)")


def emit_skill_executed_event(
    skill_id: str,
    tenant_id: str,
    input_data: Any,
    output_data: Any,
    latency_ms: float,
    line_of_moral_responsibility: str,
    error: Optional[str] = None
) -> None:
    """
    Emit skill_executed audit event (GDPR Art. 30 - records of processing).

    Args:
        skill_id: Skill identifier
        tenant_id: Tenant identifier
        input_data: Skill input (will be hashed, never stored raw)
        output_data: Skill output (will be hashed, never stored raw)
        latency_ms: Execution time in milliseconds
        line_of_moral_responsibility: Who invoked this skill (file:line)
        error: Optional error message
    """
    try:
        import hashlib
        import json

        # Hash inputs/outputs (never store raw)
        input_hash = _hash_data(input_data)
        output_hash = _hash_data(output_data) if output_data is not None else None

        emit_skill_audit_event(
            event_type="skill_executed",
            skill_id=skill_id,
            tenant_id=tenant_id,
            input_hash=input_hash,
            output_hash=output_hash,
            latency_ms=int(latency_ms),
            line_of_moral_responsibility=line_of_moral_responsibility,
            error=error
        )

    except Exception as e:
        logger.error(f"Error emitting skill_executed event: {e}")


def emit_skill_feedback_event(
    skill_id: str,
    tenant_id: str,
    feedback_type: str,  # 'outcome', 'preference', 'confidence', 'metric'
    signal: Any,
    details: Optional[Dict[str, Any]] = None
) -> None:
    """
    Emit skill_feedback audit event (learning loop signal - GDPR Art. 30).

    Args:
        skill_id: Skill identifier
        tenant_id: Tenant identifier
        feedback_type: Type of feedback (outcome/preference/confidence/metric)
        signal: Feedback signal (e.g., 0.85 confidence, 'positive' outcome)
        details: Optional extra details
    """
    try:
        payload = {
            "feedback_type": feedback_type,
            "signal": signal
        }

        if details:
            payload.update(details)

        emit_skill_audit_event(
            event_type="skill_feedback",
            skill_id=skill_id,
            tenant_id=tenant_id,
            details=payload
        )

    except Exception as e:
        logger.error(f"Error emitting skill_feedback event: {e}")


def emit_skill_config_updated_event(
    skill_id: str,
    tenant_id: str,
    param_name: str,
    old_value: Any,
    new_value: Any,
    reason: str = "optimizer",
    details: Optional[Dict[str, Any]] = None
) -> None:
    """
    Emit skill_config_updated audit event (learning optimization - GDPR Art. 30).

    Args:
        skill_id: Skill identifier
        tenant_id: Tenant identifier
        param_name: Parameter that changed
        old_value: Previous value
        new_value: New value
        reason: Why it changed ('optimizer', 'manual', 'feedback', etc.)
        details: Optional extra details
    """
    try:
        payload = {
            "param_name": param_name,
            "old_value": old_value,
            "new_value": new_value,
            "reason": reason
        }

        if details:
            payload.update(details)

        emit_skill_audit_event(
            event_type="skill_config_updated",
            skill_id=skill_id,
            tenant_id=tenant_id,
            details=payload
        )

    except Exception as e:
        logger.error(f"Error emitting skill_config_updated event: {e}")


def _hash_data(data: Any) -> str:
    """
    Hash data securely (never store raw input/output).

    Args:
        data: Data to hash

    Returns:
        SHA256 hash as hex string
    """
    try:
        import hashlib
        import json

        # Serialize to JSON for consistent hashing
        if isinstance(data, (dict, list)):
            serialized = json.dumps(data, sort_keys=True, default=str)
        elif isinstance(data, str):
            serialized = data
        else:
            serialized = str(data)

        return hashlib.sha256(serialized.encode()).hexdigest()

    except Exception as e:
        logger.error(f"Error hashing data: {e}")
        return "hash_error"


class SkillExecutionAuditor:
    """Context manager for skill execution auditing"""

    def __init__(
        self,
        skill_id: str,
        tenant_id: str,
        input_data: Any,
        line_of_moral_responsibility: str
    ):
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.input_data = input_data
        self.lom = line_of_moral_responsibility
        self.start_time = time.time()
        self.output_data = None
        self.error = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Emit audit event on skill execution completion"""
        latency_ms = (time.time() - self.start_time) * 1000
        error_msg = None

        if exc_type is not None:
            error_msg = f"{exc_type.__name__}: {exc_val}"

        emit_skill_executed_event(
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            input_data=self.input_data,
            output_data=self.output_data,
            latency_ms=latency_ms,
            line_of_moral_responsibility=self.lom,
            error=error_msg
        )

        return False  # Don't suppress exceptions


# Example usage in skill execution:
#
#  def execute(self, input_data):
#      with SkillExecutionAuditor(
#          skill_id="os.delegation_router",
#          tenant_id=self.tenant_id,
#          input_data=input_data,
#          line_of_moral_responsibility=f"{__file__}:__init__"
#      ) as auditor:
#          # Skill logic
#          output = self._route_request(input_data)
#          auditor.output_data = output
#          return output
