"""Auto-Indexing Hook — Updates KG learning loop index on event write.

This module provides the hook that integrates the learning loop index
(Stream 2.1 / ADR-0907) into the event persistence pipeline. On every
learning event write:

1. Audit chain is written FIRST (synchronous, fail-closed)
2. Index is updated SECOND (async-safe, non-blocking)

Semantic guarantees:
- Audit write is mandatory; if it fails, index update is skipped
- Index update failure is logged but does not block
- Index miss (event without a loop_id) is gracefully skipped
- All status transitions are audited

ADR-0907: KG MCP Learning-Loop Index
ADR-0314: Learning Infrastructure
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class IndexUpdateHook:
    """Async-safe hook for updating learning loop index on event write."""

    def __init__(self, learning_loop_service):
        """Initialize hook with a LearningLoopService instance.

        Args:
            learning_loop_service: Instance of LearningLoopService (from Stream 2.1)
        """
        self.service = learning_loop_service

    async def update_on_event(
        self,
        tenant_id: str,
        plugin_id: Optional[str] = None,
        loop_id: Optional[str] = None,
        feedback_signal: Optional[float] = None,
        event_type: Optional[str] = None,
    ) -> bool:
        """Update index on new learning event (non-blocking).

        This method is called from EventStore.write_event() AFTER the audit
        chain write has succeeded. It updates the learning loop index entry
        with the new event metrics.

        Args:
            tenant_id: Tenant identifier
            plugin_id: Plugin identifier (optional; skip update if missing)
            loop_id: Loop identifier (optional; skip update if missing)
            feedback_signal: Optional signal value (confidence, latency, etc.)
            event_type: Optional event type (for logging/audit)

        Returns:
            True if updated, False if skipped (no loop_id) or error (logged)

        Raises:
            Nothing - failures are logged and never block the caller
        """
        # Validate tenant and loop identifiers
        if not tenant_id:
            logger.warning("No tenant_id in index update; skipping")
            return False

        if not plugin_id or not loop_id:
            # Events without a learning_loop_id are gracefully skipped
            return False

        # Validate that the service is bound to the same tenant
        if self.service.tenant_id != tenant_id:
            logger.error(
                f"Tenant mismatch in index hook: "
                f"service={self.service.tenant_id!r}, event={tenant_id!r}"
            )
            return False

        try:
            # Run the update in a thread pool to avoid blocking
            # (LevelDB I/O can be slow)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._update_entry,
                plugin_id,
                loop_id,
                feedback_signal,
                event_type,
            )
            return result is not None
        except Exception as exc:
            # Log failure but never raise - failure should not propagate
            logger.error(
                f"Index update failed for {tenant_id}:{plugin_id}:{loop_id}: {exc}",
                exc_info=True,
            )
            return False

    def _update_entry(
        self,
        plugin_id: str,
        loop_id: str,
        feedback_signal: Optional[float],
        event_type: Optional[str],
    ):
        """Synchronous helper for updating entry (runs in executor).

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier
            feedback_signal: Optional signal value
            event_type: Optional event type

        Returns:
            Updated entry, or None on error
        """
        try:
            return self.service.update_on_event(
                plugin_id=plugin_id,
                loop_id=loop_id,
                feedback_signal=feedback_signal,
                event_type=event_type,
            )
        except Exception as exc:
            logger.error(f"Failed to update index entry: {exc}", exc_info=True)
            return None


# ── Factory for lazy initialization ──────────────────────────────────────────


_hook_instances: dict[str, IndexUpdateHook] = {}
_service_instances: dict[str, object] = {}


def get_index_update_hook(tenant_id: str) -> Optional[IndexUpdateHook]:
    """Get or create an index update hook for a tenant (lazy).

    This factory avoids creating services for all tenants at boot time.
    Each tenant gets a single long-lived service instance.

    Args:
        tenant_id: Tenant identifier

    Returns:
        IndexUpdateHook instance, or None if service cannot be initialized
    """
    from core.tenants.validation import validate_tenant_id

    try:
        tenant_id = validate_tenant_id(tenant_id)

        if tenant_id not in _hook_instances:
            # Lazy-import to avoid circular dependencies
            try:
                from core.knowledge_graph.mcp.learning_loop_service import (
                    LearningLoopService,
                )

                service = LearningLoopService(tenant_id)
                _service_instances[tenant_id] = service
                _hook_instances[tenant_id] = IndexUpdateHook(service)
            except Exception as exc:
                logger.error(
                    f"Failed to initialize learning loop service for {tenant_id}: {exc}",
                    exc_info=True,
                )
                return None

        return _hook_instances[tenant_id]
    except ValueError as exc:
        logger.error(f"Invalid tenant_id: {exc}")
        return None


def cleanup_hooks():
    """Clean up all hook instances (call at shutdown)."""
    for service in _service_instances.values():
        try:
            service.close()
        except Exception as exc:
            logger.warning(f"Error closing service: {exc}")
    _hook_instances.clear()
    _service_instances.clear()
