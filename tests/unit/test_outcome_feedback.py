"""Unit tests for Outcome Feedback (ADR-0317) — closed-loop learning."""

import csv
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.learning.outcome_feedback import (
    OutcomeFeedbackLoop,
    OutcomeFeedbackStore,
    OutcomeRecord,
    OutcomeRecorder,
    OutcomeType,
)


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
            tenant_id="tenant-1",
        )

        assert record.outcome_id == "o1"
        assert record.outcome == OutcomeType.SUCCESS
        assert record.tenant_id == "tenant-1"

    def test_record_immutability(self):
        """Outcome records are immutable."""
        record = OutcomeRecord(
            outcome_id="o1",
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            timestamp_utc=datetime.utcnow(),
            tenant_id="tenant-1",
        )

        with pytest.raises(AttributeError):
            record.outcome = OutcomeType.FAILURE  # type: ignore

    def test_record_to_payload(self):
        """Convert record to event payload."""
        record = OutcomeRecord(
            outcome_id="o1",
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.PARTIAL,
            timestamp_utc=datetime.utcnow(),
            tenant_id="tenant-1",
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
        assert record.tenant_id == "_default"

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

    def test_record_with_quality_score(self, recorder):
        """Record outcome with quality score."""
        record = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            quality_score=0.92,
            latency_ms=150,
        )

        assert record.quality_score == 0.92
        assert record.latency_ms == 150

    def test_invalid_rating(self, recorder):
        """Reject invalid rating."""
        with pytest.raises(ValueError, match="Invalid rating"):
            recorder.record_outcome(
                decision_id="d1",
                session_id="session-123",
                outcome=OutcomeType.SUCCESS,
                rating=6,  # Out of bounds
            )

    def test_invalid_quality_score(self, recorder):
        """Reject invalid quality score."""
        with pytest.raises(ValueError, match="Invalid quality_score"):
            recorder.record_outcome(
                decision_id="d1",
                session_id="session-123",
                outcome=OutcomeType.SUCCESS,
                quality_score=1.5,  # Out of bounds
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

    def test_record_with_user_id(self, recorder):
        """Record outcome with user ID (for GDPR erasure)."""
        record = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            user_id="user-123",
        )

        assert record.user_id == "user-123"


