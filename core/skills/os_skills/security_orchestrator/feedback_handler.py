"""Stream 2 Phase 2: Feedback Handler for Security Orchestrator Skill (ADR-0314).

Processes operator feedback on threat detection decisions and updates incident
confidence scores. Integrates with EventStore (Stream 4).

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


class IncidentFeedbackType(str, Enum):
    """Types of incident detection feedback."""
    REAL_THREAT = "real_threat"  # Threat detection was correct
    FALSE_POSITIVE = "false_positive"  # False alarm (not a real threat)
    MISSED_THREAT = "missed_threat"  # We missed a real threat
    SEVERITY_OVERESTIMATED = "severity_overestimated"  # Threat existed but not that severe
    SEVERITY_UNDERESTIMATED = "severity_underestimated"  # Threat was more severe
    SKIP = "skip"  # No feedback


@dataclass(frozen=True)
class IncidentFeedback:
    """Immutable feedback on a threat detection decision.

    Captures operator assessment of whether threat detection was accurate.
    """
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    incident_id: str = ""  # Incident that was flagged
    threat_type: str = ""  # Type detected (brute_force, injection, exfil, etc.)
    severity_level: str = ""  # Severity assigned (low, medium, high, critical)
    feedback_type: IncidentFeedbackType = IncidentFeedbackType.SKIP
    confidence_score: float = 0.5  # Operator's confidence in assessment (0.0-1.0)
    reason: Optional[str] = None  # Optional explanation from operator
    tenant_id: str = ""  # Tenant scope (GDPR Art. 32)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def __post_init__(self):
        """Validate feedback (frozen dataclass, fail-closed)."""
        if not self.incident_id:
            raise ValueError("incident_id required")
        if not self.threat_type:
            raise ValueError("threat_type required")
        if not self.severity_level in ("low", "medium", "high", "critical"):
            raise ValueError(f"severity_level must be low/medium/high/critical, got {self.severity_level!r}")
        if not self.tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32)")
        if not isinstance(self.confidence_score, (int, float)):
            raise ValueError("confidence_score must be numeric")
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError(f"confidence_score must be in [0.0, 1.0], got {self.confidence_score}")


class IncidentFeedbackHandler:
    """Handles operator feedback on threat detection decisions (Phase 2).

    Subscribes to incident feedback events, extracts threat assessment, and queues
    confidence updates (deferred to ThreatConfidenceScorer).

    **Responsibility:**
    1. Receive incident feedback from operator (via console, API, incident response)
    2. Validate & normalize (fail-closed)
    3. Emit LearningEvent (outcome_feedback type)
    4. Signal ThreatConfidenceScorer to recompute

    **No state change here:** this handler is pure I/O (receive → emit).
    State updates happen in ThreatConfidenceScorer (Week 2).
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str,
        skill_id: str = "os.security_orchestrator",
        skill_version: str = "1.0.0",
    ):
        """Initialize incident feedback handler.

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

    def process_feedback(self, feedback: IncidentFeedback) -> Optional[str]:
        """Process operator feedback on threat detection (audit-first).

        Flow:
        1. Validate feedback (fail-closed)
        2. Emit LearningEvent to EventStore (outcome_feedback type)
        3. Return event_id for tracking
        4. Caller (ThreatConfidenceScorer) reads event → updates threat confidence

        Args:
            feedback: IncidentFeedback with incident_id, threat assessment

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
        if feedback.feedback_type == IncidentFeedbackType.SKIP:
            logger.debug(f"Skipping incident feedback (type=SKIP): {feedback.feedback_id}")
            return None

        # Build learning event payload
        signal = {
            "feedback_type": feedback.feedback_type.value,
            "incident_id": feedback.incident_id,
            "threat_type": feedback.threat_type,
            "severity_level": feedback.severity_level,
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
            lom="security_orchestrator.feedback_handler:process_feedback:L120",
        )

        # Emit to EventStore (audit-first: chain write happens FIRST)
        # If this raises RuntimeError, nothing is persisted (fail-closed per ADR-0314)
        try:
            self.event_store.write_event(event)
        except (RuntimeError, IOError) as e:
            logger.error(
                f"Failed to write incident feedback event: {e} — feedback LOST (audit-first failure)"
            )
            raise RuntimeError(
                f"Incident feedback event rejected by audit chain: {e}"
            ) from e

        self.feedback_count += 1
        logger.info(
            f"Incident feedback processed: incident={feedback.incident_id}, "
            f"threat={feedback.threat_type}, severity={feedback.severity_level}, "
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

        Used by ThreatConfidenceScorer to fetch feedback for recomputation.

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
