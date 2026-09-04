"""Audit event emission for Context Reference Graph."""

import json
from datetime import datetime
from typing import Optional, Any, Dict

from .types import ContextDigest, ContextBuildError


# Mock audit backend (will be replaced with real backend in integration)
class _MockAuditBackend:
    """Mock audit backend for testing. In production, uses real audit_backend."""

    def __init__(self):
        self.events = []

    def write_event(self, event: Dict[str, Any]) -> None:
        self.events.append(event)

    def get_events(self):
        return self.events

    def clear(self):
        self.events.clear()


_audit_backend = _MockAuditBackend()


def set_audit_backend(backend) -> None:
    """Set the audit backend (for integration with real audit_backend)."""
    global _audit_backend
    _audit_backend = backend


def _get_current_tenant() -> str:
    """Get current tenant ID (mock, will use real context.current_tenant() later)."""
    try:
        import os
        return os.environ.get('CORVIN_TENANT_ID', '_default')
    except:
        return '_default'


def emit_digest_validated(digest: ContextDigest) -> None:
    """Audit: Digest was created + validated."""
    event = {
        'event_type': 'context_digest_validated',
        'digest_checksum': digest.checksum_sha256,
        'reference_count': digest.reference_count(),
        'total_size_bytes': digest.total_size_bytes(),
        'tenant_id': digest.tenant_id,
        'timestamp': datetime.now().isoformat(),
        'lom': digest.lom
    }
    _audit_backend.write_event(event)


def emit_reference_resolved(
    file_path: str,
    hash_expected: str,
    hash_actual: str,
    status: str,
    latency_ms: float,
    tenant_id: str = "_default"
) -> None:
    """Audit: Reference was resolved (on-demand)."""
    event = {
        'event_type': 'context_reference_resolved',
        'reference_file': file_path,
        'hash_expected': hash_expected,
        'hash_actual': hash_actual,
        'status': status,
        'latency_ms': round(latency_ms, 2),
        'tenant_id': tenant_id,
        'timestamp': datetime.now().isoformat(),
        'lom': 'core/context/reference_graph/audit.py:emit_reference_resolved'
    }
    _audit_backend.write_event(event)


def emit_reference_hash_mismatch(
    file_path: str,
    hash_expected: str,
    hash_actual: str,
    tenant_id: str = "_default"
) -> None:
    """Audit: Reference hash mismatch detected (file changed)."""
    event = {
        'event_type': 'context_reference_hash_mismatch',
        'reference_file': file_path,
        'hash_expected': hash_expected,
        'hash_actual': hash_actual,
        'status': 'error',
        'tenant_id': tenant_id,
        'timestamp': datetime.now().isoformat(),
        'lom': 'core/context/reference_graph/audit.py:emit_reference_hash_mismatch',
        'action': 'reference_not_loaded'
    }
    _audit_backend.write_event(event)


def emit_builder_error(error: ContextBuildError, tenant_id: str = "_default") -> None:
    """Audit: Builder encountered error."""
    event = {
        'event_type': 'context_builder_error',
        'reason': error.reason,
        'reference_file': error.reference_file,
        'details': error.details,
        'tenant_id': tenant_id,
        'timestamp': datetime.now().isoformat(),
        'lom': 'core/context/reference_graph/audit.py:emit_builder_error'
    }
    _audit_backend.write_event(event)


def emit_digest_validation_failed(
    reason: str,
    expected_checksum: str,
    actual_checksum: str,
    tenant_id: str = "_default"
) -> None:
    """Audit: Digest validation failed (checksum mismatch)."""
    event = {
        'event_type': 'context_digest_validation_failed',
        'reason': reason,
        'expected_checksum': expected_checksum,
        'actual_checksum': actual_checksum,
        'tenant_id': tenant_id,
        'timestamp': datetime.now().isoformat(),
        'lom': 'core/context/reference_graph/audit.py:emit_digest_validation_failed',
        'action': 'digest_not_used'
    }
    _audit_backend.write_event(event)


def emit_event(event_type: str, *, tenant_id: str, lom: str, **fields: object) -> None:
    """
    Generic audit emitter shared by Phases 2-4 (loader, DAG, dedup, learner).

    Every event carries event_type, tenant_id, timestamp and lom (ADR-0537 shape).
    Callers MUST pass hashes/counters only - never block content (ADR-0564 rule 4).
    """
    if not tenant_id:
        raise ValueError("audit event without tenant_id is not allowed (fail-closed)")
    if not lom:
        raise ValueError("audit event without lom is not allowed (fail-closed)")
    event: Dict[str, Any] = {
        'event_type': event_type,
        'tenant_id': tenant_id,
        'timestamp': datetime.now().isoformat(),
        'lom': lom,
    }
    event.update(fields)
    _audit_backend.write_event(event)


def get_audit_events():
    """Get all emitted audit events (for testing)."""
    return _audit_backend.get_events()


def clear_audit_events():
    """Clear all audit events (for testing)."""
    _audit_backend.clear()
