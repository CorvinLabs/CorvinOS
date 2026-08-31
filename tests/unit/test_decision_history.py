"""Unit tests for Decision History (ADR-0316) — user choices tracking."""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.learning.decision_history import DecisionHistoryStore, DecisionRecord, DecisionRecorder


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
            tenant_id="tenant-1",
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
            tenant_id="tenant-1",
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
            tenant_id="tenant-1",
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
        assert record.tenant_id == "_default"

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

    def test_decision_with_user_id(self, recorder):
        """Create decision with user ID (for GDPR erasure)."""
        record = recorder.create_decision(
            choice_type="model_choice",
            candidates=["opus", "sonnet", "haiku"],
            chosen="opus",
            session_id="session-456",
            user_id="user-123",
        )

        assert record.user_id == "user-123"


class TestDecisionHistoryStore:
    """Test DecisionHistoryStore — persistent time-series storage."""

    @pytest.fixture
    def store(self):
        """Create temporary store for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "decisions.db"
            yield DecisionHistoryStore(db_path)

    def test_store_initialization(self, store):
        """Store initializes database schema."""
        assert store.db_path.exists()

    def test_record_decision(self, store):
        """Record a decision persistently."""
        recorder = DecisionRecorder("tenant-1")
        decision = recorder.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="session-123",
        )

        decision_id = store.record_decision(decision)
        assert decision_id == decision.decision_id

        # Verify retrieval
        retrieved = store.get_decision(decision_id)
        assert retrieved is not None
        assert retrieved.choice_type == "skill_selection"
        assert retrieved.chosen == "a"

    def test_get_decisions_by_type(self, store):
        """Query decisions by type."""
        recorder = DecisionRecorder("tenant-1")

        # Record multiple decisions
        for i in range(5):
            decision = recorder.create_decision(
                choice_type="skill_selection",
                candidates=[f"skill-{j}" for j in range(3)],
                chosen=f"skill-{i % 3}",
                session_id=f"session-{i}",
            )
            store.record_decision(decision)

        decisions = store.get_decisions_by_type("tenant-1", "skill_selection")
        assert len(decisions) == 5

    def test_get_decisions_by_date_range(self, store):
        """Query decisions within a date range."""
        recorder = DecisionRecorder("tenant-1")

        now = datetime.utcnow()
        old_time = now - timedelta(days=2)

        # Record decision with old timestamp
        decision_old = DecisionRecord(
            decision_id="old-1",
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            timestamp_utc=old_time,
            session_id="session-old",
            tenant_id="tenant-1",
        )
        store.record_decision(decision_old)

        # Record current decision with explicit timestamp within range
        recent_time = now - timedelta(hours=1)  # 1 hour ago
        decision_new = DecisionRecord(
            decision_id="new-1",
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="b",
            timestamp_utc=recent_time,
            session_id="session-new",
            tenant_id="tenant-1",
        )
        store.record_decision(decision_new)

        # Query last day
        start = now - timedelta(days=1)
        end = now + timedelta(seconds=1)  # Add 1 second buffer for timing
        decisions = store.get_decisions_by_date_range("tenant-1", start, end)
        assert len(decisions) == 1
        assert decisions[0].decision_id == decision_new.decision_id

    def test_get_decisions_by_session(self, store):
        """Query all decisions in a session."""
        recorder = DecisionRecorder("tenant-1")

        for i in range(3):
            decision = recorder.create_decision(
                choice_type=f"choice-type-{i}",
                candidates=["a", "b"],
                chosen="a",
                session_id="session-123",
            )
            store.record_decision(decision)

        decisions = store.get_decisions_by_session("session-123")
        assert len(decisions) == 3

    def test_get_candidate_stats(self, store):
        """Compute statistics for each candidate."""
        recorder = DecisionRecorder("tenant-1")

        # Record decisions with different choices
        for i in range(10):
            decision = recorder.create_decision(
                choice_type="skill_selection",
                candidates=["skill-a", "skill-b", "skill-c"],
                chosen="skill-a" if i < 6 else ("skill-b" if i < 9 else "skill-c"),
                session_id=f"session-{i}",
            )
            store.record_decision(decision)

        stats = store.get_candidate_stats("tenant-1", "skill_selection")

        assert stats["skill-a"]["total"] == 10
        assert stats["skill-a"]["chosen"] == 6
        assert stats["skill-a"]["selection_rate"] == 0.6

        assert stats["skill-b"]["total"] == 10
        assert stats["skill-b"]["chosen"] == 3

        assert stats["skill-c"]["total"] == 10
        assert stats["skill-c"]["chosen"] == 1

    def test_delete_user_decisions_gdpr_erasure(self, store):
        """Delete all decisions for a user (GDPR Art. 17)."""
        recorder = DecisionRecorder("tenant-1")

        # Record decisions for user-1 and user-2
        for user_id in ["user-1", "user-2"]:
            for i in range(3):
                decision = recorder.create_decision(
                    choice_type="skill_selection",
                    candidates=["a", "b"],
                    chosen="a",
                    session_id=f"session-{user_id}-{i}",
                    user_id=user_id,
                )
                store.record_decision(decision)

        # Delete user-1 data
        deleted_count = store.delete_user_decisions("tenant-1", "user-1")
        assert deleted_count == 3

        # Verify user-1 data is gone, user-2 data remains
        all_decisions = store.get_decisions_by_type("tenant-1", "skill_selection")
        user_ids = [d.user_id for d in all_decisions]
        assert "user-1" not in user_ids
        assert "user-2" in user_ids

    def test_cleanup_old_decisions_retention_policy(self, store):
        """Delete decisions older than N days (retention policy)."""
        from datetime import datetime

        now = datetime.utcnow()
        old_time = now - timedelta(days=91)

        # Record old decision
        decision_old = DecisionRecord(
            decision_id="old-1",
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            timestamp_utc=old_time,
            session_id="session-old",
            tenant_id="tenant-1",
        )
        store.record_decision(decision_old)

        # Record new decision with explicit recent timestamp
        recent_time = now - timedelta(days=1)
        decision_new = DecisionRecord(
            decision_id="new-1",
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            timestamp_utc=recent_time,
            session_id="session-new",
            tenant_id="tenant-1",
        )
        store.record_decision(decision_new)

        # Cleanup old decisions (90-day retention)
        deleted_count = store.cleanup_old_decisions("tenant-1", days=90)
        assert deleted_count == 1

        # Verify new decision remains
        decisions = store.get_decisions_by_type("tenant-1", "skill_selection")
        assert len(decisions) == 1
        assert decisions[0].decision_id == decision_new.decision_id

    def test_tenant_isolation(self, store):
        """Decisions are isolated by tenant."""
        recorder1 = DecisionRecorder("tenant-1")
        recorder2 = DecisionRecorder("tenant-2")

        decision1 = recorder1.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="a",
            session_id="session-1",
        )
        decision2 = recorder2.create_decision(
            choice_type="skill_selection",
            candidates=["a", "b"],
            chosen="b",
            session_id="session-2",
        )

        store.record_decision(decision1)
        store.record_decision(decision2)

        # Query tenant-1 only
        decisions1 = store.get_decisions_by_type("tenant-1", "skill_selection")
        assert len(decisions1) == 1
        assert decisions1[0].chosen == "a"

        # Query tenant-2 only
        decisions2 = store.get_decisions_by_type("tenant-2", "skill_selection")
        assert len(decisions2) == 1
        assert decisions2[0].chosen == "b"
