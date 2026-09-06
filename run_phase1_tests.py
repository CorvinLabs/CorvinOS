#!/usr/bin/env python3
"""
Phase 1 Test Runner — Manual execution (no pytest needed).
"""

import sys
import os
from datetime import datetime, timedelta
import numpy as np

# Add project to path
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.learning.unified_loss import (
    UnifiedLossOptimizer,
    UnifiedLossSnapshot,
    MockAuditBackend,
)
from core.learning.loss_backprop import LossBackpropagator


class TestRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def run_test(self, name, test_func):
        """Run a single test."""
        try:
            test_func()
            self.passed += 1
            print(f"✓ {name}")
        except AssertionError as e:
            self.failed += 1
            self.errors.append((name, str(e)))
            print(f"✗ {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.errors.append((name, f"ERROR: {type(e).__name__}: {e}"))
            print(f"✗ {name}: ERROR: {type(e).__name__}: {e}")

    def summary(self):
        print(f"\n{'='*60}")
        print(f"PASSED: {self.passed}")
        print(f"FAILED: {self.failed}")
        print(f"TOTAL:  {self.passed + self.failed}")
        print(f"{'='*60}")
        if self.errors:
            print("\nFailed tests:")
            for name, error in self.errors:
                print(f"  {name}: {error}")
        return self.failed == 0


def assert_approx(a, b, abs_tol=0.01):
    """Simple approx assertion."""
    if abs(a - b) > abs_tol:
        raise AssertionError(f"{a} != {b} (tolerance {abs_tol})")


def synthetic_batch():
    """Generate synthetic task batch (100 tasks)."""
    batch = []
    for i in range(100):
        batch.append({
            'id': f'task_{i}',
            'confidence_score': 0.8 + np.random.uniform(-0.3, 0.2),
            'tokens_used': np.random.uniform(500, 1500),
            'budget_allocated': 1000.0,
            'latency_seconds': np.random.exponential(scale=2.0),
            'task_type': f'type_{i % 5}',
            'routed_engine': np.random.choice(['haiku', 'sonnet', 'opus', 'fable']),
        })
    return batch


def synthetic_outcomes():
    """Generate synthetic outcomes (100 outcomes)."""
    outcomes = []
    for i in range(100):
        is_correct = np.random.random() > 0.3
        engine_correct = np.random.random() > 0.4
        outcomes.append({
            'correct': is_correct,
            'engine_correct': engine_correct,
        })
    return outcomes


def synthetic_feedback():
    """Generate synthetic feedback (100 signals)."""
    feedback = []
    for i in range(100):
        if np.random.random() > 0.2:
            feedback.append({
                'signal': 'correct' if np.random.random() > 0.5 else 'incorrect',
                'timestamp': (datetime.now() - timedelta(seconds=np.random.uniform(0, 300))).isoformat(),
            })
        else:
            feedback.append(None)
    return feedback


# ============================================================================
# Test Suite
# ============================================================================

def test_L_routing_all_correct():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    batch = synthetic_batch()
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(100)]

    L_routing = optimizer._compute_L_routing(batch, outcomes)

    assert_approx(L_routing, 0.0, abs_tol=0.01)


def test_L_routing_all_wrong():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    batch = synthetic_batch()
    outcomes = [{'correct': False, 'engine_correct': False} for _ in range(100)]

    L_routing = optimizer._compute_L_routing(batch, outcomes)

    assert_approx(L_routing, 1.0, abs_tol=0.01)


def test_L_confidence_well_calibrated():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    batch = [{'confidence_score': 1.0} for _ in range(50)]
    batch += [{'confidence_score': 0.0} for _ in range(50)]

    outcomes = [{'correct': True} for _ in range(50)]
    outcomes += [{'correct': False} for _ in range(50)]

    L_confidence = optimizer._compute_L_confidence(batch, outcomes)

    assert_approx(L_confidence, 0.0, abs_tol=0.01)


def test_L_feedback_all_present():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    feedback = [{'timestamp': datetime.now().isoformat()} for _ in range(100)]

    L_feedback = optimizer._compute_L_feedback(feedback)

    if L_feedback > 0.1:
        raise AssertionError(f"L_feedback {L_feedback} should be < 0.1")


