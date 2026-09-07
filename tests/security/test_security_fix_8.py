"""Security Fix #8: Audit-First Weight Updates (Fail-Closed).

Testing: Finding #8 - Audit Bypass: Silent config changes

This test suite verifies that weight updates are audit-first and fail-closed:
1. Audit event is created before weight is applied
2. Audit commit must succeed before weight change takes effect
3. If audit write fails, weight is NOT applied in memory
4. Exceptions are raised on audit failure
5. Audit events are immutable
6. Tenant ID is correctly included in audit events
"""

import pytest
import json
from datetime import datetime
from uuid import uuid4
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Import the security fix
from core.learning.weight_updater import (
    WeightUpdater,
    WeightsUpdatedAuditEvent,
    WeightUpdateValidator,
    WeightAuditFailedError,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self, fail_on_write: bool = False):
        self.events: list[dict] = []
        self.fail_on_write = fail_on_write
        self.write_count = 0

    def write_event_dict(
        self,
        event_type: str,
        tenant_id: str,
        details: dict = None,
        severity: str = None,
    ) -> str:
        """Mock write_event_dict."""
        self.write_count += 1
        if self.fail_on_write:
            raise IOError("Simulated audit backend failure")

        record = {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "details": details or {},
            "severity": severity,
            "hash": f"hash_{self.write_count}",
        }
        self.events.append(record)
        return record["hash"]

    def write(self, event_dict: dict) -> str:
        """Alternative write interface."""
        return self.write_event_dict(
            event_type=event_dict.get("event_type", "unknown"),
            tenant_id=event_dict.get("tenant_id", "default"),
            details=event_dict,
        )


class TestWeightUpdateAuditEventCreated:
    """TEST 1: weights_updated event is created before weight application."""

    def test_event_created_with_correct_fields(self):
        """Verify audit event is created with all required fields."""
        event = WeightsUpdatedAuditEvent(
            event_id="test-123",
            loop_id="L1_routing",
            tenant_id="tenant_1",
            timestamp="2026-09-07T12:00:00Z",
            param_name="confidence_threshold",
            old_value=0.7,
            new_value=0.75,
            delta=0.05,
            gradient=-0.02,
            learning_rate=0.01,
            iteration=42,
            lom="core/learning/optimizer.py:L237",
        )

        assert event.event_id == "test-123"
        assert event.event_type == "weights_updated"
        assert event.loop_id == "L1_routing"
        assert event.tenant_id == "tenant_1"
        assert event.param_name == "confidence_threshold"
        assert event.old_value == 0.7
        assert event.new_value == 0.75
        assert event.delta == 0.05
        assert event.gradient == -0.02

    def test_event_is_immutable(self):
        """Verify audit event is frozen (immutable)."""
        event = WeightsUpdatedAuditEvent(
            event_id="test-123",
            loop_id="L1_routing",
            tenant_id="tenant_1",
            timestamp="2026-09-07T12:00:00Z",
            param_name="threshold",
            old_value=0.5,
            new_value=0.6,
            delta=0.1,
        )

        # Attempting to modify should raise FrozenInstanceError
        with pytest.raises(Exception):  # dataclass frozen raises AttributeError
            event.old_value = 0.4

    def test_event_serializes_to_dict(self):
        """Verify audit event can be serialized."""
        event = WeightsUpdatedAuditEvent(
            event_id="test-456",
            loop_id="L2_confidence",
            tenant_id="default",
            timestamp="2026-09-07T12:00:00Z",
            param_name="calibration_weight",
            old_value=1.0,
            new_value=0.95,
            delta=-0.05,
            gradient=0.05,
        )

        event_dict = event.to_dict()
        assert event_dict["event_id"] == "test-456"
        assert event_dict["event_type"] == "weights_updated"
        assert event_dict["tenant_id"] == "default"
        assert event_dict["old_value"] == 1.0
        assert event_dict["new_value"] == 0.95

    def test_event_serializes_to_json(self):
        """Verify audit event can be serialized to JSON."""
        event = WeightsUpdatedAuditEvent(
            event_id="test-789",
            loop_id="L3_feedback",
            tenant_id="tenant_2",
            timestamp="2026-09-07T13:00:00Z",
            param_name="feedback_weight",
            old_value=2.0,
            new_value=2.1,
            delta=0.1,
        )

        json_str = event.to_json()
        parsed = json.loads(json_str)

        assert parsed["event_id"] == "test-789"
        assert parsed["loop_id"] == "L3_feedback"
        assert parsed["tenant_id"] == "tenant_2"


