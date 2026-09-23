"""Audit event emission for Context Reference Graph."""

import json
import logging
from datetime import datetime
from typing import Optional, Any, Dict
from pathlib import Path

from .types import ContextDigest, ContextBuildError

logger = logging.getLogger(__name__)

# Real audit backend (from core.audit.chain)
_audit_backend = None


def _get_audit_backend():
    """Get or initialize the real audit backend (ADR-0232/0233)."""
    global _audit_backend
    if _audit_backend is None:
        try:
            from core.audit.chain import AuditChain
            import os

            # Use real audit chain from tenant-scoped path
            tenant_id = os.environ.get('CORVIN_TENANT_ID', '_default')
            corvin_home = os.environ.get('CORVIN_HOME', os.path.expanduser('~/.corvin'))

            audit_log_path = Path(corvin_home) / 'tenants' / tenant_id / 'global' / 'audit.jsonl'
            audit_log_path.parent.mkdir(parents=True, exist_ok=True)

            _audit_backend = AuditChain(audit_log_path)
            logger.info(f"Initialized real audit chain at {audit_log_path}")
        except Exception as e:
            logger.error(f"Failed to initialize audit chain: {e}. Audit events will be logged but not persisted.")
            # Fallback to no-op backend (logs only, doesn't persist)
            _audit_backend = _NoOpAuditBackend()
    return _audit_backend


class _NoOpAuditBackend:
    """No-op backend for when real audit chain is unavailable (fail-closed: logs error + continues)."""

    def write_event(self, event: Dict[str, Any]) -> None:
        logger.warning(f"Audit event not persisted (backend unavailable): {event}")

    def get_events(self):
        return []


def set_audit_backend(backend) -> None:
    """Set the audit backend (for testing only; production uses real chain)."""
    global _audit_backend
    _audit_backend = backend


def _get_current_tenant() -> str:
    """Get current tenant ID from environment."""
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
    backend = _get_audit_backend()
    try:
        backend.write_event(event)
    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")


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
    backend = _get_audit_backend()
    try:
        backend.write_event(event)
    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")


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
    backend = _get_audit_backend()
    try:
        backend.write_event(event)
    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")


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
    backend = _get_audit_backend()
    try:
        backend.write_event(event)
    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")


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
    backend = _get_audit_backend()
    try:
        backend.write_event(event)
    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")


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
    backend = _get_audit_backend()
    try:
        backend.write_event(event)
    except Exception as e:
        logger.error(f"Failed to write audit event: {e}")


def get_audit_events():
    """Get all emitted audit events (for testing)."""
    backend = _get_audit_backend()
    return backend.get_events() if hasattr(backend, 'get_events') else []


def clear_audit_events():
    """Clear all audit events (for testing)."""
    backend = _get_audit_backend()
    if hasattr(backend, 'clear'):
        backend.clear()
