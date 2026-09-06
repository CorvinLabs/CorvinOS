"""
Phase 2 Simple Tests: L5 Feedback Loop Integration (no pytest required).
"""

import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.learning.feedback_loop_l5_integration import (
    L5FeedbackLoopIntegrator,
    L5PipelineDecision,
)
from core.skills.feedback_stability import (
    FeedbackStabilityGate,
    OperatorApprovalGate,
)
from core.learning.quality_gate import QualityGate
from core.learning.conflict_resolver import ConflictResolver
from core.learning.rollback_guard import RollbackGuard


class AuditBackendMock:
    """Mock audit backend."""

    def __init__(self):
        self.events = []

    def write_event(self, event: dict) -> str:
        event_id = str(len(self.events))
        event["timestamp"] = datetime.utcnow().isoformat() + "Z"
        event["event_id"] = event_id
        self.events.append(event)
        return event_id

    def verify_chain(self) -> bool:
        return len(self.events) > 0


def test_no_drift_auto_approves():
    """Test 1: No drift → auto-approve immediately."""
    print("\n[Test 1] No drift auto-approves...")

    audit = AuditBackendMock()
    stability_gate = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate = OperatorApprovalGate(tenant_id="_default", auto_approval_confidence_threshold=0.8, audit_backend=audit)
    quality_gate = QualityGate(tenant_id="_default", audit_backend=audit)
    conflict_resolver = ConflictResolver(tenant_id="_default", audit_backend=audit)
    rollback_guard = RollbackGuard(tenant_id="_default", audit_backend=audit)

    integrator = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate,
        approval_gate=approval_gate,
        quality_gate=quality_gate,
        conflict_resolver=conflict_resolver,
        rollback_guard=rollback_guard,
        tenant_id="_default",
        audit_backend=audit,
    )

    result = integrator.process_feedback(
        skill_id="test.router",
        metric_name="confidence_threshold",
        raw_delta=0.02,
        new_config_hash="a" * 64,
    )

    assert result.final_decision == L5PipelineDecision.APPROVED_IMMEDIATELY, \
        f"Expected APPROVED_IMMEDIATELY, got {result.final_decision}"
    assert result.k1_decision is not None
    assert result.k1_decision.decision_code == "no_drift"
    print("  ✅ PASSED: No drift resulted in immediate approval")


def test_audit_trail_recorded():
    """Test 2: Audit trail is recorded."""
    print("\n[Test 2] Audit trail recorded...")

    audit = AuditBackendMock()
    stability_gate = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate = OperatorApprovalGate(tenant_id="_default", auto_approval_confidence_threshold=0.8, audit_backend=audit)
    quality_gate = QualityGate(tenant_id="_default", audit_backend=audit)
    conflict_resolver = ConflictResolver(tenant_id="_default", audit_backend=audit)
    rollback_guard = RollbackGuard(tenant_id="_default", audit_backend=audit)

    integrator = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate,
        approval_gate=approval_gate,
        quality_gate=quality_gate,
        conflict_resolver=conflict_resolver,
        rollback_guard=rollback_guard,
        tenant_id="_default",
        audit_backend=audit,
    )

    result = integrator.process_feedback(
        skill_id="test.router",
        metric_name="confidence_threshold",
        raw_delta=0.02,
        new_config_hash="a" * 64,
    )

    audit_events = [e for e in audit.events if e.get("event_type") == "l5_pipeline_complete"]
    assert len(audit_events) == 1, f"Expected 1 audit event, got {len(audit_events)}"
    event = audit_events[0]
    assert event["final_decision"] == "approved_immediately"
    assert event["tenant_id"] == "_default"
    print("  ✅ PASSED: Audit trail recorded correctly")


def test_operator_approve_recorded():
    """Test 3: Operator approval is audited."""
    print("\n[Test 3] Operator approval audited...")

    audit = AuditBackendMock()
    stability_gate = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate = OperatorApprovalGate(tenant_id="_default", auto_approval_confidence_threshold=0.8, audit_backend=audit)
    quality_gate = QualityGate(tenant_id="_default", audit_backend=audit)
    conflict_resolver = ConflictResolver(tenant_id="_default", audit_backend=audit)
    rollback_guard = RollbackGuard(tenant_id="_default", audit_backend=audit)

    integrator = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate,
        approval_gate=approval_gate,
        quality_gate=quality_gate,
        conflict_resolver=conflict_resolver,
        rollback_guard=rollback_guard,
        tenant_id="_default",
        audit_backend=audit,
    )

    approval_id = "test_approval_123"
    integrator.approve_pending(approval_id)

    audit_events = [e for e in audit.events if e.get("event_type") == "l5_operator_approval"]
    assert len(audit_events) == 1
    assert audit_events[0]["approval_id"] == approval_id
    print("  ✅ PASSED: Operator approval recorded in audit trail")


