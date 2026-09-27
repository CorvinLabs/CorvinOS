"""Stream A, Module A1 Tests: EventStore Consumer Chain E2E.

Tests verify:
1. Consumer reads from EventStore (tenant-bound)
2. Aggregator handles empty batches
3. PII scrubber: reason text dropped, reason_length kept
4. Audit write fails → consumer stops (fail-closed)
"""
import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock
from core.learning.event_store_consumer import (
    EventStoreConsumer,
    BatchAggregator,
    FeedbackFilter,
    AuditFirstWriter,
    LearningEvent,
    HistogramBucket,
    run_consumer_cycle,
)


class TestEventStoreConsumerTenantIsolation:
    """A1: Consumer enforces tenant_id isolation."""

    def test_tenant_mismatch_raises_error(self):
        """Read from mismatched tenant → ValueError (fail-closed)."""
        consumer = EventStoreConsumer(tenant_id="_default")
        mock_store = Mock()
        mock_store.tenant_id = "other_tenant"

        with pytest.raises(ValueError, match="tenant mismatch"):
            consumer.read_batch(mock_store)

    def test_tenant_match_returns_events(self):
        """Tenant match → events read successfully."""
        consumer = EventStoreConsumer(tenant_id="_default")
        mock_store = Mock()
        mock_store.tenant_id = "_default"
        mock_store.query = Mock(
            return_value=[
                {
                    "event_type": "outcome_feedback",
                    "skill_id": "os.router",
                    "timestamp": datetime.now(),
                    "payload": {"outcome": "success"},
                    "audit_ref": "hash123",
                }
            ]
        )

        events = consumer.read_batch(mock_store)

        assert len(events) == 1
        assert events[0].skill_id == "os.router"
        assert events[0].tenant_id == "_default"

    def test_empty_batch_returns_empty_list(self):
        """No events in batch → return [] (not error)."""
        consumer = EventStoreConsumer(tenant_id="_default")
        mock_store = Mock()
        mock_store.tenant_id = "_default"
        mock_store.query = Mock(return_value=[])

        events = consumer.read_batch(mock_store)

        assert events == []


class TestBatchAggregatorWindow:
    """A1: Aggregator folds events into histogram buckets."""

    def test_empty_events_returns_none(self):
        """Empty list → None (no stats to compute)."""
        bucket = BatchAggregator.fold_window([], "os.router")
        assert bucket is None

    def test_single_event_creates_bucket(self):
        """One event → HistogramBucket with count=1."""
        event = LearningEvent(
            event_type="outcome_feedback",
            skill_id="os.router",
            timestamp=datetime.now(),
            tenant_id="_default",
            payload={"outcome": "success"},
            audit_ref="hash123",
        )

        bucket = BatchAggregator.fold_window([event], "os.router")

        assert bucket is not None
        assert bucket.outcome_count == 1
        assert bucket.feedback_count == 0
        assert bucket.skill_id == "os.router"

    def test_mixed_events_aggregates_stats(self):
        """Outcome + confidence → stats folded."""
        now = datetime.now()
        events = [
            LearningEvent(
                event_type="outcome_feedback",
                skill_id="os.router",
                timestamp=now,
                tenant_id="_default",
                payload={"outcome": "success"},
                audit_ref="h1",
            ),
            LearningEvent(
                event_type="confidence_score",
                skill_id="os.router",
                timestamp=now,
                tenant_id="_default",
                payload={"confidence": 0.85},
                audit_ref="h2",
            ),
        ]

        bucket = BatchAggregator.fold_window(events, "os.router")

        assert bucket.outcome_count == 1
        assert bucket.avg_confidence == 0.85

    def test_homogeneity_check_raises_on_mismatch(self):
        """Events with different skill_id → ValueError."""
        events = [
            LearningEvent(
                event_type="outcome_feedback",
                skill_id="os.router",
                timestamp=datetime.now(),
                tenant_id="_default",
                payload={},
                audit_ref="h1",
            ),
            LearningEvent(
                event_type="outcome_feedback",
                skill_id="os.flow_guard",
                timestamp=datetime.now(),
                tenant_id="_default",
                payload={},
                audit_ref="h2",
            ),
        ]

        with pytest.raises(ValueError, match="Homogeneity violation"):
            BatchAggregator.fold_window(events, "os.router")


