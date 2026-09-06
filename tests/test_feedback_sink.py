"""Phase 4 Week 17: Feedback Schema Tests — FeedbackEvent, validation, scrubbing, buffering.

Test coverage:
- FeedbackEvent creation and validation (5 tests)
- FeedbackScrubber PII removal (4 tests)
- FeedbackValidator fail-closed checks (8 tests)
- FeedbackBuffer confidence thresholding (3 tests)
- Total: 20 tests
"""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from core.learning.feedback_sink import (
    FeedbackEvent,
    FeedbackScrubber,
    FeedbackValidator,
    FeedbackBuffer,
    OutcomeFeedbackType,
    PreferenceFeedbackType,
)


class TestFeedbackEvent:
    """Test FeedbackEvent creation and immutability."""

    def test_create_outcome_feedback(self):
        """Create feedback with outcome type."""
        event = FeedbackEvent.create(
            skill_id="os.delegation_router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            lom="test:test_create_outcome_feedback",
        )
        assert event.skill_id == "os.delegation_router"
        assert event.outcome_feedback == OutcomeFeedbackType.YES
        assert event.tenant_id == "_default"

    def test_create_quality_rating(self):
        """Create feedback with quality rating (1–5 stars)."""
        event = FeedbackEvent.create(
            skill_id="os.context_adapter",
            task_id="task-456",
            tenant_id="tenant-abc",
            quality_rating=4,
        )
        assert event.quality_rating == 4
        assert event.skill_id == "os.context_adapter"

    def test_create_preference_feedback(self):
        """Create feedback with style preference."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-789",
            tenant_id="_default",
            preference_feedback=PreferenceFeedbackType.LLM,
        )
        assert event.preference_feedback == PreferenceFeedbackType.LLM

    def test_event_immutability(self):
        """Frozen dataclass prevents mutation."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            event.outcome_feedback = OutcomeFeedbackType.NO

    def test_feedback_requires_at_least_one_type(self):
        """Must have outcome_feedback, quality_rating, or preference_feedback."""
        with pytest.raises(ValueError, match="at least one feedback type"):
            FeedbackEvent(
                feedback_id=str(uuid4()),
                skill_id="os.router",
                task_id="task-123",
                tenant_id="_default",
                timestamp=datetime.utcnow().isoformat() + "Z",
            )

    def test_serialize_to_dict(self):
        """Serialize to dict for storage."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            quality_rating=5,
            reason="Great answer",
        )
        d = event.to_dict()
        assert d["outcome_feedback"] == "yes"
        assert d["quality_rating"] == 5
        assert d["reason"] == "Great answer"


class TestFeedbackScrubber:
    """Test PII removal from feedback reasons."""

    def test_scrub_email(self):
        """Remove email addresses."""
        text = "Contact admin@example.com for help"
        scrubbed = FeedbackScrubber.scrub(text)
        assert "admin@example.com" not in scrubbed
        assert "[REDACTED]" in scrubbed

    def test_scrub_phone(self):
        """Remove phone numbers."""
        text = "Call 555-123-4567 or 555.123.4567"
        scrubbed = FeedbackScrubber.scrub(text)
        assert "555-123-4567" not in scrubbed
        assert "[REDACTED]" in scrubbed

    def test_scrub_username(self):
        """Remove known usernames."""
        text = "User shumway reported this issue"
        scrubbed = FeedbackScrubber.scrub(text)
        assert "shumway" not in scrubbed
        assert "[REDACTED]" in scrubbed

    def test_scrub_oversized_input(self):
        """Return None for inputs larger than MAX_REASON_LENGTH (1000 chars)."""
        text = "x" * 2000
        scrubbed = FeedbackScrubber.scrub(text)
        assert scrubbed is None

    def test_scrub_preserves_clean_text(self):
        """Leave clean text untouched."""
        text = "This is a great feature"
        scrubbed = FeedbackScrubber.scrub(text)
        assert scrubbed == text

    def test_scrub_none(self):
        """Handle None input gracefully."""
        assert FeedbackScrubber.scrub(None) is None


class TestFeedbackValidator:
    """Test feedback validation (fail-closed checks)."""

    def test_missing_tenant_id(self):
        """Reject feedback without tenant_id."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="",  # Empty
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert not valid
        assert "tenant_id" in error

    def test_missing_skill_id(self):
        """Reject feedback without skill_id."""
        event = FeedbackEvent(
            feedback_id=str(uuid4()),
            skill_id="",  # Empty
            task_id="task-123",
            tenant_id="_default",
            timestamp=datetime.utcnow().isoformat() + "Z",
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert not valid
        assert "skill_id" in error

    def test_missing_task_id(self):
        """Reject feedback without task_id."""
        event = FeedbackEvent(
            feedback_id=str(uuid4()),
            skill_id="os.router",
            task_id="",  # Empty
            tenant_id="_default",
            timestamp=datetime.utcnow().isoformat() + "Z",
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert not valid
        assert "task_id" in error

    def test_feedback_too_old(self):
        """Reject feedback older than FEEDBACK_WINDOW_MINUTES (60 min)."""
        old_timestamp = (datetime.utcnow() - timedelta(minutes=61)).isoformat() + "Z"
        event = FeedbackEvent(
            feedback_id=str(uuid4()),
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            timestamp=old_timestamp,
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert not valid
        assert "too old" in error

    def test_quality_rating_out_of_range(self):
        """Reject quality_rating outside 1–5 range."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            quality_rating=6,  # Invalid: > 5
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert not valid
        assert "quality_rating" in error

    def test_confidence_out_of_range(self):
        """Reject confidence outside 0–1 range."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            confidence=1.5,  # Invalid: > 1
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert not valid
        assert "confidence" in error

    def test_valid_feedback(self):
        """Accept valid feedback."""
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            quality_rating=5,
            confidence=0.95,
        )
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert valid
        assert error is None


class TestFeedbackBuffer:
    """Test feedback buffering and confidence thresholding."""

    def test_buffer_initialization(self):
        """Initialize empty feedback buffer."""
        buffer = FeedbackBuffer(min_samples=10, confidence_threshold=0.6)
        assert len(buffer.buffers) == 0

    def test_add_feedback(self):
        """Add feedback to buffer."""
        buffer = FeedbackBuffer()
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            confidence=0.9,
        )
        buffer.add(event)
        assert ("os.router", "task-123") in buffer.buffers
        assert len(buffer.buffers[("os.router", "task-123")]) == 1

    def test_ready_to_emit_requires_min_samples(self):
        """Only emit when buffer has ≥10 samples."""
        buffer = FeedbackBuffer(min_samples=10)
        for i in range(5):
            event = FeedbackEvent.create(
                skill_id="os.router",
                task_id="task-123",
                tenant_id="_default",
                outcome_feedback=OutcomeFeedbackType.YES,
                confidence=0.9,
            )
            buffer.add(event)

        # 5 samples < 10 min_samples
        assert not buffer.ready_to_emit("os.router", "task-123")

    def test_ready_to_emit_requires_confidence_threshold(self):
        """Only emit when average confidence ≥ threshold."""
        buffer = FeedbackBuffer(min_samples=3, confidence_threshold=0.8)
        for i in range(3):
            confidence = 0.5  # Below 0.8 threshold
            event = FeedbackEvent.create(
                skill_id="os.router",
                task_id="task-123",
                tenant_id="_default",
                outcome_feedback=OutcomeFeedbackType.YES,
                confidence=confidence,
            )
            buffer.add(event)

        # 3 samples >= min_samples, but confidence 0.5 < 0.8 threshold
        assert not buffer.ready_to_emit("os.router", "task-123")

    def test_ready_to_emit_succeeds(self):
        """Emit when samples + confidence meet thresholds."""
        buffer = FeedbackBuffer(min_samples=3, confidence_threshold=0.6)
        for i in range(3):
            event = FeedbackEvent.create(
                skill_id="os.router",
                task_id="task-123",
                tenant_id="_default",
                outcome_feedback=OutcomeFeedbackType.YES,
                confidence=0.9,  # >= 0.6 threshold
            )
            buffer.add(event)

        assert buffer.ready_to_emit("os.router", "task-123")

    def test_get_and_clear(self):
        """Retrieve and clear buffer."""
        buffer = FeedbackBuffer()
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
        )
        buffer.add(event)

        samples = buffer.get_and_clear("os.router", "task-123")
        assert len(samples) == 1
        assert samples[0].outcome_feedback == OutcomeFeedbackType.YES

        # Buffer should be empty after clear
        assert len(buffer.buffers[("os.router", "task-123")]) == 0

    def test_get_summary(self):
        """Get summary statistics from buffer."""
        buffer = FeedbackBuffer()
        for i in range(5):
            outcome = OutcomeFeedbackType.YES if i < 3 else OutcomeFeedbackType.NO
            event = FeedbackEvent.create(
                skill_id="os.router",
                task_id="task-123",
                tenant_id="_default",
                outcome_feedback=outcome,
                quality_rating=4,
            )
            buffer.add(event)

        summary = buffer.get_summary("os.router", "task-123")
        assert summary["count"] == 5
        assert summary["outcome_yes"] == 3
        assert summary["outcome_no"] == 2
        assert summary["quality_avg"] == 4.0


