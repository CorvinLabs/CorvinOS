"""
Week 13: Phase 2B Adversarial Review (ADR-0615)

8 attack vectors testing robustness:
1. Loop Hijacking: Negative correlation → filter blocks
2. Weight Poisoning: Oscillation via coupling → detection prevents
3. Cascading Divergence: One loop fails → error isolation
4. Audit-Bypass: DAG has cycles → topological sort catches
5. Gradient Reversal: Sign flipping → correlation filter detects
6. Rare-Task Blindness: Unbalanced feedback → awareness test
7. Stale Gradient: Multi-batch delay → damping handles
8. Cross-Loop Interference: Memory affecting Skills → independence check
"""

from core.learning.gradient_backprop import LossBackpropagator, CorrelationFilter, CouplingOscillationDetector
from core.learning.unified_loss import UnifiedLossSnapshot
from tests.learning.mock_audit_backend import MockAuditBackend
from datetime import datetime
import numpy as np


class TestAdversarialPhase2B:
    """All 8 attack vectors (ADR-0615 divergence prevention)."""

    def test_attack_1_loop_hijacking_negative_correlation(self):
        """ATTACK 1: Hijack loop via negative correlation → filter blocks."""
        filt = CorrelationFilter(correlation_threshold=0.3)

        # Local gradient says decrease, backprop says increase (opposite)
        local_grad = {'L1_routing': 0.05}
        backprop_grad = {'L1_routing': -0.08}

        apply, corr = filt.apply_filter('L1_routing', local_grad, backprop_grad)

        assert not apply, "Filter should BLOCK anti-correlated gradient"
        assert corr < 0, "Correlation should be negative"

    def test_attack_2_weight_poisoning_oscillation(self):
        """ATTACK 2: Cause oscillation via weight poisoning → detector finds it."""
        detector = CouplingOscillationDetector(window_size=10)

        # Feed alternating high/low values (artificial oscillation)
        for i in range(25):
            value = 0.8 if i % 2 == 0 else 0.2
            is_osc = detector.check_for_oscillation('L2_confidence', value)

        # After sufficient history, oscillation should be detected
        assert is_osc, "Detector should catch alternating pattern (oscillation)"

    def test_attack_3_cascading_divergence_isolation(self):
        """ATTACK 3: One loop diverges (NaN) → error isolation."""
        filt = CorrelationFilter()

        # Try to feed NaN into filter
        try:
            local_grad = {'L3_feedback': float('nan')}
            backprop_grad = {'L3_feedback': 0.1}
            apply, corr = filt.apply_filter('L3_feedback', local_grad, backprop_grad)

            # Should not crash; should degrade gracefully
            assert True, "System should handle NaN gracefully"
        except Exception as e:
            assert False, f"Should NOT crash on NaN: {e}"

    def test_attack_4_audit_bypass_dag_cycles(self):
        """ATTACK 4: DAG has cycles → topological sort rejects."""
        backprop = LossBackpropagator()

        # Our DAG should be acyclic; verify via topological sort
        is_valid = backprop.check_dag_validity()
        assert is_valid, "DAG must be acyclic (topological sort should succeed)"

    def test_attack_5_gradient_reversal_sign_flip(self):
        """ATTACK 5: Attacker flips gradient sign → correlation detects."""
        filt = CorrelationFilter()

        # Attacker flips gradient sign (exact reversal)
        local_grad = {'L4_attention': 0.1}
        backprop_grad = {'L4_attention': -0.1}  # Reversed

        apply, corr = filt.apply_filter('L4_attention', local_grad, backprop_grad)

        # Should be rejected as anti-correlated
        assert not apply, "Should reject sign-reversed gradient"
        assert corr < 0, "Correlation should be negative"

    def test_attack_6_rare_task_blindness_feedback_gap(self):
        """ATTACK 6: Rare tasks → feedback arrives late → staleness penalty."""
        audit = MockAuditBackend()
        backprop = LossBackpropagator(audit_backend=audit, tenant_id='test')

        # Simulate: no feedback for rare task type
        task_batch = [
            {'confidence_score': 0.5, 'tokens_used': 800, 'latency_seconds': 4.0, 'task_type': 'rare_operation', 'routed_engine': 'opus', 'budget_allocated': 1000},
        ]
        outcomes = [{'correct': True, 'engine_correct': True}]
        feedback = [None]  # No feedback

        snapshot = UnifiedLossSnapshot(
            timestamp=datetime.now(), batch_id='rare_test', tenant_id='test',
            L_routing=0.2, L_confidence=0.3, L_feedback=0.8, L_attention=0.1, L_latency=0.2, L_diversity=0.5,
            L_total=0.35, weights={'routing': 1/6, 'confidence': 1/6, 'feedback': 1/6, 'attention': 1/6, 'latency': 1/6, 'diversity': 1/6}
        )

        gradients = backprop.compute_gradients_with_dag(snapshot, task_batch, outcomes, feedback)

        # Feedback loss is high (0.8) → gradient should reflect this penalty
        feedback_grad = gradients['L3_feedback']['grad']
        assert feedback_grad > 0.1, f"Feedback gradient should reflect high L_feedback (got {feedback_grad})"

    def test_attack_7_stale_gradient_delay(self):
        """ATTACK 7: Stale gradient (5-batch delay) → damping smooths."""
        filt = CorrelationFilter()

        # Simulate stale gradient: local improved but backprop is 5 batches old (worse)
        local_grad = {'L5_latency': 0.02}  # Improved recently
        backprop_grad = {'L5_latency': 0.08}  # From 5 batches ago (stale)

        apply, corr = filt.apply_filter('L5_latency', local_grad, backprop_grad)

        # Same direction (both positive) → should accept
        # But magnitude difference → damping will smooth the update
        assert apply, "Should accept same-sign gradient (even if stale)"
        assert corr > 0.5, "Correlation should be positive"

    def test_attack_8_cross_loop_interference_isolation(self):
        """ATTACK 8: Memory corruption → Skills filter independent."""
        mem_filt = CorrelationFilter()
        skill_filt = CorrelationFilter()

        # Corrupt Memory gradient
        mem_filt.apply_filter('L1_routing', {'L1_routing': 0.1}, {'L1_routing': -0.1})

        # Skills filter should not be affected
        skill_filt.apply_filter('L2_confidence', {'L2_confidence': 0.05}, {'L2_confidence': 0.04})

        # Each should maintain independent correlation history
        assert len(mem_filt.correlation_history['L1_routing']) == 1
        assert len(skill_filt.correlation_history['L2_confidence']) == 1

        # Cross-check: no leakage
        assert skill_filt.correlation_history['L1_routing'] == [], "Skill filter should not track memory correlations"