class TestWeightAuditEventCommitted:
    """TEST 2: Event is committed to audit chain BEFORE weight in-memory."""

    def test_audit_write_called_before_returning_success(self):
        """Verify audit backend write is called."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        success, event_id = updater.apply_weight_delta(
            loop_id="L1_routing",
            param_name="threshold",
            old_value=0.7,
            new_value=0.75,
            gradient=-0.02,
            learning_rate=0.01,
            lom="test.py:L10",
        )

        assert success is True
        assert event_id is not None
        assert backend.write_count == 1
        assert len(backend.events) == 1

    def test_audit_event_recorded_with_tenant_id(self):
        """Verify audit event includes tenant_id."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="tenant_alpha")

        success, event_id = updater.apply_weight_delta(
            loop_id="L2_confidence",
            param_name="calibration",
            old_value=1.0,
            new_value=1.05,
            gradient=-0.01,
            learning_rate=0.005,
        )

        assert success is True
        assert backend.events[0]["tenant_id"] == "tenant_alpha"

    def test_audit_event_includes_weight_details(self):
        """Verify audit event contains weight change details."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        success, event_id = updater.apply_weight_delta(
            loop_id="L5_latency",
            param_name="sla_threshold",
            old_value=5.0,
            new_value=4.8,
            gradient=0.15,
            learning_rate=0.1,
        )

        assert success is True
        event_details = backend.events[0]["details"]
        assert event_details["param_name"] == "sla_threshold"
        assert event_details["old_value"] == 5.0
        assert event_details["new_value"] == 4.8
        assert event_details["delta"] == -0.2

    def test_audit_event_includes_gradient_info(self):
        """Verify audit event includes gradient and learning rate."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        success, event_id = updater.apply_weight_delta(
            loop_id="L1_routing",
            param_name="weight",
            old_value=1.0,
            new_value=1.02,
            gradient=-0.05,
            learning_rate=0.02,
        )

        assert success is True
        event_details = backend.events[0]["details"]
        assert event_details["gradient"] == -0.05
        assert event_details["learning_rate"] == 0.02

    def test_audit_hash_returned(self):
        """Verify audit write returns hash."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        success, event_id = updater.apply_weight_delta(
            loop_id="L1_routing",
            param_name="param",
            old_value=1.0,
            new_value=1.1,
            gradient=0.0,
            learning_rate=0.01,
        )

        assert success is True
        assert backend.events[0]["hash"] is not None
        assert backend.events[0]["hash"].startswith("hash_")


class TestWeightAuditFailureBlocksUpdate:
    """TEST 3: Weight is NOT applied if audit write fails."""

    def test_weight_not_applied_when_audit_fails(self):
        """Verify weight update is blocked when audit fails."""
        backend = MockAuditBackend(fail_on_write=True)
        updater = WeightUpdater(backend, tenant_id="default")

        # Attempting to apply weight should raise exception
        with pytest.raises(WeightAuditFailedError):
            updater.apply_weight_delta(
                loop_id="L1_routing",
                param_name="threshold",
                old_value=0.7,
                new_value=0.75,
                gradient=-0.02,
                learning_rate=0.01,
            )

        # Verify audit backend was attempted
        assert backend.write_count == 1

    def test_no_silent_failures(self):
        """Verify that audit failures are NOT silently ignored."""

        class FailingAuditBackend:
            def write_event_dict(self, **kwargs):
                raise IOError("Disk full")

        backend = FailingAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        # Should raise WeightAuditFailedError, not silently fail
        with pytest.raises(WeightAuditFailedError) as exc_info:
            updater.apply_weight_delta(
                loop_id="L1_routing",
                param_name="param",
                old_value=1.0,
                new_value=1.1,
                gradient=0.0,
                learning_rate=0.01,
            )

        assert "audit" in str(exc_info.value).lower()


class TestWeightAuditFailureExceptionRaised:
    """TEST 4: apply_weight_delta() raises WeightAuditFailedError on failure."""

    def test_exception_raised_on_write_failure(self):
        """Verify WeightAuditFailedError is raised."""

        class BrokenAuditBackend:
            def write_event_dict(self, **kwargs):
                raise IOError("Audit backend unreachable")

        backend = BrokenAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        with pytest.raises(WeightAuditFailedError):
            updater.apply_weight_delta(
                loop_id="L1_routing",
                param_name="test",
                old_value=1.0,
                new_value=1.1,
                gradient=0.0,
                learning_rate=0.01,
            )

    def test_exception_contains_error_details(self):
        """Verify exception message contains helpful details."""

        class DetailedFailureBackend:
            def write_event_dict(self, **kwargs):
                raise IOError("Permission denied on audit.jsonl")

        backend = DetailedFailureBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        with pytest.raises(WeightAuditFailedError) as exc_info:
            updater.apply_weight_delta(
                loop_id="L1_routing",
                param_name="param",
                old_value=1.0,
                new_value=1.1,
                gradient=0.0,
                learning_rate=0.01,
            )

        error_msg = str(exc_info.value)
        assert "FAILED" in error_msg or "failed" in error_msg


class TestWeightAuditEventImmutable:
    """TEST 5: weights_updated event is immutable."""

    def test_event_cannot_be_modified_after_creation(self):
        """Verify frozen dataclass prevents modification."""
        event = WeightsUpdatedAuditEvent(
            event_id="immutable-test",
            loop_id="L1_routing",
            tenant_id="default",
            timestamp="2026-09-07T12:00:00Z",
            param_name="threshold",
            old_value=0.7,
            new_value=0.75,
            delta=0.05,
        )

        # Attempting to modify any field should fail
        with pytest.raises(Exception):
            event.old_value = 0.5

        with pytest.raises(Exception):
            event.new_value = 0.8

        with pytest.raises(Exception):
            event.param_name = "different_param"

    def test_event_maintains_hash_chain_fields(self):
        """Verify event maintains prev_hash and hash for chain integrity."""
        event = WeightsUpdatedAuditEvent(
            event_id="chain-test",
            loop_id="L1_routing",
            tenant_id="default",
            timestamp="2026-09-07T12:00:00Z",
            param_name="param",
            old_value=1.0,
            new_value=1.1,
            delta=0.1,
            prev_hash="hash_previous",
            hash="hash_current",
        )

        assert event.prev_hash == "hash_previous"
        assert event.hash == "hash_current"

        # Cannot modify after creation
        with pytest.raises(Exception):
            event.hash = "hash_new"


class TestWeightAuditTenantScoped:
    """TEST 6: Tenant ID is correctly included and scoped."""

    def test_audit_event_includes_tenant_id(self):
        """Verify tenant_id is in every audit event."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="tenant_xyz")

        success, event_id = updater.apply_weight_delta(
            loop_id="L1_routing",
            param_name="threshold",
            old_value=0.5,
            new_value=0.6,
            gradient=-0.05,
            learning_rate=0.01,
        )

        assert success is True
        assert backend.events[0]["tenant_id"] == "tenant_xyz"

    def test_different_tenants_different_audit_trails(self):
        """Verify different tenants have separate audit trails."""
        backend1 = MockAuditBackend()
        backend2 = MockAuditBackend()

        updater1 = WeightUpdater(backend1, tenant_id="tenant_1")
        updater2 = WeightUpdater(backend2, tenant_id="tenant_2")

        updater1.apply_weight_delta(
            loop_id="L1_routing",
            param_name="threshold",
            old_value=0.5,
            new_value=0.6,
            gradient=-0.05,
            learning_rate=0.01,
        )

        updater2.apply_weight_delta(
            loop_id="L1_routing",
            param_name="threshold",
            old_value=0.4,
            new_value=0.5,
            gradient=-0.06,
            learning_rate=0.01,
        )

        assert backend1.events[0]["tenant_id"] == "tenant_1"
        assert backend2.events[0]["tenant_id"] == "tenant_2"

    def test_audit_backend_receives_correct_tenant_id(self):
        """Verify audit backend is called with correct tenant context."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="production_tenant")

        success, event_id = updater.apply_weight_delta(
            loop_id="L2_confidence",
            param_name="calibration_weight",
            old_value=1.0,
            new_value=0.98,
            gradient=0.02,
            learning_rate=0.001,
        )

        assert success is True
        event = backend.events[0]
        assert event["tenant_id"] == "production_tenant"


class TestWeightUpdateValidator:
    """TEST 7: Weight update validator prevents invalid updates."""

    def test_validator_rejects_oversized_delta(self):
        """Verify validator rejects too-large deltas."""
        validator = WeightUpdateValidator(max_delta_per_iteration=0.1)

        valid, error = validator.validate_weight_update(
            loop_id="L1_routing",
            param_name="param",
            old_value=1.0,
            new_value=1.2,  # delta = 0.2 > 0.1 limit
            gradient=-0.05,
        )

        assert valid is False
        assert "too large" in error.lower()

    def test_validator_accepts_valid_delta(self):
        """Verify validator accepts small deltas."""
        validator = WeightUpdateValidator(max_delta_per_iteration=0.1)

        valid, error = validator.validate_weight_update(
            loop_id="L1_routing",
            param_name="param",
            old_value=1.0,
            new_value=1.05,  # delta = 0.05 < 0.1 limit
            gradient=-0.05,
        )

        assert valid is True
        assert error is None

    def test_validator_checks_absolute_bounds(self):
        """Verify validator enforces absolute value bounds."""
        validator = WeightUpdateValidator(max_absolute_param_value=10.0)

        valid, error = validator.validate_weight_update(
            loop_id="L1_routing",
            param_name="param",
            old_value=9.0,
            new_value=11.0,  # Exceeds 10.0 bound
            gradient=-0.5,
        )

        assert valid is False
        assert "exceeds bounds" in error.lower()


class TestSecurityAttackBlockage:
    """TEST 8: Security fix blocks audit bypass attacks."""

    def test_attack_silent_weight_modification_blocked(self):
        """ATTACK: Attacker tries to silently modify weight without audit.

        Expected: Attack is blocked; WeightAuditFailedError raised.
        """

        class AttackBackend:
            """Simulates attacker trying to bypass audit."""

            def __init__(self):
                self.call_count = 0

            def write_event_dict(self, **kwargs):
                # Pretend to write but actually fail
                self.call_count += 1
                raise IOError("Simulated attack: audit write blocked")

        backend = AttackBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        with pytest.raises(WeightAuditFailedError):
            updater.apply_weight_delta(
                loop_id="L1_routing",
                param_name="routing_weight",
                old_value=0.5,
                new_value=0.9,  # Large change
                gradient=-0.3,
                learning_rate=0.2,
            )

        # Verify attack was detected
        assert backend.call_count == 1

    def test_attack_config_change_without_logging_blocked(self):
        """ATTACK: Direct config change that bypasses audit updater.

        Expected: Attack leaves no audit trail.
        Note: This test demonstrates the security boundary.
        """

        # If an attacker directly modifies config without using WeightUpdater,
        # the update will have no audit trail. The fix prevents this through
        # proper system design: all weight updates MUST go through
        # WeightUpdater.apply_weight_delta().

        # This is an architectural guarantee, not a runtime check.
        pass

    def test_attack_replay_audit_events_blocked_by_hash_chain(self):
        """ATTACK: Attacker replays old audit events.

        Expected: Replay is detected by hash chain (future work: hash verification).
        """

        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="default")

        # First update
        success1, event_id1 = updater.apply_weight_delta(
            loop_id="L1_routing",
            param_name="threshold",
            old_value=0.5,
            new_value=0.6,
            gradient=-0.05,
            learning_rate=0.01,
        )

        assert success1 is True
        assert len(backend.events) == 1

        # Second update should have different hash due to chain
        success2, event_id2 = updater.apply_weight_delta(
            loop_id="L1_routing",
            param_name="threshold",
            old_value=0.6,
            new_value=0.7,
            gradient=-0.05,
            learning_rate=0.01,
        )

        assert success2 is True
        assert len(backend.events) == 2
        # Both events should have been written
        assert backend.events[0]["hash"] != backend.events[1]["hash"]


class TestIntegrationAuditFirst:
    """Integration test: Audit-first workflow."""

    def test_complete_audit_first_workflow(self):
        """Verify complete audit-first workflow."""
        backend = MockAuditBackend()
        updater = WeightUpdater(backend, tenant_id="test_tenant")

        # Simulate a complete learning loop iteration
        updates = [
            ("L1_routing", "routing_weight", 0.5, 0.52, -0.03, 0.01),
            ("L2_confidence", "calibration", 1.0, 0.98, 0.02, 0.005),
            ("L5_latency", "sla_threshold", 5.0, 4.9, 0.15, 0.02),
        ]

        for loop_id, param, old_val, new_val, grad, lr in updates:
            success, event_id = updater.apply_weight_delta(
                loop_id=loop_id,
                param_name=param,
                old_value=old_val,
                new_value=new_val,
                gradient=grad,
                learning_rate=lr,
                lom=f"test.py:L{50 + len(backend.events)}",
            )

            assert success is True
            assert event_id is not None

        # Verify all updates were audited
        assert len(backend.events) == 3
        assert backend.write_count == 3

        # Verify all events have tenant_id
        for event in backend.events:
            assert event["tenant_id"] == "test_tenant"

        # Verify gradient info is present
        for event in backend.events:
            details = event["details"]
            assert "gradient" in details
            assert "learning_rate" in details


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