def test_L_attention_on_budget():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    batch = [
        {
            'tokens_used': 1000.0,
            'budget_allocated': 1000.0,
        }
        for _ in range(100)
    ]

    L_attention = optimizer._compute_L_attention(batch)

    assert_approx(L_attention, 0.0, abs_tol=0.1)


def test_L_latency_sla_met():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    batch = [{'latency_seconds': 2.0} for _ in range(100)]

    L_latency = optimizer._compute_L_latency(batch)

    if L_latency > 0.5:
        raise AssertionError(f"L_latency {L_latency} should be < 0.5")


def test_L_diversity_full_coverage():
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=MockAuditBackend())
    batch = []
    for i in range(100):
        batch.append({
            'task_type': f'type_{i % 15}',
            'routed_engine': np.random.choice(['haiku', 'sonnet', 'opus', 'fable']),
        })

    L_diversity = optimizer._compute_L_diversity(batch)

    if L_diversity > 0.5:
        raise AssertionError(f"L_diversity {L_diversity} should be < 0.5")


def test_unified_loss_e2e_all_good():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = synthetic_batch()
    outcomes = synthetic_outcomes()
    feedback = synthetic_feedback()

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    if snapshot is None:
        raise AssertionError("snapshot is None")
    if snapshot.L_total < 0.0 or snapshot.L_total > 6.0:
        raise AssertionError(f"L_total {snapshot.L_total} out of range [0, 6]")
    if snapshot.hash is None:
        raise AssertionError("hash is None")


def test_unified_loss_weighted_sum():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [{'confidence_score': 0.9, 'tokens_used': 1000.0, 'budget_allocated': 1000.0, 'latency_seconds': 2.0, 'task_type': 'type_1', 'routed_engine': 'opus'} for _ in range(10)]
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(10)]
    feedback = [{'timestamp': datetime.now().isoformat()} for _ in range(10)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    expected = (
        optimizer.weights['routing'] * snapshot.L_routing +
        optimizer.weights['confidence'] * snapshot.L_confidence +
        optimizer.weights['feedback'] * snapshot.L_feedback +
        optimizer.weights['attention'] * snapshot.L_attention +
        optimizer.weights['latency'] * snapshot.L_latency +
        optimizer.weights['diversity'] * snapshot.L_diversity
    )

    assert_approx(snapshot.L_total, expected, abs_tol=0.001)


def test_unified_loss_nan_handling():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)

    snapshot = optimizer.compute_batch_loss([], [], [])

    if np.isnan(snapshot.L_total):
        raise AssertionError("L_total is NaN")
    if snapshot.L_total < 0.0:
        raise AssertionError("L_total is negative")


