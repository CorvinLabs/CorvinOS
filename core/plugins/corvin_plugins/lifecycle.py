"""Plugin lifecycle event emitters — ADR-0682 (Learning k=6).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
registry's real load/unload/disable path audits through ``PluginContext.
audit_emit`` (``plugin.loaded`` / ``plugin.disabled`` …); these emitters are
reached only from ``lifecycle_loader`` (itself uncalled) and tests.

Emits immutable, non-blocking audit events for plugin lifecycle:
- plugin_loaded: Plugin init started and completed
- plugin_executed: Plugin method executed (success or error)
- plugin_error: Plugin error or exception
- plugin_disabled: Plugin disabled (manual or tripwire)

All events are:
- Immutable (frozen dataclass)
- Tenant-scoped (tenant_id on every event)
- Non-blocking (failures logged, never raised)
- PII fail-closed (detect PII in payloads, redact or drop)
- Queued, NOT yet audited: an event lands in the tenant's durable queue
  (``core.audit.event_queue``) and reaches the audit chain only when that
  queue is drained. A failed enqueue is logged and the event is lost, which is
  why nothing here may be described as an audit record.
"""

from __future__ import annotations

import logging
import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

from core.audit.event_queue import EventQueue

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginLifecycleEvent:
    """Immutable plugin lifecycle event.

    GDPR Art. 30, 32: Audit-logged, tenant-scoped, hash-linked to chain.
    """
    event_type: str  # 'plugin_loaded' | 'plugin_executed' | 'plugin_error' | 'plugin_disabled'
    plugin_id: str
    tenant_id: str
    timestamp: str  # ISO 8601

    # Optional fields (vary by event_type)
    version: Optional[str] = None
    method_name: Optional[str] = None
    input_hash: Optional[str] = None  # SHA256 of input, never raw payload
    output_hash: Optional[str] = None
    error_type: Optional[str] = None
    error_message_hash: Optional[str] = None  # Never raw error message
    latency_ms: Optional[float] = None
    reason: Optional[str] = None  # For plugin_disabled
    priority: str = "LOW"  # HIGH for plugin_executed, LOW for plugin_loaded

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


def _scrub_pii(data: Any, depth: int = 0, max_depth: int = 3) -> str:
    """Scrub PII from payload and return SHA256 hash.

    Fail-closed: if PII detected, redact and hash the scrubbed version.
    Never return raw payloads in audit events.

    Returns:
        SHA256 hash of scrubbed payload (never the raw payload itself)
    """
    if depth > max_depth:
        return hashlib.sha256(b"<truncated>").hexdigest()

    try:
        # Stringify and check for PII patterns
        payload_str = json.dumps(data, default=str)

        # PII detection (fail-closed)
        pii_patterns = [
            "@",  # Email-like
            "token",  # Auth token
            "key",  # API key
            "password",  # Obvious secret
            "secret",  # Obvious secret
            "credential",  # Explicit credential
        ]

        has_pii = any(pattern.lower() in payload_str.lower() for pattern in pii_patterns)

        if has_pii:
            # Redact known PII and hash
            scrubbed = "<pii_redacted>"
        else:
            scrubbed = payload_str

        return hashlib.sha256(scrubbed.encode()).hexdigest()
    except Exception as e:
        logger.warning(f"Error scrubbing PII: {e}")
        # Fail closed: return hash of "redacted" if scrubbing fails
        return hashlib.sha256(b"<redacted_due_to_error>").hexdigest()


def emit_plugin_loaded(
    plugin_id: str,
    tenant_id: str,
    version: Optional[str] = None,
) -> None:
    """Emit plugin_loaded event (non-blocking, audit-first).

    Called when plugin initialization completes successfully.

    Args:
        plugin_id: Plugin identifier
        tenant_id: Tenant scope
        version: Plugin version
    """
    try:
        event = PluginLifecycleEvent(
            event_type="plugin_loaded",
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            version=version,
            priority="LOW",
        )
        _enqueue_event(event)
    except Exception as e:
        logger.warning(f"Failed to emit plugin_loaded for {plugin_id}: {e}")
        # Non-blocking: continue regardless


def emit_plugin_executed(
    plugin_id: str,
    tenant_id: str,
    method_name: str,
    input_payload: Any,
    output_payload: Any,
    latency_ms: float,
    version: Optional[str] = None,
) -> None:
    """Emit plugin_executed event (non-blocking, audit-first, HIGH priority).

    Called after plugin method execution (success or error).
    Inputs/outputs are hashed, never stored raw.

    Args:
        plugin_id: Plugin identifier
        tenant_id: Tenant scope
        method_name: Method executed
        input_payload: Input (hashed)
        output_payload: Output (hashed)
        latency_ms: Execution time in milliseconds
        version: Plugin version
    """
    try:
        event = PluginLifecycleEvent(
            event_type="plugin_executed",
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            method_name=method_name,
            input_hash=_scrub_pii(input_payload),
            output_hash=_scrub_pii(output_payload),
            latency_ms=latency_ms,
            version=version,
            priority="HIGH",
        )
        _enqueue_event(event)
    except Exception as e:
        logger.warning(f"Failed to emit plugin_executed for {plugin_id}.{method_name}: {e}")
        # Non-blocking: continue regardless


def emit_plugin_error(
    plugin_id: str,
    tenant_id: str,
    method_name: Optional[str],
    error: Exception,
    version: Optional[str] = None,
) -> None:
    """Emit plugin_error event (non-blocking, audit-first).

    Called when plugin raises an exception.
    Error message is hashed, never stored raw.

    Args:
        plugin_id: Plugin identifier
        tenant_id: Tenant scope
        method_name: Method that raised (optional)
        error: Exception object
        version: Plugin version
    """
    try:
        event = PluginLifecycleEvent(
            event_type="plugin_error",
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            method_name=method_name,
            error_type=type(error).__name__,
            error_message_hash=hashlib.sha256(str(error).encode()).hexdigest(),
            version=version,
            priority="HIGH",
        )
        _enqueue_event(event)
    except Exception as e:
        logger.warning(f"Failed to emit plugin_error for {plugin_id}: {e}")
        # Non-blocking: continue regardless


def emit_plugin_disabled(
    plugin_id: str,
    tenant_id: str,
    reason: str = "admin_request",
) -> None:
    """Emit plugin_disabled event (non-blocking, audit-first).

    Called when plugin is disabled (manual or tripwire).

    Args:
        plugin_id: Plugin identifier
        tenant_id: Tenant scope
        reason: Disable reason (e.g., 'admin_request', 'health_check_failed', 'tripwire')
    """
    try:
        event = PluginLifecycleEvent(
            event_type="plugin_disabled",
            plugin_id=plugin_id,
            tenant_id=tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            reason=reason,
            priority="HIGH",
        )
        _enqueue_event(event)
    except Exception as e:
        logger.warning(f"Failed to emit plugin_disabled for {plugin_id}: {e}")
        # Non-blocking: continue regardless


def _enqueue_event(event: PluginLifecycleEvent) -> None:
    """Enqueue event to durable SQLite queue (audit-first integration).

    This is called by all emit_* functions.
    Delegates to event_queue.py for durable storage.

    Args:
        event: PluginLifecycleEvent to enqueue
    """
    try:
        # The event's OWN tenant queue — never a shared/_default one (ADR-0007).
        EventQueue(tenant_id=event.tenant_id).enqueue(event)
    except Exception as e:
        logger.warning("Failed to enqueue plugin lifecycle event %s: %s",
                       event.event_type, type(e).__name__)
        # Non-blocking: continue regardless
