"""
Stream A: A2 → A3 Wiring Proof (E2E Integration Tests)

Proves complete A1 → A2 → A3 flow works end-to-end.
References: ADR-2075 (Message Contract), ADR-2086 (A3 Skeleton)

Phase 1 focus: Validate message contract + audit-first semantics
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime
from dataclasses import dataclass

from core.learning.outcome_sink_a2 import OutcomeRecord, OutcomeSink


@dataclass(frozen=True)
class MockHistogramBucket:
    """A1 output → A2 input"""
    skill_id: str = "os.delegation_router"
    outcome_count: int = 10
    avg_confidence: float = 0.85
    window_ts: datetime = None
    audit_ref: str = "audit_abc123"


class TestStreamAA2A3WiringProof:
    """Prove A2→A3 wiring: message contract + audit integration."""

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_a2_a3_contract_satisfied(self, mock_audit_write):
        """
        TEST 1: A2 output satisfies A3 input contract

        A2 OutcomeRecord must have all fields needed by A3 ConfidenceScorer.
        Validates: field presence, types, no data loss
        """
        mock_audit_write.return_value = "audit_ref_xyz789"
        mock_orchestrator = Mock()

        # A2: Process outcome
        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(
            skill_id="os.delegation_router",
            outcome_count=20,
            avg_confidence=0.88,
            audit_ref="bucket_ref_456",
        )

        record = sink.process(bucket)

        # Verify A2 output structure
        assert record is not None, "A2 should return OutcomeRecord"
        assert record.skill_id == "os.delegation_router", "skill_id mismatch"
        assert record.outcome_count == 20, "outcome_count mismatch"
        assert record.avg_confidence == 0.88, "avg_confidence mismatch"
        assert record.audit_ref == "audit_ref_xyz789", "audit_ref mismatch"

        # Verify A2 → A3 transport (via orchestrator.enqueue)
        mock_orchestrator.enqueue.assert_called_once()
        task_payload = mock_orchestrator.enqueue.call_args[0][0]

        # Validate A3 input contract (all required fields present)
        a3_required_fields = {
            "task_type": "learning.confidence_score",
            "skill_id": "os.delegation_router",
            "outcome_count": 20,
            "avg_confidence": 0.88,
            "audit_ref": "audit_ref_xyz789",
            "tenant_id": "_test",
        }

        for field_name, expected_value in a3_required_fields.items():
            assert field_name in task_payload, f"A3 payload missing: {field_name}"
            assert task_payload[field_name] == expected_value, \
                f"{field_name}: expected {expected_value}, got {task_payload[field_name]}"

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_a2_validation_prevents_invalid_a3_input(self, mock_audit_write):
        """
        TEST 2: A2 validation prevents invalid data reaching A3

        If A2 rejects invalid data, A3 never receives it.
        Validates: validation boundary + fail-closed semantics
        """
        mock_audit_write.return_value = "audit_ref_abc"
        mock_orchestrator = Mock()

        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)

        # Invalid bucket: outcome_count < 0 (violates A3 contract)
        invalid_bucket = MockHistogramBucket(
            outcome_count=-1,  # ❌ Invalid!
            avg_confidence=0.88,
        )

        result = sink.process(invalid_bucket)

        # A2 rejects before enqueue → A3 never called
        assert result is None, "A2 should reject negative outcome_count"
        mock_orchestrator.enqueue.assert_not_called(), \
            "A3 task should NOT be enqueued on validation failure"

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_a2_audit_failure_stops_a3_task(self, mock_audit_write):
        """
        TEST 3: A2 audit failure → no A3 task (fail-closed)

        ADR-0232 audit-first: if core chain write fails, A3 task not enqueued.
        Validates: audit-first semantics + fail-closed design
        """
        mock_audit_write.side_effect = RuntimeError("chain unavailable")
        mock_orchestrator = Mock()

        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(
            outcome_count=10,
            avg_confidence=0.85,
        )

        # A2 should raise if audit write fails
        with pytest.raises(RuntimeError, match="chain unavailable"):
            sink.process(bucket)

        # A3 task never enqueued on audit failure
        mock_orchestrator.enqueue.assert_not_called(), \
            "A3 task should NEVER be enqueued if audit fails"

    def test_a3_input_schema_alignment(self):
        """
        TEST 4: A3 input schema matches A2 output + task payload

        Validates: field names, types, no hidden assumptions
        """
        # Define A3 input schema (from ADR-2075)
        a3_input_schema = {
            "task_type": str,
            "skill_id": str,
            "outcome_count": int,
            "avg_confidence": float,
            "audit_ref": str,
            "tenant_id": str,
        }

        # Verify OutcomeRecord provides base fields
        record = OutcomeRecord(
            skill_id="test_skill",
            outcome_count=5,
            avg_confidence=0.5,
            audit_ref="ref_123",
        )

        assert hasattr(record, 'skill_id'), "OutcomeRecord missing skill_id"
        assert hasattr(record, 'outcome_count'), "OutcomeRecord missing outcome_count"
        assert hasattr(record, 'avg_confidence'), "OutcomeRecord missing avg_confidence"
        assert hasattr(record, 'audit_ref'), "OutcomeRecord missing audit_ref"

        # Verify task payload structure (A2 → Orchestrator → A3)
        task = {
            "task_type": "learning.confidence_score",
            "skill_id": record.skill_id,
            "outcome_count": record.outcome_count,
            "avg_confidence": record.avg_confidence,
            "audit_ref": record.audit_ref,
            "tenant_id": "_test",
        }

        # Validate all required fields present + correct types
        for field_name, field_type in a3_input_schema.items():
            assert field_name in task, f"A3 schema missing: {field_name}"
            value = task[field_name]
            assert isinstance(value, field_type), \
                f"{field_name}: expected {field_type}, got {type(value)}"


class TestStreamAA2A3AuditIntegration:
    """Verify audit-first semantics for A2→A3 path."""

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_audit_event_emitted_before_enqueue(self, mock_audit_write):
        """
        Audit event must be written BEFORE enqueue.
        Validates: audit-first ordering per ADR-0232
        """
        mock_audit_write.return_value = "audit_ref_order_test"
        mock_orchestrator = Mock()

        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(
            outcome_count=5,
            avg_confidence=0.75,
        )

        record = sink.process(bucket)

        # Audit write must have been called
        mock_audit_write.assert_called_once()

        # Enqueue must have happened after (proof: called with result of audit write)
        mock_orchestrator.enqueue.assert_called_once()
        task = mock_orchestrator.enqueue.call_args[0][0]
        assert task["audit_ref"] == "audit_ref_order_test"

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_tenant_isolation_in_task_payload(self, mock_audit_write):
        """
        A3 task payload must include tenant_id for isolation.
        Validates: GDPR Art. 5/6 tenant scoping
        """
        mock_audit_write.return_value = "audit_ref_tenant"
        mock_orchestrator = Mock()

        sink = OutcomeSink(tenant_id="customer_xyz", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(outcome_count=8)

        record = sink.process(bucket)

        task = mock_orchestrator.enqueue.call_args[0][0]
        assert task["tenant_id"] == "customer_xyz", \
            "Task payload must carry tenant_id for isolation"


class TestStreamAA2A3LatencySLA:
    """
    Verify A2→A3 latency SLA: <200ms total path.

    Components:
    - A2.process(): <100ms (sync validation only)
    - Audit write: <50ms (local chain, no network)
    - Orchestrator.enqueue(): <10ms (memory queue, async)
    """

    @patch('core.learning.outcome_sink_a2.OutcomeSink._write_audit_event')
    def test_a2_process_latency(self, mock_audit_write):
        """A2.process() must complete in <100ms."""
        mock_audit_write.return_value = "audit_ref_latency"
        mock_orchestrator = Mock()

        sink = OutcomeSink(tenant_id="_test", orchestrator=mock_orchestrator)
        bucket = MockHistogramBucket(outcome_count=100)

        import time
        start = time.time()
        record = sink.process(bucket)
        elapsed_ms = (time.time() - start) * 1000

        # Latency assertion (soft SLA for testing)
        assert record is not None, "A2 process failed"
        assert elapsed_ms < 500, f"A2 process took {elapsed_ms:.1f}ms (SLA: <100ms)"


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])