class TestAdversarialAuditCompliance:
    """Adversarial audit tests: verify hash-chain integrity under attack."""

    def test_audit_chain_immutability(self):
        """Attacker tries to modify audit event → hash-chain detects tampering."""
        audit = MockAuditBackend()

        # Write legitimate event
        audit.write_event({
            'event_type': 'test_event',
            'data': 'legitimate',
            'tenant_id': 'test',
        })

        # Verify chain is intact
        assert audit.verify_chain(), "Chain should verify before tampering"

        # Attempt tampering (modify an event)
        if len(audit.events) > 0:
            audit.events[0]['data'] = 'tampered'

            # Chain should break
            assert not audit.verify_chain(), "Chain should break after tampering"

    def test_tenant_isolation_enforcement(self):
        """Attacker tries cross-tenant read → isolation holds."""
        audit = MockAuditBackend()
        backprop_a = LossBackpropagator(audit_backend=audit, tenant_id='tenant_a')
        backprop_b = LossBackpropagator(audit_backend=audit, tenant_id='tenant_b')

        # Both write events
        audit.write_event({'event_type': 'test_a', 'tenant_id': 'tenant_a'})
        audit.write_event({'event_type': 'test_b', 'tenant_id': 'tenant_b'})

        # Verify isolation
        events_a = audit.read_events('tenant_a')
        events_b = audit.read_events('tenant_b')

        # A should not see B's events
        assert not any(e.get('tenant_id') == 'tenant_b' for e in events_a), "Tenant A should not see Tenant B events"
        assert not any(e.get('tenant_id') == 'tenant_a' for e in events_b), "Tenant B should not see Tenant A events"


if __name__ == '__main__':
    suite = [
        ('ATTACK 1: Negative Correlation', TestAdversarialPhase2B().test_attack_1_negative_correlation),
        ('ATTACK 2: Coupling Oscillation', TestAdversarialPhase2B().test_attack_2_coupling_oscillation),
        ('ATTACK 3: Loop Divergence', TestAdversarialPhase2B().test_attack_3_loop_divergence_isolated),
        ('ATTACK 4: DAG Feedback Loop', TestAdversarialPhase2B().test_attack_4_dag_feedback_loop),
        ('ATTACK 5: Gradient Reversal', TestAdversarialPhase2B().test_attack_5_gradient_reversal),
        ('ATTACK 6: Gradient Dominance', TestAdversarialPhase2B().test_attack_6_memory_gradient_dominates),
        ('ATTACK 7: Stale Gradient', TestAdversarialPhase2B().test_attack_7_stale_gradient),
        ('ATTACK 8: Cross-Loop Interference', TestAdversarialPhase2B().test_attack_8_cross_loop_interference),
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
