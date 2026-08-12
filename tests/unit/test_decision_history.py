"""Tests for Decision History (ADR-0316)."""

import pytest
from datetime import datetime
from core.learning.decision_history import DecisionRecord, DecisionRecorder


class TestDecisionRecord:
    """Test decision record model."""

    def test_create_record(self):
        """Create a decision record."""
        record = DecisionRecord(
            decision_id="d1",
            choice_type="skill_selection",
            candidates=["skill-a", "skill-b", "skill-c"],
            chosen="skill-a",
            timestamp_utc=datetime.utcnow(),
            session_id="session-123",
        )

        assert record.decision_id == "d1"
        assert record.chosen == "skill-a"
        assert len(record.candidates) == 3

    def test_record_immutability(self):
        """Decision records are immutable."""
        record = DecisionRecord(
            decision_id="d1",
            choice_type="skill_selection",
            candidates=["skill-a"],
            chosen="skill-a",
            timestamp_utc=datetime.utcnow(),
            session_id="session-123",
        )

        with pytest.raises(AttributeError):
            record.chosen = "skill-b"

    def test_record_to_payload(self):
        """Convert record to event payload."""
        record = DecisionRecord(
            decision_id="d1",
            choice_type="model_choice",
            candidates=["gpt-4", "claude-3", "gemini"],
            chosen="claude-3",
            timestamp_utc=datetime.utcnow(),
            session_id="session-456",
            confidence_score=0.92,
            reasoning="Best latency/quality trade-off",
        )

        payload = record.to_payload()

        assert payload["decision_id"] == "d1"
        assert payload["chosen"] == "claude-3"
        assert payload["confidence_score"] == 0.92


class TestDecisionRecorder:
    """Test decision recorder."""

    @pytest.fixture
    def recorder(self):
        """Create recorder instance."""
        return DecisionRecorder("_default")

    def test_create_decision(self, recorder):
        """Create a decision via recorder."""
        record = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["ranking", "summarizer", "code_review"],
            chosen="ranking",
            session_id="session-789",
        )

        assert record.choice_type == "skill_selection"
        assert record.chosen == "ranking"
        assert record.decision_id is not None

    def test_decision_with_confidence(self, recorder):
        """Include confidence score from ADR-0315."""
        record = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="session-123",
            confidence_score=0.85,
        )

        assert record.confidence_score == 0.85

    def test_too_many_candidates(self, recorder):
        """Reject if too many candidates (>100)."""
        candidates = [f"skill-{i}" for i in range(101)]

        with pytest.raises(ValueError, match="Too many candidates"):
            recorder.create_decision(
                choice_type="skill_selection",
                candidates=candidates,
                chosen="skill-0",
                session_id="session-123",
            )

    def test_chosen_not_in_candidates(self, recorder):
        """Reject if chosen is not in candidates."""
        with pytest.raises(ValueError, match="not in candidates"):
            recorder.create_decision(
                choice_type="skill_selection",
                candidates=["a", "b", "c"],
                chosen="d",
                session_id="session-123",
            )

    def test_invalid_confidence_score(self, recorder):
        """Reject invalid confidence score."""
        with pytest.raises(ValueError, match="Invalid confidence_score"):
            recorder.create_decision(
                choice_type="skill_selection",
                candidates=["a", "b"],
                chosen="a",
                session_id="session-123",
                confidence_score=1.5,  # Out of bounds
            )

    def test_secret_redaction(self, recorder):
        """Redact potential secrets in reasoning."""
        record = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="session-123",
            reasoning="Used api_key=secret123 for auth",
        )

        assert record.reasoning == "[redacted]"

    def test_safe_reasoning(self, recorder):
        """Keep safe reasoning untouched."""
        reasoning = "Low variance + high relevance"
        record = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="session-123",
            reasoning=reasoning,
        )

        assert record.reasoning == reasoning

    def test_all_decision_types(self, recorder):
        """Support multiple decision types."""
        types = ["skill_selection", "model_choice", "routing"]

        for choice_type in types:
            record = recorder.create_decision(
                choice_type=choice_type,
                candidates=["opt-1", "opt-2"],
                chosen="opt-1",
                session_id="session-123",
            )

            assert record.choice_type == choice_type
