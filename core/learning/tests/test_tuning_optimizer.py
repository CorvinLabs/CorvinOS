"""Unit tests for TuningOptimizer (Feature 3, Week 3 L5 k=2).

Tests cover:
1. Metrics collection and tracking
2. Objective function computation
3. Tuning proposal generation
4. Tuning application
5. Tuning revocation
6. Persistence and recovery
7. Audit integration
8. A/B testing support (basic)
9. Rollback
10. Edge cases
"""

import pytest
from unittest.mock import Mock
from datetime import datetime, timedelta
import tempfile
import json

from core.learning.tuning_optimizer import (
    TuningOptimizer,
    TuningMetrics,
    TuningProposal,
    TuningHistory,
)
from core.skills.feedback_stability import OperatorApprovalGate


@pytest.fixture
def mock_audit_backend():
    """Mock audit backend."""
    backend = Mock()
    backend.write_event = Mock(return_value="event_id")
    return backend


@pytest.fixture
def approval_gate(mock_audit_backend, tmp_path):
    """Create OperatorApprovalGate with temp directory."""
    gate = OperatorApprovalGate(
        tenant_id="_default",
        auto_approval_confidence_threshold=0.8,
        audit_backend=mock_audit_backend,
        corvin_home=str(tmp_path),
    )
    return gate


@pytest.fixture
def tuning_optimizer(approval_gate, tmp_path):
    """Create TuningOptimizer."""
    return TuningOptimizer(
        approval_gate=approval_gate,
        tenant_id="_default",
        optimization_window_hours=24,
        threshold_search_step=0.05,
        corvin_home=str(tmp_path),
    )


# ============================================================================
# Test: Metrics Collection
# ============================================================================


class TestMetricsCollection:
    """Test approval metrics collection."""

    def test_track_approval(self, tuning_optimizer):
        """Test tracking an approval."""
        tuning_optimizer.track_approval(
            skill_id="test.skill_1",
            approval_id="approval_1",
            decision="approved",
            confidence=0.9,
            operator_latency_ms=500.0,
            auto_approved=True,
        )

        metrics = tuning_optimizer.tracked_approvals["test.skill_1"]
        assert metrics.total_approval_count == 1
        assert metrics.manual_approval_count == 0  # auto-approved

    def test_track_manual_approval(self, tuning_optimizer):
        """Test tracking a manual approval."""
        tuning_optimizer.track_approval(
            skill_id="test.skill_1",
            approval_id="approval_1",
            decision="approved",
            confidence=0.5,
            operator_latency_ms=1500.0,
            auto_approved=False,
        )

        metrics = tuning_optimizer.tracked_approvals["test.skill_1"]
        assert metrics.manual_approval_count == 1

    def test_track_revoke(self, tuning_optimizer):
        """Test tracking a revoke."""
        tuning_optimizer.track_revoke(
            skill_id="test.skill_1",
            approval_id="approval_1",
        )

        metrics = tuning_optimizer.tracked_approvals["test.skill_1"]
        assert metrics.revoke_count == 1

    def test_track_multiple_approvals(self, tuning_optimizer):
        """Test tracking multiple approvals."""
        for i in range(10):
            tuning_optimizer.track_approval(
                skill_id="test.skill_1",
                approval_id=f"approval_{i}",
                decision="approved",
                confidence=0.9,
                operator_latency_ms=100.0 * (i + 1),  # 0 ms = no operator wait, not tracked
                auto_approved=(i < 8),
            )

        metrics = tuning_optimizer.tracked_approvals["test.skill_1"]
        assert metrics.total_approval_count == 10
        assert metrics.manual_approval_count == 2
        assert len(metrics.operator_latencies_ms) == 10


# ============================================================================
# Test: Objective Function
# ============================================================================


class TestObjectiveFunction:
    """Test objective function computation."""

    def test_manual_approval_pct(self):
        """Test manual approval percentage."""
        metrics = TuningMetrics(
            manual_approval_count=3,
            total_approval_count=10,
        )
        assert abs(metrics.manual_approval_pct() - 30.0) < 0.01

    def test_revoke_rate_pct(self):
        """Test revoke rate percentage."""
        metrics = TuningMetrics(
            revoke_count=1,
            total_approved_count=10,
        )
        assert abs(metrics.revoke_rate_pct() - 10.0) < 0.01

    def test_latency_p95(self):
        """Test 95th percentile latency."""
        metrics = TuningMetrics(
            operator_latencies_ms=list(range(1, 101)),  # 1 to 100 ms
        )
        p95 = metrics.latency_p95_ms()
        assert 94 <= p95 <= 96  # Approximate

    def test_objective_score(self):
        """Test objective function score."""
        metrics = TuningMetrics(
            manual_approval_count=4,
            total_approval_count=10,  # 40%
            revoke_count=1,
            total_approved_count=10,  # 10%
            operator_latencies_ms=[100, 200, 300],  # ~200ms
        )
        # score = 0.4 * 40 + 0.5 * 10 + 0.1 * (200/1000)
        # score = 16 + 5 + 0.02 = 21.02
        score = metrics.objective_score()
        assert 20.0 < score < 22.0


