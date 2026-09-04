"""Tests for L5 k=5: Rollback Guard (ADR-0582)."""

import pytest
from datetime import datetime, timedelta
from core.learning.rollback_guard import (
    RollbackGuard,
    RollbackRequest,
    Criticality,
    DEFAULT_HOLD_HOURS,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)
        return len(self.events)


@pytest.fixture
def rollback_guard():
    """Create a RollbackGuard with mock audit backend."""
    audit = MockAuditBackend()
    return RollbackGuard(tenant_id="_default", audit_backend=audit)


class TestApprovalRegistration:
    """Test approval registration and hold period setup."""

    def test_register_approval_default_hold(self, rollback_guard):
        """Test registering an approval with default hold period."""
        rollback_guard.register_approval(
            approval_id="test_approval_1",
            skill_id="skill_a",
            criticality=Criticality.MEDIUM,
        )

        # Default for MEDIUM criticality is 12 hours
        assert rollback_guard.skill_hold_config["skill_a"] == 12

    def test_register_approval_critical(self, rollback_guard):
        """Test registering a critical Skill (1h hold)."""
        rollback_guard.register_approval(
            approval_id="test_approval_2",
            skill_id="skill_critical",
            criticality=Criticality.CRITICAL,
        )

        assert rollback_guard.skill_hold_config["skill_critical"] == 1

    def test_register_approval_custom_hold(self, rollback_guard):
        """Test registering an approval with custom hold period."""
        rollback_guard.register_approval(
            approval_id="test_approval_3",
            skill_id="skill_custom",
            criticality=Criticality.MEDIUM,
            custom_hold_hours=24,
        )

        assert rollback_guard.skill_hold_config["skill_custom"] == 24


class TestRevokePermissions:
    """Test revoke permission logic."""

    def test_can_revoke_after_hold_expires(self, rollback_guard):
        """Test that revoke is allowed after hold period expires."""
        # Register approval with 1-second hold (for testing)
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.CRITICAL,
        )

        # Override the hold period to 0 hours (immediate)
        rollback_guard.skill_hold_config["skill_test"] = 0

        # Should be allowed immediately
        allowed, reason = rollback_guard.can_revoke("test_approval", "skill_test")

        assert allowed is True
        assert reason is None

    def test_cannot_revoke_during_hold_period(self, rollback_guard):
        """Test that revoke is blocked during hold period."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,  # 12h hold
        )

        allowed, reason = rollback_guard.can_revoke("test_approval", "skill_test")

        assert allowed is False
        assert "remaining" in reason.lower()

    def test_time_remaining_format(self, rollback_guard):
        """Test that time remaining is properly formatted."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,  # 12h
        )

        allowed, reason = rollback_guard.can_revoke("test_approval", "skill_test")

        # Should be HH:MM:SS remaining
        assert ":" in reason
        assert "remaining" in reason.lower()


class TestForceRevoke:
    """Test force-revoke mechanism."""

    def test_force_revoke_allowed(self, rollback_guard):
        """Test that operator can force-revoke during hold period."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )

        decision = rollback_guard.request_revoke(
            approval_id="test_approval",
            skill_id="skill_test",
            operator_id="operator:alice",
            force=True,
            reason="Prod outage; config causing high latency",
        )

        assert decision.allowed is True

    def test_force_revoke_requires_reason(self, rollback_guard):
        """Test that force-revoke requires a reason."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )

        with pytest.raises(ValueError, match="requires a reason"):
            rollback_guard.request_revoke(
                approval_id="test_approval",
                skill_id="skill_test",
                operator_id="operator:alice",
                force=True,
                reason="",
            )

    def test_force_revoke_reason_length_limit(self, rollback_guard):
        """Test that reason has a max length."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )

        long_reason = "x" * 600  # 600 chars, over limit of 500

        with pytest.raises(ValueError, match="Reason too long"):
            rollback_guard.request_revoke(
                approval_id="test_approval",
                skill_id="skill_test",
                operator_id="operator:alice",
                force=True,
                reason=long_reason,
            )

    def test_force_revoke_audited(self, rollback_guard):
        """Test that force-revoke is properly audited."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.CRITICAL,
        )

        rollback_guard.request_revoke(
            approval_id="test_approval",
            skill_id="skill_test",
            operator_id="operator:alice",
            force=True,
            reason="Test override",
        )

        # Check audit events
        events = rollback_guard.audit_backend.events
        assert len(events) > 0
        # Should have at least revoke_requested and force_revoked events
        event_types = [e.get("event_type") for e in events]
        assert "skill_approval_revoke_requested" in event_types
        assert "skill_approval_force_revoked" in event_types

    def test_force_revoke_records_metrics(self, rollback_guard):
        """Test that force-revoke records override metrics."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )

        rollback_guard.request_revoke(
            approval_id="test_approval",
            skill_id="skill_test",
            operator_id="operator:alice",
            force=True,
            reason="Test override",
        )

        metrics = rollback_guard.get_override_metrics("skill_test")

        assert "test_approval" in metrics
        assert metrics["test_approval"].skill_id == "skill_test"


class TestOperatorValidation:
    """Test operator_id validation."""

    def test_invalid_operator_id_revoke(self, rollback_guard):
        """Test that invalid operator_id is rejected."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )

        # Invalid operator_id (too short, contains uppercase)
        with pytest.raises(ValueError, match="operator_id"):
            rollback_guard.request_revoke(
                approval_id="test_approval",
                skill_id="skill_test",
                operator_id="Invalid",  # Uppercase
                force=True,
                reason="Test",
            )

    def test_valid_operator_id_formats(self, rollback_guard):
        """Test various valid operator_id formats."""
        valid_ids = [
            "operator:alice",
            "user.bob",
            "admin-charlie",
            "system_daemon",
            "srv-api-123",
        ]

        for op_id in valid_ids:
            rollback_guard.register_approval(
                approval_id=f"approval_{op_id}",
                skill_id="skill_test",
                criticality=Criticality.MEDIUM,
            )

            # Should not raise
            try:
                rollback_guard.request_revoke(
                    approval_id=f"approval_{op_id}",
                    skill_id="skill_test",
                    operator_id=op_id,
                    force=True,
                    reason="Test",
                )
            except ValueError as e:
                pytest.fail(f"Valid operator_id rejected: {op_id}: {e}")


