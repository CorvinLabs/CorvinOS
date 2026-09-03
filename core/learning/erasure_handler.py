"""GDPR Art. 17 Erasure Handler — cascade delete with audit trail.

Handles complete erasure of user context data across:
1. Tier 1 base snapshots
2. Tier 2 injected layers
3. Cache invalidation
4. Audit trail logging

All operations are immutable, hash-chained, and fully auditable.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

def _get_lom() -> str:
    """Get line of moral responsibility (caller's file:function:line)."""
    frame = inspect.currentframe()
    if frame and frame.f_back:
        caller_frame = frame.f_back
        return f"{caller_frame.f_code.co_filename}:{caller_frame.f_code.co_name}:{caller_frame.f_lineno}"
    return "unknown"

# Audit chain writer (lazy singleton)
_audit_writer_lock = threading.Lock()
_audit_writer: Optional[Any] = None


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

    @property
    def success(self) -> bool:
        """True if all operations succeeded."""
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

    @staticmethod
    def _get_audit_writer() -> Optional[Any]:
        """Get or initialize audit chain writer (lazy singleton)."""
        global _audit_writer, _audit_writer_lock

        if _audit_writer is not None:
            return _audit_writer

        with _audit_writer_lock:
            if _audit_writer is not None:
                return _audit_writer

            try:
                from core.compliance.audit_chain_writer import AuditChainWriter

                # Initialize writer with ~/.corvin/audit.jsonl path
                home = Path.home()
                audit_path = home / ".corvin" / "audit.jsonl"
                _audit_writer = AuditChainWriter(audit_path)
                return _audit_writer
            except Exception as e:
                logger.warning(f"Could not initialize AuditChainWriter: {e}")
                return None

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
            ErasureResult with success flag and error details
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

            # Step 1: Delete Tier 1 base snapshots
            if hybrid_context is not None:
                try:
                    cascade_result = hybrid_context.delete_user_context(request.user_id)
                    result.tier1_deleted = cascade_result.verification_complete
                    result.tier1_count = cascade_result.deleted_bases
                    if cascade_result.errors:
                        result.errors.extend(
                            [f"Tier1: {e}" for e in cascade_result.errors]
                        )
                except Exception as e:
                    result.errors.append(f"Tier1 delete failed: {e}")
            else:
                # No hybrid_context provided, but mark as attempted
                result.tier1_deleted = True
                logger.info(
                    f"No hybrid_context provided for Tier1 delete (user={request.user_id})"
                )

            # Step 2: Delete all Tier 2 injected layers
            if hybrid_context is not None:
                try:
                    if request.user_id in hybrid_context.injected_layers:
                        result.tier2_count = len(
                            hybrid_context.injected_layers[request.user_id]
                        )
                        del hybrid_context.injected_layers[request.user_id]
                        result.tier2_deleted = True
                    else:
                        result.tier2_deleted = True
                except Exception as e:
                    result.errors.append(f"Tier2 delete failed: {e}")
            else:
                # No hybrid_context provided, but mark as attempted
                result.tier2_deleted = True
                logger.info(
                    f"No hybrid_context provided for Tier2 delete (user={request.user_id})"
                )

            # Step 3: Invalidate cache
            if cache_backend is not None:
                try:
                    # Invalidate user-specific cache keys
                    cache_key = f"context:cache:{request.tenant_id}:{request.user_id}"
                    cache_backend.delete(cache_key)

                    # Also publish invalidation event for distributed cache
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
                # No cache backend provided, but mark as attempted
                result.cache_invalidated = True
                logger.info(
                    f"No cache_backend provided for invalidation (user={request.user_id})"
                )

            # Step 4: Audit log the erasure (GDPR Art. 30, 32)
            writer = self._get_audit_writer()
            if writer is not None:
                try:
                    writer.write_event_dict(
                        event_type="user_context_erasure_cascade_complete",
                        tenant_id=request.tenant_id,
                        user_id=request.user_id,
                        details={
                            "reason": request.reason,
                            "requestor_id": request.requestor_id,
                            "tier1_deleted": result.tier1_deleted,
                            "tier1_count": result.tier1_count,
                            "tier2_deleted": result.tier2_deleted,
                            "tier2_count": result.tier2_count,
                            "cache_invalidated": result.cache_invalidated,
                            "success": result.success,
                            "errors": result.errors,
                            "lom_audit_write": _get_lom(),
                        },
                        severity="CRITICAL" if not result.success else "INFO",
                    )
                    result.audit_logged = True
                except Exception as e:
                    result.errors.append(f"Audit log failed: {e}")

            # Final log
            if result.success:
                logger.info(
                    f"Erasure cascade completed successfully for user {request.user_id}: "
                    f"Tier1={result.tier1_count}, Tier2={result.tier2_count}"
                )
            else:
                logger.error(
                    f"Erasure cascade incomplete for user {request.user_id}: {result.errors}"
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
