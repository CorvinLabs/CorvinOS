#!/usr/bin/env python3
"""
Validation script for L5 k=2: OperatorApprovalGate constraints.

Runs all 5 critical constraints without pytest (pure Python).
Returns exit code 0 if all pass, 1 if any fail.
"""

import sys
import os
from datetime import datetime, timedelta

# Add core to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.skills.feedback_stability import (
    OperatorApprovalGate,
    DriftAlert,
    ApprovalReasonCode,
    ApprovalDecision,
    ScrubbedDriftAlert,
)


class MockAuditBackend:
    """Mock audit backend for validation."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict) -> None:
        self.events.append(event)

    def get_events_by_type(self, event_type: str):
        return [e for e in self.events if e.get("event_type") == event_type]


def test_constraint_1_audit_trail():
    """Constraint #1: Linearizable Audit Trail."""
    print("\n[C1] Testing Linearizable Audit Trail...")
    gate = OperatorApprovalGate(tenant_id="test_tenant")
    audit = MockAuditBackend()

    drift = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
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
        audit_backend=audit,
    )

    # C1a: Request emits event
    requested_events = audit.get_events_by_type("skill_approval_requested")
    assert len(requested_events) == 1, f"Expected 1 request event, got {len(requested_events)}"
    assert requested_events[0]["approval_id"] == record.approval_id
    assert requested_events[0]["tenant_id"] == "test_tenant"
    print("  ✓ Approval request emits audit event")

    # C1b: Approval decision emits event
    success = gate.operator_approve(record.approval_id, "operator:alice", audit_backend=audit)
    assert success, "Approval should succeed"

    granted_events = audit.get_events_by_type("skill_approval_granted")
    assert len(granted_events) == 1, f"Expected 1 granted event, got {len(granted_events)}"
    assert granted_events[0]["operator_id"] == "operator:alice"
    print("  ✓ Approval decision emits audit event")

    # C1c: Rejection emits event
    drift2 = DriftAlert(
        skill_id="skill.formatter",
        metric_name="style",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )

    record2, _ = gate.request_approval(
        drift2,
        confidence=0.5,
        prev_config_hash="abc",
        next_config_hash="def",
        audit_backend=audit,
    )

    gate.operator_reject(
        record2.approval_id,
        "operator:bob",
        reason="Too risky",
        audit_backend=audit,
    )

    denied_events = audit.get_events_by_type("skill_approval_denied")
    assert len(denied_events) == 1, f"Expected 1 denied event, got {len(denied_events)}"
    print("  ✓ Rejection emits audit event")

    print("✓ CONSTRAINT #1 PASSED: Linearizable Audit Trail")
    return True


def test_constraint_2_auto_approval():
    """Constraint #2: Auto-Approval for Low-Risk."""
    print("\n[C2] Testing Auto-Approval for Low-Risk...")
    gate = OperatorApprovalGate(auto_approval_confidence_threshold=0.8)

    # C2a: High confidence auto-approves
    drift_high = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        consecutive_high_deltas=3,
    )

    record_high, auto_high = gate.request_approval(
        drift_high,
        confidence=0.85,
        prev_config_hash="abc",
        next_config_hash="def",
    )

    assert auto_high is True, "High confidence should auto-approve"
    assert record_high.decision == ApprovalDecision.APPROVED
    assert record_high.operator_id == "system:auto"
    print("  ✓ High confidence (0.85) auto-approves")

    # C2b: Low confidence requires operator
    drift_low = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )

    record_low, auto_low = gate.request_approval(
        drift_low,
        confidence=0.6,
        prev_config_hash="abc",
        next_config_hash="def",
    )

    assert auto_low is False, "Low confidence should queue"
    assert record_low.decision == ApprovalDecision.PENDING
    assert "skill.router" in gate.pending_approvals
    print("  ✓ Low confidence (0.6) queues for operator")

    # C2c: Multiple high-confidence don't queue
    gate_multi = OperatorApprovalGate(auto_approval_confidence_threshold=0.8)
    for i in range(5):
        drift = DriftAlert(
            skill_id=f"skill.{i}",
            metric_name="metric",
            smoothed_delta=0.1,
            drift_threshold=0.15,
            consecutive_high_deltas=3,
        )
        _, auto = gate_multi.request_approval(
            drift, confidence=0.9, prev_config_hash=f"a{i}", next_config_hash=f"b{i}"
        )
        assert auto is True

    assert len(gate_multi.pending_approvals) == 0, "All high-confidence should auto-approve"
    print("  ✓ Multiple high-confidence don't queue")

    print("✓ CONSTRAINT #2 PASSED: Auto-Approval for Low-Risk")
    return True


def test_constraint_3_scrubbed_payload():
    """Constraint #3: Scrubbed Alert Payload (No PII/Training Data)."""
    print("\n[C3] Testing Scrubbed Alert Payload...")
    gate = OperatorApprovalGate()

    # C3a: Scrub removes raw deltas
    drift = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.2,
        drift_threshold=0.15,
        recent_deltas=[0.2, 0.25, 0.18],
        consecutive_high_deltas=3,
    )

    scrubbed = gate.scrub_alert(drift, confidence=0.9)
    assert isinstance(scrubbed, ScrubbedDriftAlert)
    assert scrubbed.magnitude == 0.2
    assert scrubbed.confidence == 0.9
    assert not hasattr(scrubbed, "recent_deltas"), "Scrubbed should not have raw deltas"
    print("  ✓ Scrub removes raw_deltas")

    # C3b: Uses reason codes (enum)
    drift_consistent = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.2,
        drift_threshold=0.15,
        consecutive_high_deltas=3,
    )
    scrubbed_consistent = gate.scrub_alert(drift_consistent, confidence=0.9)
    assert scrubbed_consistent.reason_code == ApprovalReasonCode.CONSISTENT_PATTERN
    print("  ✓ Consistent deltas → CONSISTENT_PATTERN reason code")

    drift_noise = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.2,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )
    scrubbed_noise = gate.scrub_alert(drift_noise, confidence=0.5)
    assert scrubbed_noise.reason_code == ApprovalReasonCode.RANDOM_NOISE
    print("  ✓ Single delta → RANDOM_NOISE reason code")

    # C3c: Approval record contains scrubbed alert
    record, _ = gate.request_approval(
        drift, confidence=0.9, prev_config_hash="abc", next_config_hash="def"
    )
    assert isinstance(record.scrubbed_alert, ScrubbedDriftAlert)
    assert not hasattr(record.scrubbed_alert, "recent_deltas")
    print("  ✓ Approval record contains scrubbed alert")

    print("✓ CONSTRAINT #3 PASSED: Scrubbed Alert Payload")
    return True


