"""Phase 3: Feedback Processor — Wiring Closure (ADR-0876).

Closes the feedback→optimizer→config→execution loop:
  1. User gives feedback on a Skill decision
  2. Optimizer analyzes feedback + decides on parameter deltas
  3. FeedbackProcessor applies deltas to Skill config (via SkillAdapter)
  4. Next execution loads the updated config and behaves differently

This module is the CLOSURE: it bridges feedback events and config updates.
Without it, optimizer decisions (parameter deltas) have no effect on real skill execution.

Fail-soft: feedback processing errors are logged but never raised to break
user experience. Audit trail records what happened.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizerDelta:
    """Result of optimizer analysis: proposed parameter changes."""

    skill_id: str  # e.g., "os.delegation_router"
    tenant_id: str  # Tenant scope
    param_deltas: dict[str, float]  # {param_name: delta_value}
    confidence_delta: float  # Improvement score (positive or negative)
    reason: str = "optimizer_computed"  # Why we're making this change


class FeedbackProcessor:
    """Processes feedback and applies config deltas to Skills (ADR-0876).

    The processor acts as the glue between the feedback/outcome signals and
    the SkillAdapter that persists optimized config. When an optimizer
    computes parameter deltas from user feedback, the processor applies them
    atomically and emits audit events.

    Guarantees:
    - Atomic: deltas are applied all-or-nothing (via SkillAdapter._locked)
    - Audited: every change is hash-chained to the audit trail
    - Fail-soft: processing errors are logged but never raise exceptions
    - Synchronous: next skill execution immediately sees the new config
    """

    def __init__(self):
        """Initialize the feedback processor."""
        self._import_adapter_on_first_use = True  # Lazy-load SkillAdapter

    def process_optimizer_delta(
        self,
        delta: OptimizerDelta,
        *,
        dry_run: bool = False,
    ) -> bool:
        """Apply an optimizer-computed delta to Skill config.

        Args:
            delta: OptimizerDelta with skill_id, tenant_id, param_deltas, confidence_delta
            dry_run: If True, compute delta but don't persist (for testing)

        Returns:
            True if delta was successfully applied, False if processing failed

        Side effects (if dry_run=False):
            - Calls SkillAdapter.apply_config_delta() (audit-first, fail-closed)
            - Emits skill_config_updated audit event (hash-chained)
            - Persists new config to disk
            - Next skill execution loads the new config
        """
        try:
            from core.skills.os_skills.skill_adapter import SkillAdapter
        except ImportError as e:
            logger.error(
                "FeedbackProcessor: cannot import SkillAdapter: %s — config update skipped",
                type(e).__name__,
            )
            return False

        if dry_run:
            logger.info(
                "FeedbackProcessor (dry-run): would apply delta to %s: %r "
                "(confidence_delta: %.2f%%)",
                delta.skill_id,
                delta.param_deltas,
                delta.confidence_delta * 100,
            )
            return True

        try:
            adapter = SkillAdapter(
                skill_id=delta.skill_id,
                tenant_id=delta.tenant_id,
            )
            # Apply delta (audit-first, fail-closed on audit write)
            updated_config = adapter.apply_config_delta(
                param_deltas=delta.param_deltas,
                confidence_delta=delta.confidence_delta,
            )
            logger.info(
                "FeedbackProcessor: applied delta to %s (tenant=%s, confidence_delta=%.2f%%) — "
                "next execution will use new config",
                delta.skill_id,
                delta.tenant_id,
                delta.confidence_delta * 100,
            )
            return True

        except Exception as e:  # noqa: BLE001 — fail-soft, log and continue
            logger.error(
                "FeedbackProcessor: failed to apply delta to %s (tenant=%s): %s",
                delta.skill_id,
                delta.tenant_id,
                type(e).__name__,
            )
            return False

    def process_feedback_and_optimize(
        self,
        feedback_event: Any,  # FeedbackEvent
        optimizer: Any,  # Optimizer (e.g., confidence_optimizer)
        *,
        dry_run: bool = False,
    ) -> bool:
        """End-to-end: feedback → optimize → apply delta.

        This is the full closure: given a feedback event and an optimizer,
        compute the parameter delta and apply it to the Skill config.

        Args:
            feedback_event: A FeedbackEvent (from feedback_sink.py)
            optimizer: An optimizer instance with compute_delta() method
            dry_run: If True, don't persist (for testing)

        Returns:
            True if the full cycle succeeded, False if anything failed

        Side effects:
            - Calls optimizer.compute_delta(feedback_event) → OptimizerDelta
            - Calls apply_config_delta() to persist the delta
        """
        try:
            # Step 1: Optimizer analyzes feedback → computes delta
            delta_result = optimizer.compute_delta(feedback_event)
            if not isinstance(delta_result, OptimizerDelta):
                logger.warning(
                    "FeedbackProcessor: optimizer.compute_delta() returned invalid type: %s",
                    type(delta_result).__name__,
                )
                return False

            # Step 2: Apply delta to Skill config (closes the loop)
            return self.process_optimizer_delta(delta_result, dry_run=dry_run)

        except Exception as e:  # noqa: BLE001 — fail-soft
            logger.error(
                "FeedbackProcessor: end-to-end cycle failed: %s",
                type(e).__name__,
            )
            return False


def create_feedback_processor() -> FeedbackProcessor:
    """Factory for creating a FeedbackProcessor instance."""
    return FeedbackProcessor()
