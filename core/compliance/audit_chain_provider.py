"""Audit Chain Provider — Singleton access to core immutable audit chain (ADR-0232/0233).

This module provides centralized access to the hash-chained audit log that is
the SINGLE SOURCE OF TRUTH for all compliance records (GDPR Art. 30/32).

Pattern:
    from core.compliance.audit_chain_provider import get_audit_chain_writer

    writer = get_audit_chain_writer(tenant_id="_default")
    await writer.write_event(event)

Key guarantees:
- One chain per tenant (isolated under tenants/{tenant_id}/global/forge/audit.jsonl)
- Hash-chained and tamper-resistant
- Immutable append-only (fail-closed on write errors)
- Thread-safe (internal locking)
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from core.compliance.audit_chain_writer import AuditChainWriter
from corvin_operator.bridges.shared.paths import tenant_audit_chain


# Singleton cache: tenant_id → AuditChainWriter instance
_CHAIN_WRITERS: dict[str, AuditChainWriter] = {}
_CHAIN_WRITERS_LOCK = threading.Lock()


def get_audit_chain_writer(tenant_id: str = "_default") -> AuditChainWriter:
    """Get or create the audit chain writer for a tenant.

    SINGLE SOURCE OF TRUTH for all audit events (ADR-0232/0233).
    Thread-safe singleton per tenant.

    Args:
        tenant_id: Tenant scope (default: "_default")

    Returns:
        AuditChainWriter instance (cached, reused across calls)

    Guarantees:
        - Same instance returned for same tenant_id
        - Path is tenant_audit_chain(tenant_id)
        - Hash-chain state loaded from disk on first access
        - Thread-safe
    """
    with _CHAIN_WRITERS_LOCK:
        if tenant_id not in _CHAIN_WRITERS:
            # Get canonical path for this tenant's audit chain
            chain_path = tenant_audit_chain(tenant_id)

            # Ensure parent directory exists
            chain_path.parent.mkdir(parents=True, exist_ok=True)

            # Create or load existing chain
            _CHAIN_WRITERS[tenant_id] = AuditChainWriter(str(chain_path))

        return _CHAIN_WRITERS[tenant_id]


# Convenience alias for backward compatibility
get_audit_backend = get_audit_chain_writer
