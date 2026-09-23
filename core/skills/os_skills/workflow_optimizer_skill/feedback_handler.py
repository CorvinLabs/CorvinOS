"""Stream 1 Phase 2: Feedback Handler for Workflow Optimizer Skill (ADR-0314).

Processes operator feedback on routing decisions and updates Bayesian
confidence scores. Integrates with EventStore (Stream 4).

**Compliance:**
- GDPR Art. 30/32: All feedback events audited
- ADR-0314: Learning events emitted + persisted
- Fail-closed: missing feedback type → rejected
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from uuid import uuid4

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """Types of feedback the operator can provide on routing decisions."""
    CORRECT = "correct"  # Routing was correct
    INCORRECT = "incorrect"  # Routing was wrong
    PARTIAL = "partial"  # Routing was partially correct (right model, wrong reason)
    SKIP = "skip"  # No feedback / don't count


@dataclass(frozen=True)
class RoutingFeedback:
    """Immutable feedback on a routing decision.

    Captures operator assessment of whether the routing choice was appropriate.
    """
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""  # The task that was routed
    routed_model: str = ""  # The model we chose (haiku/sonnet/opus)
    task_complexity: str = ""  # Complexity tier we assigned (simple/medium/complex)
    feedback_type: FeedbackType = FeedbackType.SKIP
    confidence_score: float = 0.5  # Operator's confidence in routing (0.0-1.0)
    reason: Optional[str] = None  # Optional explanation from operator
    tenant_id: str = ""  # Tenant scope (GDPR Art. 32)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def __post_init__(self):
        """Validate feedback (frozen dataclass, fail-closed)."""
        if not self.task_id:
            raise ValueError("task_id required")
        if not self.routed_model:
            raise ValueError("routed_model required")
        if not self.tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")
        if not isinstance(self.confidence_score, (int, float)):
            raise ValueError("confidence_score must be numeric")
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError(f"confidence_score must be in [0.0, 1.0], got {self.confidence_score}")


class FeedbackHandler:
    """Handles operator feedback on routing decisions (Phase 2).

    Subscribes to feedback events, extracts routing assessment, and queues
    confidence updates (deferred to ConfidenceCalculator).

    **Responsibility:**
    1. Receive feedback from operator (via console, API, CLI)
    2. Validate & normalize (fail-closed)
    3. Emit LearningEvent (outcome_feedback type)
    4. Signal ConfidenceCalculator to recompute

    **No state change here:** this handler is pure I/O (receive → emit).
    State updates happen in ConfidenceCalculator (Week 2).
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str,
        skill_id: str = "os.workflow_optimizer_l5",
        skill_version: str = "1.0.0",
    ):
        """Initialize feedback handler.

        Args:
            event_store: EventStore instance (from Stream 4, Week 1)
            tenant_id: Tenant scope (GDPR Art. 32)
            skill_id: Skill identifier (for audit trail)
            skill_version: Skill version at processing time
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version
        self.feedback_count = 0

    def process_feedback(self, feedback: RoutingFeedback) -> Optional[str]:
        """Process operator feedback on routing decision (audit-first).

        Flow:
        1. Validate feedback (fail-closed)
        2. Emit LearningEvent to EventStore (outcome_feedback type)
        3. Return event_id for tracking
        4. Caller (ConfidenceCalculator) reads event → updates weights

        Args:
            feedback: RoutingFeedback with task_id, routed_model, assessment

        Returns:
            event_id (uuid) of the emitted LearningEvent

        Raises:
            ValueError: Invalid feedback format
            RuntimeError: EventStore write failed (audit-first fail-closed)
        """
        # Validate tenant isolation (GDPR Art. 32, fail-closed)
        if feedback.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: feedback tenant={feedback.tenant_id}, "
                f"handler tenant={self.tenant_id}"
            )

        # Skip non-feedback
        if feedback.feedback_type == FeedbackType.SKIP:
            logger.debug(f"Skipping feedback (type=SKIP): {feedback.feedback_id}")
            return None

        # Build learning event payload
        signal = {
            "feedback_type": feedback.feedback_type.value,
            "task_id": feedback.task_id,
            "routed_model": feedback.routed_model,
            "task_complexity": feedback.task_complexity,
            "operator_confidence": feedback.confidence_score,
            # Note: reason is NOT included (PII scrubbing — see event_store.py)
        }

        # Create LearningEvent (outcome_feedback type)
        # NOTE: reason field is deliberately excluded from signal to prevent PII leakage
        event = LearningEvent.create(
            event_type=EventType.FEEDBACK,  # outcome_feedback
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal=signal,
            skill_version=self.skill_version,
            lom="workflow_optimizer_skill.feedback_handler:process_feedback:L120",
        )

        # Emit to EventStore (audit-first: chain write happens FIRST)
        # If this raises RuntimeError, nothing is persisted (fail-closed per ADR-0314)
        try:
            self.event_store.write_event(event)
        except (RuntimeError, IOError) as e:
            logger.error(
                f"Failed to write feedback event: {e} — feedback LOST (audit-first failure)"
            )
            raise RuntimeError(
                f"Feedback event rejected by audit chain: {e}"
            ) from e

        self.feedback_count += 1
        logger.info(
            f"Feedback processed: task={feedback.task_id}, model={feedback.routed_model}, "
            f"type={feedback.feedback_type.value}, confidence={feedback.confidence_score}, "
            f"total_feedback_count={self.feedback_count}"
        )
        return event.event_id

    def get_recent_feedback(
        self,
        limit: int = 100,
        since: Optional[str] = None,
    ) -> list[LearningEvent]:
        """Retrieve recent feedback events for this skill (query interface).

        Used by ConfidenceCalculator to fetch feedback for recomputation.

        Args:
            limit: Max events to return (default 100)
            since: ISO 8601 timestamp — return events after this time

        Returns:
            List of LearningEvent (outcome_feedback type, sorted chronological)
        """
        events = self.event_store.query_events(
            tenant_id=self.tenant_id,
            event_type=EventType.FEEDBACK,
            skill_id=self.skill_id,
            since=since,
            limit=limit,
            offset=0,
            newest_first=False,  # Chronological order (oldest first)
        )
        return events

    def count_feedback(self) -> int:
        """Count total feedback events for this skill in this tenant.

        Returns:
            Total feedback event count
        """
        return self.event_store.count_events(
            tenant_id=self.tenant_id,
            event_type=EventType.FEEDBACK,
        )
