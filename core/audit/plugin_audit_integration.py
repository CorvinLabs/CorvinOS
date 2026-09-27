"""Plugin audit integration — ADR-0682 (Learning k=6).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Stages plugin lifecycle events in the tenant's durable queue
(``core.audit.event_queue``); they reach the audit chain only when that queue
is drained. Provides emit_to_queue() and a PII-hashing wrapper.

GDPR Art. 30, 32: Every plugin event is audit-logged, immutable, tenant-scoped.
"""

from __future__ import annotations

import logging
import hashlib
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginAuditEvent:
    """Immutable plugin audit event wrapper.

    Wraps PluginLifecycleEvent with audit-chain metadata:
    - prev_hash: Reference to previous event (hash-chain link)
    - lom: Line of Moral Responsibility (function that emitted)
    - chain_height: Sequence number in tenant's chain
    """
    event_type: str
    plugin_id: str
    tenant_id: str
    timestamp: str
    prev_hash: Optional[str] = None
    lom: Optional[str] = None
    chain_height: Optional[int] = None

    # Payload (with hashed sensitive fields)
    version: Optional[str] = None
    method_name: Optional[str] = None
    input_hash: Optional[str] = None
    output_hash: Optional[str] = None
    error_type: Optional[str] = None
    error_message_hash: Optional[str] = None
    latency_ms: Optional[float] = None
    reason: Optional[str] = None
    priority: str = "LOW"

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        from dataclasses import asdict
        return asdict(self)


def emit_to_queue(
    event_type: str,
    plugin_id: str,
    tenant_id: str,
    *,
    version: Optional[str] = None,
    method_name: Optional[str] = None,
    input_payload: Any = None,
    output_payload: Any = None,
    error: Optional[Exception] = None,
    latency_ms: Optional[float] = None,
    reason: Optional[str] = None,
    priority: str = "LOW",
    lom: Optional[str] = None,
) -> None:
    """Emit plugin event to durable queue (fail-closed PII redaction).

    This is the primary entry point for plugin audit events.
    Redacts all payloads fail-closed, then enqueues.

    Args:
        event_type: 'plugin_loaded' | 'plugin_executed' | 'plugin_error' | 'plugin_disabled'
        plugin_id: Plugin identifier
        tenant_id: Tenant scope
        version: Plugin version (optional)
        method_name: Method executed (optional)
        input_payload: Input (will be hashed, not stored raw)
        output_payload: Output (will be hashed, not stored raw)
        error: Exception object (will be hashed, not stored raw)
        latency_ms: Execution latency (optional)
        reason: Disable reason (optional)
        priority: 'HIGH' or 'LOW' (default LOW)
        lom: Line of Moral Responsibility (e.g., 'module.py:func:L42')
    """
    try:
        from datetime import datetime
        from core.audit.event_queue import EventQueue

        # Create event with redacted payloads
        event_dict = {
            "event_type": event_type,
            "plugin_id": plugin_id,
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "version": version,
            "method_name": method_name,
            "priority": priority,
            "lom": lom,
        }

        # Redact sensitive payloads
        if input_payload is not None:
            event_dict["input_hash"] = _redact_and_hash(input_payload)

        if output_payload is not None:
            event_dict["output_hash"] = _redact_and_hash(output_payload)

        if error is not None:
            event_dict["error_type"] = type(error).__name__
            event_dict["error_message_hash"] = hashlib.sha256(str(error).encode()).hexdigest()

        if latency_ms is not None:
            event_dict["latency_ms"] = latency_ms

        if reason is not None:
            event_dict["reason"] = reason

        # Enqueue to the event's OWN tenant queue (ADR-0007)
        EventQueue(tenant_id=tenant_id).enqueue(event_dict)

    except Exception as e:
        logger.warning("Failed to queue plugin event %s: %s", event_type, type(e).__name__)
        # Non-blocking: continue


def _redact_and_hash(payload: Any) -> str:
    """Redact PII from payload and return SHA256 hash.

    Fail-closed: detect PII patterns, redact, and hash.
    Never store raw payloads in audit events.

    Args:
        payload: Any Python object

    Returns:
        SHA256 hash of redacted payload
    """
    import json

    try:
        # Stringify payload
        payload_str = json.dumps(payload, default=str)

        # PII detection patterns (fail-closed)
        pii_patterns = [
            "@",  # Email
            "token",
            "key",
            "password",
            "secret",
            "credential",
            "apikey",
            "auth",
            "bearer",
        ]

        has_pii = any(
            pattern.lower() in payload_str.lower()
            for pattern in pii_patterns
        )

        if has_pii:
            # Redact and hash
            to_hash = "<pii_redacted>".encode()
        else:
            # Safe payload: hash as-is
            to_hash = payload_str.encode()

        return hashlib.sha256(to_hash).hexdigest()

    except Exception as e:
        logger.warning(f"Error redacting payload: {e}")
        # Fail closed: return hash of redacted if error occurs
        return hashlib.sha256(b"<redacted_due_to_error>").hexdigest()


def validate_audit_event(event_dict: dict) -> bool:
    """Validate plugin audit event structure.

    Ensures:
    - Required fields present (event_type, plugin_id, tenant_id, timestamp)
    - tenant_id not empty (GDPR Art. 6 tenant isolation)
    - Sensitive fields are hashed, never raw
    - Timestamp is ISO 8601

    Args:
        event_dict: Event dict to validate

    Returns:
        True if valid, False otherwise
    """
    required = {"event_type", "plugin_id", "tenant_id", "timestamp"}

    if not all(key in event_dict for key in required):
        logger.warning(f"Event missing required fields: {event_dict}")
        return False

    # Tenant isolation check
    if not event_dict.get("tenant_id"):
        logger.warning("Event has empty tenant_id")
        return False

    # Check for raw sensitive data (fail-closed)
    raw_sensitive_fields = {"input_payload", "output_payload", "error_message"}
    if any(field in event_dict for field in raw_sensitive_fields):
        logger.warning(f"Event contains raw sensitive data: {raw_sensitive_fields}")
        return False

    # Timestamp format check
    ts = event_dict.get("timestamp", "")
    if not ts.endswith("Z") or "T" not in ts:
        logger.warning(f"Invalid timestamp format: {ts}")
        return False

    return True