def test_gradient_routing_sign_wrong_outcome():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [{'confidence_score': 0.95} for _ in range(100)]
    outcomes = [{'engine_correct': False, 'correct': False} for _ in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    if gradients['routing'] <= 0:
        raise AssertionError(f"grad_routing {gradients['routing']} should be positive")


def test_gradient_confidence_sign_calibration_error():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [{'confidence_score': 0.99} for _ in range(100)]
    outcomes = [{'correct': False} for _ in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    if gradients['confidence'] <= 0:
        raise AssertionError(f"grad_confidence {gradients['confidence']} should be positive")


def test_gradient_feedback_sign_low_arrival():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [{'tokens_used': 500.0, 'budget_allocated': 1000.0, 'latency_seconds': 2.0} for _ in range(100)]
    outcomes = [{'correct': True} for _ in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    if gradients['feedback'] >= 0:
        raise AssertionError(f"grad_feedback {gradients['feedback']} should be negative")


def test_gradient_clipping():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = []
    for i in range(100):
        batch.append({
            'confidence_score': np.random.uniform(0, 1),
            'tokens_used': np.random.uniform(500, 2000),
            'budget_allocated': 1000.0,
            'latency_seconds': np.random.exponential(scale=10.0),
            'task_type': 'type_1',
            'routed_engine': 'opus',
        })
    outcomes = [{'correct': i % 2 == 0, 'engine_correct': i % 3 == 0} for i in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    for key, grad in gradients.items():
        if grad < -1.0 or grad > 1.0:
            raise AssertionError(f"grad_{key} {grad} not in [-1, 1]")


def test_gradient_dag_edges_recorded():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [{'confidence_score': 0.5} for _ in range(10)]
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(10)]
    feedback = [{'timestamp': datetime.now().isoformat()} for _ in range(10)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    events = audit.read_events(tenant_id='_default')
    gradient_events = [e for e in events if e['event_type'] == 'loss_gradient_computed']

    if len(gradient_events) == 0:
        raise AssertionError("No gradient events recorded")
    if 'dag_edges' not in gradient_events[-1]:
        raise AssertionError("dag_edges not in gradient event")


def test_audit_event_written_on_loss_compute():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = synthetic_batch()
    outcomes = synthetic_outcomes()
    feedback = synthetic_feedback()

    optimizer.compute_batch_loss(batch, outcomes, feedback)

    events = audit.read_events(tenant_id='_default')
    loss_events = [e for e in events if e['event_type'] == 'unified_loss_computed']

    if len(loss_events) != 1:
        raise AssertionError(f"Expected 1 loss event, got {len(loss_events)}")


def test_audit_hash_chain_integrity():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = synthetic_batch()
    outcomes = synthetic_outcomes()
    feedback = synthetic_feedback()

    for _ in range(5):
        optimizer.compute_batch_loss(batch, outcomes, feedback)

    chain_ok = audit.verify_chain()
    if not chain_ok:
        raise AssertionError("Hash chain is broken")


def test_audit_tenant_isolation():
    audit = MockAuditBackend()
    opt1 = UnifiedLossOptimizer(tenant_id='tenant_1', audit_backend=audit)
    opt2 = UnifiedLossOptimizer(tenant_id='tenant_2', audit_backend=audit)

    batch = synthetic_batch()
    outcomes = synthetic_outcomes()
    feedback = synthetic_feedback()

    opt1.compute_batch_loss(batch, outcomes, feedback)
    opt2.compute_batch_loss(batch, outcomes, feedback)

    events_t1 = audit.read_events(tenant_id='tenant_1')
    events_t2 = audit.read_events(tenant_id='tenant_2')

    if not all(e['tenant_id'] == 'tenant_1' for e in events_t1):
        raise AssertionError("Events leaked to tenant_1")
    if not all(e['tenant_id'] == 'tenant_2' for e in events_t2):
        raise AssertionError("Events leaked to tenant_2")
    if len(events_t1) == 0 or len(events_t2) == 0:
        raise AssertionError("No events recorded")


def test_audit_weight_update_recorded():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    new_weights = {
        'routing': 0.25,
        'confidence': 0.25,
        'feedback': 0.15,
        'attention': 0.15,
        'latency': 0.10,
        'diversity': 0.10,
    }

    optimizer.update_weights(new_weights)

    events = audit.read_events(tenant_id='_default')
    weight_events = [e for e in events if e['event_type'] == 'weights_updated']

    if len(weight_events) != 1:
        raise AssertionError(f"Expected 1 weight event, got {len(weight_events)}")
    if weight_events[0]['new_weights'] != new_weights:
        raise AssertionError("Weights not recorded correctly")


def test_fail_closed_audit_abort():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)

    # Simulate audit failure
    original_write = audit.write_event
    audit.write_event = lambda x: None

    try:
        optimizer.compute_batch_loss(synthetic_batch(), synthetic_outcomes(), synthetic_feedback())
        raise AssertionError("Should have raised RuntimeError")
    except RuntimeError as e:
        if "Audit write failed" not in str(e):
            raise AssertionError(f"Wrong error message: {e}")


def test_fail_closed_nan_gradient():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [
        {
            'confidence_score': float('nan'),
            'tokens_used': 1000.0,
            'budget_allocated': 1000.0,
            'latency_seconds': 2.0,
            'task_type': 'type_1',
            'routed_engine': 'opus',
        }
        for _ in range(10)
    ]
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(10)]
    feedback = [None for _ in range(10)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    if snapshot is None:
        raise AssertionError("snapshot is None")


def test_empty_batch_no_crash():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)

    snapshot = optimizer.compute_batch_loss([], [], [])

    if snapshot is None:
        raise AssertionError("snapshot is None")
    if np.isnan(snapshot.L_total):
        raise AssertionError("L_total is NaN")


def test_batch_size_one():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)
    batch = [{'confidence_score': 0.9, 'tokens_used': 1000.0, 'budget_allocated': 1000.0, 'latency_seconds': 2.0, 'task_type': 'type_1', 'routed_engine': 'opus'}]
    outcomes = [{'correct': True, 'engine_correct': True}]
    feedback = [None]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    if snapshot is None:
        raise AssertionError("snapshot is None")


def test_weights_sum_to_one():
    audit = MockAuditBackend()
    optimizer = UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit)

    total_weight = sum(optimizer.weights.values())

    assert_approx(total_weight, 1.0, abs_tol=0.001)


