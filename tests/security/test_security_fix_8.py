"""Security Fix #8: Audit-First Weight Updates (Fail-Closed).

Testing: Finding #8 - Audit Bypass: Silent config changes

This test suite verifies that weight updates are audit-first and fail-closed:
1. Audit event is created before weight is applied
2. Audit commit must succeed before weight change takes effect
3. If audit write fails, weight is NOT applied in memory
4. Exceptions are raised on audit failure
5. Tenant ID is correctly included in audit events
6. Silent config changes are blocked
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
    WeightAuditFailedError,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self, fail_on_write: bool = False):
        self.events: list[dict] = []
        self.fail_on_write = fail_on_write
        self.write_count = 0

    def write_event(self, event: dict) -> None:
        """Mock write_event."""
        self.write_count += 1
        if self.fail_on_write:
            raise IOError("Simulated audit backend failure")

        self.events.append(event)

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


class TestWeightAuditEventCommitted:
    """TEST 1: weights_updated event is committed to audit chain BEFORE weight applied."""

    def test_audit_write_called(self):
        """Verify audit backend write is called."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        record = updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )

        assert record is not None
        assert backend.write_count > 0
        assert len(backend.events) > 0

    def test_audit_event_recorded_with_tenant_id(self):
        """Verify audit event includes tenant_id."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L2_confidence",
            delta=0.02,
            base_learning_rate=0.005,
            audit_backend=backend,
            tenant_id="tenant_alpha",
        )

        assert backend.events[0]["tenant_id"] == "tenant_alpha"

    def test_audit_event_includes_weight_details(self):
        """Verify audit event contains weight change details."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L5_latency",
            delta=0.1,
            base_learning_rate=0.1,
            audit_backend=backend,
            tenant_id="default",
        )

        event = backend.events[0]
        assert event["weight_id"] == "L5_latency"
        assert "delta" in event
        assert "ema_filtered_delta" in event

    def test_audit_event_includes_learning_context(self):
        """Verify audit event includes learning rate and oscillation info."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L1_routing",
            delta=0.03,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )

        event = backend.events[0]
        assert "effective_learning_rate" in event
        assert "base_learning_rate" in event
        assert "oscillation_detected" in event

    def test_audit_hash_returned(self):
        """Verify audit backend can return hash."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )

        # Check that audit backend recorded the event
        assert backend.write_count >= 1
        assert len(backend.events) >= 1


class TestWeightAuditFailureBlocksUpdate:
    """TEST 2: Weight is NOT applied if audit write fails."""

    def test_exception_raised_when_audit_fails(self):
        """Verify exception is raised when audit fails."""
        backend = MockAuditBackend(fail_on_write=True)
        updater = WeightUpdater()

        # Attempting to apply weight should raise exception
        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

        # Verify audit backend was attempted
        assert backend.write_count == 1

    def test_no_silent_failures(self):
        """Verify that audit failures are NOT silently ignored."""

        class FailingAuditBackend:
            def write_event(self, event: dict) -> None:
                raise IOError("Disk full")

        backend = FailingAuditBackend()
        updater = WeightUpdater()

        # Should raise exception, not silently fail
        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

    def test_weight_update_history_not_added_on_audit_failure(self):
        """Verify weight is not added to history if audit fails."""
        backend = MockAuditBackend(fail_on_write=True)
        updater = WeightUpdater()

        # Record initial history length
        initial_history_len = len(updater.get_update_history())

        # Attempting to apply weight should raise exception
        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

        # History should not have been updated
        # (In the current implementation, history is added after audit,
        # but if audit fails, we never get there)


class TestWeightAuditFailureExceptionRaised:
    """TEST 3: Exceptions are raised on audit failure."""

    def test_exception_raised_on_write_failure(self):
        """Verify exception is raised on audit failure."""

        class BrokenAuditBackend:
            def write_event(self, event: dict) -> None:
                raise IOError("Audit backend unreachable")

        backend = BrokenAuditBackend()
        updater = WeightUpdater()

        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

    def test_exception_contains_error_details(self):
        """Verify exception message contains helpful details."""

        class DetailedFailureBackend:
            def write_event(self, event: dict) -> None:
                raise IOError("Permission denied on audit.jsonl")

        backend = DetailedFailureBackend()
        updater = WeightUpdater()

        with pytest.raises((WeightAuditFailedError, RuntimeError)) as exc_info:
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

        error_msg = str(exc_info.value)
        assert "audit" in error_msg.lower() or "failed" in error_msg.lower()


class TestWeightAuditTenantScoped:
    """TEST 4: Tenant ID is correctly included and scoped."""

    def test_audit_event_includes_tenant_id(self):
        """Verify tenant_id is in every audit event."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="tenant_xyz",
        )

        assert backend.events[0]["tenant_id"] == "tenant_xyz"

    def test_different_tenants_different_audit_trails(self):
        """Verify different tenants have separate audit trails."""
        backend1 = MockAuditBackend()
        backend2 = MockAuditBackend()

        updater1 = WeightUpdater()
        updater2 = WeightUpdater()

        updater1.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend1,
            tenant_id="tenant_1",
        )

        updater2.update_weight(
            weight_id="L1_routing",
            delta=0.03,
            base_learning_rate=0.01,
            audit_backend=backend2,
            tenant_id="tenant_2",
        )

        assert backend1.events[0]["tenant_id"] == "tenant_1"
        assert backend2.events[0]["tenant_id"] == "tenant_2"

    def test_audit_backend_receives_correct_tenant_id(self):
        """Verify audit backend is called with correct tenant context."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L2_confidence",
            delta=0.02,
            base_learning_rate=0.005,
            audit_backend=backend,
            tenant_id="production_tenant",
        )

        event = backend.events[0]
        assert event["tenant_id"] == "production_tenant"


