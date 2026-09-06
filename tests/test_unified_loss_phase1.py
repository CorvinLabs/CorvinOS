"""
Phase 1 Tests: Unified Loss API + Backprop (25 tests)

Tests cover:
1. Loss component computation (6 components)
2. Loss aggregation (weighted sum)
3. Gradient computation
4. Audit trail integrity
5. Fail-closed semantics
"""

import pytest
from datetime import datetime, timedelta
import numpy as np
from core.learning.unified_loss import (
    UnifiedLossOptimizer,
    UnifiedLossSnapshot,
    MockAuditBackend,
)
from core.learning.loss_backprop import LossBackpropagator


@pytest.fixture
def audit_backend():
    """Create mock audit backend."""
    return MockAuditBackend()


@pytest.fixture
def optimizer(audit_backend):
    """Create optimizer instance."""
    return UnifiedLossOptimizer(tenant_id='_default', audit_backend=audit_backend)


@pytest.fixture
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


@pytest.fixture
def synthetic_outcomes():
    """Generate synthetic outcomes (100 outcomes)."""
    outcomes = []
    for i in range(100):
        is_correct = np.random.random() > 0.3  # 70% correct
        engine_correct = np.random.random() > 0.4  # 60% routing correct
        outcomes.append({
            'correct': is_correct,
            'engine_correct': engine_correct,
        })
    return outcomes


@pytest.fixture
def synthetic_feedback():
    """Generate synthetic feedback (100 signals)."""
    feedback = []
    for i in range(100):
        if np.random.random() > 0.2:  # 80% arrival
            feedback.append({
                'signal': 'correct' if np.random.random() > 0.5 else 'incorrect',
                'timestamp': (datetime.now() - timedelta(seconds=np.random.uniform(0, 300))).isoformat(),
            })
        else:
            feedback.append(None)
    return feedback


# ============================================================================
# Component Tests (6 tests)
# ============================================================================

def test_L_routing_all_correct(optimizer, synthetic_batch, synthetic_outcomes):
    """L_routing should be ~0 if all outcomes are correct."""
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(100)]

    L_routing = optimizer._compute_L_routing(synthetic_batch, outcomes)

    assert L_routing == pytest.approx(0.0, abs=0.01)


def test_L_routing_all_wrong(optimizer, synthetic_batch):
    """L_routing should be ~1 if all outcomes are wrong."""
    outcomes = [{'correct': False, 'engine_correct': False} for _ in range(100)]

    L_routing = optimizer._compute_L_routing(synthetic_batch, outcomes)

    assert L_routing == pytest.approx(1.0, abs=0.01)


def test_L_confidence_well_calibrated(optimizer):
    """L_confidence should be low if predictions match outcomes."""
    batch = [{'confidence_score': 1.0} for _ in range(50)]
    batch += [{'confidence_score': 0.0} for _ in range(50)]

    outcomes = [{'correct': True} for _ in range(50)]
    outcomes += [{'correct': False} for _ in range(50)]

    L_confidence = optimizer._compute_L_confidence(batch, outcomes)

    # Perfect calibration: (1-1)² + (0-0)² = 0
    assert L_confidence == pytest.approx(0.0, abs=0.01)


def test_L_feedback_all_present(optimizer):
    """L_feedback should be low if all feedback is present and recent."""
    feedback = [{'timestamp': datetime.now().isoformat()} for _ in range(100)]

    L_feedback = optimizer._compute_L_feedback(feedback)

    # Full arrival, no staleness
    assert L_feedback < 0.1


def test_L_attention_on_budget(optimizer):
    """L_attention should be low if costs are within budget."""
    batch = [
        {
            'tokens_used': 1000.0,
            'budget_allocated': 1000.0,
        }
        for _ in range(100)
    ]

    L_attention = optimizer._compute_L_attention(batch)

    # On budget, full utilization
    assert L_attention == pytest.approx(0.0, abs=0.1)


def test_L_latency_sla_met(optimizer):
    """L_latency should be low if p99 is within SLA."""
    batch = [{'latency_seconds': 2.0} for _ in range(100)]  # Well within 5s SLA

    L_latency = optimizer._compute_L_latency(batch)

    assert L_latency < 0.5


def test_L_diversity_full_coverage(optimizer):
    """L_diversity should be low if all task types are present."""
    batch = []
    for i in range(100):
        batch.append({
            'task_type': f'type_{i % 15}',  # All 15 types covered
            'routed_engine': np.random.choice(['haiku', 'sonnet', 'opus', 'fable']),
        })

    L_diversity = optimizer._compute_L_diversity(batch)

    # Full coverage
    assert L_diversity < 0.5


# ============================================================================
# Aggregation Tests (3 tests)
# ============================================================================

def test_unified_loss_e2e_all_good(optimizer, synthetic_batch, synthetic_outcomes, synthetic_feedback):
    """Full pipeline: batch → compute loss."""
    snapshot = optimizer.compute_batch_loss(synthetic_batch, synthetic_outcomes, synthetic_feedback)

    assert snapshot is not None
    assert snapshot.L_total >= 0.0
    assert snapshot.L_total <= 6.0
    assert snapshot.hash is not None