def test_constraint_4_ttl():
    """Constraint #4: Approval TTL (Expires after 12h)."""
    print("\n[C4] Testing Approval TTL...")
    gate = OperatorApprovalGate(approval_ttl_hours=12)

    drift = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )

    # C4a: Approval has TTL
    record, _ = gate.request_approval(
        drift, confidence=0.5, prev_config_hash="abc", next_config_hash="def"
    )

    now = datetime.utcnow().replace(tzinfo=None)
    expiry = datetime.fromisoformat(record.ttl_expires.replace("Z", "")).replace(tzinfo=None)
    delta_seconds = (expiry - now).total_seconds()

    # Should be approximately 12 hours (43200 seconds)
    assert 11 * 3600 < delta_seconds < 13 * 3600, (
        f"TTL should be ~12h, got {delta_seconds/3600:.1f}h"
    )
    print(f"  ✓ TTL set to {delta_seconds/3600:.1f} hours")

    # C4b: Custom TTL honored
    gate_short = OperatorApprovalGate(approval_ttl_hours=2)
    record_short, _ = gate_short.request_approval(
        drift, confidence=0.5, prev_config_hash="abc", next_config_hash="def"
    )

    now = datetime.utcnow().replace(tzinfo=None)
    expiry_short = datetime.fromisoformat(record_short.ttl_expires.replace("Z", "")).replace(tzinfo=None)
    delta_short = (expiry_short - now).total_seconds()

    assert 1.9 * 3600 < delta_short < 2.1 * 3600, f"TTL should be ~2h, got {delta_short/3600:.1f}h"
    print(f"  ✓ Custom TTL (2h) honored")

    print("✓ CONSTRAINT #4 PASSED: Approval TTL")
    return True


def test_constraint_5_revoke():
    """Constraint #5: Operator Can Revert."""
    print("\n[C5] Testing Operator Can Revert...")
    gate = OperatorApprovalGate()
    audit = MockAuditBackend()

    drift = DriftAlert(
        skill_id="skill.router",
        metric_name="threshold",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        consecutive_high_deltas=3,
    )

    # C5a: Revoke approved request
    record, auto = gate.request_approval(
        drift,
        confidence=0.9,  # Auto-approved
        prev_config_hash="abc",
        next_config_hash="def",
        audit_backend=audit,
    )

    assert record.decision == ApprovalDecision.APPROVED
    assert auto is True
    print("  ✓ Request auto-approved (high confidence)")

    success = gate.operator_revoke(
        record.approval_id,
        "operator:alice",
        reason="Performance regression detected",
        audit_backend=audit,
    )

    assert success is True, "Revoke should succeed"
    print("  ✓ Revoke succeeds")

    # C5b: Status shows revoked
    status = gate.get_approval_status(record.approval_id)
    assert status.decision == ApprovalDecision.REVOKED
    assert "Performance" in status.revoke_reason
    print("  ✓ Status shows REVOKED with reason")

    # C5c: Revoke emits audit event
    revoke_events = audit.get_events_by_type("skill_approval_revoked")
    assert len(revoke_events) == 1, f"Expected 1 revoke event, got {len(revoke_events)}"
    assert revoke_events[0]["approval_id"] == record.approval_id
    print("  ✓ Revoke emits audit event")

    # C5d: Cannot revoke non-approved
    drift2 = DriftAlert(
        skill_id="skill.formatter",
        metric_name="style",
        smoothed_delta=0.1,
        drift_threshold=0.15,
        consecutive_high_deltas=1,
    )

    record2, _ = gate.request_approval(
        drift2,
        confidence=0.5,  # Pending
        prev_config_hash="abc",
        next_config_hash="def",
    )

    revoke_pending = gate.operator_revoke(record2.approval_id, "operator:alice")
    assert revoke_pending is False, "Cannot revoke pending"
    print("  ✓ Cannot revoke non-approved request")

    print("✓ CONSTRAINT #5 PASSED: Operator Can Revert")
    return True


def main():
    """Run all constraint validations."""
    print("=" * 70)
    print("L5 k=2: OperatorApprovalGate — Constraint Validation")
    print("=" * 70)

    try:
        c1 = test_constraint_1_audit_trail()
        c2 = test_constraint_2_auto_approval()
        c3 = test_constraint_3_scrubbed_payload()
        c4 = test_constraint_4_ttl()
        c5 = test_constraint_5_revoke()

        if all([c1, c2, c3, c4, c5]):
            print("\n" + "=" * 70)
            print("✓ ALL 5 CONSTRAINTS PASSED")
            print("=" * 70)
            return 0
        else:
            print("\n✗ SOME CONSTRAINTS FAILED")
            return 1

    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
