"""Tests for Outcome Feedback (ADR-0317)."""

import pytest
from datetime import datetime
from core.learning.outcome_feedback import OutcomeRecord, OutcomeRecorder, OutcomeType


class TestOutcomeRecord:
    """Test outcome record model."""

    def test_create_record(self):
        """Create an outcome record."""
        record = OutcomeRecord(
            outcome_id="o1",
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            timestamp_utc=datetime.utcnow(),
        )

        assert record.outcome_id == "o1"
        assert record.outcome == OutcomeType.SUCCESS

    def test_record_immutability(self):
        """Outcome records are immutable."""
        record = OutcomeRecord(
            outcome_id="o1",
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            timestamp_utc=datetime.utcnow(),
        )

        with pytest.raises(AttributeError):
            record.outcome = OutcomeType.FAILURE

    def test_record_to_payload(self):
        """Convert record to event payload."""
        record = OutcomeRecord(
            outcome_id="o1",
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.PARTIAL,
            timestamp_utc=datetime.utcnow(),
            feedback_text="Correct but slow",
            rating=3,
        )

        payload = record.to_payload()

        assert payload["outcome_id"] == "o1"
        assert payload["outcome"] == "partial"
        assert payload["rating"] == 3


class TestOutcomeRecorder:
    """Test outcome recorder."""

    @pytest.fixture
    def recorder(self):
        """Create recorder instance."""
        return OutcomeRecorder("_default")

    def test_record_success(self, recorder):
        """Record a success outcome."""
        record = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
        )

        assert record.outcome == OutcomeType.SUCCESS
        assert record.outcome_id is not None

    def test_record_with_feedback(self, recorder):
        """Record outcome with user feedback."""
        record = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.PARTIAL,
            feedback_text="Good but could be faster",
            rating=4,
        )

        assert record.feedback_text == "Good but could be faster"
        assert record.rating == 4

    def test_invalid_rating(self, recorder):
        """Reject invalid rating."""
        with pytest.raises(ValueError, match="Invalid rating"):
            recorder.record_outcome(
                decision_id="d1",
                session_id="session-123",
                outcome=OutcomeType.SUCCESS,
                rating=6,  # Out of bounds
            )

    def test_secret_redaction_in_feedback(self, recorder):
        """Redact potential secrets in feedback."""
        record = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.FAILURE,
            feedback_text="Failed with api_key=secret123",
        )

        assert record.feedback_text == "[redacted]"

    def test_all_outcome_types(self, recorder):
        """Support all outcome types."""
        types = [OutcomeType.SUCCESS, OutcomeType.PARTIAL, OutcomeType.FAILURE]

        for outcome_type in types:
            record = recorder.record_outcome(
                decision_id="d1",
                session_id="session-123",
                outcome=outcome_type,
            )

            assert record.outcome == outcome_type
