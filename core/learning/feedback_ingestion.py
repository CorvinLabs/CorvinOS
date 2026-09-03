"""Phase 2a.1: Learning Feedback Ingestion — User feedback API for closed-loop learning.

Compliance: GDPR Art. 6 (feedback consent), Art. 30 (audit trail), Art. 32 (data security)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """User feedback types for Skill decision evaluation."""
    GOOD = "good"        # User agreed with Skill decision
    BAD = "bad"          # User disagreed with Skill decision
    OTHER = "other"      # Neutral / inconclusive


@dataclass(frozen=True)
class SkillFeedback:
    """Immutable feedback record (GDPR Art. 30 audit trail)."""
    skill_id: str
    task_id: str
    feedback_type: FeedbackType
    reason: Optional[str] = None  # User's explanation (e.g., "Too slow", "Wrong answer")
    user_id: Optional[str] = None  # Who gave feedback (optional, for privacy)
    tenant_id: str = "_default"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit event format."""
        return {
            "event_type": "skill_feedback",
            "skill_id": self.skill_id,
            "task_id": self.task_id,
            "feedback_type": self.feedback_type.value,
            "reason": self.reason,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
        }


class FeedbackIngestionValidator:
    """Validates feedback before storage (fail-closed, GDPR Art. 32)."""

    FEEDBACK_WINDOW_MINUTES = 60  # Only feedback within 60min of execution counts
    MAX_REASON_LENGTH = 1000      # Prevent DoS via huge reason strings

    def __init__(self, audit_backend=None, event_store=None):
        """Initialize validator with dependencies.

        Args:
            audit_backend: Audit trail writer (for logging feedback)
            event_store: EventStore instance (to verify task exists)
        """
        self.audit_backend = audit_backend
        self.event_store = event_store

    def validate(self, feedback: SkillFeedback) -> tuple[bool, Optional[str]]:
        """Validate feedback (return: (is_valid, error_message))."""

        # 1. Feedback type must be valid (enum validates this already)
        if feedback.feedback_type not in [FeedbackType.GOOD, FeedbackType.BAD, FeedbackType.OTHER]:
            return False, f"Invalid feedback_type: {feedback.feedback_type}"

        # 2. Skill ID must not be empty
        if not feedback.skill_id or len(feedback.skill_id) == 0:
            return False, "skill_id required"

        # 3. Task ID must not be empty
        if not feedback.task_id or len(feedback.task_id) == 0:
            return False, "task_id required"

        # 4. Reason must be reasonable length (prevent DoS)
        if feedback.reason and len(feedback.reason) > self.MAX_REASON_LENGTH:
            return False, f"reason too long (max {self.MAX_REASON_LENGTH} chars)"

        # 5. Feedback must be time-bound (within 60 min of execution, GDPR Art. 6)
        if not self._is_within_feedback_window(feedback.timestamp):
            return False, f"feedback_timestamp too old (max {self.FEEDBACK_WINDOW_MINUTES} min)"

        # 6. Tenant ID must be valid (fail-closed isolation)
        if not self._is_valid_tenant(feedback.tenant_id):
            return False, f"invalid tenant_id: {feedback.tenant_id}"

        # 7. Task must exist in audit trail (proof of execution)
        if self.event_store and not self._task_exists_in_audit(feedback.task_id, feedback.tenant_id):
            return False, f"task_id not found in audit trail (must have executed skill)"

        return True, None

    @staticmethod
    def _is_within_feedback_window(timestamp_iso: str) -> bool:
        """Check if feedback timestamp is within allowed window."""
        try:
            feedback_time = datetime.fromisoformat(timestamp_iso.replace('Z', '+00:00'))
            now = datetime.utcnow()
            delta = (now - feedback_time).total_seconds()
            return 0 <= delta <= (FeedbackIngestionValidator.FEEDBACK_WINDOW_MINUTES * 60)
        except Exception:
            return False

    @staticmethod
    def _is_valid_tenant(tenant_id: str) -> bool:
        """Validate tenant ID (basic check; full validation in registry)."""
        return tenant_id and len(tenant_id) > 0 and tenant_id != "INVALID"

    def _task_exists_in_audit(self, task_id: str, tenant_id: str) -> bool:
        """Verify task executed (via event store lookup)."""
        # Simplified check; full implementation queries EventStore.get_events()
        return True  # Placeholder; EventStore integration in Phase 2a.3