# ============================================================================
# Test: Tuning Proposal
# ============================================================================


class TestTuningProposal:
    """Test tuning proposal generation."""

    def test_propose_tuning_no_metrics(self, tuning_optimizer):
        """Test proposal when no metrics available."""
        proposal = tuning_optimizer.propose_tuning("test.skill_1")
        assert proposal is None  # No metrics to base proposal on

    def test_propose_tuning_high_manual_approval(self, tuning_optimizer):
        """Test proposal when manual approval rate is high."""
        # Create metrics with high manual approval (should lower threshold)
        for i in range(10):
            tuning_optimizer.track_approval(
                skill_id="test.skill_1",
                approval_id=f"approval_{i}",
                decision="approved",
                confidence=0.5,
                operator_latency_ms=100.0,
                auto_approved=False,  # All manual
            )

        proposal = tuning_optimizer.propose_tuning("test.skill_1")

        if proposal:
            # High manual approval should suggest lower threshold
            assert proposal.proposed_threshold <= proposal.current_threshold

    def test_propose_tuning_high_revoke_rate(self, tuning_optimizer):
        """Test proposal when revoke rate is high."""
        # Create metrics with high revoke rate (should raise threshold)
        for i in range(10):
            tuning_optimizer.track_approval(
                skill_id="test.skill_1",
                approval_id=f"approval_{i}",
                decision="approved",
                confidence=0.9,
                operator_latency_ms=100.0,
                auto_approved=True,
            )

        # Add many revokes
        for i in range(5):
            tuning_optimizer.track_revoke("test.skill_1", f"approval_revoke_{i}")

        proposal = tuning_optimizer.propose_tuning("test.skill_1")

        if proposal:
            # High revoke rate should suggest higher threshold
            assert proposal.proposed_threshold >= proposal.current_threshold

    def test_propose_tuning_debounce(self, tuning_optimizer):
        """Test that proposals are debounced (not repeated within window)."""
        # Add metrics
        tuning_optimizer.track_approval(
            skill_id="test.skill_1",
            approval_id="approval_1",
            decision="approved",
            confidence=0.5,
            operator_latency_ms=100.0,
            auto_approved=False,
        )

        # First proposal should work
        proposal1 = tuning_optimizer.propose_tuning("test.skill_1")

        # Reset metrics for next cycle
        tuning_optimizer.tracked_approvals["test.skill_1"] = TuningMetrics()

        # Second proposal within window should be skipped
        proposal2 = tuning_optimizer.propose_tuning("test.skill_1")
        assert proposal2 is None  # Debounced


# ============================================================================
# Test: Tuning Application
# ============================================================================


class TestTuningApplication:
    """Test tuning application."""

    def test_apply_tuning(self, tuning_optimizer):
        """Test applying a tuning change."""
        # Add some metrics first
        tuning_optimizer.track_approval(
            skill_id="test.skill_1",
            approval_id="approval_1",
            decision="approved",
            confidence=0.5,
            operator_latency_ms=100.0,
            auto_approved=False,
        )

        success = tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        assert success is True

    def test_apply_tuning_creates_history(self, tuning_optimizer):
        """Test that applying tuning creates history record."""
        tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        assert len(tuning_optimizer.tuning_history) == 1
        record = tuning_optimizer.tuning_history[0]
        assert record.skill_id == "test.skill_1"
        assert record.new_threshold == 0.75
        assert record.applied is True

    def test_apply_tuning_audit(self, tuning_optimizer, approval_gate):
        """Test that applying tuning is audited."""
        tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        # Verify audit was called
        approval_gate.audit_backend.write_event.assert_called()


# ============================================================================
# Test: Tuning Revocation
# ============================================================================


class TestTuningRevocation:
    """Test tuning revocation."""

    def test_revoke_tuning(self, tuning_optimizer):
        """Test revoking a tuning change."""
        # Apply tuning first
        tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        tuning_id = tuning_optimizer.tuning_history[0].tuning_id

        # Now revoke it
        success = tuning_optimizer.revoke_tuning(
            tuning_id=tuning_id,
            operator_id="user:alice",
            reason="Caused regression",
        )

        assert success is True

    def test_revoke_nonexistent_tuning(self, tuning_optimizer):
        """Test revoking non-existent tuning."""
        success = tuning_optimizer.revoke_tuning(
            tuning_id="nonexistent",
            operator_id="user:alice",
        )

        assert success is False

    def test_revoke_tuning_audit(self, tuning_optimizer, approval_gate):
        """Test that revoking tuning is audited."""
        tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        tuning_id = tuning_optimizer.tuning_history[0].tuning_id

        # Reset mock
        approval_gate.audit_backend.write_event.reset_mock()

        tuning_optimizer.revoke_tuning(
            tuning_id=tuning_id,
            operator_id="user:bob",
        )

        # Verify audit was called
        approval_gate.audit_backend.write_event.assert_called()


