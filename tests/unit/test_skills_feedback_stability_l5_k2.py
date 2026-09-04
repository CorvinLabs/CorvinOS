"""Unit Tests: L5 k=2 — Operator Approval Gate (Fail-Closed Learning Control)."""

import pytest
from datetime import datetime, timedelta
from core.skills.feedback_stability import (
    OperatorApprovalGate,
    DriftAlert,
    ScrubbedDriftAlert,
    OperatorApprovalRecord,
    ApprovalReasonCode,
    ApprovalDecision,
)


class MockAuditBackend:
    """Mock audit backend for testing audit trail integration."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict) -> None:
        """Record an audit event."""
        self.events.append(event)

    def get_events_by_type(self, event_type: str) -> list:
        """Retrieve events by type."""
        return [e for e in self.events if e.get("event_type") == event_type]


# ============================================================================
# Constraint #1: Linearizable Audit Trail
# ============================================================================

class TestAuditLinearity:
    """Test audit trail is linearizable (CAS + chain-verified)."""

    def test_approval_requires_audit_backend(self):
        """OperatorApprovalGate should require audit_backend (fail-closed)."""
        with pytest.raises(RuntimeError, match="audit_backend is required"):
            OperatorApprovalGate(tenant_id="test_tenant", audit_backend=None)

    def test_approval_request_emits_audit_event(self):
        """Requesting approval should emit audit event."""
        audit = MockAuditBackend()
        gate = OperatorApprovalGate(tenant_id="test_tenant", audit_backend=audit)

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="confidence_threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2, 0.25, 0.18],
            consecutive_high_deltas=3,
            requires_operator_approval=True,
        )

        record, auto = gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="abc123",
            next_config_hash="def456",
        )

        # Verify audit event was logged
        events = audit.get_events_by_type("skill_approval_requested")
        assert len(events) == 1
        assert events[0]["approval_id"] == record.approval_id
        assert events[0]["tenant_id"] == "test_tenant"
        assert events[0]["skill_id"] == "skill.router"

    def test_approval_decision_emits_audit_event(self):
        """Operator approval should emit audit event."""
        audit = MockAuditBackend()
        gate = OperatorApprovalGate(tenant_id="test_tenant", audit_backend=audit)

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.05,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.5,  # Below auto-threshold
            prev_config_hash="abc123",
            next_config_hash="def456",
        )

        # Operator approves
        approval_id = record.approval_id
        gate.operator_approve(approval_id, "operator:alice")

        events = audit.get_events_by_type("skill_approval_granted")
        assert len(events) == 1
        assert events[0]["operator_id"] == "operator:alice"
        assert events[0]["approval_id"] == approval_id

    def test_approval_rejection_emits_audit_event(self):
        """Operator rejection should emit audit event."""
        gate = OperatorApprovalGate()
        audit = MockAuditBackend()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.05,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="abc",
            next_config_hash="def",
            audit_backend=audit,
        )

        # Operator rejects
        gate.operator_reject(
            record.approval_id,
            "operator:bob",
            reason="Threshold change too risky",
            audit_backend=audit,
        )

        events = audit.get_events_by_type("skill_approval_denied")
        assert len(events) == 1
        assert events[0]["operator_id"] == "operator:bob"
        assert "risky" in events[0]["reason"]


# ============================================================================
# Constraint #2: Auto-Approval for Low-Risk
# ============================================================================

class TestAutoApproval:
    """Test auto-approval for high-confidence deltas (reduce operator overload)."""

    def test_high_confidence_auto_approves(self):
        """Delta with confidence > 0.8 should auto-approve."""
        gate = OperatorApprovalGate(auto_approval_confidence_threshold=0.8)

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=3,
        )

        record, auto = gate.request_approval(
            drift,
            confidence=0.85,  # > 0.8
            prev_config_hash="abc",
            next_config_hash="def",
        )

        assert auto is True
        assert record.decision == ApprovalDecision.APPROVED
        assert record.operator_id == "system:auto"

    def test_low_confidence_requires_operator(self):
        """Delta with confidence < 0.8 should queue for operator."""
        gate = OperatorApprovalGate(auto_approval_confidence_threshold=0.8)

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, auto = gate.request_approval(
            drift,
            confidence=0.6,  # < 0.8
            prev_config_hash="abc",
            next_config_hash="def",
        )

        assert auto is False
        assert record.decision == ApprovalDecision.PENDING
        assert "skill.router" in gate.pending_approvals

    def test_auto_approval_reduces_queue(self):
        """Multiple high-confidence deltas should not queue."""
        gate = OperatorApprovalGate(auto_approval_confidence_threshold=0.8)

        for i in range(5):
            drift = DriftAlert(
                skill_id=f"skill.{i}",
                metric_name="threshold",
                smoothed_delta=0.1,
                drift_threshold=0.15,
                consecutive_high_deltas=3,
            )

            _, auto = gate.request_approval(
                drift,
                confidence=0.9,  # All high-confidence
                prev_config_hash=f"abc{i}",
                next_config_hash=f"def{i}",
            )

            assert auto is True

        # Pending queue should be empty
        assert len(gate.pending_approvals) == 0


# ============================================================================
# Constraint #3: Scrubbed Alert Payload (No PII/Training Data)
# ============================================================================

class TestScrubbedAlertPayload:
    """Test alerts are scrubbed to prevent PII/training data leakage."""

    def test_scrub_alert_removes_raw_deltas(self):
        """Scrubbed alert should not contain recent_deltas."""
        gate = OperatorApprovalGate()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2, 0.25, 0.18],  # Could reveal training data
            consecutive_high_deltas=3,
        )

        scrubbed = gate.scrub_alert(drift, confidence=0.9)

        # Scrubbed version should not have recent_deltas
        assert isinstance(scrubbed, ScrubbedDriftAlert)
        assert scrubbed.magnitude == 0.2
        assert scrubbed.confidence == 0.9
        assert not hasattr(scrubbed, "recent_deltas")

    def test_scrub_alert_uses_reason_codes(self):
        """Scrubbed alert should use enum reason codes, not raw data."""
        gate = OperatorApprovalGate()

        drift_consistent = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            consecutive_high_deltas=3,  # Multiple high deltas
        )

        scrubbed = gate.scrub_alert(drift_consistent, confidence=0.9)
        assert scrubbed.reason_code == ApprovalReasonCode.CONSISTENT_PATTERN

        # Single high delta = noise
        drift_noise = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        scrubbed_noise = gate.scrub_alert(drift_noise, confidence=0.5)
        assert scrubbed_noise.reason_code == ApprovalReasonCode.RANDOM_NOISE

    def test_scrubbed_alert_in_approval_record(self):
        """Approval record should contain scrubbed alert, not original."""
        gate = OperatorApprovalGate()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.2,
            drift_threshold=0.15,
            recent_deltas=[0.2, 0.25, 0.18],
            consecutive_high_deltas=3,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.9,
            prev_config_hash="abc",
            next_config_hash="def",
        )

        # Record should have scrubbed alert
        assert isinstance(record.scrubbed_alert, ScrubbedDriftAlert)
        assert record.scrubbed_alert.magnitude == 0.2
        assert not hasattr(record.scrubbed_alert, "recent_deltas")


# ============================================================================
# Constraint #4: Approval TTL (Expires after 12h)
# ============================================================================

class TestApprovalTTL:
    """Test approvals expire after 12 hours."""

    def test_approval_has_expiry(self):
        """Approval should have TTL expiry timestamp."""
        gate = OperatorApprovalGate(approval_ttl_hours=12)

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="abc",
            next_config_hash="def",
        )

        # TTL should be ~12h from now
        now = datetime.utcnow()
        expiry = datetime.fromisoformat(record.ttl_expires.replace("Z", "+00:00"))
        delta = expiry - now

        # Should be approximately 12 hours
        assert 11 * 3600 < delta.total_seconds() < 13 * 3600

    def test_expired_approval_rejected(self):
        """Operator cannot approve an expired request."""
        gate = OperatorApprovalGate(approval_ttl_hours=0)  # Instant expiry

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="abc",
            next_config_hash="def",
        )

        # Wait a bit to ensure expiry
        import time
        time.sleep(0.1)

        # Try to approve
        success = gate.operator_approve(record.approval_id, "operator:alice")

        # Should fail (expired)
        assert success is False


# ============================================================================
# Constraint #5: Operator Can Revert (Revoke with Audit Trail)
# ============================================================================

class TestOperatorRevoke:
    """Test operator can revoke previously-approved changes."""

    def test_operator_can_revoke_approved(self):
        """Operator can revoke an approved config change."""
        gate = OperatorApprovalGate()
        audit = MockAuditBackend()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=3,
        )

        record, auto = gate.request_approval(
            drift,
            confidence=0.9,  # Auto-approved
            prev_config_hash="abc",
            next_config_hash="def",
            audit_backend=audit,
        )

        # Verify it's approved
        assert record.decision == ApprovalDecision.APPROVED

        # Revoke it
        success = gate.operator_revoke(
            record.approval_id,
            "operator:alice",
            reason="Caused performance regression",
            audit_backend=audit,
        )

        assert success is True

        # Status should show revoked
        status = gate.get_approval_status(record.approval_id)
        assert status.decision == ApprovalDecision.REVOKED
        assert status.revoke_reason == "Caused performance regression"

    def test_revoke_emits_audit_event(self):
        """Revoke should emit audit event."""
        gate = OperatorApprovalGate()
        audit = MockAuditBackend()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=3,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.9,
            prev_config_hash="abc",
            next_config_hash="def",
            audit_backend=audit,
        )

        gate.operator_revoke(
            record.approval_id,
            "operator:alice",
            reason="Regression detected",
            audit_backend=audit,
        )

        events = audit.get_events_by_type("skill_approval_revoked")
        assert len(events) == 1
        assert events[0]["approval_id"] == record.approval_id
        assert "Regression" in events[0]["reason"]

    def test_cannot_revoke_non_approved(self):
        """Cannot revoke a rejected or pending approval."""
        gate = OperatorApprovalGate()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.5,  # Pending
            prev_config_hash="abc",
            next_config_hash="def",
        )

        # Try to revoke pending
        success = gate.operator_revoke(
            record.approval_id,
            "operator:alice",
            reason="Never mind",
        )

        assert success is False


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for full approval workflow."""

    def test_end_to_end_approval_workflow(self):
        """Complete workflow: request → operator approves → audit trail."""
        gate = OperatorApprovalGate()
        audit = MockAuditBackend()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.05,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        # 1. Request approval (low confidence, queued)
        record, auto = gate.request_approval(
            drift,
            confidence=0.6,
            prev_config_hash="old_config_hash",
            next_config_hash="new_config_hash",
            audit_backend=audit,
        )

        assert auto is False
        assert len(gate.get_pending_approvals()) == 1

        # 2. Operator approves
        success = gate.operator_approve(
            record.approval_id,
            "operator:alice",
            audit_backend=audit,
        )

        assert success is True
        assert len(gate.get_pending_approvals()) == 0

        # 3. Verify audit trail
        approval_events = audit.get_events_by_type("skill_approval_requested")
        assert len(approval_events) == 1

        granted_events = audit.get_events_by_type("skill_approval_granted")
        assert len(granted_events) == 1

    def test_multiple_skills_independent_queues(self):
        """Different skills should have independent approval queues."""
        gate = OperatorApprovalGate()

        drift1 = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.05,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        drift2 = DriftAlert(
            skill_id="skill.formatter",
            metric_name="style",
            smoothed_delta=0.03,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        # Request for both
        r1, _ = gate.request_approval(drift1, 0.6, "a1", "b1")
        r2, _ = gate.request_approval(drift2, 0.6, "a2", "b2")

        # Both should be pending
        pending = gate.get_pending_approvals()
        assert len(pending) == 2

        # Approve one
        gate.operator_approve(r1.approval_id, "operator:alice")

        # Other should still be pending
        pending = gate.get_pending_approvals()
        assert len(pending) == 1
        assert pending[0].scrubbed_alert.skill_id == "skill.formatter"

    def test_approval_record_has_config_hashes(self):
        """Approval record should track config hashes for reversibility."""
        gate = OperatorApprovalGate()

        drift = DriftAlert(
            skill_id="skill.router",
            metric_name="threshold",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        record, _ = gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="abc123",
            next_config_hash="def456",
        )

        assert record.prev_config_hash == "abc123"
        assert record.next_config_hash == "def456"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
