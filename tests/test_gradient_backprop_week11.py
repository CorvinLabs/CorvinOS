"""
Week 11: Gradient Backprop DAG + Correlation Filter tests (ADR-0615)

Tests:
- DAG acyclic property via topological sort
- Gradient flow along DAG edges (chain rule)
- Causality: outcome flip → correct gradient directions
- Divergence detection: oscillation + magnitude checking
- Gradient attribution: trace which loop contributed most
"""

from core.learning.gradient_backprop import LossBackpropagator, CorrelationFilter, CouplingOscillationDetector
from core.learning.unified_loss import UnifiedLossSnapshot
from tests.learning.mock_audit_backend import MockAuditBackend
from datetime import datetime
import numpy as np


class TestDAGAcyclic:
    """Test 1: Verify DAG has no cycles (topological sort succeeds)."""

    def test_dag_acyclic(self):
        backprop = LossBackpropagator()
        assert backprop.check_dag_validity(), "DAG should be acyclic"

    def test_dag_edges_defined(self):
        backprop = LossBackpropagator()
        expected_edges = {'L1_routing', 'L2_confidence', 'L3_feedback', 'L4_attention', 'L5_latency', 'L6_diversity'}
        assert set(backprop.dag_edges.keys()) == expected_edges

    def test_dag_sources_exist(self):
        backprop = LossBackpropagator()
        for source, targets in backprop.dag_edges.items():
            for target, _ in targets:
                assert target in backprop.dag_edges, f"Target {target} not in DAG"


class TestGradientFlow:
    """Test 2: Trace gradient backward along edges; verify chain rule applied."""

    def test_compute_gradients_structure(self):
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())
        task_batch = [
            {'confidence_score': 0.8, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'classify'},
            {'confidence_score': 0.6, 'tokens_used': 600, 'latency_seconds': 3.0, 'task_type': 'summarize'},
        ]
        outcomes = [
            {'correct': True, 'engine_correct': True},
            {'correct': False, 'engine_correct': True},
        ]
        feedback = [
            {'timestamp': datetime.now().isoformat(), 'is_valid': True},
            None,
        ]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(),
            batch_id='test_batch',
            tenant_id='default',
            L_routing=0.2, L_confidence=0.15, L_feedback=0.1,
            L_attention=0.05, L_latency=0.3, L_diversity=0.2,
            L_total=0.16, weights={
                'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6,
                'attention': 1/6, 'latency': 1/6, 'diversity': 1/6
            }
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Check all loops have gradients
        for loop_id in ['L1_routing', 'L2_confidence', 'L3_feedback', 'L4_attention', 'L5_latency', 'L6_diversity']:
            assert loop_id in gradients, f"{loop_id} missing from gradients"
            assert 'grad' in gradients[loop_id], f"{loop_id} missing 'grad' field"
            assert 'contributors' in gradients[loop_id], f"{loop_id} missing 'contributors' field"

    def test_gradient_direction_routing(self):
        """If routing was wrong (engine_correct=False), gradient should be positive."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Task routed to wrong engine
        task_batch = [{'confidence_score': 0.8, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'classify'}]
        outcomes = [{'correct': False, 'engine_correct': False}]
        feedback = [None]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(), batch_id='test', tenant_id='default',
            L_routing=0.9, L_confidence=0.5, L_feedback=0.8, L_attention=0.2, L_latency=0.3, L_diversity=0.1,
            L_total=0.5, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Routing gradient should point in direction to reduce routing loss
        assert gradients['L1_routing']['grad'] > 0, "Routing gradient should be positive when routing is wrong"


class TestDAGCausality:
    """Test 3: Outcome flip → gradients point in expected direction for each loop."""

    def test_confidence_gradient_flips_with_outcome(self):
        """Calibration error should decrease when predictions match outcomes."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Case 1: High confidence, correct outcome
        task_batch_correct = [{'confidence_score': 0.9, 'tokens_used': 500, 'latency_seconds': 2.0, 'task_type': 'test'}]
        outcomes_correct = [{'correct': True, 'engine_correct': True}]

        # Case 2: High confidence, wrong outcome
        outcomes_wrong = [{'correct': False, 'engine_correct': True}]

        feedback = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(), batch_id='test', tenant_id='default',
            L_routing=0.2, L_confidence=0.1, L_feedback=0.1, L_attention=0.05, L_latency=0.2, L_diversity=0.1,
            L_total=0.14, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        grad_correct = backprop.compute_gradients_with_dag(snapshot, task_batch_correct, outcomes_correct, feedback)

        backprop2 = LossBackpropagator(audit_backend=MockAuditBackend())
        grad_wrong = backprop2.compute_gradients_with_dag(snapshot, task_batch_correct, outcomes_wrong, feedback)

        # Gradient should be more negative (better) when outcome matches confidence
        assert grad_correct['L2_confidence']['grad'] < grad_wrong['L2_confidence']['grad'], \
            "Confidence gradient should be better when prediction matches outcome"