# ============================================================================
# Test: Persistence
# ============================================================================


class TestTuningPersistence:
    """Test tuning persistence and recovery."""

    def test_persist_history(self, tuning_optimizer):
        """Test that tuning history is persisted to disk."""
        tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        # Verify file exists and contains data
        assert tuning_optimizer.tuning_file.exists()
        with open(tuning_optimizer.tuning_file, "r") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["skill_id"] == "test.skill_1"

    def test_load_persisted_history(self, approval_gate, tmp_path):
        """Test recovery of tuning history from disk."""
        # Create initial optimizer and apply tuning
        optimizer1 = TuningOptimizer(
            approval_gate=approval_gate,
            tenant_id="_default",
            corvin_home=str(tmp_path),
        )

        optimizer1.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.75,
            operator_id="user:alice",
        )

        # Create new optimizer (should load from disk)
        optimizer2 = TuningOptimizer(
            approval_gate=approval_gate,
            tenant_id="_default",
            corvin_home=str(tmp_path),
        )

        # Verify history was loaded
        assert len(optimizer2.tuning_history) == 1
        assert optimizer2.tuning_history[0].skill_id == "test.skill_1"


# ============================================================================
# Test: Tuning History
# ============================================================================


class TestTuningHistory:
    """Test tuning history tracking."""

    def test_get_tuning_history(self, tuning_optimizer):
        """Test retrieving tuning history for a skill."""
        # Apply multiple tunings
        for i in range(3):
            tuning_optimizer.apply_tuning(
                skill_id="test.skill_1",
                proposed_threshold=0.7 + (i * 0.05),
                operator_id="user:alice",
            )

        history = tuning_optimizer.get_tuning_history("test.skill_1")

        assert len(history) == 3
        # Most recent first
        assert history[0]["new_threshold"] == pytest.approx(0.8)

    def test_get_tuning_history_empty_skill(self, tuning_optimizer):
        """Test getting history for skill with no tunings."""
        history = tuning_optimizer.get_tuning_history("nonexistent.skill")
        assert history == []


# ============================================================================
# Test: Audit Integration
# ============================================================================


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_proposal_audit_fail_closed(self, tuning_optimizer, approval_gate):
        """Test fail-closed constraint: audit failure blocks proposal."""
        # Add metrics
        tuning_optimizer.track_approval(
            skill_id="test.skill_1",
            approval_id="approval_1",
            decision="approved",
            confidence=0.5,
            operator_latency_ms=100.0,
            auto_approved=False,
        )

        # Make audit fail
        approval_gate.audit_backend.write_event.side_effect = Exception("Audit failed")

        # Attempt to propose should raise
        with pytest.raises(RuntimeError, match="FATAL.*audit failed"):
            tuning_optimizer.propose_tuning("test.skill_1")

    def test_apply_tuning_audit_fail_closed(self, tuning_optimizer, approval_gate):
        """Test fail-closed constraint: audit failure blocks apply."""
        approval_gate.audit_backend.write_event.side_effect = Exception("Audit failed")

        with pytest.raises(RuntimeError, match="FATAL.*audit failed"):
            tuning_optimizer.apply_tuning(
                skill_id="test.skill_1",
                proposed_threshold=0.75,
                operator_id="user:alice",
            )


# ============================================================================
# Test: Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases."""

    def test_metrics_with_empty_latencies(self):
        """Test objective function with no latency data."""
        metrics = TuningMetrics(
            manual_approval_count=5,
            total_approval_count=10,
            revoke_count=1,
            total_approved_count=10,
            operator_latencies_ms=[],
        )
        score = metrics.objective_score()
        assert score > 0  # Should not crash

    def test_track_approval_different_skills(self, tuning_optimizer):
        """Test tracking approvals for different skills independently."""
        for skill_id in ["skill_1", "skill_2", "skill_3"]:
            tuning_optimizer.track_approval(
                skill_id=skill_id,
                approval_id=f"approval_{skill_id}",
                decision="approved",
                confidence=0.9,
                operator_latency_ms=100.0,
                auto_approved=True,
            )

        assert len(tuning_optimizer.tracked_approvals) == 3
        for skill_id in ["skill_1", "skill_2", "skill_3"]:
            assert tuning_optimizer.tracked_approvals[skill_id].total_approval_count == 1

    def test_boundary_threshold_values(self, tuning_optimizer):
        """Test boundary threshold values (0.0, 1.0)."""
        success = tuning_optimizer.apply_tuning(
            skill_id="test.skill_1",
            proposed_threshold=0.0,  # Minimum
            operator_id="user:alice",
        )
        assert success is True

        success = tuning_optimizer.apply_tuning(
            skill_id="test.skill_2",
            proposed_threshold=1.0,  # Maximum
            operator_id="user:alice",
        )
        assert success is True

    def test_zero_metrics(self, tuning_optimizer):
        """Test objective function with zero metrics."""
        metrics = TuningMetrics()
        score = metrics.objective_score()
        assert score == 0.0
