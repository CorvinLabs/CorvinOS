#!/usr/bin/env python3
"""
Standalone test for Security Fix #8: Audit-First Weight Updates

Tests the fail-closed audit gate implementation without pytest.
"""

import sys
import json
from datetime import datetime

# Add core to path
sys.path.insert(0, "/home/shumway/projects/CorvinOS")

from core.learning.weight_updater import (
    WeightUpdater,
    WeightAuditFailedError,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self, fail_on_write=False):
        self.events = []
        self.fail_on_write = fail_on_write
        self.write_count = 0

    def write_event(self, event):
        """Mock write_event."""
        self.write_count += 1
        if self.fail_on_write:
            raise IOError("Simulated audit backend failure")
        self.events.append(event)

    def write_event_dict(self, event_type, tenant_id, details=None, severity=None):
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


def test_1_audit_write_called():
    """TEST 1: Verify audit backend write is called."""
    print("\n[TEST 1] Audit write is called...")
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
    print("  ✓ Audit write called successfully")
    print(f"  ✓ {backend.write_count} audit events recorded")
    return True


def test_2_audit_includes_tenant_id():
    """TEST 2: Verify audit event includes tenant_id."""
    print("\n[TEST 2] Audit event includes tenant_id...")
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
    print("  ✓ Tenant ID correctly included in audit event")
    return True


def test_3_audit_includes_weight_details():
    """TEST 3: Verify audit event contains weight change details."""
    print("\n[TEST 3] Audit event includes weight details...")
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
    print("  ✓ Weight details included in audit event")
    print(f"  ✓ Delta: {event['delta']}, EMA delta: {event['ema_filtered_delta']}")
    return True


def test_4_exception_on_audit_failure():
    """TEST 4: Verify exception raised on audit failure."""
    print("\n[TEST 4] Exception raised on audit failure...")
    backend = MockAuditBackend(fail_on_write=True)
    updater = WeightUpdater()

    try:
        updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )
        print("  ✗ Expected exception was not raised!")
        return False
    except (WeightAuditFailedError, RuntimeError) as e:
        print("  ✓ Exception raised on audit failure")
        print(f"  ✓ Exception type: {type(e).__name__}")
        print(f"  ✓ Error message: {str(e)[:80]}...")
        assert backend.write_count == 1
        print("  ✓ Audit backend was called before exception")
        return True


def test_5_no_silent_failures():
    """TEST 5: Verify no silent failures on audit backend errors."""
    print("\n[TEST 5] No silent failures on audit errors...")

    class FailingAuditBackend:
        def write_event(self, event):
            raise IOError("Disk full")

    backend = FailingAuditBackend()
    updater = WeightUpdater()

    try:
        updater.update_weight(
            weight_id="L1_routing",
            delta=0.05,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )
        print("  ✗ Expected exception was not raised!")
        return False
    except (WeightAuditFailedError, RuntimeError) as e:
        print("  ✓ Audit failure triggers exception (not silent)")
        print(f"  ✓ Error: {str(e)[:80]}...")
        return True


def test_6_different_tenants_isolated():
    """TEST 6: Verify different tenants are isolated in audit trail."""
    print("\n[TEST 6] Different tenants are isolated...")
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
    print("  ✓ Tenant 1 events isolated")
    print("  ✓ Tenant 2 events isolated")
    return True


def test_7_audit_includes_learning_context():
    """TEST 7: Verify audit event includes learning context."""
    print("\n[TEST 7] Audit event includes learning context...")
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
    print("  ✓ Effective learning rate: {:.6f}".format(event["effective_learning_rate"]))
    print("  ✓ Base learning rate: {:.6f}".format(event["base_learning_rate"]))
    print("  ✓ Oscillation detected: {}".format(event["oscillation_detected"]))
    return True


def test_8_attack_silent_modification_blocked():
    """TEST 8 (Attack): Silent weight modification without audit is blocked."""
    print("\n[TEST 8] ATTACK - Silent weight modification blocked...")

    class AttackBackend:
        def __init__(self):
            self.call_count = 0

        def write_event(self, event):
            self.call_count += 1
            raise IOError("Simulated attack: audit write blocked")

    backend = AttackBackend()
    updater = WeightUpdater()

    try:
        updater.update_weight(
            weight_id="L1_routing",
            delta=0.5,  # Large suspicious change
            base_learning_rate=0.2,
            audit_backend=backend,
            tenant_id="default",
        )
        print("  ✗ Attack should have been blocked!")
        return False
    except (WeightAuditFailedError, RuntimeError):
        print("  ✓ Attack blocked - audit failure detected")
        print(f"  ✓ Audit backend was called: {backend.call_count} time(s)")
        return True


def test_9_history_only_contains_audited_updates():
    """TEST 9: Verify only audited updates appear in history."""
    print("\n[TEST 9] History only contains audited updates...")
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
    try:
        updater.update_weight(
            weight_id="L2_confidence",
            delta=0.03,
            base_learning_rate=0.01,
            audit_backend=backend_fail,
            tenant_id="default",
        )
    except (WeightAuditFailedError, RuntimeError):
        pass  # Expected

    # History should only contain the audited update
    history = updater.get_update_history()
    assert len(history) == 1
    assert history[0].weight_id == "L1_routing"
    print("  ✓ Only 1 audited update in history")
    print("  ✓ Failed update (audit error) not added to history")
    return True


def test_10_audit_fail_closed_integration():
    """TEST 10: Complete fail-closed integration test."""
    print("\n[TEST 10] Complete fail-closed integration...")
    backend = MockAuditBackend()
    updater = WeightUpdater()

    # Apply multiple updates
    deltas = [0.05, -0.02, 0.01]
    for delta in deltas:
        updater.update_weight(
            weight_id="test_weight",
            delta=delta,
            base_learning_rate=0.01,
            audit_backend=backend,
            tenant_id="default",
        )

    # Verify all updates were audited
    assert backend.write_count >= 3
    print(f"  ✓ {backend.write_count} audit writes completed")

    # Verify audit events have required fields for state reconstruction
    audited_events = [e for e in backend.events if e.get("event_type") == "weight_updated"]
    for i, event in enumerate(audited_events):
        assert "delta" in event
        assert "ema_filtered_delta" in event
        assert "effective_learning_rate" in event
        assert "timestamp" in event

    print(f"  ✓ {len(audited_events)} weight_updated events audited")
    print("  ✓ All events contain required fields for state reconstruction")
    return True


def main():
    """Run all tests."""
    print("=" * 70)
    print("Security Fix #8: Audit-First Weight Updates (Fail-Closed)")
    print("Testing: Finding #8 - Audit Bypass: Silent config changes")
    print("=" * 70)

    tests = [
        test_1_audit_write_called,
        test_2_audit_includes_tenant_id,
        test_3_audit_includes_weight_details,
        test_4_exception_on_audit_failure,
        test_5_no_silent_failures,
        test_6_different_tenants_isolated,
        test_7_audit_includes_learning_context,
        test_8_attack_silent_modification_blocked,
        test_9_history_only_contains_audited_updates,
        test_10_audit_fail_closed_integration,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ Test failed with exception: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)

    if failed == 0:
        print("\n✓ All tests passed! Security Fix #8 is working correctly.")
        return 0
    else:
        print(f"\n✗ {failed} test(s) failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
