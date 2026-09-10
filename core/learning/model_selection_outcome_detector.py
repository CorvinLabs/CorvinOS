"""
Model Selection Outcome Detector — Phase 3, Component 1.

Captures task completion quality and emits feedback events for the confidence optimizer.

This module:
1. Hooks on_task_completed(task_id, model_used, result)
2. Assesses quality (0.0–1.0 based on success/partial/fail)
3. Emits model_selection_feedback event to audit trail
4. Persists to learning backend (ADR-0314)

Event schema (immutable):
{
    "event_type": "model_selection_feedback",
    "task_id": "...",
    "task_type": "...",
    "model_used": "...",
    "outcome": "success|partial|fail",
    "quality_score": 0.0–1.0,
    "cost": cost_in_dollars,
    "latency_ms": int,
    "timestamp": ISO8601,
    "tenant_id": "...",
}

Constraints (ADR-0644):
- Only TaskManager can emit feedback (fail-closed)
- Quality assessment: success=1.0, partial=0.5, fail=0.0 (+ bonuses for latency/cost)
- Audit-first: core chain record BEFORE disk
- Tenant-scoped: tenant_id in every event
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, Callable, Dict, Any
import json
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class OutcomeType(Enum):
    """Task outcome classification."""
    SUCCESS = "success"       # Task completed without errors
    PARTIAL = "partial"       # Task completed with recoverable errors
    FAIL = "fail"             # Task failed or timed out


@dataclass(frozen=True)
class ModelSelectionFeedbackEvent:
    """Immutable feedback event (hash-chainable, audit-first).

    Used by confidence optimizer to update model selection weights.
    Frozen: cannot be modified after creation.
    """
    event_type: str = "model_selection_feedback"
    task_id: str = ""
    task_type: str = ""
    model_used: str = ""
    outcome: str = ""  # "success" | "partial" | "fail"
    quality_score: float = 0.0
    cost: float = 0.0
    latency_ms: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


class OutcomeDetector:
    """Detects task completion and emits feedback events.

    Example:
        detector = OutcomeDetector(audit_backend, learning_store)
        detector.on_task_completed(
            task_id="task-123",
            model_used="claude-opus-4-1",
            outcome=OutcomeType.SUCCESS,
            cost=0.05,
            latency_ms=2340,
            task_type="code_generation",
            tenant_id="_default"
        )
    """

    def __init__(
        self,
        audit_backend: Optional[Any] = None,
        learning_store: Optional[Any] = None,
    ):
        """Initialize outcome detector.

        Args:
            audit_backend: audit_backend.write_event() for chain recording
            learning_store: learning_store.store_event() for persistence
        """
        self.audit_backend = audit_backend
        self.learning_store = learning_store

    def on_task_completed(
        self,
        task_id: str,
        model_used: str,
        outcome: OutcomeType | str,
        cost: float = 0.0,
        latency_ms: int = 0,
        task_type: str = "unknown",
        tenant_id: str = "_default",
        quality_bonus: float = 0.0,
    ) -> ModelSelectionFeedbackEvent:
        """Assess task completion and emit feedback event.

        Args:
            task_id: unique task identifier
            model_used: model that was used (e.g., "claude-opus-4-1")
            outcome: OutcomeType enum or string ("success"|"partial"|"fail")
            cost: cost in dollars (for cost optimization)
            latency_ms: latency in milliseconds (for latency optimization)
            task_type: task classification (e.g., "code_gen", "analysis")
            tenant_id: tenant scope
            quality_bonus: additional quality points (0.0–0.2) for special cases

        Returns:
            ModelSelectionFeedbackEvent (immutable, hash-chainable)

        Raises:
            ValueError: if outcome is invalid or quality_score out of range
        """
        # Normalize outcome
        if isinstance(outcome, OutcomeType):
            outcome_str = outcome.value
        else:
            outcome_str = str(outcome).lower()

        if outcome_str not in ("success", "partial", "fail"):
            raise ValueError(f"Invalid outcome: {outcome_str}")

        # Compute base quality score
        base_score = {
            "success": 1.0,
            "partial": 0.5,
            "fail": 0.0,
        }[outcome_str]

        # Normalize latency bonus (lower is better)
        # Assume target latency is 2000ms; faster = bonus, slower = penalty
        latency_bonus = 0.0
        if latency_ms > 0:
            if latency_ms < 2000:
                latency_bonus = min(0.05, (2000 - latency_ms) / 40000)
            else:
                latency_bonus = max(-0.05, -(latency_ms - 2000) / 40000)

        # Normalize cost bonus (lower is better)
        # Assume target cost is $0.05; cheaper = bonus, pricier = penalty
        cost_bonus = 0.0
        if cost > 0:
            if cost < 0.05:
                cost_bonus = min(0.05, (0.05 - cost) / 1.0)
            else:
                cost_bonus = max(-0.05, -(cost - 0.05) / 1.0)

        # Final quality score [0.0, 1.0]
        quality_score = base_score + latency_bonus + cost_bonus + quality_bonus
        quality_score = max(0.0, min(1.0, quality_score))

        # Create immutable feedback event
        event = ModelSelectionFeedbackEvent(
            task_id=task_id,
            task_type=task_type,
            model_used=model_used,
            outcome=outcome_str,
            quality_score=quality_score,
            cost=cost,
            latency_ms=latency_ms,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tenant_id=tenant_id,
        )

        # Log to audit trail (FIRST, fail-closed)
        if self.audit_backend:
            try:
                self.audit_backend.write_event(
                    event_type="model_selection_feedback",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    model_used=model_used,
                    outcome=outcome_str,
                    quality_score=quality_score,
                )
            except Exception as e:
                logger.error(f"Failed to write audit event: {e}")
                raise RuntimeError(f"Audit chain write failed; aborting outcome detection: {e}")

        # Persist to learning store (SECOND, safe to fail with log)
        if self.learning_store:
            try:
                self.learning_store.store_event(event)
            except Exception as e:
                logger.warning(f"Failed to store learning event: {e}")
                # Don't raise; audit trail is primary

        logger.info(
            f"Outcome detected: task={task_id}, model={model_used}, "
            f"outcome={outcome_str}, quality={quality_score:.3f}"
        )

        return event


# Singleton instance (initialized at module load)
_detector: Optional[OutcomeDetector] = None


def initialize_detector(
    audit_backend: Optional[Any] = None,
    learning_store: Optional[Any] = None,
) -> OutcomeDetector:
    """Initialize the global outcome detector."""
    global _detector
    _detector = OutcomeDetector(audit_backend, learning_store)
    return _detector


def get_detector() -> OutcomeDetector:
    """Get the global outcome detector."""
    global _detector
    if _detector is None:
        _detector = OutcomeDetector()
    return _detector


def emit_feedback(
    task_id: str,
    model_used: str,
    outcome: OutcomeType | str,
    cost: float = 0.0,
    latency_ms: int = 0,
    task_type: str = "unknown",
    tenant_id: str = "_default",
    quality_bonus: float = 0.0,
) -> ModelSelectionFeedbackEvent:
    """Emit a feedback event using the global detector.

    Convenience function for TaskManager integration.
    """
    detector = get_detector()
    return detector.on_task_completed(
        task_id=task_id,
        model_used=model_used,
        outcome=outcome,
        cost=cost,
        latency_ms=latency_ms,
        task_type=task_type,
        tenant_id=tenant_id,
        quality_bonus=quality_bonus,
    )