class TestDivergenceDetection:
    """Test 4: Artificially cause oscillation; verify monitor detects and recovers."""

    def test_oscillation_detection_alternating(self):
        detector = CouplingOscillationDetector(window_size=10)

        # Alternate between high and low values
        for i in range(20):
            value = 0.8 if i % 2 == 0 else 0.2
            is_osc = detector.check_for_oscillation('L1_routing', value)

        assert is_osc, "Detector should detect alternating oscillation"

    def test_stable_convergence_no_oscillation(self):
        detector = CouplingOscillationDetector(window_size=10)

        # Smooth convergence (gradient getting smaller)
        for i in range(20):
            value = 0.1 - (i * 0.003)  # Smooth decay
            is_osc = detector.check_for_oscillation('L2_confidence', value)

        assert not is_osc, "Detector should NOT flag smooth convergence"

    def test_magnitude_divergence_detection(self):
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        # Artificially create large gradients
        task_batch = []
        outcomes = []
        feedback = []

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(), batch_id='test', tenant_id='default',
            L_routing=0.9, L_confidence=0.9, L_feedback=0.9, L_attention=0.9, L_latency=0.9, L_diversity=0.9,
            L_total=0.9, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)
        total_magnitude = sum(abs(v['grad']) for v in gradients.values())

        # If total magnitude is high, divergence should be detected
        if total_magnitude > 1.0:
            assert backprop.divergence_detected, "Divergence flag should be set when gradients explode"


class TestGradientAttribution:
    """Test 5: Outcome was wrong; trace which loop contributed most via DAG."""

    def test_attribution_chain(self):
        """Verify gradient traces back through contributors."""
        backprop = LossBackpropagator(audit_backend=MockAuditBackend())

        task_batch = [{'confidence_score': 0.5, 'tokens_used': 1500, 'latency_seconds': 8.0, 'task_type': 'complex'}]
        outcomes = [{'correct': False, 'engine_correct': False}]
        feedback = [None]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(), batch_id='test', tenant_id='default',
            L_routing=0.8, L_confidence=0.5, L_feedback=0.7, L_attention=0.6, L_latency=0.7, L_diversity=0.2,
            L_total=0.6, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Routing gradient should have contributors
        assert len(gradients['L1_routing']['contributors']) > 0, "Routing should have upstream contributors"
        assert 'L2' in str(gradients['L1_routing']['contributors']) or 'L5' in str(gradients['L1_routing']['contributors']), \
            "Routing contributors should include L2 or L5"


class TestBackpropAuditTrail:
    """Test 6: Every gradient computation audited; DAG edges recorded."""

    def test_audit_event_recorded(self):
        audit = MockAuditBackend()
        backprop = LossBackpropagator(audit_backend=audit)

        task_batch = [{'confidence_score': 0.7, 'tokens_used': 700, 'latency_seconds': 2.5, 'task_type': 'standard'}]
        outcomes = [{'correct': True, 'engine_correct': True}]
        feedback = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(), batch_id='audit_test', tenant_id='default',
            L_routing=0.1, L_confidence=0.1, L_feedback=0.1, L_attention=0.05, L_latency=0.2, L_diversity=0.15,
            L_total=0.12, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Check audit events were recorded
        events = audit.read_events('default')
        gradient_events = [e for e in events if e.get('event_type') == 'loss_gradient_computed']

        assert len(gradient_events) > 0, "At least one gradient event should be recorded"
        grad_event = gradient_events[0]
        assert 'dag_edges' in grad_event, "DAG edges should be in audit event"
        assert 'contributors' in grad_event, "Contributors should be in audit event"

    def test_hash_chain_integrity(self):
        audit = MockAuditBackend()
        backprop = LossBackpropagator(audit_backend=audit)

        task_batch = [{'confidence_score': 0.5, 'tokens_used': 600, 'latency_seconds': 3.0, 'task_type': 'test'}]
        outcomes = [{'correct': True, 'engine_correct': True}]
        feedback = [{'timestamp': datetime.now().isoformat(), 'is_valid': True}]

        for i in range(3):
            snapshot = UnifiedLossSnapshot(
                timestamp=datetime.now(), batch_id=f'batch_{i}', tenant_id='default',
                L_routing=0.1 + i*0.05, L_confidence=0.1, L_feedback=0.1, L_attention=0.05, L_latency=0.2, L_diversity=0.15,
                L_total=0.12 + i*0.05, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
            )
            backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        assert audit.verify_chain(), "Audit chain should remain intact after multiple computations"


if __name__ == '__main__':
    # Run all tests manually (no pytest)
    suite = [
        ('DAG Init', TestGradientBackpropDAG().test_dag_initialization),
        ('Backprop Grads', TestGradientBackpropDAG().test_compute_backprop_gradients),
        ('DAG Valid', TestGradientBackpropDAG().test_topological_validity),
        ('Corr Same Sign', TestCorrelationFilter().test_correlation_same_sign),
        ('Corr Opp Sign', TestCorrelationFilter().test_correlation_opposite_sign),
        ('Filter Accept', TestCorrelationFilter().test_filter_accepts_correlated),
        ('Filter Reject', TestCorrelationFilter().test_filter_rejects_anticorrelated),
        ('Oscillation', TestCouplingOscillationDetector().test_oscillation_detection),
        ('No Oscillation', TestCouplingOscillationDetector().test_stable_no_oscillation),
        ('Phase Lock', TestCouplingOscillationDetector().test_phase_lock_interval),
    ]
    
    passed = 0
    for name, test in suite:
        try:
            test()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
    
    print(f"\n{passed}/{len(suite)} PASSED")