# ============================================================================
# Main
# ============================================================================

if __name__ == '__main__':
    runner = TestRunner()

    print("=" * 60)
    print("PHASE 1 TEST SUITE: Unified Loss API + Backprop")
    print("=" * 60 + "\n")

    # Component Tests
    print("COMPONENT TESTS:")
    runner.run_test("test_L_routing_all_correct", test_L_routing_all_correct)
    runner.run_test("test_L_routing_all_wrong", test_L_routing_all_wrong)
    runner.run_test("test_L_confidence_well_calibrated", test_L_confidence_well_calibrated)
    runner.run_test("test_L_feedback_all_present", test_L_feedback_all_present)
    runner.run_test("test_L_attention_on_budget", test_L_attention_on_budget)
    runner.run_test("test_L_latency_sla_met", test_L_latency_sla_met)
    runner.run_test("test_L_diversity_full_coverage", test_L_diversity_full_coverage)

    # Aggregation Tests
    print("\nAGGREGATION TESTS:")
    runner.run_test("test_unified_loss_e2e_all_good", test_unified_loss_e2e_all_good)
    runner.run_test("test_unified_loss_weighted_sum", test_unified_loss_weighted_sum)
    runner.run_test("test_unified_loss_nan_handling", test_unified_loss_nan_handling)

    # Gradient Tests
    print("\nGRADIENT TESTS:")
    runner.run_test("test_gradient_routing_sign_wrong_outcome", test_gradient_routing_sign_wrong_outcome)
    runner.run_test("test_gradient_confidence_sign_calibration_error", test_gradient_confidence_sign_calibration_error)
    runner.run_test("test_gradient_feedback_sign_low_arrival", test_gradient_feedback_sign_low_arrival)
    runner.run_test("test_gradient_clipping", test_gradient_clipping)
    runner.run_test("test_gradient_dag_edges_recorded", test_gradient_dag_edges_recorded)

    # Audit Tests
    print("\nAUDIT TESTS:")
    runner.run_test("test_audit_event_written_on_loss_compute", test_audit_event_written_on_loss_compute)
    runner.run_test("test_audit_hash_chain_integrity", test_audit_hash_chain_integrity)
    runner.run_test("test_audit_tenant_isolation", test_audit_tenant_isolation)
    runner.run_test("test_audit_weight_update_recorded", test_audit_weight_update_recorded)

    # Fail-Closed Tests
    print("\nFAIL-CLOSED TESTS:")
    runner.run_test("test_fail_closed_audit_abort", test_fail_closed_audit_abort)
    runner.run_test("test_fail_closed_nan_gradient", test_fail_closed_nan_gradient)

    # Edge Case Tests
    print("\nEDGE CASE TESTS:")
    runner.run_test("test_empty_batch_no_crash", test_empty_batch_no_crash)
    runner.run_test("test_batch_size_one", test_batch_size_one)
    runner.run_test("test_weights_sum_to_one", test_weights_sum_to_one)

    # Summary
    success = runner.summary()
    sys.exit(0 if success else 1)
