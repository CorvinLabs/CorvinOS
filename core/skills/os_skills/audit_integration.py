"""
Skills Execution + Audit Integration — Emit audit events for all skill operations

GDPR Art. 30 compliance: Every skill execution, feedback, and config change must be
recorded in the immutable audit trail.

Writes ``skill_executed`` / ``skill_feedback`` / ``skill_config_updated`` into THE
tenant chain through :func:`core.skills.skill_audit.emit_skill_audit`
(``forge.security_events.write_event`` on the tenant chain). Until the adversarial
review round 5 (2026-09-28) this module imported ``core.compliance.audit_backend``,
which does not exist: the ImportError was caught and logged, and EVERY emit —
including the console DoD-verifier route's — was dropped.

Metadata only: inputs/outputs are SHA-256 digests, an error is its exception
class name, a feedback signal / config value is kept only when it is a number or
bool (otherwise its digest). Every field is also subject to the ``write_event``
allowlist floor (``_EVENT_ALLOWLIST`` in ``forge/security_events.py``).
"""

import hashlib
import logging
import re
from typing import Any, Dict, Optional
import time

logger = logging.getLogger(__name__)

#: Event types this module writes (registered in ``EVENT_SEVERITY`` and
#: ``_EVENT_ALLOWLIST`` in ``corvin_operator/forge/forge/security_events.py``).
SKILL_AUDIT_EVENT_TYPES = ("skill_executed", "skill_feedback", "skill_config_updated")


def _scalar_or_digest(prefix: str, value: Any) -> Dict[str, Any]:
    """``{prefix: value}`` for a number/bool, else ``{prefix_sha256: digest}``.

    A feedback signal or a config value may be free text (a user's comment, a
    prompt fragment); only a scalar is safe to chain verbatim.
    """
    if isinstance(value, bool) or (isinstance(value, (int, float)) and value == value):
        return {prefix: value}
    if value is None:
        return {}
    return {f"{prefix}_sha256": _hash_data(value)}


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
) -> bool:
    """
    Emit an immutable audit event for skill operations (GDPR Art. 30).

    Returns True when the record was hash-chained into the tenant chain, False
    (logged at ERROR) when it was not — never raises into the skill operation.

    Args:
        event_type: 'skill_executed', 'skill_feedback', or 'skill_config_updated'
        skill_id: Skill identifier (e.g., 'os.delegation_router')
        tenant_id: Tenant (must match the process tenant, fail-closed)
        input_hash: SHA256 of input (never store raw input)
        output_hash: SHA256 of output (never store raw output)
        latency_ms: Execution latency in milliseconds
        line_of_moral_responsibility: Stack frame reference (e.g., 'adapter.py:237')
        details: Optional extra metadata (dropped unless allowlisted)
        error: Optional error CLASS NAME (a message is never chained)
    """
    if event_type not in SKILL_AUDIT_EVENT_TYPES:
        logger.error("Skill audit emit refused: unknown event_type %r", event_type)
        return False
    if not tenant_id or not skill_id:
        logger.error("Skill audit emit refused: missing tenant_id or skill_id (%s)", event_type)
        return False
    try:
        from core.skills.skill_audit import emit_skill_audit
    except Exception as exc:  # noqa: BLE001
        logger.error("Skill audit writer not importable (%s) — %s NOT chained",
                     type(exc).__name__, event_type)
        return False

    payload: Dict[str, Any] = dict(details or {})
    payload["skill_id"] = skill_id
    if input_hash:
        payload["input_hash"] = input_hash
    if output_hash:
        payload["output_hash"] = output_hash
    if latency_ms and latency_ms > 0:
        payload["latency_ms"] = int(latency_ms)
    if line_of_moral_responsibility:
        payload["lom"] = line_of_moral_responsibility
    if error:
        # Class name only: ``str(exc)`` can carry paths, prompts or user data.
        payload["error_type"] = str(error).split(":", 1)[0].strip()[:80]
        payload["status"] = "error"
    ok = emit_skill_audit(tenant_id, event_type, tool=skill_id, details=payload)
    if ok:
        logger.debug("Skill audit chained: event=%s skill=%s", event_type, skill_id)
    return ok


