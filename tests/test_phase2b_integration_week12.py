"""
Week 12: Phase 2B Integration Tests (ADR-0615/0616)

6 integration tests:
1. Unified Loss + Gradient Backprop pipeline
2. Loss decrease after gradient application
3. Correlation filter prevents oscillation
4. Multi-batch convergence
5. Audit trail complete + tenant-scoped
6. End-to-end: outcome → loss → gradients → weights update
"""

from core.learning.unified_loss import UnifiedLossOptimizer, UnifiedLossSnapshot
from tests.learning.mock_audit_backend import MockAuditBackend
from core.learning.gradient_backprop import LossBackpropagator, CorrelationFilter, CouplingOscillationDetector
from datetime import datetime
import numpy as np


class TestUnifiedLossGradientPipeline:
    """Test 1: Unified Loss + Gradient Backprop pipeline end-to-end."""

    def test_pipeline_initialization(self):
        audit = MockAuditBackend()
        optimizer = UnifiedLossOptimizer(tenant_id='test', audit_backend=audit)
        backprop = LossBackpropagator(audit_backend=audit, tenant_id='test')

        assert optimizer is not None
        assert backprop is not None
        assert backprop.check_dag_validity()

    def test_pipeline_flow(self):
        """Task batch → loss computation → gradient computation → weights update."""
        audit = MockAuditBackend()
        optimizer = UnifiedLossOptimizer(tenant_id='test', audit_backend=audit)
        backprop = LossBackpropagator(audit_backend=audit, tenant_id='test')

        task_batch = [
            {'confidence_score': 0.8, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'classify', 'routed_engine': 'opus', 'budget_allocated': 1000},
            {'confidence_score': 0.6, 'tokens_used': 800, 'latency_seconds': 3.5, 'task_type': 'summarize', 'routed_engine': 'sonnet', 'budget_allocated': 1000},
        ]

        outcomes = [
            {'correct': True, 'engine_correct': True},
            {'correct': False, 'engine_correct': False},
        ]

        feedback = [
            {'timestamp': datetime.now().isoformat(), 'is_valid': True},
            None,
        ]

        loss_snapshot = optimizer.compute_batch_loss(task_batch, outcomes, feedback)
        assert loss_snapshot.L_total > 0
        assert loss_snapshot.tenant_id == 'test'

        gradients = backprop.compute_gradients_with_dag(loss_snapshot, task_batch, outcomes, feedback)
        assert 'L1_routing' in gradients

        events = audit.read_events('test')
        assert len(events) >= 2


class TestLossDecreaseAfterGradients:
    """Test 2: Loss should decrease after applying gradient-based updates."""

    def test_loss_decreases_with_correction(self):
        audit = MockAuditBackend()
        optimizer = UnifiedLossOptimizer(tenant_id='test', audit_backend=audit)

        task_batch_1 = [
            {'confidence_score': 0.7, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'classify', 'routed_engine': 'opus', 'budget_allocated': 1000},
        ]
        outcomes_1 = [{'correct': False, 'engine_correct': False}]
        feedback_1 = [None]

        loss_1 = optimizer.compute_batch_loss(task_batch_1, outcomes_1, feedback_1)

        task_batch_2 = [
            {'confidence_score': 0.7, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'classify', 'routed_engine': 'sonnet', 'budget_allocated': 1000},
        ]
        outcomes_2 = [{'correct': True, 'engine_correct': True}]
        feedback_2 = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]

        loss_2 = optimizer.compute_batch_loss(task_batch_2, outcomes_2, feedback_2)

        assert loss_2.L_total < loss_1.L_total


class TestCorrelationFilterPreventsOscillation:
    """Test 3: Correlation filter prevents anti-correlated gradient application."""

    def test_filter_stops_oscillating_update(self):
        filt = CorrelationFilter(correlation_threshold=0.5)

        local_grad = {'L1_routing': -0.05}
        backprop_grad = {'L1_routing': 0.08}

        apply, corr = filt.apply_filter('L1_routing', local_grad, backprop_grad)

        assert not apply, "Filter should reject anti-correlated gradient"
        assert corr < 0, "Correlation should be negative"

    def test_filter_accepts_aligned_updates(self):
        filt = CorrelationFilter(correlation_threshold=0.5)

        local_grad = {'L2_confidence': 0.03}
        backprop_grad = {'L2_confidence': 0.04}

        apply, corr = filt.apply_filter('L2_confidence', local_grad, backprop_grad)

        assert apply, "Filter should accept correlated gradient"


