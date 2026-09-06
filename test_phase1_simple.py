#!/usr/bin/env python3
"""Phase 1 Simple Test Runner (no external dependencies)."""

import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.learning.unified_loss_simple import UnifiedLossOptimizer, MockAuditBackend
from datetime import datetime, timedelta
import random


def test_summary(name, passed, failed):
    total = passed + failed
    pct = (passed / total * 100) if total > 0 else 0
    status = "✓ PASS" if failed == 0 else f"✗ FAIL ({failed} errors)"
    print(f"{status} | {name}: {passed}/{total} ({pct:.0f}%)")
    return failed == 0


class SimpleTest:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def run(self, name, func):
        try:
            func()
            self.passed += 1
            print(f"  ✓ {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"  ✗ {name}: {e}")

    def report(self):
        print(f"\n{'='*70}")
        print(f"PHASE 1: Unified Loss API + Backprop")
        print(f"{'='*70}")
        print(f"PASSED: {self.passed} | FAILED: {self.failed} | TOTAL: {self.passed + self.failed}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors[:5]:  # Show first 5
                print(f"  - {name}: {error}")
        print(f"{'='*70}\n")
        return self.failed == 0


# Fixtures
def make_audit():
    return MockAuditBackend()


def make_optimizer(tenant='_default'):
    return UnifiedLossOptimizer(tenant_id=tenant, audit_backend=make_audit())


def batch_100():
    """100 synthetic tasks."""
    return [
        {
            'id': f'task_{i}',
            'confidence_score': 0.5 + (i % 100) / 200,
            'tokens_used': 500 + (i % 100) * 10,
            'budget_allocated': 1000.0,
            'latency_seconds': 2.0 + (i % 10) * 0.1,
            'task_type': f'type_{i % 5}',
            'routed_engine': ['haiku', 'sonnet', 'opus', 'fable'][i % 4],
        }
        for i in range(100)
    ]


def outcomes_100_good():
    """All correct outcomes."""
    return [{'correct': True, 'engine_correct': True} for _ in range(100)]


def outcomes_100_bad():
    """All wrong outcomes."""
    return [{'correct': False, 'engine_correct': False} for _ in range(100)]


def feedback_100_all():
    """All feedback present."""
    return [{'timestamp': datetime.now().isoformat()} for _ in range(100)]


def feedback_100_none():
    """No feedback."""
    return [None for _ in range(100)]


# ============================================================================
# TEST SUITE
# ============================================================================

if __name__ == '__main__':
    test = SimpleTest()

    # ====== Component Tests ======
    print("COMPONENT TESTS:")

    def test_L_routing_all_correct():
        opt = make_optimizer()
        b = batch_100()
        o = outcomes_100_good()
        L = opt._compute_L_routing(b, o)
        assert L < 0.01, f"Expected ~0, got {L}"

    def test_L_routing_all_wrong():
        opt = make_optimizer()
        b = batch_100()
        o = outcomes_100_bad()
        L = opt._compute_L_routing(b, o)
        assert L > 0.95, f"Expected ~1, got {L}"

    def test_L_confidence_perfect():
        opt = make_optimizer()
        b = [{'confidence_score': 1.0}] * 50 + [{'confidence_score': 0.0}] * 50
        o = [{'correct': True}] * 50 + [{'correct': False}] * 50
        L = opt._compute_L_confidence(b, o)
        assert L < 0.01, f"Expected ~0, got {L}"

    def test_L_feedback_all_present():
        opt = make_optimizer()
        f = feedback_100_all()
        L = opt._compute_L_feedback(f)
        assert L < 0.1, f"Expected < 0.1, got {L}"

    def test_L_feedback_all_missing():
        opt = make_optimizer()
        f = feedback_100_none()
        L = opt._compute_L_feedback(f)
        assert L > 0.9, f"Expected > 0.9, got {L}"

    def test_L_attention_on_budget():
        opt = make_optimizer()
        b = [{'tokens_used': 1000.0, 'budget_allocated': 1000.0} for _ in range(100)]
        L = opt._compute_L_attention(b)
        assert L < 0.2, f"Expected < 0.2, got {L}"

    test.run("L_routing_all_correct", test_L_routing_all_correct)
    test.run("L_routing_all_wrong", test_L_routing_all_wrong)
    test.run("L_confidence_perfect", test_L_confidence_perfect)
    test.run("L_feedback_all_present", test_L_feedback_all_present)
    test.run("L_feedback_all_missing", test_L_feedback_all_missing)
    test.run("L_attention_on_budget", test_L_attention_on_budget)

    # ====== Integration Tests ======
    print("\nINTEGRATION TESTS:")

    def test_compute_batch_loss():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        b = batch_100()
        o = outcomes_100_good()
        f = feedback_100_all()
        snapshot = opt.compute_batch_loss(b, o, f)
        assert snapshot is not None, "snapshot is None"
        assert 0 <= snapshot.L_total <= 6, f"L_total {snapshot.L_total} out of range"
        assert snapshot.hash is not None, "hash is None"

    def test_loss_weighted_sum():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        b = [{'confidence_score': 0.9, 'tokens_used': 1000, 'budget_allocated': 1000, 'latency_seconds': 2.0, 'task_type': 'type_1', 'routed_engine': 'opus'}] * 10
        o = [{'correct': True, 'engine_correct': True}] * 10
        f = [{'timestamp': datetime.now().isoformat()}] * 10
        snapshot = opt.compute_batch_loss(b, o, f)

        expected = (
            opt.weights['routing'] * snapshot.L_routing +
            opt.weights['confidence'] * snapshot.L_confidence +
            opt.weights['feedback'] * snapshot.L_feedback +
            opt.weights['attention'] * snapshot.L_attention +
            opt.weights['latency'] * snapshot.L_latency +
            opt.weights['diversity'] * snapshot.L_diversity
        )
        assert abs(snapshot.L_total - expected) < 0.001, f"Expected {expected}, got {snapshot.L_total}"

    def test_empty_batch():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        snapshot = opt.compute_batch_loss([], [], [])
        assert snapshot is not None
        assert snapshot.L_total >= 0

    test.run("compute_batch_loss", test_compute_batch_loss)
    test.run("loss_weighted_sum", test_loss_weighted_sum)
    test.run("empty_batch", test_empty_batch)

    # ====== Audit Tests ======
    print("\nAUDIT TESTS:")

    def test_audit_event_written():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        opt.compute_batch_loss(batch_100(), outcomes_100_good(), feedback_100_all())
        events = audit.read_events(tenant_id='_default')
        loss_events = [e for e in events if e['event_type'] == 'unified_loss_computed']
        assert len(loss_events) == 1, f"Expected 1 event, got {len(loss_events)}"

    def test_hash_chain_integrity():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        for _ in range(5):
            opt.compute_batch_loss(batch_100(), outcomes_100_good(), feedback_100_all())
        assert audit.verify_chain(), "Hash chain is broken"

    def test_tenant_isolation():
        audit = make_audit()
        opt1 = UnifiedLossOptimizer(tenant_id='tenant_1', audit_backend=audit)
        opt2 = UnifiedLossOptimizer(tenant_id='tenant_2', audit_backend=audit)

        opt1.compute_batch_loss(batch_100(), outcomes_100_good(), feedback_100_all())
        opt2.compute_batch_loss(batch_100(), outcomes_100_bad(), feedback_100_none())

        events_t1 = audit.read_events(tenant_id='tenant_1')
        events_t2 = audit.read_events(tenant_id='tenant_2')

        assert all(e['tenant_id'] == 'tenant_1' for e in events_t1), "Tenant 1 isolation broken"
        assert all(e['tenant_id'] == 'tenant_2' for e in events_t2), "Tenant 2 isolation broken"
        assert len(events_t1) > 0, "No events for tenant 1"
        assert len(events_t2) > 0, "No events for tenant 2"

    def test_weight_update_audit():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        new_weights = {'routing': 0.25, 'confidence': 0.25, 'feedback': 0.15, 'attention': 0.15, 'latency': 0.10, 'diversity': 0.10}
        opt.update_weights(new_weights)
        events = audit.read_events(tenant_id='_default')
        weight_events = [e for e in events if e['event_type'] == 'weights_updated']
        assert len(weight_events) == 1, f"Expected 1 weight event, got {len(weight_events)}"

    test.run("audit_event_written", test_audit_event_written)
    test.run("hash_chain_integrity", test_hash_chain_integrity)
    test.run("tenant_isolation", test_tenant_isolation)
    test.run("weight_update_audit", test_weight_update_audit)

    # ====== Fail-Closed Tests ======
    print("\nFAIL-CLOSED TESTS:")

    def test_audit_failure_abort():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        audit.write_event = lambda x: None  # Simulate failure
        try:
            opt.compute_batch_loss(batch_100(), outcomes_100_good(), feedback_100_all())
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Audit write failed" in str(e), f"Wrong error: {e}"

    test.run("audit_failure_abort", test_audit_failure_abort)

    # ====== Edge Cases ======
    print("\nEDGE CASES:")

    def test_weights_sum_to_one():
        opt = make_optimizer()
        total = sum(opt.weights.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_batch_size_one():
        audit = make_audit()
        opt = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
        b = [{'confidence_score': 0.9, 'tokens_used': 1000, 'budget_allocated': 1000, 'latency_seconds': 2.0, 'task_type': 'type_1', 'routed_engine': 'opus'}]
        o = [{'correct': True, 'engine_correct': True}]
        f = [None]
        snapshot = opt.compute_batch_loss(b, o, f)
        assert snapshot is not None

    test.run("weights_sum_to_one", test_weights_sum_to_one)
    test.run("batch_size_one", test_batch_size_one)

    # ====== FINAL REPORT ======
    success = test.report()
    sys.exit(0 if success else 1)
