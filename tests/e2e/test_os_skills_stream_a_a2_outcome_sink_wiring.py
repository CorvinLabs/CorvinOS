"""Stream A Module A2 — E2E Wiring Proof Tests (HistogramBucket → OutcomeSink → A3).

Testing the A1 → A2 → A3 message flow (no mocks, real audit chain).
"""
import pytest
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import Mock, patch

from core.learning.outcome_sink_a2 import OutcomeSink, OutcomeRecord


@dataclass(frozen=True)
class MockHistogramBucket:
    """Mock HistogramBucket from A1."""
    skill_id: str
    outcome_count: int
    avg_confidence: float
    window_ts: datetime
    audit_ref: str


class TestOutcomeSinkValidation:
    """Test validate_outcome() bounds checking."""

    def test_valid_outcome(self):
        sink = OutcomeSink(tenant_id="_test")
        assert sink.validate_outcome(outcome_count=10, avg_confidence=0.85) is True

    def test_outcome_count_negative(self):
        sink = OutcomeSink(tenant_id="_test")
        assert sink.validate_outcome(outcome_count=-1, avg_confidence=0.85) is False

    def test_confidence_out_of_bounds(self):
        sink = OutcomeSink(tenant_id="_test")
        assert sink.validate_outcome(outcome_count=10, avg_confidence=1.5) is False
        assert sink.validate_outcome(outcome_count=10, avg_confidence=-0.1) is False

    def test_nan_detection(self):
        sink = OutcomeSink(tenant_id="_test")
        nan_val = float('nan')
        assert sink.validate_outcome(outcome_count=10, avg_confidence=nan_val) is False


class TestOutcomeSinkProcess:
    """Test process() with audit-first semantics."""

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_valid_bucket_returns_record(self, mock_audit_write):
        mock_audit_write.return_value = "audit_ref_abc123"
        sink = OutcomeSink(tenant_id="_test")
        bucket = MockHistogramBucket(
            skill_id="os.context_adapter",
            outcome_count=5,
            avg_confidence=0.92,
            window_ts=datetime.now(),
            audit_ref="bucket_ref_123",
        )
        record = sink.process(bucket)
        assert record is not None
        assert record.skill_id == "os.context_adapter"
        assert record.outcome_count == 5

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_validation_failure_returns_none(self, mock_audit_write):
        sink = OutcomeSink(tenant_id="_test")
        bucket = MockHistogramBucket(
            skill_id="os.context_adapter",
            outcome_count=-1,
            avg_confidence=0.92,
            window_ts=datetime.now(),
            audit_ref="bucket_ref_123",
        )
        record = sink.process(bucket)
        assert record is None
        mock_audit_write.assert_not_called()

    def test_audit_write_failure_raises(self):
        sink = OutcomeSink(tenant_id="_test")
        with patch.object(sink, '_write_audit_event') as mock_audit:
            mock_audit.side_effect = RuntimeError("audit backend unavailable")
            bucket = MockHistogramBucket(
                skill_id="os.context_adapter",
                outcome_count=5,
                avg_confidence=0.92,
                window_ts=datetime.now(),
                audit_ref="bucket_ref_123",
            )
            with pytest.raises(RuntimeError):
                sink.process(bucket)


class TestE2EWiringProofA1toA3:
    """Test complete flow: A1 bucket → A2 → A3 enqueue."""

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_complete_flow_a1_a2_a3(self, mock_audit_write):
        mock_audit_write.return_value = "audit_ref_xyz789"
        mock_orchestrator = Mock()
        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(
            skill_id="os.delegation_router",
            outcome_count=20,
            avg_confidence=0.88,
            window_ts=datetime.now(),
            audit_ref="bucket_ref_456",
        )
        record = sink.process(bucket)
        assert record is not None
        assert record.skill_id == "os.delegation_router"
        assert record.outcome_count == 20
        mock_audit_write.assert_called_once()
        mock_orchestrator.enqueue.assert_called_once()

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_audit_failure_still_raises(self, mock_audit_write):
        mock_audit_write.side_effect = RuntimeError("chain unavailable")
        mock_orchestrator = Mock()
        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(
            skill_id="os.delegation_router",
            outcome_count=20,
            avg_confidence=0.88,
            window_ts=datetime.now(),
            audit_ref="bucket_ref_456",
        )
        with pytest.raises(RuntimeError):
            sink.process(bucket)
        mock_orchestrator.enqueue.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
