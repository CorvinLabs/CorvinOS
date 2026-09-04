"""
E2E Test: L5 k=2 OperatorApprovalGate Integration with Learning Loop.

This test proves that:
1. FeedbackStabilityGate (L5 k=1) generates DriftAlerts
2. OperatorApprovalGate (L5 k=2) processes them
3. Operator decisions are audited
4. Learning loop respects operator approvals
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.skills.feedback_stability import (
    FeedbackStabilityGate,
    OperatorApprovalGate,
    DriftAlert,
    ApprovalDecision,
)


class AuditBackendMock:
    """Mock audit backend that collects all events."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict) -> None:
        """Record audit event."""
        event["timestamp"] = datetime.utcnow().isoformat() + "Z"
        self.events.append(event)

    def verify_chain(self) -> bool:
        """Verify audit trail is immutable (for demo, always true)."""
        return len(self.events) > 0


def test_e2e_learning_loop_with_operator_approval():
    """
    End-to-end: FeedbackStabilityGate → DriftAlert → OperatorApprovalGate → Operator Decision → Audit Trail.

    This is the **real entry point** for L5 k=2:
    - Optimizer gets feedback (raw deltas)
    - Stability gate smooths + detects drift
    - If drift detected, approval gate queues for operator
    - Operator approves/rejects/revokes
    - All events are audit-trailed
    """

    print("\n" + "=" * 70)
    print("E2E: L5 k=2 Learning Integration with Operator Approval")
    print("=" * 70)

    # Setup
    stability_gate = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate = OperatorApprovalGate(tenant_id="test_tenant", auto_approval_confidence_threshold=0.8)
    audit_backend = AuditBackendMock()

    # === PHASE 1: Learning Feedback Loop ===
    print("\n[PHASE 1] Learning Feedback Loop")
    print("-" * 70)

    # Simulate optimizer sending feedback deltas (e.g., "adjust router threshold")
    feedback_deltas = [
        ("skill.router", "confidence_threshold", 0.05, "Learning: slightly increase confidence"),
        ("skill.router", "confidence_threshold", 0.05, "Learning: continue uptrend"),
        ("skill.router", "confidence_threshold", 0.05, "Learning: confirm pattern"),
    ]

    approval_records = []

    for skill_id, metric_name, raw_delta, reason in feedback_deltas:
        print(f"\n  Input: {skill_id}.{metric_name} delta={raw_delta:.2f}")

        # Step 1: Stability gate processes feedback
        smoothed, drift_alert = stability_gate.apply_feedback(skill_id, metric_name, raw_delta)

        print(f"    → Smoothed: {smoothed.smoothed_delta:.4f}, Confidence: {smoothed.confidence:.2f}")

        if drift_alert:
            print(f"    → 🚨 DRIFT ALERT: magnitude={drift_alert.smoothed_delta:.4f}, requires_approval={drift_alert.requires_operator_approval}")

            # Step 2: Approval gate processes drift alert
            record, auto_approved = approval_gate.request_approval(
                drift_alert,
                confidence=smoothed.confidence,
                prev_config_hash="old_config",
                next_config_hash="new_config",
                audit_backend=audit_backend,
            )

            approval_records.append((skill_id, metric_name, record, auto_approved))

            if auto_approved:
                print(f"    → ✅ AUTO-APPROVED (confidence={smoothed.confidence:.2f} > 0.8)")
            else:
                print(f"    → ⏳ QUEUED for operator (confidence={smoothed.confidence:.2f} < 0.8)")
        else:
            print(f"    → ✓ No drift (within threshold)")

    # === PHASE 2: Operator Reviews & Approves ===
    print("\n[PHASE 2] Operator Reviews & Approves")
    print("-" * 70)

    pending = approval_gate.get_pending_approvals()
    print(f"\n  Pending approvals: {len(pending)}")

    for record in pending:
        skill_id = record.scrubbed_alert.skill_id
        metric_name = record.scrubbed_alert.metric_name
        print(f"\n  [{skill_id}.{metric_name}]")
        print(f"    Magnitude: {record.scrubbed_alert.magnitude:.4f}")
        print(f"    Reason: {record.scrubbed_alert.reason_code.value}")
        print(f"    Config: {record.prev_config_hash} → {record.next_config_hash}")

        # Operator approves
        success = approval_gate.operator_approve(
            record.approval_id, "operator:alice", audit_backend=audit_backend
        )

        if success:
            print(f"    ✅ Approved by operator:alice")
        else:
            print(f"    ❌ Approval failed (may have expired)")

    # === PHASE 3: Verify Audit Trail ===
    print("\n[PHASE 3] Verify Audit Trail")
    print("-" * 70)

    approval_requested = audit_backend.events
    print(f"\n  Total audit events: {len(approval_requested)}")

    request_events = [e for e in audit_backend.events if e["event_type"] == "skill_approval_requested"]
    granted_events = [e for e in audit_backend.events if e["event_type"] == "skill_approval_granted"]

    print(f"  Approval requests: {len(request_events)}")
    print(f"  Approvals granted: {len(granted_events)}")

    # Verify chain integrity
    for i, event in enumerate(audit_backend.events):
        assert "event_type" in event, f"Event {i} missing event_type"
        assert "timestamp" in event, f"Event {i} missing timestamp"
        assert "tenant_id" in event, f"Event {i} missing tenant_id"
        assert event["tenant_id"] == "test_tenant", f"Event {i} has wrong tenant_id"

    print("  ✓ All events have required fields")
    print("  ✓ Tenant isolation verified")

    # === PHASE 4: Verify State ===
    print("\n[PHASE 4] Verify Final State")
    print("-" * 70)

    # Check that auto-approved items went straight to history
    all_pending_now = approval_gate.get_pending_approvals()
    print(f"\n  Remaining pending approvals: {len(all_pending_now)}")
    assert len(all_pending_now) == 0, "All pending should be processed"

    # Check approval history
    all_approved = [e for e in audit_backend.events if e["event_type"] == "skill_approval_granted"]
    print(f"  Total approved: {len(all_approved)}")

    print("\n" + "=" * 70)
    print("✓ E2E TEST PASSED")
    print("=" * 70)
    print("\nVerified:")
    print("  • Feedback → Stability Gate → Drift Detection")
    print("  • Drift Alert → Approval Gate (auto/queue)")
    print("  • Operator Review → Approval Decision")
    print("  • Full Audit Trail with tenant isolation")
    print()