class TestFeedbackIntegration:
    """Integration tests for feedback collection workflow."""

    def test_full_feedback_workflow(self):
        """End-to-end: create → validate → scrub → buffer → emit."""
        # 1. Create feedback
        scrubber = FeedbackScrubber()
        reason = scrubber.scrub("Contacted user@example.com for feedback")
        event = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-123",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            quality_rating=5,
            confidence=0.95,
            reason=reason,
        )

        # 2. Validate
        validator = FeedbackValidator()
        valid, error = validator.validate(event)
        assert valid, error

        # 3. Buffer
        buffer = FeedbackBuffer(min_samples=3)
        for i in range(3):
            buffer.add(event)

        # 4. Check if ready to emit
        assert buffer.ready_to_emit("os.router", "task-123")

        # 5. Emit (retrieve and clear)
        samples = buffer.get_and_clear("os.router", "task-123")
        assert len(samples) == 3

    def test_feedback_isolation_by_skill_task(self):
        """Feedback for different skills/tasks are isolated."""
        buffer = FeedbackBuffer(min_samples=1)

        # Add feedback for os.router
        event1 = FeedbackEvent.create(
            skill_id="os.router",
            task_id="task-1",
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.YES,
            confidence=0.9,
        )
        buffer.add(event1)

        # Add feedback for os.context_adapter (different skill)
        event2 = FeedbackEvent.create(
            skill_id="os.context_adapter",
            task_id="task-1",  # Same task ID
            tenant_id="_default",
            outcome_feedback=OutcomeFeedbackType.NO,
            confidence=0.8,
        )
        buffer.add(event2)

        # Both should be ready to emit (min_samples=1)
        assert buffer.ready_to_emit("os.router", "task-1")
        assert buffer.ready_to_emit("os.context_adapter", "task-1")

        # Buffers should be separate
        samples1 = buffer.get_and_clear("os.router", "task-1")
        samples2 = buffer.get_and_clear("os.context_adapter", "task-1")
        assert len(samples1) == 1
        assert len(samples2) == 1
        assert samples1[0].skill_id != samples2[0].skill_id