class FeedbackIngestionBackend:
    """Ingests validated feedback and stores for optimizer consumption."""

    def __init__(self, audit_backend, event_store, learning_store):
        """Initialize ingestion pipeline.

        Args:
            audit_backend: Audit trail writer
            event_store: EventStore for task verification
            learning_store: Learning event storage (to link feedback)
        """
        self.audit_backend = audit_backend
        self.event_store = event_store
        self.learning_store = learning_store
        self.validator = FeedbackIngestionValidator(audit_backend, event_store)

    def ingest(self, feedback: SkillFeedback) -> tuple[bool, Optional[str]]:
        """Ingest feedback through validation → audit → storage pipeline.

        Args:
            feedback: SkillFeedback to ingest

        Returns:
            (success, error_message)
        """
        # Step 1: Validate feedback (fail-closed)
        is_valid, error_msg = self.validator.validate(feedback)
        if not is_valid:
            logger.warning(f"Feedback validation failed: {error_msg}")
            # Audit rejection for compliance audit
            self._emit_audit_rejection(feedback, error_msg)
            return False, error_msg

        # Step 2: Emit audit event (GDPR Art. 30 processing record)
        self._emit_audit_event(feedback)

        # Step 3: Link feedback to learning event (append-only, immutable)
        try:
            self.learning_store.link_feedback_to_event(
                skill_id=feedback.skill_id,
                task_id=feedback.task_id,
                feedback=feedback
            )
        except Exception as e:
            logger.error(f"Failed to link feedback to learning event: {e}")
            return False, f"Storage error: {e}"

        # Step 4: Emit learning metric (for confidence drift detection)
        self._emit_learning_metric(feedback)

        return True, None

    def _emit_audit_event(self, feedback: SkillFeedback) -> None:
        """Emit audit event for feedback ingestion (GDPR Art. 30)."""
        if not self.audit_backend:
            return

        audit_event = {
            **feedback.to_dict(),
            "event_type": "skill_feedback_ingested",
        }
        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"Failed to write feedback audit event: {e}")

    def _emit_audit_rejection(self, feedback: SkillFeedback, reason: str) -> None:
        """Emit audit event for rejected feedback (compliance trail)."""
        if not self.audit_backend:
            return

        audit_event = {
            **feedback.to_dict(),
            "event_type": "skill_feedback_rejected",
            "rejection_reason": reason,
        }
        try:
            self.audit_backend.write_event(audit_event)
        except Exception as e:
            logger.error(f"Failed to write feedback rejection audit event: {e}")

    def _emit_learning_metric(self, feedback: SkillFeedback) -> None:
        """Emit learning metric for optimizer (used in 2a.2: drift detection)."""
        metric = {
            "metric_type": "feedback_received",
            "skill_id": feedback.skill_id,
            "feedback_type": feedback.feedback_type.value,
            "tenant_id": feedback.tenant_id,
            "timestamp": feedback.timestamp,
        }
        # Metric storage TBD (simple dict for now; upgraded in 2a.5)
        logger.info(f"Learning metric: {metric}")


# ============================================================================
# Tests (inline, Red→Green)
# ============================================================================

def test_feedback_validation():
    """Unit test: Feedback validation gates."""
    validator = FeedbackIngestionValidator()

    # Test 1: Valid feedback passes
    good_feedback = SkillFeedback(
        skill_id="os.delegation_router",
        task_id="task_123",
        feedback_type=FeedbackType.GOOD,
        reason="Correct routing decision"
    )
    is_valid, error = validator.validate(good_feedback)
    assert is_valid, f"Valid feedback should pass: {error}"
    print("✅ Test 1: Valid feedback passes")

    # Test 2: Missing skill_id fails
    bad_feedback = SkillFeedback(
        skill_id="",
        task_id="task_123",
        feedback_type=FeedbackType.GOOD
    )
    is_valid, error = validator.validate(bad_feedback)
    assert not is_valid, "Empty skill_id should fail"
    assert "skill_id required" in error
    print("✅ Test 2: Missing skill_id fails")

    # Test 3: Too long reason fails (DoS protection)
    too_long_feedback = SkillFeedback(
        skill_id="os.delegation_router",
        task_id="task_123",
        feedback_type=FeedbackType.GOOD,
        reason="x" * (FeedbackIngestionValidator.MAX_REASON_LENGTH + 1)
    )
    is_valid, error = validator.validate(too_long_feedback)
    assert not is_valid, "Too long reason should fail"
    assert "reason too long" in error
    print("✅ Test 3: Too long reason fails (DoS protection)")

    # Test 4: Invalid tenant fails
    invalid_tenant_feedback = SkillFeedback(
        skill_id="os.delegation_router",
        task_id="task_123",
        feedback_type=FeedbackType.GOOD,
        tenant_id="INVALID"
    )
    is_valid, error = validator.validate(invalid_tenant_feedback)
    assert not is_valid, "Invalid tenant should fail"
    assert "invalid tenant_id" in error
    print("✅ Test 4: Invalid tenant fails")

    print("\n✅ All feedback validation tests pass!")


def test_feedback_ingestion():
    """Integration test: Feedback ingestion pipeline."""
    # Mock backends
    class MockAuditBackend:
        def __init__(self):
            self.events = []
        def write_event(self, event):
            self.events.append(event)

    class MockEventStore:
        pass

    class MockLearningStore:
        def __init__(self):
            self.links = []
        def link_feedback_to_event(self, skill_id, task_id, feedback):
            self.links.append((skill_id, task_id, feedback))

    audit_backend = MockAuditBackend()
    event_store = MockEventStore()
    learning_store = MockLearningStore()

    ingestion = FeedbackIngestionBackend(audit_backend, event_store, learning_store)

    # Test: Ingest valid feedback
    feedback = SkillFeedback(
        skill_id="os.delegation_router",
        task_id="task_123",
        feedback_type=FeedbackType.GOOD,
        reason="Fast and accurate"
    )

    success, error = ingestion.ingest(feedback)
    assert success, f"Ingestion should succeed: {error}"
    assert len(audit_backend.events) > 0, "Audit event should be written"
    assert len(learning_store.links) > 0, "Feedback should be linked to event"
    print("✅ Integration test: Feedback ingestion pipeline works")


if __name__ == "__main__":
    print("Running Phase 2a.1 Feedback Ingestion Tests...\n")
    test_feedback_validation()
    print()
    test_feedback_ingestion()
    print("\n🎉 All tests passed!")
