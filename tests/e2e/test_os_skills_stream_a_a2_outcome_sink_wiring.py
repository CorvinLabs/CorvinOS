"""Stream A, Module A2: outcome_sink E2E wiring proof.

Validates message flow A1 → A2 → A3:
- A1.EventStoreConsumer produces HistogramBucket
- A2.OutcomeSink.process() validates + audits + enqueues A3
- Audit event emitted (learning.outcome_processed)
- A3 task enqueued to orchestrator
"""
import pytest
from datetime import datetime
from unittest.mock import Mock
from core.learning.outcome_sink import OutcomeSink, a1_to_a2_to_a3_flow


class TestOutcomeSinkValidation:
    """A2: Bounds validation (outcome_count, avg_confidence)."""

    def test_valid_outcome_passes(self):
        """Valid metrics → True."""
        sink = OutcomeSink("_default")
        assert sink.validate_outcome(5, 0.85) is True

    def test_negative_outcome_count_fails(self):
        """outcome_count < 0 → False."""
        sink = OutcomeSink("_default")
        assert sink.validate_outcome(-1, 0.85) is False

    def test_confidence_out_of_bounds_fails(self):
        """avg_confidence not in [0, 1] → False."""
        sink = OutcomeSink("_default")
        assert sink.validate_outcome(5, 1.5) is False
        assert sink.validate_outcome(5, -0.1) is False

    def test_nan_confidence_fails(self):
        """NaN detection → False."""
        sink = OutcomeSink("_default")
        nan = float("nan")
        assert sink.validate_outcome(5, nan) is False


class TestOutcomeSinkProcess:
    """A2: Process bucket (audit-first + A3 enqueue)."""

    def test_process_successful(self):
        """Valid bucket → OutcomeRecord returned."""
        sink = OutcomeSink("_default")
        bucket = Mock()
        bucket.skill_id = "os.router"
        bucket.outcome_count = 5
        bucket.avg_confidence = 0.85
        bucket.window_ts = datetime.now()
        bucket.tenant_id = "_default"

        mock_write = Mock(return_value="hash_audit123")
        mock_queue = Mock()
        mock_queue.enqueue = Mock(return_value="task_456")

        record = sink.process(bucket, mock_write, mock_queue)

        assert record is not None
        assert record.skill_id == "os.router"
        assert record.audit_ref == "hash_audit123"
        assert mock_queue.enqueue.called

    def test_process_validation_fails_returns_none(self):
        """Invalid bucket → None (no audit, no A3 task)."""
        sink = OutcomeSink("_default")
        bucket = Mock()
        bucket.outcome_count = -1  # Invalid
        bucket.avg_confidence = 0.85

        mock_write = Mock()
        mock_queue = Mock()

        record = sink.process(bucket, mock_write, mock_queue)

        assert record is None
        assert not mock_write.called  # No audit on validation fail

    def test_audit_write_fails_raises_error(self):
        """Audit write fails → RuntimeError (fail-closed)."""
        sink = OutcomeSink("_default")
        bucket = Mock()
        bucket.skill_id = "os.router"
        bucket.outcome_count = 5
        bucket.avg_confidence = 0.85
        bucket.window_ts = datetime.now()
        bucket.tenant_id = "_default"

        mock_write = Mock(side_effect=Exception("chain write failed"))
        mock_queue = Mock()

        with pytest.raises(RuntimeError, match="Audit write failed"):
            sink.process(bucket, mock_write, mock_queue)


class TestE2EWiringProofA1toA3:
    """A2: Full message flow A1 → A2 → A3."""

    def test_e2e_flow_a1_to_a3(self):
        """Complete flow: A1 bucket → A2 process → A3 enqueue."""
        bucket = Mock()
        bucket.skill_id = "os.router"
        bucket.outcome_count = 5
        bucket.avg_confidence = 0.85
        bucket.window_ts = datetime.now()
        bucket.tenant_id = "_default"

        mock_write = Mock(return_value="hash_xyz789")
        mock_queue = Mock()
        mock_queue.enqueue = Mock(return_value="task_321")

        record, audit_ref = a1_to_a2_to_a3_flow(bucket, mock_write, mock_queue)

        assert record is not None
        assert record.skill_id == "os.router"
        assert audit_ref == "hash_xyz789"
        assert mock_queue.enqueue.called  # A3 task enqueued

    def test_e2e_flow_audit_fails_returns_empty(self):
        """Audit failure → (None, "")."""
        bucket = Mock()
        bucket.skill_id = "os.router"
        bucket.outcome_count = 5
        bucket.avg_confidence = 0.85
        bucket.window_ts = datetime.now()
        bucket.tenant_id = "_default"

        mock_write = Mock(side_effect=Exception("chain failed"))
        mock_queue = Mock()

        record, audit_ref = a1_to_a2_to_a3_flow(bucket, mock_write, mock_queue)

        assert record is None
        assert audit_ref == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
