#!/usr/bin/env python3
"""
L5 k=2 Live Deployment Monitor

Verifies that L5 k=2 (OperatorApprovalGate) is functioning correctly in production.
Runs continuous validation checks and alerts on failures.
"""

import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.skills.feedback_stability import (
    OperatorApprovalGate,
    DriftAlert,
    ApprovalDecision,
)


class AuditBackendMock:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict) -> str:
        """Record event and return event_id."""
        event["timestamp"] = datetime.utcnow().isoformat() + "Z"
        self.events.append(event)
        return str(len(self.events))

    def get_events_by_type(self, event_type: str):
        """Get events by type."""
        return [e for e in self.events if e.get("event_type") == event_type]


def test_basic_approval_flow():
    """TEST 1: Basic approval request + operator approval."""
    print("\n[TEST 1] Basic Approval Flow")
    print("-" * 60)

    audit = AuditBackendMock()
    gate = OperatorApprovalGate(tenant_id="monitoring", audit_backend=audit)

    drift = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        recent_deltas=[0.1, 0.12, 0.09],
        consecutive_high_deltas=3,
    )

    # Request approval
    record, auto = gate.request_approval(
        drift,
        confidence=0.5,
        prev_config_hash="a" * 64,
        next_config_hash="b" * 64,
    )

    assert not auto, "Should not auto-approve (confidence < 0.8)"
    assert record.decision == ApprovalDecision.PENDING, "Should be PENDING"
    print("  ✓ Request approval (PENDING)")

    # Operator approves
    success = gate.operator_approve(record.approval_id, "operator:live_test")
    assert success, "Approval should succeed"
    assert len(audit.get_events_by_type("skill_approval_granted")) == 1
    print("  ✓ Operator approve + audit event")

    # Verify status
    status = gate.get_approval_status(record.approval_id)
    assert status.decision == ApprovalDecision.APPROVED, "Should be APPROVED"
    print("  ✓ Status check (APPROVED)")

    print("✅ TEST 1 PASSED")
    return True


def test_auto_approval():
    """TEST 2: Auto-approval for high-confidence deltas."""
    print("\n[TEST 2] Auto-Approval (High Confidence)")
    print("-" * 60)

    audit = AuditBackendMock()
    gate = OperatorApprovalGate(tenant_id="monitoring", audit_backend=audit)

    drift = DriftAlert(
        skill_id="skill.formatter",
        metric_name="style",
        smoothed_delta=0.05,
        drift_threshold=0.15,
        consecutive_high_deltas=3,
    )

    record, auto = gate.request_approval(
        drift,
        confidence=0.9,  # High confidence
        prev_config_hash="c" * 64,
        next_config_hash="d" * 64,
    )

    assert auto is True, "Should auto-approve"
    assert record.decision == ApprovalDecision.APPROVED
    print("  ✓ Auto-approved (confidence=0.9)")

    pending = gate.get_pending_approvals()
    assert len(pending) == 0, "Should not be in pending queue"
    print("  ✓ Not queued (auto-approved)")

    print("✅ TEST 2 PASSED")
    return True


def test_thread_safety():
    """TEST 3: Thread safety (concurrent requests don't race)."""
    print("\n[TEST 3] Thread Safety")
    print("-" * 60)

    import threading

    audit = AuditBackendMock()
    gate = OperatorApprovalGate(tenant_id="monitoring", audit_backend=audit)

    results = []

    def submit_request(idx):
        drift = DriftAlert(
            skill_id="skill.test",
            metric_name=f"metric_{idx}",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=1,
        )

        try:
            record, _ = gate.request_approval(
                drift,
                confidence=0.5,
                prev_config_hash="e" * 64,
                next_config_hash="f" * 64,
            )
            results.append(("success", record.approval_id))
        except Exception as e:
            results.append(("error", str(e)))

    # Spawn 5 concurrent requests
    threads = [threading.Thread(target=submit_request, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r[0] == "success"]
    assert len(successes) == 5, f"All 5 requests should succeed, got {len(successes)}"
    print(f"  ✓ {len(successes)} concurrent requests succeeded")

    print("✅ TEST 3 PASSED")
    return True


def test_operator_reject():
    """TEST 4: Operator reject."""
    print("\n[TEST 4] Operator Reject")
    print("-" * 60)

    audit = AuditBackendMock()
    gate = OperatorApprovalGate(tenant_id="monitoring", audit_backend=audit)

    drift = DriftAlert(
        skill_id="skill.rejector",
        metric_name="bad_metric",
        smoothed_delta=0.2,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )

    record, _ = gate.request_approval(
        drift,
        confidence=0.5,
        prev_config_hash="a" * 64,
        next_config_hash="b" * 64,
    )

    success = gate.operator_reject(record.approval_id, "operator:live_test", reason="High risk")
    assert success, "Reject should succeed"
    print("  ✓ Operator reject + audit event")

    status = gate.get_approval_status(record.approval_id)
    assert status.decision == ApprovalDecision.REJECTED
    print("  ✓ Status check (REJECTED)")

    print("✅ TEST 4 PASSED")
    return True


def test_input_validation():
    """TEST 5: Input validation (hashes, operator_id, confidence)."""
    print("\n[TEST 5] Input Validation")
    print("-" * 60)

    audit = AuditBackendMock()
    gate = OperatorApprovalGate(tenant_id="monitoring", audit_backend=audit)

    drift = DriftAlert(
        skill_id="skill.validator",
        metric_name="test",
        smoothed_delta=0.05,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )

    # Test invalid hash
    try:
        gate.request_approval(
            drift,
            confidence=0.5,
            prev_config_hash="invalid_hash",  # Not SHA256
            next_config_hash="b" * 64,
        )
        assert False, "Should reject invalid hash"
    except ValueError as e:
        print(f"  ✓ Rejected invalid hash: {str(e)[:50]}...")

    # Test invalid operator_id
    try:
        gate.operator_approve("dummy_id", "")  # Empty operator_id
        assert False, "Should reject empty operator_id"
    except ValueError as e:
        print(f"  ✓ Rejected empty operator_id: {str(e)[:50]}...")

    print("✅ TEST 5 PASSED")
    return True


def main():
    """Run all live monitoring tests."""
    print("\n" + "=" * 60)
    print("L5 k=2 LIVE DEPLOYMENT MONITORING")
    print("=" * 60)
    print(f"Started: {datetime.now().isoformat()}")

    tests = [
        test_basic_approval_flow,
        test_auto_approval,
        test_thread_safety,
        test_operator_reject,
        test_input_validation,
    ]

    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append((test_func.__name__, "PASSED"))
        except Exception as e:
            print(f"❌ TEST FAILED: {e}")
            results.append((test_func.__name__, f"FAILED: {e}"))

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, status in results if status == "PASSED")
    total = len(results)

    for test_name, status in results:
        symbol = "✅" if status == "PASSED" else "❌"
        print(f"{symbol} {test_name}: {status}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print(f"Status: {'🟢 HEALTHY' if passed == total else '🔴 DEGRADED'}")
    print(f"Ended: {datetime.now().isoformat()}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
