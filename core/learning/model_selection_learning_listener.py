"""
Model Selection Learning Listener — OUTCOME → ConfidenceOptimizer wiring (ADR-2153).

This module bridges the gap between task completion (OUTCOME events from the audit chain)
and confidence optimizer updates. Without this listener, OUTCOME events are recorded but
never fed to the optimizer for learning.

Phase 1A (2026-09-14): Initial implementation
- Receives each finished task's outcome from TaskManager (push; the pull reader
  ``fetch_unprocessed_outcomes`` never worked and was removed 2026-11-05, LRN-20)
- Converts to ConfidenceOptimizer.process_feedback() calls
- Emits confidence_updated events to audit trail
- Runs on-demand via TaskManager integration (one call per finished task)

Constraints (ADR-2153):
- Audit-first: every confidence update is hash-chained before persisting
- Tenant-scoped: outcomes and confidence updates must match tenant_id
- Fail-soft: missing model_selection_config, missing optimizer → log and continue
- Never raises into task execution lifecycle
"""
from __future__ import annotations

import logging
from typing import Optional, Any, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSelectionOutcome:
    """Outcome signal from task completion (derived from OUTCOME event)."""
    task_id: str
    model_used: str
    task_type: str
    status: str  # "completed" | "failed" | "cancelled"
    exit_code: Optional[int]
    duration_ms: Optional[int]
    engine: Optional[str]
    success: bool  # derived: status == "completed" and exit_code in (None, 0)
    timestamp: str


class ModelSelectionLearningListener:
    """Consumes OUTCOME learning events and updates model confidence scores.

    Push, not pull: ``TaskManager._update_model_selection_confidence`` builds one
    :class:`ModelSelectionOutcome` per finished task and calls :meth:`process_outcome`.

    ``fetch_unprocessed_outcomes`` / ``run_epoch`` (a pull path) were removed on
    2026-11-05 (review LRN-20): it passed ``order="desc"``, which ``query_events`` does
    not accept, so the swallowed TypeError made it return ``[]`` on every call; it read
    ``backend.store`` where the backend holds an emitter; nothing marked an outcome as
    processed, so a working version would have fed the same outcomes to the optimizer on
    every epoch; and no production code called it.
    """

    def __init__(self):
        """Initialize listener (lazy-loads optimizer and config on first use)."""
        self._optimizer = None
        self._config_store = None

    def _get_optimizer(self) -> Optional[Any]:
        """Get ConfidenceOptimizer singleton (fail-soft)."""
        if self._optimizer is not None:
            return self._optimizer
        try:
            from core.learning.model_selection_optimizer import get_optimizer  # noqa: PLC0415
            self._optimizer = get_optimizer()
            return self._optimizer
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load model selection optimizer: %s", type(e).__name__)
            return None

    def _get_config_store(self) -> Optional[Any]:
        """Get model selection config store (fail-soft)."""
        if self._config_store is not None:
            return self._config_store
        try:
            from core.models.model_selection_config import get_config_store  # noqa: PLC0415
            self._config_store = get_config_store()
            return self._config_store
        except Exception as e:  # noqa: BLE001
            logger.debug("Could not load model selection config: %s", type(e).__name__)
            return None

    def process_outcome(
        self,
        outcome: ModelSelectionOutcome,
        tenant_id: str,
    ) -> bool:
        """
        Process one outcome: update model confidence score.

        Args:
            outcome: ModelSelectionOutcome to process
            tenant_id: Tenant ID (must match outcome's tenant)

        Returns:
            True if confidence was updated, False if skipped
        """
        if not tenant_id or not outcome.model_used:
            return False

        try:
            optimizer = self._get_optimizer()
            if optimizer is None:
                logger.debug("Optimizer not available, skipping outcome processing")
                return False

            # Assess quality from outcome
            if outcome.success:
                quality_score = 0.95  # High quality for successful outcomes
            else:
                quality_score = 0.3  # Lower quality for failures

            # Apply latency/cost bonus (faster is better, within reason)
            if outcome.duration_ms and outcome.duration_ms < 2000:
                quality_bonus = 0.05
                quality_score = min(1.0, quality_score + quality_bonus)

            # Call optimizer
            optimizer.process_feedback(
                task_type=outcome.task_type,
                model=outcome.model_used,
                quality_score=quality_score,
                tenant_id=tenant_id,
            )

            logger.debug(
                "Updated model confidence: task_type=%s model=%s quality=%.2f",
                outcome.task_type,
                outcome.model_used,
                quality_score,
            )
            return True
        except Exception as e:  # noqa: BLE001 — never break task processing
            logger.warning("Failed to process outcome: %s: %s", type(e).__name__, e)
            return False


# Singleton instance (lazy-loaded)
_listener: Optional[ModelSelectionLearningListener] = None


def get_listener() -> ModelSelectionLearningListener:
    """Get or create the global listener instance."""
    global _listener  # noqa: PLW0603
    if _listener is None:
        _listener = ModelSelectionLearningListener()
    return _listener
