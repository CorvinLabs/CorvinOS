"""Stream 3 Phase 2: Feedback Handler for Flow Guard Skill (ADR-0314).

Processes operator feedback on data flow policy decisions and updates
policy confidence scores. Integrates with EventStore (Stream 4).

**Compliance:**
- GDPR Art. 30/32: All feedback events audited
- ADR-0314: Learning events emitted + persisted
- Fail-closed: invalid feedback rejected immediately
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


class PolicyFeedbackType(str, Enum):
    """Types of policy feedback for data flow decisions."""
    ALLOW_CORRECT = "allow_correct"  # Flow was correctly allowed
    DENY_CORRECT = "deny_correct"  # Flow was correctly denied
    ALLOW_WRONG = "allow_wrong"  # Flow should have been denied
    DENY_WRONG = "deny_wrong"  # Flow should have been allowed
    EXCEPTION_REQUEST = "exception_request"  # Request override (with justification)
    SKIP = "skip"  # No feedback


@dataclass(frozen=True)
class PolicyFeedback:
    """Immutable feedback on a data flow policy decision.

    Captures operator assessment of whether the policy choice was correct.
    """
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    flow_id: str = ""  # The flow that was classified
    data_class: str = ""  # Data classification (PII, API_KEY, PUBLIC, etc.)
    engine: str = ""  # Target engine (claude-haiku, claude-sonnet, etc.)
    destination: str = ""  # Destination (console, webhook, file, etc.)
    policy_decision: str = ""  # Decision we made (allow/deny)
    feedback_type: PolicyFeedbackType = PolicyFeedbackType.SKIP
    confidence_score: float = 0.5  # Operator's confidence (0.0-1.0)
    exception_ttl_hours: Optional[int] = None  # Exception expiration (for EXCEPTION_REQUEST)
    tenant_id: str = ""  # Tenant scope (GDPR Art. 32)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def __post_init__(self):
        """Validate feedback (frozen dataclass, fail-closed)."""
        if not self.flow_id:
            raise ValueError("flow_id required")
        if not self.data_class:
            raise ValueError("data_class required")
        if not self.engine:
            raise ValueError("engine required")
        if not self.destination:
            raise ValueError("destination required")
        if not self.policy_decision in ("allow", "deny"):
            raise ValueError(f"policy_decision must be 'allow' or 'deny', got {self.policy_decision!r}")
        if not self.tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError(f"confidence_score must be in [0.0, 1.0], got {self.confidence_score}")
        if self.feedback_type == PolicyFeedbackType.EXCEPTION_REQUEST and not self.exception_ttl_hours:
            raise ValueError("exception_ttl_hours required for EXCEPTION_REQUEST")


class PolicyFeedbackHandler:
    """Handles operator feedback on data flow policy decisions (Phase 2).

    Subscribes to policy feedback events, extracts flow classification assessment,
    and queues policy confidence updates (deferred to PolicyConfidenceScorer).

    **Responsibility:**
    1. Receive policy feedback from operator
    2. Validate & normalize (fail-closed)
    3. Emit LearningEvent (preference_feedback type)
    4. Signal PolicyConfidenceScorer to recompute

    **No state change here:** pure I/O (receive → emit).
    State updates happen in PolicyConfidenceScorer (Week 2).
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str,
        skill_id: str = "os.flow_guard",
        skill_version: str = "1.0.0",
    ):
        """Initialize policy feedback handler.

        Args:
            event_store: EventStore instance (from Stream 4)
            tenant_id: Tenant scope (GDPR Art. 32)
            skill_id: Skill identifier (for audit trail)
            skill_version: Skill version at processing time
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version
        self.feedback_count = 0

    def process_feedback(
        self,
        feedback: PolicyFeedback,
    ) -> Optional[str]:
        """Process operator feedback on policy decision (audit-first).

        Flow:
        1. Validate feedback (fail-closed)
        2. Emit LearningEvent to EventStore (preference_feedback type)
        3. Handle exception requests (if applicable)
        4. Return event_id for tracking
        5. Caller (PolicyConfidenceScorer) reads event → updates policy thresholds

        Args:
            feedback: PolicyFeedback with flow_id, data_class, policy assessment

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
        if feedback.feedback_type == PolicyFeedbackType.SKIP:
            logger.debug(f"Skipping feedback (type=SKIP): {feedback.feedback_id}")
            return None

        # Build learning event payload
        signal = {
            "feedback_type": feedback.feedback_type.value,
            "flow_id": feedback.flow_id,
            "data_class": feedback.data_class,
            "engine": feedback.engine,
            "destination": feedback.destination,
            "policy_decision": feedback.policy_decision,
            "operator_confidence": feedback.confidence_score,
        }

        # Handle exception requests separately
        if feedback.feedback_type == PolicyFeedbackType.EXCEPTION_REQUEST:
            signal["exception_ttl_hours"] = feedback.exception_ttl_hours

        # Create LearningEvent (preference_feedback type)
        event = LearningEvent.create(
            event_type=EventType.PREFERENCE,  # preference_feedback (policy preference)
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal=signal,
            skill_version=self.skill_version,
            lom="flow_guard.feedback_handler:process_feedback:L110",
        )

        # Emit to EventStore (audit-first: chain write happens FIRST)
        try:
            self.event_store.write_event(event)
        except (RuntimeError, IOError) as e:
            logger.error(
                f"Failed to write policy feedback event: {e} — feedback LOST (audit-first failure)"
            )
            raise RuntimeError(
                f"Policy feedback event rejected by audit chain: {e}"
            ) from e

        self.feedback_count += 1
        logger.info(
            f"Policy feedback processed: flow={feedback.flow_id}, data_class={feedback.data_class}, "
            f"decision={feedback.policy_decision}, type={feedback.feedback_type.value}, "
            f"confidence={feedback.confidence_score}, total_feedback_count={self.feedback_count}"
        )
        return event.event_id

    def get_recent_feedback(
        self,
        limit: int = 100,
        since: Optional[str] = None,
    ) -> list[LearningEvent]:
        """Retrieve recent feedback events for this skill (query interface).

        Used by PolicyConfidenceScorer to fetch feedback for recomputation.

        Args:
            limit: Max events to return (default 100)
            since: ISO 8601 timestamp — return events after this time

        Returns:
            List of LearningEvent (preference_feedback type, sorted chronological)
        """
        events = self.event_store.query_events(
            tenant_id=self.tenant_id,
            event_type=EventType.PREFERENCE,
            skill_id=self.skill_id,
            since=since,
            limit=limit,
            offset=0,
            newest_first=False,  # Chronological order
        )
        return events

    def count_feedback(self) -> int:
        """Count total feedback events for this skill in this tenant.

        Returns:
            Total feedback event count
        """
        return self.event_store.count_events(
            tenant_id=self.tenant_id,
            event_type=EventType.PREFERENCE,
        )