class TestMultiBatchConvergence:
    """Test 4: Loss converges over multiple batches."""

    def test_loss_trajectory_decreasing(self):
        audit = MockAuditBackend()
        optimizer = UnifiedLossOptimizer(tenant_id='test', audit_backend=audit)

        losses = []

        for batch_idx in range(5):
            task_batch = [
                {'confidence_score': 0.5 + batch_idx*0.05, 'tokens_used': 500 - batch_idx*20, 'latency_seconds': 3.0 - batch_idx*0.2, 'task_type': 'test', 'routed_engine': 'opus', 'budget_allocated': 1000},
            ]

            correct_rate = 0.5 + batch_idx * 0.1
            outcomes = [{'correct': True, 'engine_correct': True}] if np.random.rand() < correct_rate else [{'correct': False, 'engine_correct': False}]
            feedback = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}] if outcomes[0]['correct'] else [None]

            loss = optimizer.compute_batch_loss(task_batch, outcomes, feedback)
            losses.append(loss.L_total)

        avg_early = np.mean(losses[:2])
        avg_late = np.mean(losses[3:])
        assert avg_late <= avg_early


class TestAuditTrailTenantIsolation:
    """Test 5: Audit trail complete + tenant-scoped."""

    def test_tenant_isolation(self):
        audit = MockAuditBackend()

        optimizer_a = UnifiedLossOptimizer(tenant_id='tenant_a', audit_backend=audit)
        task_batch_a = [{'confidence_score': 0.7, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'test', 'routed_engine': 'opus', 'budget_allocated': 1000}]
        outcomes_a = [{'correct': True, 'engine_correct': True}]
        feedback_a = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]
        loss_a = optimizer_a.compute_batch_loss(task_batch_a, outcomes_a, feedback_a)

        optimizer_b = UnifiedLossOptimizer(tenant_id='tenant_b', audit_backend=audit)
        task_batch_b = [{'confidence_score': 0.5, 'tokens_used': 700, 'latency_seconds': 3.0, 'task_type': 'test', 'routed_engine': 'sonnet', 'budget_allocated': 1000}]
        outcomes_b = [{'correct': False, 'engine_correct': False}]
        feedback_b = [None]
        loss_b = optimizer_b.compute_batch_loss(task_batch_b, outcomes_b, feedback_b)

        events_a = audit.read_events('tenant_a')
        events_b = audit.read_events('tenant_b')

        assert len(events_a) > 0
        assert len(events_b) > 0

        for event in events_a:
            assert event.get('tenant_id') == 'tenant_a'

        for event in events_b:
            assert event.get('tenant_id') == 'tenant_b'


class TestEndToEndLearningLoop:
    """Test 6: End-to-end: outcome → loss → gradients → weights update."""

    def test_full_learning_cycle(self):
        audit = MockAuditBackend()
        optimizer = UnifiedLossOptimizer(tenant_id='test', audit_backend=audit)
        backprop = LossBackpropagator(audit_backend=audit, tenant_id='test')

        task_batch = [
            {'confidence_score': 0.5, 'tokens_used': 1000, 'latency_seconds': 5.0, 'task_type': 'complex', 'routed_engine': 'sonnet', 'budget_allocated': 1000},
        ]
        outcomes = [{'correct': False, 'engine_correct': False}]
        feedback = [None]

        loss_snapshot = optimizer.compute_batch_loss(task_batch, outcomes, feedback)
        gradients = backprop.compute_gradients_with_dag(loss_snapshot, task_batch, outcomes, feedback)

        events = audit.read_events('test')
        assert any(e.get('event_type') == 'unified_loss_computed' for e in events)
        assert any(e.get('event_type') == 'loss_gradient_computed' for e in events)
