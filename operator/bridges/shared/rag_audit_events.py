"""L16: Audit Event Emission for RAG Queries.

Emits tamper-evident audit events for all RAG operations through the SAME
forge security-events writer the rest of the system uses, so every RAG event
joins the per-tenant L16 hash chain at ``<tenant_home>/global/forge/audit.jsonl``.

There is NO standalone, un-chained ``rag_audit.jsonl`` — that bypassed the
hash chain and ignored ``CORVIN_HOME`` / tenant routing.

Metadata-only compliance: no query text, retrieved documents, or provider
URLs ever land in the chain. Only the metadata-only fields below are emitted;
the ADR-0129 audit-detail floor in ``write_event`` is the structural backstop.
"""
from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _security_events():
    """Import the forge security-events module (dual import context).

    Loaded as the top-level ``security_events`` (forge/forge on sys.path in the
    adapter runtime) or as the ``forge.security_events`` package (console/tests).
    """
    try:
        from forge import security_events as se  # type: ignore[import]
        return se
    except ImportError:
        import security_events as se  # type: ignore[import]
        return se


def _forge_paths():
    try:
        from forge import paths as p  # type: ignore[import]
        return p
    except ImportError:
        import paths as p  # type: ignore[import]
        return p


class RAGAuditEventType(Enum):
    """L16 audit event types (registered as unknown→INFO in security_events;
    the metadata-only floor still applies)."""
    QUERY_EXECUTED = "rag.query_executed"
    QUERY_BLOCKED = "rag.query_blocked"
    PROVIDER_HEALTH_CHECK = "rag.provider_health_check"
    ERASURE_REQUESTED = "rag.erasure_requested"
    CLASSIFICATION_APPLIED = "rag.classification_applied"


# Metadata-only allow-list. Defence-in-depth on top of the ADR-0129 floor in
# write_event(): NEVER query text, retrieved documents, or provider URLs.
_SAFE_FIELDS = frozenset({
    "query_count",
    "provider_count",
    "classification",
    "status",
    "reason",
    "latency_ms",
    "cache_hit",
    "result_count",
    "provider_id",
})


class RAGAuditEmitter:
    """Emit L16 hash-chained audit events for RAG operations.

    Events are written to the per-tenant forge audit chain at
    ``<tenant_home>/global/forge/audit.jsonl`` via the shared
    ``forge.security_events.write_event`` chokepoint — the same chain the
    console and every other layer use.
    """

    def __init__(self, audit_dir: Optional[Path] = None):
        # ``audit_dir`` retained for backward compatibility but no longer used
        # to construct a standalone file — the per-tenant chain path is derived
        # from the tenant_id at emit time.
        pass

    def _chain_path(self, tenant_id: str) -> Path:
        """Resolve the per-tenant forge audit chain (honours CORVIN_HOME)."""
        return _forge_paths().tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

    def emit(
        self,
        event_type: RAGAuditEventType,
        tenant_id: str,
        details: dict,
    ) -> bool:
        """Emit a hash-chained audit event (metadata-only, no sensitive data).

        Args:
            event_type: Type of event
            tenant_id: Tenant identifier (per-tenant chain isolation)
            details: Metadata-only event details

        Returns:
            True if event emitted successfully
        """
        try:
            # Filter to the metadata-only allow-list (defence-in-depth; the
            # write_event floor is the structural backstop).
            detail_keys = set(details.keys())
            if not detail_keys.issubset(_SAFE_FIELDS):
                unsafe = detail_keys - _SAFE_FIELDS
                logger.warning(
                    f"Unsafe RAG audit fields detected (removing): {unsafe}"
                )
            safe_details = {k: v for k, v in details.items() if k in _SAFE_FIELDS}
            safe_details["tenant_id"] = tenant_id

            _security_events().write_event(
                self._chain_path(tenant_id),
                event_type.value,
                details=safe_details,
            )
            logger.debug(f"RAG audit event emitted to chain: {event_type.value}")
            return True

        except Exception as e:  # noqa: BLE001 — audit is best-effort
            logger.error(f"Failed to emit RAG audit event: {e}")
            return False

    def query_executed(
        self,
        tenant_id: str,
        provider_count: int,
        result_count: int,
        latency_ms: int,
        cache_hit: bool = False,
        classification: str = "PUBLIC",
    ) -> bool:
        """Emit query execution event."""
        return self.emit(
            RAGAuditEventType.QUERY_EXECUTED,
            tenant_id,
            {
                "provider_count": provider_count,
                "result_count": result_count,
                "latency_ms": latency_ms,
                "cache_hit": cache_hit,
                "classification": classification,
            },
        )

    def query_blocked(
        self,
        tenant_id: str,
        reason: str,
        classification: str = "UNKNOWN",
    ) -> bool:
        """Emit query blocked event."""
        return self.emit(
            RAGAuditEventType.QUERY_BLOCKED,
            tenant_id,
            {
                "reason": reason,
                "classification": classification,
            },
        )

    def erasure_requested(
        self,
        tenant_id: str,
        provider_id: str,
    ) -> bool:
        """Emit erasure request event."""
        return self.emit(
            RAGAuditEventType.ERASURE_REQUESTED,
            tenant_id,
            {
                "provider_id": provider_id,
            },
        )