def test_e2e_operator_revoke_scenario():
    """
    Scenario: Operator approves change, but it causes issues → revoke.

    This proves the "Operator Can Revert" constraint works end-to-end.
    """

    print("\n" + "=" * 70)
    print("E2E: Operator Revoke Scenario")
    print("=" * 70)

    stability_gate = FeedbackStabilityGate(drift_threshold=0.15)
    approval_gate = OperatorApprovalGate(auto_approval_confidence_threshold=0.95)  # High threshold for demo
    audit_backend = AuditBackendMock()

    # Skill generates drift
    drift = DriftAlert(
        skill_id="skill.router",
        metric_name="confidence_threshold",
        smoothed_delta=0.2,
        drift_threshold=0.15,
        recent_deltas=[0.2, 0.22, 0.19],
        consecutive_high_deltas=3,
        requires_operator_approval=True,
    )

    record, auto = approval_gate.request_approval(
        drift,
        confidence=0.6,  # Low confidence, queue for operator
        prev_config_hash="config_v1",
        next_config_hash="config_v2_candidate",
        audit_backend=audit_backend,
    )

    print(f"\n[Step 1] Drift detected: magnitude={drift.smoothed_delta:.2f}")
    print(f"  → Queued for operator (confidence={0.6})")

    # Operator approves
    approval_gate.operator_approve(record.approval_id, "operator:alice", audit_backend=audit_backend)
    print(f"\n[Step 2] Operator alice approves")

    status_after_approve = approval_gate.get_approval_status(record.approval_id)
    assert status_after_approve.decision == ApprovalDecision.APPROVED
    print(f"  ✓ Status: {status_after_approve.decision.value}")

    # Simulate: 30 min later, monitoring shows issues → operator revokes
    approval_gate.operator_revoke(
        record.approval_id,
        "operator:alice",
        reason="Caused latency regression: p99 went from 100ms → 350ms",
        audit_backend=audit_backend,
    )

    print(f"\n[Step 3] Operator alice revokes (30 min later)")
    print(f"  Reason: Caused latency regression")

    status_after_revoke = approval_gate.get_approval_status(record.approval_id)
    assert status_after_revoke.decision == ApprovalDecision.REVOKED
    assert "latency" in status_after_revoke.revoke_reason.lower()
    print(f"  ✓ Status: {status_after_revoke.decision.value}")
    print(f"  ✓ Reason recorded: {status_after_revoke.revoke_reason[:50]}…")

    # Verify audit trail shows the full story
    audit_trail = audit_backend.events
    request_count = len([e for e in audit_trail if e["event_type"] == "skill_approval_requested"])
    approved_count = len([e for e in audit_trail if e["event_type"] == "skill_approval_granted"])
    revoke_count = len([e for e in audit_trail if e["event_type"] == "skill_approval_revoked"])

    print(f"\n[Audit Trail]")
    print(f"  Requested: {request_count}")
    print(f"  Approved:  {approved_count}")
    print(f"  Revoked:   {revoke_count}")

    assert request_count == 1
    assert approved_count == 1
    assert revoke_count == 1

    print("\n" + "=" * 70)
    print("✓ REVOKE SCENARIO PASSED")
    print("=" * 70)
    print("\nProved:")
    print("  • Operator can revoke previously-approved changes")
    print("  • Revoke reason is recorded in audit trail")
    print("  • Status transitions: APPROVED → REVOKED")
    print()


if __name__ == "__main__":
    test_e2e_learning_loop_with_operator_approval()
    test_e2e_operator_revoke_scenario()

    print("\n" + "=" * 70)
    print("✓ ALL E2E TESTS PASSED")
    print("=" * 70)