class TestMetricsComputation:
    """Test metrics computation and learning."""

    def test_override_metrics_recorded(self, rollback_guard):
        """Test that override metrics are properly recorded."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.MEDIUM,
        )

        rollback_guard.request_revoke(
            approval_id="test_approval",
            skill_id="skill_test",
            operator_id="operator:alice",
            force=True,
            reason="Override test",
        )

        metrics = rollback_guard.get_override_metrics("skill_test")

        assert len(metrics) > 0
        assert "test_approval" in metrics

    def test_override_rate_computation(self, rollback_guard):
        """Test override rate computation."""
        # Register multiple approvals
        for i in range(5):
            rollback_guard.register_approval(
                approval_id=f"approval_{i}",
                skill_id="skill_test",
                criticality=Criticality.MEDIUM,
            )

        # Force-revoke one
        rollback_guard.request_revoke(
            approval_id="approval_0",
            skill_id="skill_test",
            operator_id="operator:alice",
            force=True,
            reason="Test",
        )

        rate, count = rollback_guard.compute_override_rate("skill_test")

        # At least 1 override recorded
        assert count > 0

    def test_suggest_hold_adjustment(self, rollback_guard):
        """Test hold period adjustment suggestion."""
        # Need ≥5 samples to suggest adjustment
        for i in range(6):
            rollback_guard.register_approval(
                approval_id=f"approval_{i}",
                skill_id="skill_test",
                criticality=Criticality.MEDIUM,
            )

        # Force-revoke a couple
        for i in range(2):
            rollback_guard.request_revoke(
                approval_id=f"approval_{i}",
                skill_id="skill_test",
                operator_id="operator:alice",
                force=True,
                reason="Test",
            )

        # Adjustment suggestion (may be None if not enough data)
        suggestion = rollback_guard.suggest_hold_adjustment("skill_test")

        # Should be reasonable if returned
        if suggestion is not None:
            assert suggestion >= 1


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_normal_revoke_audited(self, rollback_guard):
        """Test that normal revoke is audited."""
        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.CRITICAL,  # 1h, immediately allowed after
        )

        # Override to allow immediate revoke
        rollback_guard.skill_hold_config["skill_test"] = 0

        rollback_guard.request_revoke(
            approval_id="test_approval",
            skill_id="skill_test",
            operator_id="operator:alice",
        )

        events = rollback_guard.audit_backend.events
        assert len(events) > 0
        event_types = [e.get("event_type") for e in events]
        assert "skill_approval_revoke_requested" in event_types

    def test_audit_failure_blocks_revoke(self, rollback_guard):
        """Test that audit failure blocks revoke (fail-closed)."""

        class FailingAudit:
            def write_event(self, event):
                raise RuntimeError("Audit failed")

        rollback_guard.register_approval(
            approval_id="test_approval",
            skill_id="skill_test",
            criticality=Criticality.CRITICAL,
        )

        rollback_guard.audit_backend = FailingAudit()

        with pytest.raises(RuntimeError, match="audit failed"):
            rollback_guard.request_revoke(
                approval_id="test_approval",
                skill_id="skill_test",
                operator_id="operator:alice",
                force=True,
                reason="Test",
            )


class TestEdgeCases:
    """Test edge cases."""

    def test_revoke_nonexistent_approval(self, rollback_guard):
        """Test revoking an approval that doesn't exist."""
        allowed, reason = rollback_guard.can_revoke(
            "nonexistent_approval", "skill_test"
        )

        assert allowed is False
        assert "not found" in reason.lower()

    def test_criticality_levels(self, rollback_guard):
        """Test all criticality levels."""
        for criticality in [Criticality.CRITICAL, Criticality.MEDIUM, Criticality.LOW]:
            rollback_guard.register_approval(
                approval_id=f"approval_{criticality.value}",
                skill_id=f"skill_{criticality.value}",
                criticality=criticality,
            )

            expected_hold = DEFAULT_HOLD_HOURS[criticality]
            actual_hold = rollback_guard.skill_hold_config[f"skill_{criticality.value}"]

            assert actual_hold == expected_hold
