"""GDPR Art. 17 Erasure Handler — cascade delete with audit trail.

Handles complete erasure of user context data across:
1. Tier 1 base snapshots
2. Tier 2 injected layers
3. Cache invalidation
4. Audit trail logging (CORE hash-chained writer, fail-closed)

A tier that was NOT processed (no backend passed) is reported as NOT deleted:
``ErasureResult.success`` must never claim an erasure that touched nothing.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any

from .event_persistence import core_audit_event

logger = logging.getLogger(__name__)


def _get_lom() -> str:
    """Get line of moral responsibility (caller's file:function:line)."""
    frame = inspect.currentframe()
    if frame and frame.f_back:
        caller_frame = frame.f_back
        return f"{caller_frame.f_code.co_filename}:{caller_frame.f_code.co_name}:{caller_frame.f_lineno}"
    return "unknown"


@dataclass
class ErasureRequest:
    """GDPR Art. 17 erasure request."""

    user_id: str
    tenant_id: str
    requested_at: datetime
    reason: str  # "user_request", "compliance", "enforcement", etc.
    requestor_id: Optional[str] = None  # Who made the request (admin user_id)


@dataclass
class ErasureResult:
    """Result of cascade delete operation."""

    user_id: str
    tenant_id: str
    tier1_deleted: bool
    tier1_count: int
    tier2_deleted: bool
    tier2_count: int
    cache_invalidated: bool
    audit_logged: bool
    errors: list[str] = field(default_factory=list)
    completed_at: str = ""
    skipped: list[str] = field(default_factory=list)  # tiers with no backend supplied

    @property
    def success(self) -> bool:
        """True only if EVERY tier was actually processed and succeeded."""
        return (
            self.tier1_deleted
            and self.tier2_deleted
            and self.cache_invalidated
            and self.audit_logged
            and len(self.errors) == 0
        )


class ErasureHandler:
    """Handle GDPR erasure cascades with audit trail (Art. 17, 30, 32)."""

    def __init__(self):
        """Initialize erasure handler."""
        self._lock = threading.Lock()

    def process_erasure(
        self,
        request: ErasureRequest,
        hybrid_context: Optional[Any] = None,
        cache_backend: Optional[Any] = None,
    ) -> ErasureResult:
        """Process GDPR erasure request with cascade delete.

        Args:
            request: ErasureRequest with user_id, tenant_id, reason
            hybrid_context: HybridContextModel instance (optional, for Tier 1/2 delete)
            cache_backend: Cache backend instance (optional, for invalidation)

        Returns:
            ErasureResult. A tier whose backend was not supplied is reported as
            NOT deleted (listed in ``skipped``) — the result is then not a
            success, because nothing proves the data is gone.

        Raises:
            RuntimeError: if the core audit writer is unavailable / the audit
                record did not commit (fail-closed, ADR-0232/0233)
        """
        with self._lock:
            result = ErasureResult(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                tier1_deleted=False,
                tier1_count=0,
                tier2_deleted=False,
                tier2_count=0,
                cache_invalidated=False,
                audit_logged=False,
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )

            # Step 1 + 2: Tier 1 base snapshots and Tier 2 layers (one cascade,
            # verified by HybridContextModel.delete_user_context)
            if hybrid_context is not None:
                try:
                    cascade_result = hybrid_context.delete_user_context(request.user_id)
                    result.tier1_deleted = cascade_result.verification_complete
                    result.tier2_deleted = cascade_result.verification_complete
                    result.tier1_count = cascade_result.deleted_bases
                    result.tier2_count = cascade_result.deleted_layers
                    if cascade_result.errors:
                        result.errors.extend(
                            [f"Tier1/2: {e}" for e in cascade_result.errors]
                        )
                except Exception as e:
                    result.errors.append(f"Tier1/2 delete failed: {e}")
            else:
                result.skipped.extend(["tier1", "tier2"])
                logger.warning(
                    f"No hybrid_context provided — Tier1/Tier2 NOT erased (user={request.user_id})"
                )

            # Step 3: Invalidate cache
            if cache_backend is not None:
                try:
                    cache_key = f"context:cache:{request.tenant_id}:{request.user_id}"
                    cache_backend.delete(cache_key)

                    if hasattr(cache_backend, "publish"):
                        cache_backend.publish(
                            f"cache:invalidation:{request.tenant_id}",
                            f"user_id={request.user_id}",
                        )

                    result.cache_invalidated = True
                    logger.info(f"Cache invalidated for user {request.user_id}")
                except Exception as e:
                    result.errors.append(f"Cache invalidation failed: {e}")
            else:
                result.skipped.append("cache")
                logger.warning(
                    f"No cache_backend provided — cache NOT invalidated (user={request.user_id})"
                )

            # Step 4: Audit the erasure on the CORE chain (GDPR Art. 30, 32).
            # Counts and flags only; fail-closed — an unauditable erasure raises.
            complete = (
                result.tier1_deleted and result.tier2_deleted
                and result.cache_invalidated and not result.errors
            )
            core_audit_event(
                "user_context_erasure_cascade_complete",
                tenant_id=request.tenant_id,
                user=request.user_id,
                details={
                    "component": "erasure_handler",
                    "reason": request.reason,
                    "requestor_id": request.requestor_id,
                    "tier1_deleted": result.tier1_deleted,
                    "tier1_count": result.tier1_count,
                    "tier2_deleted": result.tier2_deleted,
                    "tier2_count": result.tier2_count,
                    "cache_invalidated": result.cache_invalidated,
                    "skipped": list(result.skipped),
                    "complete": complete,
                    "error_count": len(result.errors),
                    "lom_audit_write": _get_lom(),
                },
            )
            result.audit_logged = True

            if result.success:
                logger.info(
                    f"Erasure cascade completed successfully for user {request.user_id}: "
                    f"Tier1={result.tier1_count}, Tier2={result.tier2_count}"
                )
            else:
                logger.error(
                    f"Erasure cascade incomplete for user {request.user_id}: "
                    f"skipped={result.skipped}, errors={result.errors}"
                )

            return result


# Global instance (singleton)
_global_erasure_handler: Optional[ErasureHandler] = None
_global_erasure_handler_lock = threading.Lock()


def get_erasure_handler() -> ErasureHandler:
    """Get or create global erasure handler."""
    global _global_erasure_handler, _global_erasure_handler_lock

    if _global_erasure_handler is None:
        with _global_erasure_handler_lock:
            if _global_erasure_handler is None:
                _global_erasure_handler = ErasureHandler()

    return _global_erasure_handler