class TestOutcomeFeedbackStore:
    """Test OutcomeFeedbackStore — persistent storage."""

    @pytest.fixture
    def store(self):
        """Create temporary store for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    def test_store_initialization(self, store):
        """Store initializes database schema."""
        assert store.db_path.exists()

    def test_record_outcome(self, store):
        """Record an outcome persistently."""
        recorder = OutcomeRecorder("tenant-1")
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
            rating=5,
        )

        outcome_id = store.record_outcome(outcome)
        assert outcome_id == outcome.outcome_id

        # Verify retrieval
        retrieved = store.get_outcome(outcome_id, tenant_id="_default")
        assert retrieved is not None
        assert retrieved.outcome == OutcomeType.SUCCESS
        assert retrieved.rating == 5

    def test_get_outcomes_by_decision(self, store):
        """Get all outcomes for a decision."""
        recorder = OutcomeRecorder("tenant-1")

        decision_id = "d1"
        for i in range(3):
            outcome = recorder.record_outcome(
                decision_id=decision_id,
                session_id=f"session-{i}",
                outcome=OutcomeType.SUCCESS if i == 0 else OutcomeType.PARTIAL,
                rating=i + 3,
            )
            store.record_outcome(outcome)

        outcomes = store.get_outcomes_by_decision(decision_id, tenant_id="_default")
        assert len(outcomes) == 3

    def test_get_outcomes_by_type(self, store):
        """Query outcomes by type."""
        recorder = OutcomeRecorder("tenant-1")

        for i in range(5):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id=f"session-{i}",
                outcome=OutcomeType.SUCCESS if i < 3 else OutcomeType.FAILURE,
            )
            store.record_outcome(outcome)

        success_outcomes = store.get_outcomes_by_type("tenant-1", OutcomeType.SUCCESS)
        assert len(success_outcomes) == 3

    def test_compute_success_rate(self, store):
        """Compute success rate."""
        recorder = OutcomeRecorder("tenant-1")

        outcomes = [
            OutcomeType.SUCCESS,
            OutcomeType.SUCCESS,
            OutcomeType.FAILURE,
        ]

        for outcome in outcomes:
            record = recorder.record_outcome(
                decision_id="d1",
                session_id="session-123",
                outcome=outcome,
            )
            store.record_outcome(record)

        success_rate = store.compute_success_rate("tenant-1")
        assert success_rate == pytest.approx(2 / 3, rel=0.1)

    def test_export_training_data_csv(self, store):
        """Export outcomes as CSV for training."""
        recorder = OutcomeRecorder("tenant-1")

        for i in range(5):
            outcome = recorder.record_outcome(
                decision_id=f"d{i}",
                session_id=f"session-{i}",
                outcome=OutcomeType.SUCCESS if i % 2 == 0 else OutcomeType.FAILURE,
                rating=i + 1,  # Rating must be 1-5, not 0-4
            )
            store.record_outcome(outcome)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "training_data.csv"
            count = store.export_training_data_csv("tenant-1", output_path)

            assert count == 5
            assert output_path.exists()

            # Verify CSV contents
            with open(output_path, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)
                assert len(rows) == 6  # Header + 5 data rows
                assert rows[0][0] == "outcome_id"

    def test_confidence_delta_success_with_high_rating(self, store):
        """Backprop: success + high rating → +0.15."""
        delta = store.compute_confidence_delta(OutcomeType.SUCCESS, rating=5)
        assert delta == 0.15

    def test_confidence_delta_success_no_rating(self, store):
        """Backprop: success + no rating → +0.10."""
        delta = store.compute_confidence_delta(OutcomeType.SUCCESS)
        assert delta == 0.10

    def test_confidence_delta_partial(self, store):
        """Backprop: partial → 0.0 (neutral)."""
        delta = store.compute_confidence_delta(OutcomeType.PARTIAL)
        assert delta == 0.0

    def test_confidence_delta_failure_with_low_rating(self, store):
        """Backprop: failure + low rating → -0.20."""
        delta = store.compute_confidence_delta(OutcomeType.FAILURE, rating=1)
        assert delta == -0.20

    def test_confidence_delta_failure_no_rating(self, store):
        """Backprop: failure + no rating → -0.15."""
        delta = store.compute_confidence_delta(OutcomeType.FAILURE)
        assert delta == -0.15

    def test_delete_user_outcomes_gdpr_erasure(self, store):
        """Delete all outcomes for a user (GDPR Art. 17)."""
        recorder = OutcomeRecorder("tenant-1")

        # Record outcomes for two users
        for user_id in ["user-1", "user-2"]:
            for i in range(3):
                outcome = recorder.record_outcome(
                    decision_id=f"d-{user_id}-{i}",
                    session_id=f"session-{user_id}-{i}",
                    outcome=OutcomeType.SUCCESS,
                    user_id=user_id,
                )
                store.record_outcome(outcome)

        # Delete user-1 data
        deleted_count = store.delete_user_outcomes("tenant-1", "user-1")
        assert deleted_count == 3

    def test_cleanup_old_outcomes_retention_policy(self, store):
        """Delete outcomes older than N days."""
        recorder = OutcomeRecorder("tenant-1")

        now = datetime.utcnow()
        old_time = now - timedelta(days=91)

        # Record old outcome
        outcome_old = OutcomeRecord(
            outcome_id="o-old",
            decision_id="d-old",
            session_id="session-old",
            outcome=OutcomeType.SUCCESS,
            timestamp_utc=old_time,
            tenant_id="tenant-1",
        )
        store.record_outcome(outcome_old)

        # Record new outcome
        outcome_new = recorder.record_outcome(
            decision_id="d-new",
            session_id="session-new",
            outcome=OutcomeType.SUCCESS,
        )
        store.record_outcome(outcome_new)

        # Cleanup old outcomes (90-day retention)
        deleted_count = store.cleanup_old_outcomes("tenant-1", days=90)
        assert deleted_count == 1


class TestOutcomeFeedbackLoop:
    """Test OutcomeFeedbackLoop — async feedback processing."""

    @pytest.fixture
    def store(self):
        """Create temporary store."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "outcomes.db"
            yield OutcomeFeedbackStore(db_path)

    @pytest.fixture
    def loop(self, store):
        """Create feedback loop."""
        return OutcomeFeedbackLoop("tenant-1", store, max_queue_size=100)

    @pytest.mark.asyncio
    async def test_loop_start_stop(self, loop):
        """Test loop lifecycle (start/stop)."""
        await loop.start()
        assert loop._worker_task is not None
        await loop.stop()
        assert loop._worker_task is None

    @pytest.mark.asyncio
    async def test_emit_outcome(self, loop, store):
        """Emit an outcome (non-blocking)."""
        await loop.start()

        recorder = OutcomeRecorder("tenant-1")
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
        )

        await loop.emit_outcome(outcome)
        await loop.flush()
        await loop.stop()

        # Verify outcome was persisted
        retrieved = store.get_outcome(outcome.outcome_id, tenant_id="_default")
        assert retrieved is not None

    @pytest.mark.asyncio
    async def test_tenant_mismatch_rejected(self, loop):
        """Reject outcomes from different tenant."""
        await loop.start()

        recorder = OutcomeRecorder("tenant-2")  # Different tenant
        outcome = recorder.record_outcome(
            decision_id="d1",
            session_id="session-123",
            outcome=OutcomeType.SUCCESS,
        )

        with pytest.raises(ValueError, match="Tenant mismatch"):
            await loop.emit_outcome(outcome)

        await loop.stop()
