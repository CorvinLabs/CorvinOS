"""Tests for Learning Event Schema (ADR-0314)."""

import pytest
from datetime import datetime
from uuid import uuid4

from core.learning.event_schema import (
    LearningEvent,
    LearningEventType,
    ConfidenceScorePayload,
    DecisionRecordPayload,
    UserFeedbackPayload,
    OutcomeObservedPayload,
    PreferenceSetPayload,
    AttentionConsumedPayload,
    MetricAggregatedPayload,
)


class TestLearningEventSchema:
    """Test event schema definition."""

    def test_create_event_minimal(self):
        """Create event with minimal fields."""
        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={"relevance_score": 0.95},
        )

        assert event.event_type == LearningEventType.CONFIDENCE_SCORE
        assert event.tenant_id == "_default"
        assert event.event_id is not None

    def test_event_immutability(self):
        """Verify events are frozen (immutable)."""
        event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,
            tenant_id="_default",
            instance_id="console-1",
            skill_name="ranking",
            session_id="session-123",
            timestamp_utc=datetime.utcnow(),
            payload={},
        )

        with pytest.raises(AttributeError):
            event.tenant_id = "other"

    def test_event_to_audit_format(self):
        """Convert event to audit.jsonl format."""
        now = datetime.utcnow()
        event = LearningEvent(
            event_type=LearningEventType.USER_FEEDBACK,
            tenant_id="_default",
            instance_id="console-1",
            skill_name=None,
            session_id="session-123",
            timestamp_utc=now,
            user_id="user-1",
            payload={"feedback_value": "thumbs_up"},
            tags=["manual", "high-priority"],
        )

        audit_dict = event.to_audit_event()

        assert audit_dict["event_type"] == "learning.feedback.user_provided"
        assert audit_dict["tenant_id"] == "_default"
        assert audit_dict["instance_id"] == "console-1"
        assert audit_dict["user_id"] == "user-1"
        assert audit_dict["tags"] == ["manual", "high-priority"]

    def test_all_event_types_defined(self):
        """Verify all 8 event types are defined."""
        expected_types = {
            LearningEventType.CONFIDENCE_SCORE,
            LearningEventType.DECISION_RECORD,
            LearningEventType.USER_FEEDBACK,
            LearningEventType.OUTCOME_OBSERVED,
            LearningEventType.PREFERENCE_SET,
            LearningEventType.ATTENTION_CONSUMED,
            LearningEventType.ATTENTION_REFUNDED,
            LearningEventType.METRIC_AGGREGATED,
        }

        actual_types = set(LearningEventType)

        assert expected_types == actual_types


class TestPayloadSchemas:
    """Test event-specific payload types."""

    def test_confidence_score_payload(self):
        """Create confidence score payload."""
        payload = ConfidenceScorePayload(
            relevance_score=0.95,
            reliability_score=0.87,
            model="ensemble",
            reasoning="High relevance due to exact match; reliability lower due to variance",
        )

        assert payload.relevance_score == 0.95
        assert payload.reliability_score == 0.87

    def test_decision_record_payload(self):
        """Create decision record payload."""
        payload = DecisionRecordPayload(
            decision_id="decision-456",
            choice_type="skill_selection",
            candidates=["skill-a", "skill-b", "skill-c"],
            chosen="skill-a",
            context={"task_type": "summarize", "input_tokens": 500},
        )

        assert payload.chosen == "skill-a"
        assert payload.context["input_tokens"] == 500

    def test_user_feedback_payload(self):
        """Create user feedback payload."""
        payload = UserFeedbackPayload(
            feedback_type="rating",
            feedback_value=4,
            decision_id="decision-456",
            context="Output was clear but missing examples",
        )

        assert payload.feedback_type == "rating"
        assert payload.feedback_value == 4

    def test_outcome_observed_payload(self):
        """Create outcome observed payload."""
        payload = OutcomeObservedPayload(
            decision_id="decision-456",
            outcome_type="latency",
            outcome_value=1250.5,  # milliseconds
            window_seconds=300,
        )

        assert payload.outcome_type == "latency"
        assert payload.window_seconds == 300

    def test_attention_consumed_payload(self):
        """Create attention consumed payload."""
        payload = AttentionConsumedPayload(
            budget_type="adr_reads",
            amount=2,
            remaining=8,
            reason="ADR-0314 design review",
        )

        assert payload.amount == 2
        assert payload.remaining == 8


class TestEventGeneration:
    """Test creating events in realistic scenarios."""

    def test_skill_execution_event(self):
        """Create event for skill execution outcome."""
        event = LearningEvent(
            event_type=LearningEventType.OUTCOME_OBSERVED,
            tenant_id="_default",
            instance_id="bridge-discord-1",
            skill_name="code_reviewer",
            session_id="session-789",
            timestamp_utc=datetime.utcnow(),
            payload={
                "decision_id": "review-001",
                "outcome_type": "user_accepted_suggestion",
                "outcome_value": True,
                "window_seconds": 60,
            },
        )

        assert event.event_type == LearningEventType.OUTCOME_OBSERVED
        assert event.skill_name == "code_reviewer"
        audit = event.to_audit_event()
        assert audit["event_type"] == "learning.outcome.observed"

    def test_preference_learning_event(self):
        """Create event for learning user preferences."""
        event = LearningEvent(
            event_type=LearningEventType.PREFERENCE_SET,
            tenant_id="_default",
            instance_id="console-main",
            skill_name=None,
            session_id="session-999",
            timestamp_utc=datetime.utcnow(),
            user_id="user-42",
            payload={
                "preference_key": "decision_style",
                "preference_value": "pragmatic",
                "confidence": 0.8,
            },
        )

        assert event.user_id == "user-42"
        assert event.payload["preference_value"] == "pragmatic"

    def test_metrics_aggregation_event(self):
        """Create event for aggregated metrics."""
        event = LearningEvent(
            event_type=LearningEventType.METRIC_AGGREGATED,
            tenant_id="_default",
            instance_id="daemon-1",
            skill_name="ranking",
            session_id="hourly-2026-08-12-14",
            timestamp_utc=datetime.utcnow(),
            payload={
                "metric_name": "skill_error_rate",
                "window_seconds": 3600,
                "value": 0.02,
                "sample_count": 145,
            },
        )

        assert event.payload["metric_name"] == "skill_error_rate"
        assert event.payload["sample_count"] == 145