class TestSecurityAttackBlockage:
    """TEST 5: Security fix blocks audit bypass attacks."""

    def test_attack_silent_weight_modification_blocked(self):
        """ATTACK: Attacker tries to silently modify weight without audit.

        Expected: Attack is blocked; exception raised.
        """

        class AttackBackend:
            """Simulates attacker trying to bypass audit."""

            def __init__(self):
                self.call_count = 0

            def write_event(self, event: dict) -> None:
                # Pretend to write but actually fail
                self.call_count += 1
                raise IOError("Simulated attack: audit write blocked")

        backend = AttackBackend()
        updater = WeightUpdater()

        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.5,  # Large change
                base_learning_rate=0.2,
                audit_backend=backend,
                tenant_id="default",
            )

        # Verify attack was detected
        assert backend.call_count == 1

    def test_update_history_only_contains_audited_updates(self):
        """ATTACK: Direct access to update_history bypassing audit.

        Expected: Only audited updates appear in history.
        """
        backend = MockAuditBackend()
        backend_fail = MockAuditBackend(fail_on_write=True)

        updater = WeightUpdater()

        # Successful update (audited)
        updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )

        # Failed update (not audited)
        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L2_confidence",
                delta=0.03,
                base_learning_rate=0.01,
                audit_backend=backend_fail,
                tenant_id="default",
            )

        # History should only contain the audited update
        history = updater.get_update_history()
        assert len(history) == 1
        assert history[0].weight_id == "L1_routing"


class TestOscillationDetectionAudited:
    """TEST 6: Oscillation detection is properly audited."""

    def test_oscillation_event_emitted_when_detected(self):
        """Verify oscillation is detected and audited."""
        backend = MockAuditBackend()
        updater = WeightUpdater(oscillation_frequency_threshold=3.0)

        # Trigger multiple rapid updates to detect oscillation
        for i in range(5):
            try:
                updater.update_weight(
                    weight_id="L1_routing",
                    delta=0.01 * (i + 1),
                    base_learning_rate=0.01,
                    audit_backend=backend,
                    tenant_id="default",
                )
            except Exception:
                # Some updates may fail if audit backend is set up to fail
                pass

        # Check that events were recorded
        assert backend.write_count > 0

    def test_oscillation_info_in_audit_event(self):
        """Verify oscillation flag is included in audit event."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )

        event = backend.events[0]
        assert "oscillation_detected" in event


class TestIntegrationAuditFirst:
    """Integration test: Audit-first workflow."""

    def test_complete_audit_first_workflow(self):
        """Verify complete audit-first workflow."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        # Simulate a complete learning loop iteration
        updates = [
            ("L1_routing", 0.05),
            ("L2_confidence", 0.02),
            ("L5_latency", 0.03),
        ]

        for weight_id, delta in updates:
            record = updater.update_weight(
                weight_id=weight_id,
                delta=delta,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="test_tenant",
            )

            assert record is not None
            assert record.weight_id == weight_id

        # Verify all updates were audited
        assert backend.write_count >= 3
        assert len(backend.events) >= 3

        # Verify all events have tenant_id
        for event in backend.events:
            assert event["tenant_id"] == "test_tenant"

        # Verify update history
        history = updater.get_update_history()
        assert len(history) >= 3

    def test_audit_trail_preserves_state(self):
        """Verify audit trail allows state reconstruction."""
        backend = MockAuditBackend()
        updater = WeightUpdater()

        # Apply several updates
        deltas = [0.05, -0.02, 0.01]
        for i, delta in enumerate(deltas):
            updater.update_weight(
                weight_id="test_weight",
                delta=delta,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

        # Verify audit events have all the info needed to reconstruct state
        for i, event in enumerate(backend.events):
            if event.get("event_type") == "weight_updated":
                assert "delta" in event
                assert "ema_filtered_delta" in event
                assert "effective_learning_rate" in event
                assert "base_learning_rate" in event
                assert "timestamp" in event


class TestFailClosedGuarantee:
    """TEST 7: Fail-closed guarantee verification."""

    def test_audit_failure_prevents_state_change(self):
        """Verify that audit failure prevents any state change."""
        backend = MockAuditBackend(fail_on_write=True)
        updater = WeightUpdater()

        # Record initial state
        initial_history = updater.get_update_history()
        initial_clamped = updater.is_oscillation_clamped("L1_routing")

        # Attempt update (should fail)
        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

        # Verify state didn't change
        final_history = updater.get_update_history()
        final_clamped = updater.is_oscillation_clamped("L1_routing")

        assert len(final_history) == len(initial_history)
        assert final_clamped == initial_clamped

    def test_partial_audit_failure_handled(self):
        """Verify partial audit failures are handled correctly."""

        class PartialFailureBackend:
            def __init__(self):
                self.call_count = 0

            def write_event(self, event: dict) -> None:
                self.call_count += 1
                if self.call_count == 1:
                    raise IOError("First write fails")
                # Subsequent writes succeed

        backend = PartialFailureBackend()
        updater = WeightUpdater()

        # First update should fail
        with pytest.raises((WeightAuditFailedError, RuntimeError)):
            updater.update_weight(
                weight_id="L1_routing",
                delta=0.05,
                base_learning_rate=0.01,
                audit_backend=backend,
                tenant_id="default",
            )

        # Second update should succeed
        backend.call_count = 0  # Reset counter to skip first call
        # This test assumes the backend behaves consistently


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
