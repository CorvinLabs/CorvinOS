"""Audit Integration for Learning Tuning (Fix #8 — Audit Bypass).

Ensures ALL optimizer tuning operations emit audit events in a fail-closed manner.
No silent optimization: every parameter change is audited before application.

Compliance: ADR-0232/0233 (audit-first), GDPR Art. 30/32 (record-keeping).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class TuningOperationType(str, Enum):
    """Classification of tuning operations (for audit event filtering)."""
    GRADIENT_STEP = "gradient_step"           # Automated gradient descent
    FEEDBACK_SIGNAL = "feedback_signal"       # User feedback → parameter adjustment
    ROLLBACK = "rollback"                     # Restore to previous state
    MANUAL_SET = "manual_set"                 # Operator-initiated state change
    CONVERGENCE_CHECK = "convergence_check"   # Detection/logging only (not a change)
    CONSERVATIVE_MODE = "conservative_mode"   # Mode transition (not a param change)


@dataclass(frozen=True)
class TuningOperation:
    """Immutable record of one tuning operation (pre-audit)."""
    operation_type: TuningOperationType
    reason: str  # Why this change is being made
    old_values: Dict[str, float]  # Snapshot before change
    new_values: Dict[str, float]  # Proposed new values
    details: Dict[str, Any]  # Extra context (gradients, feedback confidence, etc.)


class AuditIntegration:
    """Wraps optimizer tuning to ensure audit-first behavior (fail-closed).

    Workflow:
    1. Optimizer proposes a TuningOperation
    2. AuditIntegration emits audit event FIRST via the chain writer
    3. If audit commit succeeds, apply the change
    4. If audit commit fails, raise RuntimeError and DO NOT apply change
    """

    def __init__(
        self,
        tenant_id: str,
        loop_id: str,
        audit_fn: Optional[Callable[..., str]] = None,
    ):
        """Initialize audit integration.

        Args:
            tenant_id: Tenant identifier (e.g., "_default")
            loop_id: Loop identifier (e.g., "meta", "core", "infra")
            audit_fn: Optional callable(event_type, *, tenant_id, details) -> audit_ref
                     Defaults to core_audit_event from event_persistence
        """
        self.tenant_id = tenant_id
        self.loop_id = loop_id

        if audit_fn is None:
            # Import lazily to avoid circular dependencies
            from core.learning.event_persistence import core_audit_event
            audit_fn = core_audit_event

        self._audit_fn = audit_fn
        self._operation_count = 0
        self._failed_audits = 0
        self._successful_audits = 0

    def audit_and_apply(
        self,
        operation: TuningOperation,
        apply_callback: Callable[[Dict[str, float]], None],
    ) -> str:
        """Audit-first application: emit event BEFORE applying change.

        Args:
            operation: The tuning operation to perform
            apply_callback: Function that applies change (called ONLY after audit succeeds)
                           Signature: apply_callback(new_values) -> None

        Returns:
            audit_ref: The audit event reference (uuid4)

        Raises:
            RuntimeError: If audit write does not commit (fail-closed)
        """
        self._operation_count += 1

        # Compute the delta for audit record
        changes = {
            name: {
                "old": float(operation.old_values.get(name, 0.0)),
                "new": float(operation.new_values.get(name, 0.0)),
            }
            for name in operation.new_values.keys()
            if operation.new_values.get(name) != operation.old_values.get(name)
        }

        if not changes:
            logger.debug(f"[AuditIntegration] No changes detected; skipping audit")
            return ""  # No-op; return empty ref

        # Emit audit event to the core chain FIRST
        audit_details = {
            "loop_id": self.loop_id,
            "operation_type": operation.operation_type.value,
            "reason": operation.reason,
            "operation_count": self._operation_count,
            "changes": changes,
            **operation.details,  # Extra context
        }

        try:
            audit_ref = self._audit_fn(
                "learning.hyperparameter_changed",
                tenant_id=self.tenant_id,
                details=audit_details,
            )
            logger.info(
                f"[AuditIntegration] {self.loop_id}: "
                f"{operation.operation_type.value} audited (ref={audit_ref})"
            )
        except RuntimeError as e:
            self._failed_audits += 1
            logger.error(
                f"[AuditIntegration] Audit FAILED for {operation.operation_type.value}: {e} "
                f"— refusing to apply change (fail-closed)"
            )
            raise RuntimeError(
                f"Tuning audit failed ({self.loop_id}): {e} — change NOT applied"
            ) from e

        # Audit succeeded; NOW apply the change
        try:
            apply_callback(operation.new_values)
            self._successful_audits += 1
            logger.info(
                f"[AuditIntegration] {self.loop_id}: "
                f"Change applied after audit success (ref={audit_ref})"
            )
            return audit_ref
        except Exception as e:
            # Apply callback failed — log but don't re-raise.
            # The change was audited (immutable record exists) even though
            # the state update failed. This is acceptable: the audit trail
            # is the ground truth; transient apply failures are recoverable
            # on restart (state will be loaded from checkpoint).
            logger.error(
                f"[AuditIntegration] Apply callback failed AFTER audit (ref={audit_ref}): {e} "
                f"— audit trail is source of truth"
            )
            return audit_ref

    def get_stats(self) -> Dict[str, int]:
        """Return audit operation statistics."""
        return {
            "total_operations": self._operation_count,
            "successful_audits": self._successful_audits,
            "failed_audits": self._failed_audits,
        }