_CODE_RE = re.compile(r"[A-Za-z0-9_.\-]+")


def _code(value: Any, fallback: str = "other") -> str:
    """A short identifier-like code, or ``fallback`` — never free text."""
    v = str(value or "")
    if v and len(v) <= 64 and _CODE_RE.fullmatch(v):
        return v
    return fallback


def emit_skill_executed_event(
    skill_id: str,
    tenant_id: str,
    input_data: Any,
    output_data: Any,
    latency_ms: float,
    line_of_moral_responsibility: str,
    error: Optional[str] = None
) -> bool:
    """
    Emit skill_executed audit event (GDPR Art. 30 - records of processing).

    Args:
        skill_id: Skill identifier
        tenant_id: Tenant identifier
        input_data: Skill input (will be hashed, never stored raw)
        output_data: Skill output (will be hashed, never stored raw)
        latency_ms: Execution time in milliseconds
        line_of_moral_responsibility: Who invoked this skill (file:line)
        error: Optional error ("ExcType: message" — only the type is chained)
    """
    try:
        return emit_skill_audit_event(
            event_type="skill_executed",
            skill_id=skill_id,
            tenant_id=tenant_id,
            input_hash=_hash_data(input_data),
            output_hash=_hash_data(output_data) if output_data is not None else None,
            latency_ms=int(latency_ms or 0),
            line_of_moral_responsibility=line_of_moral_responsibility,
            error=error,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error emitting skill_executed event: %s", type(e).__name__)
        return False


def emit_skill_feedback_event(
    skill_id: str,
    tenant_id: str,
    feedback_type: str,  # 'outcome', 'preference', 'confidence', 'metric'
    signal: Any,
    details: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Emit skill_feedback audit event (learning loop signal - GDPR Art. 30).

    Args:
        skill_id: Skill identifier
        tenant_id: Tenant identifier
        feedback_type: Type of feedback (outcome/preference/confidence/metric)
        signal: Feedback signal — chained verbatim only when a number/bool,
            otherwise as its SHA-256 digest (``signal_sha256``)
        details: Optional extra metadata (dropped unless allowlisted)
    """
    try:
        payload: Dict[str, Any] = dict(details or {})
        payload["feedback_type"] = _code(feedback_type)
        payload.update(_scalar_or_digest("signal", signal))
        return emit_skill_audit_event(
            event_type="skill_feedback",
            skill_id=skill_id,
            tenant_id=tenant_id,
            details=payload,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error emitting skill_feedback event: %s", type(e).__name__)
        return False


def emit_skill_config_updated_event(
    skill_id: str,
    tenant_id: str,
    param_name: str,
    old_value: Any,
    new_value: Any,
    reason: str = "optimizer",
    details: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Emit skill_config_updated audit event (learning optimization - GDPR Art. 30).

    Args:
        skill_id: Skill identifier
        tenant_id: Tenant identifier
        param_name: Parameter that changed (identifier)
        old_value: Previous value (verbatim only when a number/bool)
        new_value: New value (verbatim only when a number/bool)
        reason: Reason CODE ('optimizer', 'manual', 'feedback', ...)
        details: Optional extra metadata (dropped unless allowlisted)
    """
    try:
        payload: Dict[str, Any] = dict(details or {})
        payload["param_name"] = _code(param_name, "unnamed")
        payload.update(_scalar_or_digest("old_value", old_value))
        payload.update(_scalar_or_digest("new_value", new_value))
        payload["reason_code"] = _code(reason)
        return emit_skill_audit_event(
            event_type="skill_config_updated",
            skill_id=skill_id,
            tenant_id=tenant_id,
            details=payload,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Error emitting skill_config_updated event: %s", type(e).__name__)
        return False


def _hash_data(data: Any) -> str:
    """
    Hash data securely (never store raw input/output).

    Args:
        data: Data to hash

    Returns:
        SHA256 hash as hex string
    """
    try:
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