class TestFeedbackFilterPIIScrubbing:
    """A1: PII scrubber drops text, keeps metadata."""

    def test_reason_text_dropped_length_kept(self):
        """'reason' text removed, 'reason_length' added."""
        event = LearningEvent(
            event_type="outcome_feedback",
            skill_id="os.router",
            timestamp=datetime.now(),
            tenant_id="_default",
            payload={"outcome": "success", "reason": "user was confused"},
            audit_ref="h1",
        )

        scrubbed = FeedbackFilter.scrub_pii(event)

        assert scrubbed is not None
        assert "reason" not in scrubbed.payload
        assert scrubbed.payload["reason_length"] == len("user was confused")  # 17

    def test_operator_id_dropped(self):
        """'operator_id' removed (PII)."""
        event = LearningEvent(
            event_type="outcome_feedback",
            skill_id="os.router",
            timestamp=datetime.now(),
            tenant_id="_default",
            payload={"outcome": "success", "operator_id": "user@example.com"},
            audit_ref="h1",
        )

        scrubbed = FeedbackFilter.scrub_pii(event)

        assert scrubbed is not None
        assert "operator_id" not in scrubbed.payload

    def test_pii_pattern_detected_returns_none(self):
        """Email/phone patterns → None (fail-closed)."""
        event = LearningEvent(
            event_type="outcome_feedback",
            skill_id="os.router",
            timestamp=datetime.now(),
            tenant_id="_default",
            payload={"email": "admin@corp.com"},
            audit_ref="h1",
        )

        scrubbed = FeedbackFilter.scrub_pii(event)

        assert scrubbed is None


class TestAuditFirstWriter:
    """A1: Audit write fails → consumer stops (fail-closed)."""

    def test_successful_audit_write_returns_ref(self):
        """Audit write succeeds → returns audit_ref."""
        bucket = HistogramBucket(
            skill_id="os.router",
            window_ts=datetime.now(),
            outcome_count=1,
            avg_confidence=0.85,
            feedback_count=0,
            audit_ref="",
        )
        mock_write = Mock(return_value="hash_abc123")

        ref = AuditFirstWriter.write_event("_default", bucket, mock_write)

        assert ref == "hash_abc123"
        assert mock_write.called

    def test_audit_write_fails_raises_error(self):
        """Audit write fails → RuntimeError (fail-closed, no retry)."""
        bucket = HistogramBucket(
            skill_id="os.router",
            window_ts=datetime.now(),
            outcome_count=1,
            avg_confidence=0.85,
            feedback_count=0,
            audit_ref="",
        )
        mock_write = Mock(side_effect=Exception("chain write failed"))

        with pytest.raises(RuntimeError, match="Audit write failed"):
            AuditFirstWriter.write_event("_default", bucket, mock_write)


class TestConsumerCycleE2E:
    """A1: Full cycle RED → GREEN."""

    def test_e2e_consumer_cycle_reads_aggregates_audits(self):
        """Full cycle: read events → aggregate → audit → emit."""
        # Setup mocks
        mock_store = Mock()
        mock_store.tenant_id = "_default"
        mock_store.query = Mock(
            return_value=[
                {
                    "event_type": "outcome_feedback",
                    "skill_id": "os.router",
                    "timestamp": datetime.now(),
                    "payload": {"outcome": "success"},
                    "audit_ref": "h1",
                },
                {
                    "event_type": "confidence_score",
                    "skill_id": "os.router",
                    "timestamp": datetime.now(),
                    "payload": {"confidence": 0.85},
                    "audit_ref": "h2",
                },
            ]
        )
        mock_write = Mock(return_value="hash_xyz789")

        # Run cycle
        count = run_consumer_cycle("_default", mock_store, mock_write)

        assert count == 2  # Both events processed
        assert mock_write.called  # Audit event emitted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