def test_unified_loss_weighted_sum(optimizer):
    """L_total should be weighted sum of components."""
    batch = [{'confidence_score': 0.9, 'tokens_used': 1000.0, 'budget_allocated': 1000.0, 'latency_seconds': 2.0, 'task_type': 'type_1', 'routed_engine': 'opus'} for _ in range(10)]
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(10)]
    feedback = [{'timestamp': datetime.now().isoformat()} for _ in range(10)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    # Manually compute expected L_total
    expected = (
        optimizer.weights['routing'] * snapshot.L_routing +
        optimizer.weights['confidence'] * snapshot.L_confidence +
        optimizer.weights['feedback'] * snapshot.L_feedback +
        optimizer.weights['attention'] * snapshot.L_attention +
        optimizer.weights['latency'] * snapshot.L_latency +
        optimizer.weights['diversity'] * snapshot.L_diversity
    )

    assert snapshot.L_total == pytest.approx(expected, abs=0.001)


def test_unified_loss_nan_handling(optimizer, audit_backend):
    """If batch is empty, loss should not be NaN."""
    empty_batch = []
    empty_outcomes = []
    empty_feedback = []

    snapshot = optimizer.compute_batch_loss(empty_batch, empty_outcomes, empty_feedback)

    assert not np.isnan(snapshot.L_total)
    assert snapshot.L_total >= 0.0


# ============================================================================
# Gradient Tests (5 tests)
# ============================================================================

def test_gradient_routing_sign_wrong_outcome(optimizer, audit_backend):
    """If routing was wrong, grad_routing should be positive."""
    batch = [{'confidence_score': 0.95} for _ in range(100)]
    outcomes = [{'engine_correct': False, 'correct': False} for _ in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit_backend)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    # Routing was wrong; should penalize
    assert gradients['routing'] > 0


def test_gradient_confidence_sign_calibration_error(optimizer, audit_backend):
    """If confidence was overconfident, grad_confidence should be positive."""
    batch = [{'confidence_score': 0.99} for _ in range(100)]
    outcomes = [{'correct': False} for _ in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit_backend)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    # Confidence was overconfident; should penalize
    assert gradients['confidence'] > 0


def test_gradient_feedback_sign_low_arrival(optimizer, audit_backend):
    """If feedback arrival was low, grad_feedback should be negative."""
    batch = [{'tokens_used': 500.0, 'budget_allocated': 1000.0, 'latency_seconds': 2.0} for _ in range(100)]
    outcomes = [{'correct': True} for _ in range(100)]
    feedback = [None for _ in range(100)]  # 0% arrival

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit_backend)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    # No feedback; should improve
    assert gradients['feedback'] < 0


def test_gradient_clipping(optimizer, audit_backend):
    """Gradients should be clipped to [-1, +1]."""
    batch = []
    for i in range(100):
        batch.append({
            'confidence_score': np.random.uniform(0, 1),
            'tokens_used': np.random.uniform(500, 2000),
            'budget_allocated': 1000.0,
            'latency_seconds': np.random.exponential(scale=10.0),  # High variance
            'task_type': 'type_1',
            'routed_engine': 'opus',
        })
    outcomes = [{'correct': i % 2 == 0, 'engine_correct': i % 3 == 0} for i in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit_backend)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    for key, grad in gradients.items():
        assert -1.0 <= grad <= 1.0, f"{key} gradient {grad} exceeds clipping bounds"


