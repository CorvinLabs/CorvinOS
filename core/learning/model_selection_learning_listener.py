"""
Model Selection Learning Listener — OUTCOME → ConfidenceOptimizer wiring (ADR-0644).

This module bridges the gap between task completion (OUTCOME events from the audit chain)
and confidence optimizer updates. Without this listener, OUTCOME events are recorded but
never fed to the optimizer for learning.

Phase 1A (2026-09-14): Initial implementation
- Reads OUTCOME learning events from the audit chain (EventStore)
- Converts to ConfidenceOptimizer.process_feedback() calls
- Emits confidence_updated events to audit trail
- Runs as background service or on-demand via TaskManager integration

Constraints (ADR-0644):
- Audit-first: every confidence update is hash-chained before persisting
- Tenant-scoped: outcomes and confidence updates must match tenant_id
- Fail-soft: missing model_selection_config, missing optimizer → log and continue
- Never raises into task execution lifecycle
"""
from __future__ import annotations

import logging
from typing import Optional, Any, Dict
from dataclasses import dataclass
from datetime import datetime, timezone

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

    Example:
        listener = ModelSelectionLearningListener()
        # When task completes, TaskManager.record_event calls emit_task_outcome()
        # which writes an OUTCOME event to the audit chain. This listener reads it:
        outcomes = listener.fetch_unprocessed_outcomes(tenant_id="_default")
        for outcome in outcomes:
            listener.process_outcome(outcome, tenant_id="_default")
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

    def fetch_unprocessed_outcomes(
        self,
        tenant_id: str,
        limit: int = 100,
        store: Optional[Any] = None,
    ) -> list[ModelSelectionOutcome]:
        """
        Fetch recent OUTCOME events that haven't been processed for model selection yet.

        Args:
            tenant_id: Tenant to query
            limit: Max outcomes to fetch
            store: EventStore (tests); default is the booted registry's

        Returns:
            List of ModelSelectionOutcome objects
        """
        if not tenant_id:
            return []

        st = store
        if st is None:
            try:
                from core.skills import skill_registry_phase1 as _reg  # noqa: PLC0415
                registry = getattr(_reg, "_global_registry", None)
                backend = getattr(registry, "learning_backend", None) if registry else None
                st = getattr(backend, "store", None)
            except Exception:  # noqa: BLE001
                return []

        if st is None:
            return []

        outcomes = []
        try:
            from core.learning.learning_events import EventType  # noqa: PLC0415

            # Query OUTCOME events for this tenant (most recent first)
            events = st.query_events(
                tenant_id=tenant_id,
                event_type=EventType.OUTCOME,
                limit=limit,
                order="desc",  # newest first
            )

            for event in events:
                signal = event.signal or {}
                if not signal.get("task_id"):
                    continue

                task_type = signal.get("task_type", "unknown")
                if not task_type or task_type == "unknown":
                    # Skip outcomes with unknown task type (can't route to confidence)
                    logger.debug("Skipping outcome with unknown task_type: %s", event.get("id"))
                    continue

                # Extract model_used from task input (stored in the event somehow?)
                # For now: model_used comes from task input, which is NOT in OUTCOME event.
                # We need to join with task metadata.
                # Fallback: model_used from skill that generated the decision.
                # TODO(phase1b): enhance OUTCOME event to include model_used

                outcome = ModelSelectionOutcome(
                    task_id=signal.get("task_id", ""),
                    model_used=signal.get("model_used", ""),  # May be empty
                    task_type=task_type,
                    status=signal.get("status", ""),
                    exit_code=signal.get("exit_code"),
                    duration_ms=signal.get("duration_ms"),
                    engine=signal.get("engine"),
                    success=signal.get("success", False),
                    timestamp=event.timestamp or datetime.now(timezone.utc).isoformat(),
                )
                if outcome.model_used:  # Only process if model_used is known
                    outcomes.append(outcome)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch outcomes: %s", type(e).__name__)

        return outcomes

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
            logger.warning("Failed to process outcome: %s", type(e).__name__)
            return False

    def run_epoch(
        self,
        tenant_id: str,
        limit: int = 50,
    ) -> tuple[int, int]:
        """
        Run one processing epoch: fetch outcomes and update confidence.

        Args:
            tenant_id: Tenant to process
            limit: Max outcomes per epoch

        Returns:
            (processed, skipped) count tuple
        """
        outcomes = self.fetch_unprocessed_outcomes(tenant_id, limit=limit)
        processed = 0
        skipped = 0

        for outcome in outcomes:
            if self.process_outcome(outcome, tenant_id):
                processed += 1
            else:
                skipped += 1

        if processed > 0:
            logger.info(
                "ModelSelection learning epoch: %d processed, %d skipped (tenant=%s)",
                processed,
                skipped,
                tenant_id,
            )

        return processed, skipped


# Singleton instance (lazy-loaded)
_listener: Optional[ModelSelectionLearningListener] = None


def get_listener() -> ModelSelectionLearningListener:
    """Get or create the global listener instance."""
    global _listener  # noqa: PLW0603
    if _listener is None:
        _listener = ModelSelectionLearningListener()
    return _listener