def test_operator_reject_recorded():
    """Test 4: Operator rejection is audited."""
    print("\n[Test 4] Operator rejection audited...")

    audit = AuditBackendMock()
    stability_gate = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate = OperatorApprovalGate(tenant_id="_default", auto_approval_confidence_threshold=0.8, audit_backend=audit)
    quality_gate = QualityGate(tenant_id="_default", audit_backend=audit)
    conflict_resolver = ConflictResolver(tenant_id="_default", audit_backend=audit)
    rollback_guard = RollbackGuard(tenant_id="_default", audit_backend=audit)

    integrator = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate,
        approval_gate=approval_gate,
        quality_gate=quality_gate,
        conflict_resolver=conflict_resolver,
        rollback_guard=rollback_guard,
        tenant_id="_default",
        audit_backend=audit,
    )

    approval_id = "test_approval_456"
    integrator.reject_pending(approval_id, "Not confident")

    audit_events = [e for e in audit.events if e.get("event_type") == "l5_operator_rejection"]
    assert len(audit_events) == 1
    assert audit_events[0]["approval_id"] == approval_id
    print("  ✅ PASSED: Operator rejection recorded in audit trail")


def test_operator_revoke_recorded():
    """Test 5: Operator revoke is audited."""
    print("\n[Test 5] Operator revoke audited...")

    audit = AuditBackendMock()
    stability_gate = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate = OperatorApprovalGate(tenant_id="_default", auto_approval_confidence_threshold=0.8, audit_backend=audit)
    quality_gate = QualityGate(tenant_id="_default", audit_backend=audit)
    conflict_resolver = ConflictResolver(tenant_id="_default", audit_backend=audit)
    rollback_guard = RollbackGuard(tenant_id="_default", audit_backend=audit)

    integrator = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate,
        approval_gate=approval_gate,
        quality_gate=quality_gate,
        conflict_resolver=conflict_resolver,
        rollback_guard=rollback_guard,
        tenant_id="_default",
        audit_backend=audit,
    )

    approval_id = "approved_change_789"
    integrator.revoke_approved(approval_id, "operator_1", "Performance degraded")

    audit_events = [e for e in audit.events if e.get("event_type") == "l5_operator_revoke"]
    assert len(audit_events) == 1
    assert audit_events[0]["approval_id"] == approval_id
    assert audit_events[0]["operator_id"] == "operator_1"
    print("  ✅ PASSED: Operator revoke recorded in audit trail")


def test_tenant_isolation():
    """Test 6: Tenant isolation."""
    print("\n[Test 6] Tenant isolation...")

    audit_1 = AuditBackendMock()
    audit_2 = AuditBackendMock()

    stability_gate_1 = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate_1 = OperatorApprovalGate(tenant_id="tenant_1", auto_approval_confidence_threshold=0.8, audit_backend=audit_1)
    quality_gate_1 = QualityGate(tenant_id="tenant_1", audit_backend=audit_1)
    conflict_resolver_1 = ConflictResolver(tenant_id="tenant_1", audit_backend=audit_1)
    rollback_guard_1 = RollbackGuard(tenant_id="tenant_1", audit_backend=audit_1)

    integrator_1 = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate_1,
        approval_gate=approval_gate_1,
        quality_gate=quality_gate_1,
        conflict_resolver=conflict_resolver_1,
        rollback_guard=rollback_guard_1,
        tenant_id="tenant_1",
        audit_backend=audit_1,
    )

    stability_gate_2 = FeedbackStabilityGate(ema_alpha=0.3, drift_threshold=0.15, drift_window=3)
    approval_gate_2 = OperatorApprovalGate(tenant_id="tenant_2", auto_approval_confidence_threshold=0.8, audit_backend=audit_2)
    quality_gate_2 = QualityGate(tenant_id="tenant_2", audit_backend=audit_2)
    conflict_resolver_2 = ConflictResolver(tenant_id="tenant_2", audit_backend=audit_2)
    rollback_guard_2 = RollbackGuard(tenant_id="tenant_2", audit_backend=audit_2)

    integrator_2 = L5FeedbackLoopIntegrator(
        stability_gate=stability_gate_2,
        approval_gate=approval_gate_2,
        quality_gate=quality_gate_2,
        conflict_resolver=conflict_resolver_2,
        rollback_guard=rollback_guard_2,
        tenant_id="tenant_2",
        audit_backend=audit_2,
    )

    result_1 = integrator_1.process_feedback(
        skill_id="test.router",
        metric_name="confidence_threshold",
        raw_delta=0.02,
        new_config_hash="a" * 64,
    )

    result_2 = integrator_2.process_feedback(
        skill_id="test.router",
        metric_name="confidence_threshold",
        raw_delta=0.02,
        new_config_hash="b" * 64,
    )

    assert result_1.pipeline_id != result_2.pipeline_id
    assert integrator_1.get_pipeline_result(result_1.pipeline_id) is not None
    assert integrator_1.get_pipeline_result(result_2.pipeline_id) is None
    assert integrator_2.get_pipeline_result(result_2.pipeline_id) is not None
    assert integrator_2.get_pipeline_result(result_1.pipeline_id) is None
    print("  ✅ PASSED: Tenant isolation verified")


def run_all_tests():
    """Run all tests."""
    print("\n" + "=" * 70)
    print("L5 Phase 2 Tests: Feedback Loop Integration")
    print("=" * 70)

    tests = [
        test_no_drift_auto_approves,
        test_audit_trail_recorded,
        test_operator_approve_recorded,
        test_operator_reject_recorded,
        test_operator_revoke_recorded,
        test_tenant_isolation,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