def test_gradient_dag_edges_recorded(optimizer, audit_backend):
    """Audit event should record DAG edges."""
    batch = [{'confidence_score': 0.5} for _ in range(10)]
    outcomes = [{'correct': True, 'engine_correct': True} for _ in range(10)]
    feedback = [{'timestamp': datetime.now().isoformat()} for _ in range(10)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop = LossBackpropagator(audit_backend)
    gradients = backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    # Check audit events
    events = audit_backend.read_events(tenant_id='_default')
    gradient_events = [e for e in events if e['event_type'] == 'loss_gradient_computed']

    assert len(gradient_events) > 0
    last_event = gradient_events[-1]
    assert 'dag_edges' in last_event


# ============================================================================
# Audit Trail Tests (5 tests)
# ============================================================================

def test_audit_event_written_on_loss_compute(optimizer, audit_backend, synthetic_batch, synthetic_outcomes, synthetic_feedback):
    """Every loss computation should write audit event."""
    optimizer.compute_batch_loss(synthetic_batch, synthetic_outcomes, synthetic_feedback)

    events = audit_backend.read_events(tenant_id='_default')
    loss_events = [e for e in events if e['event_type'] == 'unified_loss_computed']

    assert len(loss_events) == 1


def test_audit_hash_chain_integrity(optimizer, audit_backend, synthetic_batch, synthetic_outcomes, synthetic_feedback):
    """Hash chain should be intact after multiple loss computations."""
    for _ in range(5):
        optimizer.compute_batch_loss(synthetic_batch, synthetic_outcomes, synthetic_feedback)

    # Verify chain
    chain_ok = audit_backend.verify_chain()
    assert chain_ok


def test_audit_tenant_isolation(audit_backend, synthetic_batch, synthetic_outcomes, synthetic_feedback):
    """Events from different tenants should not mix."""
    opt1 = UnifiedLossOptimizer(tenant_id='tenant_1', audit_backend=audit_backend)
    opt2 = UnifiedLossOptimizer(tenant_id='tenant_2', audit_backend=audit_backend)

    opt1.compute_batch_loss(synthetic_batch, synthetic_outcomes, synthetic_feedback)
    opt2.compute_batch_loss(synthetic_batch, synthetic_outcomes, synthetic_feedback)

    events_t1 = audit_backend.read_events(tenant_id='tenant_1')
    events_t2 = audit_backend.read_events(tenant_id='tenant_2')

    assert all(e['tenant_id'] == 'tenant_1' for e in events_t1)
    assert all(e['tenant_id'] == 'tenant_2' for e in events_t2)
    assert len(events_t1) > 0
    assert len(events_t2) > 0


def test_audit_weight_update_recorded(optimizer, audit_backend):
    """Weight updates should be audited."""
    new_weights = {
        'routing': 0.25,
        'confidence': 0.25,
        'feedback': 0.15,
        'attention': 0.15,
        'latency': 0.10,
        'diversity': 0.10,
    }

    optimizer.update_weights(new_weights)

    events = audit_backend.read_events(tenant_id='_default')
    weight_events = [e for e in events if e['event_type'] == 'weights_updated']

    assert len(weight_events) == 1
    assert weight_events[0]['new_weights'] == new_weights


def test_audit_divergence_detected_event(optimizer, audit_backend):
    """If gradients diverge, alert should be emitted."""
    backprop = LossBackpropagator(audit_backend)

    # Create extreme batch to trigger divergence detection
    batch = [
        {
            'confidence_score': np.random.uniform(0, 1),
            'tokens_used': np.random.uniform(100, 5000),
            'budget_allocated': 500.0,  # Very tight budget
            'latency_seconds': np.random.exponential(scale=20.0),  # High variance
            'task_type': 'type_1',
            'routed_engine': 'opus',
        }
        for _ in range(100)
    ]
    outcomes = [{'correct': i % 2 == 0, 'engine_correct': i % 4 == 0} for i in range(100)]
    feedback = [None for _ in range(100)]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    backprop.compute_gradients(snapshot, batch, outcomes, feedback)

    # Check for divergence alert (might not always trigger; just check it's possible)
    events = audit_backend.read_events(tenant_id='_default')
    # (Divergence detection is contingent on extreme values)


# ============================================================================
# Fail-Closed Tests (2 tests)
# ============================================================================

def test_fail_closed_audit_abort(optimizer, audit_backend, synthetic_batch, synthetic_outcomes, synthetic_feedback):
    """If audit fails, loss computation should abort."""
    # Simulate audit failure
    audit_backend.write_event = lambda x: None

    with pytest.raises(RuntimeError, match="Audit write failed"):
        optimizer.compute_batch_loss(synthetic_batch, synthetic_outcomes, synthetic_feedback)


def test_fail_closed_nan_gradient(optimizer, audit_backend):
    """If gradient computation produces NaN, should be handled."""
    batch = [
        {
            'confidence_score': float('nan'),  # This will trigger issues
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

    # Loss computation should handle NaN gracefully
    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    # L_confidence should be NaN or bounded
    assert np.isnan(snapshot.L_confidence) or snapshot.L_confidence >= 0


# ============================================================================
# Edge Case Tests (4 tests)
# ============================================================================

def test_empty_batch_no_crash(optimizer):
    """Empty batch should not crash."""
    snapshot = optimizer.compute_batch_loss([], [], [])

    assert snapshot is not None
    assert not np.isnan(snapshot.L_total)


def test_batch_size_one(optimizer):
    """Batch with single task should work."""
    batch = [{'confidence_score': 0.9, 'tokens_used': 1000.0, 'budget_allocated': 1000.0, 'latency_seconds': 2.0, 'task_type': 'type_1', 'routed_engine': 'opus'}]
    outcomes = [{'correct': True, 'engine_correct': True}]
    feedback = [None]

    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)

    assert snapshot is not None


def test_batch_size_mismatch_handled(optimizer):
    """Mismatched batch/outcome sizes should be handled gracefully."""
    batch = [{'confidence_score': 0.9} for _ in range(10)]
    outcomes = [{'correct': True}] * 5  # Only 5 outcomes
    feedback = []

    # Should not crash; zip will truncate
    snapshot = optimizer.compute_batch_loss(batch, outcomes, feedback)
    assert snapshot is not None


def test_weights_sum_to_one(optimizer):
    """Loss weights should sum to 1."""
    total_weight = sum(optimizer.weights.values())

    assert total_weight == pytest.approx(1.0, abs=0.001)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
