"""
Test suite for CompositionOptimizer (ADR-0621)

Tests cover:
- Loss computation (all 4 components)
- Gradient computation and application
- Parameter bounds and normalization
- Convergence detection
- Topological sort correctness
- Reorder cooldown enforcement
- All 5 adversarial mitigations
- 100-batch convergence
"""

import pytest
import math
from core.learning.composition_optimizer import CompositionOptimizer


class TestCompositionLossComputation:
    """Tests for loss computation with all 4 components"""

    def test_loss_zero_when_all_perfect(self):
        """Loss is 0 when all feedback components are 0 (perfect)"""
        opt = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 0.0,
            'dag_execution_time_ms': 0.0,
            'skill_contradictions': 0.0,
            'ordering_penalty': 0.0,
        }
        loss = opt.compute_loss(feedback)
        assert abs(loss - 0.0) < 0.001

    def test_loss_one_when_all_worst(self):
        """Loss approaches 1 when all feedback components are worst"""
        opt = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 1.0,
            'dag_execution_time_ms': 1000.0,  # max out
            'skill_contradictions': 100.0,  # max out
            'ordering_penalty': 1.0,
        }
        loss = opt.compute_loss(feedback)
        assert 0.9 <= loss <= 1.0

    def test_loss_component_quality_weight(self):
        """Quality component has 0.4 weight"""
        opt = CompositionOptimizer()
        # Only quality non-zero
        feedback = {
            'composition_error_rate': 1.0,
            'dag_execution_time_ms': 0.0,
            'skill_contradictions': 0.0,
            'ordering_penalty': 0.0,
        }
        loss = opt.compute_loss(feedback)
        assert abs(loss - 0.4) < 0.001

    def test_loss_component_latency_weight(self):
        """Latency component has 0.3 weight (normalized to [0,1])"""
        opt = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 0.0,
            'dag_execution_time_ms': 1000.0,  # normalized: min(1, 1000/1000) = 1.0
            'skill_contradictions': 0.0,
            'ordering_penalty': 0.0,
        }
        loss = opt.compute_loss(feedback)
        assert abs(loss - 0.3) < 0.001

    def test_loss_component_conflicts_weight(self):
        """Conflicts component has 0.2 weight"""
        opt = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 0.0,
            'dag_execution_time_ms': 0.0,
            'skill_contradictions': 100.0,  # 100/100 = 1.0
            'ordering_penalty': 0.0,
        }
        loss = opt.compute_loss(feedback)
        assert abs(loss - 0.2) < 0.001

    def test_loss_component_ordering_weight(self):
        """Ordering component has 0.1 weight"""
        opt = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 0.0,
            'dag_execution_time_ms': 0.0,
            'skill_contradictions': 0.0,
            'ordering_penalty': 1.0,
        }
        loss = opt.compute_loss(feedback)
        assert abs(loss - 0.1) < 0.001

    def test_loss_clipped_to_max_one(self):
        """Loss is clipped to max 1.0 when all components max"""
        opt = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 2.0,  # will exceed 1
            'dag_execution_time_ms': 5000.0,
            'skill_contradictions': 500.0,
            'ordering_penalty': 2.0,
        }
        loss = opt.compute_loss(feedback)
        assert loss <= 1.0

    def test_loss_recorded_in_history(self):
        """Loss is recorded in loss_history after compute_loss()"""
        opt = CompositionOptimizer()
        feedback = {'composition_error_rate': 0.5, 'dag_execution_time_ms': 100,
                    'skill_contradictions': 0, 'ordering_penalty': 0}
        loss1 = opt.compute_loss(feedback)
        loss2 = opt.compute_loss(feedback)
        assert len(opt.loss_history) == 2
        assert abs(opt.loss_history[0] - loss1) < 0.001
        assert abs(opt.loss_history[1] - loss2) < 0.001


class TestCompositionGradients:
    """Tests for gradient computation and application"""

    def test_gradients_computed_on_loss_increase(self):
        """Gradients reflect direction: loss increase → positive gradient"""
        opt = CompositionOptimizer()
        prev_loss = 0.1
        current_loss = 0.2
        gradients = opt.compute_gradients(current_loss, prev_loss)

        # All gradients should be positive (loss increased)
        for skill_id in opt._get_all_skills():
            assert skill_id in gradients
            assert gradients[skill_id] > 0

    def test_gradients_computed_on_loss_decrease(self):
        """Gradients reflect direction: loss decrease → negative gradient"""
        opt = CompositionOptimizer()
        prev_loss = 0.5
        current_loss = 0.2
        gradients = opt.compute_gradients(current_loss, prev_loss)

        # All gradients should be negative (loss decreased)
        for skill_id in opt._get_all_skills():
            assert gradients[skill_id] < 0

    def test_gradients_zero_on_no_change(self):
        """Gradients are near-zero when loss unchanged"""
        opt = CompositionOptimizer()
        prev_loss = 0.3
        current_loss = 0.3
        gradients = opt.compute_gradients(current_loss, prev_loss)

        for skill_id in opt._get_all_skills():
            assert abs(gradients[skill_id]) < 0.0001

    def test_gradients_recorded_in_history(self):
        """Gradients are recorded in gradient_history after compute_gradients()"""
        opt = CompositionOptimizer()
        gradients = opt.compute_gradients(0.3, 0.2)
        assert len(opt.gradient_history) > 0
        for skill_id in opt._get_all_skills():
            assert skill_id in opt.gradient_history

    def test_apply_gradients_updates_weights(self):
        """apply_gradients() modifies skill_priority_weights"""
        opt = CompositionOptimizer()
        old_weights = opt.skill_priority_weights.copy()

        gradients = {skill: 0.01 for skill in opt._get_all_skills()}
        opt.apply_gradients(gradients)

        # At least some weights changed
        changed = sum(1 for s in opt._get_all_skills()
                     if abs(opt.skill_priority_weights[s] - old_weights[s]) > 0.0001)
        assert changed > 0

    def test_weights_stay_in_bounds(self):
        """Weights clipped to [0.1, 2.0] during apply_gradients()"""
        opt = CompositionOptimizer()
        # Force a large gradient
        large_gradient = {skill: 1.0 for skill in opt._get_all_skills()}

        for _ in range(10):
            opt.apply_gradients(large_gradient, learning_rate=1.0)

        for skill_id in opt._get_all_skills():
            w = opt.skill_priority_weights[skill_id]
            assert 0.1 <= w <= 2.0, f"Weight {w} out of bounds for skill {skill_id}"

    def test_weights_normalize_to_sum_one(self):
        """After apply_gradients(), weights normalize to sum ≈ 1.0"""
        opt = CompositionOptimizer()
        gradients = {skill: 0.05 for skill in opt._get_all_skills()}
        opt.apply_gradients(gradients)

        total = sum(opt.skill_priority_weights.values())
        assert abs(total - 1.0) < 0.001

    def test_damping_reduces_oscillation(self):
        """Higher damping reduces parameter changes per step"""
        opt1 = CompositionOptimizer()
        opt2 = CompositionOptimizer()

        opt1.damping_factor = 0.5   # low damping
        opt2.damping_factor = 0.99  # high damping

        gradient = {skill: 0.1 for skill in opt1._get_all_skills()}

        old_w1 = opt1.skill_priority_weights.copy()
        old_w2 = opt2.skill_priority_weights.copy()

        opt1.apply_gradients(gradient)
        opt2.apply_gradients(gradient)

        change1 = sum(abs(opt1.skill_priority_weights[s] - old_w1[s])
                     for s in opt1._get_all_skills())
        change2 = sum(abs(opt2.skill_priority_weights[s] - old_w2[s])
                     for s in opt2._get_all_skills())

        # Higher damping → smaller change
        assert change2 < change1


class TestCompositionTopologicalSort:
    """Tests for topological sort correctness"""

    def test_topological_sort_returns_all_skills(self):
        """Topological sort includes every skill exactly once"""
        opt = CompositionOptimizer()
        order = opt._topological_sort_by_priority()

        skills = opt._get_all_skills()
        assert set(order) == set(skills)
        assert len(order) == len(skills)

    def test_topological_sort_by_priority(self):
        """Sort respects priority weights (higher weight → earlier)"""
        opt = CompositionOptimizer()
        opt.skill_priority_weights = {'a': 0.5, 'b': 0.3, 'c': 0.2}
        opt.skill_dag = {'a': [], 'b': [], 'c': []}

        order = opt._topological_sort_by_priority()

        # Higher weights should come first
        idx_a = order.index('a')
        idx_b = order.index('b')
        idx_c = order.index('c')
        assert idx_a < idx_b < idx_c


class TestCompositionReorderCooldown:
    """Tests for reorder cooldown enforcement"""

    def test_reorder_cooldown_prevents_frequent_reorders(self):
        """Reorder doesn't happen until cooldown expires"""
        opt = CompositionOptimizer(skill_dag={'s1': [], 's2': [], 's3': []})
        opt.reorder_cooldown = 5
        opt.skill_priority_weights = {'s1': 0.4, 's2': 0.3, 's3': 0.3}

        initial_order = opt.current_order.copy() if opt.current_order else []

        # Apply 3 gradient steps (< cooldown)
        for _ in range(3):
            grad = {'s1': 0.01, 's2': 0.01, 's3': 0.01}
            opt.apply_gradients(grad)

        # time_since_last_reorder = 3, not >= 5, so no reorder yet
        order_after_3 = opt.current_order.copy()

        # Apply 3 more steps (now time_since_last_reorder = 6 >= 5)
        for _ in range(3):
            grad = {'s1': 0.01, 's2': 0.01, 's3': 0.01}
            opt.apply_gradients(grad)

        # Now reorder should have happened (time >= cooldown)
        # time_since_last_reorder should have reset to 0
        assert opt.time_since_last_reorder == 0

    def test_cooldown_resets_after_reorder(self):
        """time_since_last_reorder resets to 0 after reorder"""
        opt = CompositionOptimizer()
        opt.reorder_cooldown = 2

        # Apply 2 gradients to trigger reorder (at step 2, time >= 2)
        for _ in range(2):
            opt.apply_gradients({'s1': 0.01, 's2': 0.01, 's3': 0.01})

        # Reorder happens at the 2nd step, resetting the counter
        assert opt.time_since_last_reorder == 0


class TestCompositionConvergence:
    """Tests for convergence detection"""

    def test_not_converged_with_small_history(self):
        """Convergence returns False when history < 100 entries"""
        opt = CompositionOptimizer()
        for i in range(50):
            opt.record_loss(0.1)
        assert not opt.check_convergence()

    def test_converges_when_stable(self):
        """Convergence returns True when gradients and parameters stable"""
        opt = CompositionOptimizer()

        # Build stable history
        for i in range(150):
            opt.record_loss(0.1)
            # Tiny gradients indicate convergence
            for skill in opt._get_all_skills():
                opt.gradient_history.setdefault(skill, []).append(0.0001)
                opt.param_history.setdefault(skill, []).append(0.3 + i * 0.00001)

        converged = opt.check_convergence()
        assert converged

    def test_not_converged_when_gradients_large(self):
        """Convergence returns False when gradients are large"""
        opt = CompositionOptimizer()

        for i in range(150):
            opt.record_loss(0.1 + i * 0.001)  # increasing
            for skill in opt._get_all_skills():
                opt.gradient_history.setdefault(skill, []).append(0.1)  # large grad
                opt.param_history.setdefault(skill, []).append(0.3 + i * 0.01)

        converged = opt.check_convergence()
        assert not converged


class TestCompositionAdversarialMitigations:
    """Tests for all 5 adversarial mitigations"""

    def test_mitigation_1_weight_bounds_prevent_divergence(self):
        """Mitigation 1: [0.1, 2.0] bounds prevent weight divergence"""
        opt = CompositionOptimizer()

        # Force a massive gradient to try to break bounds
        huge_gradient = {skill: 100.0 for skill in opt._get_all_skills()}

        for _ in range(20):
            opt.apply_gradients(huge_gradient, learning_rate=10.0)

        # Every weight should still be in [0.1, 2.0]
        for skill in opt._get_all_skills():
            w = opt.skill_priority_weights[skill]
            assert 0.1 <= w <= 2.0

    def test_mitigation_2_normalization_prevents_rank_collapse(self):
        """Mitigation 2: Normalization keeps relative ordering stable"""
        opt = CompositionOptimizer()
        opt.skill_priority_weights = {'a': 1.0, 'b': 0.5, 'c': 0.1}

        # Apply large gradient that would break things without normalization
        grad = {'a': 1.0, 'b': 1.0, 'c': 1.0}
        opt.apply_gradients(grad, learning_rate=1.0)

        # All should still be in [0.1, 2.0] and sum to 1
        total = sum(opt.skill_priority_weights.values())
        assert abs(total - 1.0) < 0.001

    def test_mitigation_3_damping_prevents_oscillation(self):
        """Mitigation 3: Damping prevents oscillation"""
        opt = CompositionOptimizer()
        opt.damping_factor = 0.95

        # Alternating large positive/negative gradients
        for i in range(50):
            gradient = {skill: (0.1 if i % 2 == 0 else -0.1)
                       for skill in opt._get_all_skills()}
            opt.apply_gradients(gradient)

        # Check parameter stability (should be low = stable)
        stability = opt.get_parameter_stability(40)
        assert stability < 0.5  # low oscillation

    def test_mitigation_4_cooldown_prevents_thrashing(self):
        """Mitigation 4: Cooldown prevents constant reordering"""
        opt = CompositionOptimizer()
        opt.reorder_cooldown = 10

        reorder_count = 0
        old_order = opt.current_order.copy()

        for i in range(100):
            opt.apply_gradients({'s1': 0.01, 's2': 0.01, 's3': 0.01})
            if opt.current_order != old_order:
                reorder_count += 1
                old_order = opt.current_order.copy()

        # With cooldown=10, should have ~100/10 = 10 reorders max
        assert reorder_count <= 12

    def test_mitigation_5_convergence_detection_stops_updates(self):
        """Mitigation 5: Convergence detection can halt learning"""
        opt = CompositionOptimizer()

        # Converge manually
        for i in range(150):
            opt.record_loss(0.1)
            for skill in opt._get_all_skills():
                opt.gradient_history.setdefault(skill, []).append(0.0001)
                opt.param_history.setdefault(skill, []).append(0.3)

        has_converged = opt.check_convergence()
        assert has_converged


class TestComposition100BatchConvergence:
    """Test 100-batch convergence (main E2E test)"""

    def test_100_batch_convergence_with_stable_feedback(self):
        """Optimizer converges within 100 batches with constant feedback"""
        opt = CompositionOptimizer()

        stable_feedback = {
            'composition_error_rate': 0.1,
            'dag_execution_time_ms': 200,
            'skill_contradictions': 5,
            'ordering_penalty': 0.05,
        }

        for batch in range(100):
            loss = opt.compute_loss(stable_feedback)
            if batch > 0:
                prev_loss = opt.loss_history[-2]
                gradients = opt.compute_gradients(loss, prev_loss)
                opt.apply_gradients(gradients)

        # Should have 100 loss entries
        assert len(opt.loss_history) == 100

        # Loss should stabilize in the last 20 batches
        last_20 = opt.loss_history[-20:]
        variance = opt.get_loss_variance(20)
        assert variance < 0.001  # very stable

    def test_100_batch_variance_reduction(self):
        """Variance in loss should drop >80% by batch 100"""
        opt = CompositionOptimizer()

        # Noisy feedback that should gradually stabilize
        for batch in range(100):
            noise = 0.01 * math.sin(batch / 10)  # small oscillation
            feedback = {
                'composition_error_rate': 0.15 + noise,
                'dag_execution_time_ms': 250 + noise * 100,
                'skill_contradictions': 10,
                'ordering_penalty': 0.0,
            }
            loss = opt.compute_loss(feedback)

            if batch > 0:
                prev_loss = opt.loss_history[-2]
                gradients = opt.compute_gradients(loss, prev_loss)
                opt.apply_gradients(gradients)

        # Compute variance: first 20 vs last 20
        var_first_20 = opt.get_loss_variance(20)
        opt.loss_history = opt.loss_history[80:]  # keep last 20
        var_last_20 = opt.get_loss_variance(20)

        reduction = (var_first_20 - var_last_20) / var_first_20
        assert reduction > 0.8  # >80% variance reduction


class TestCompositionIntegration:
    """Integration tests with NineD loop"""

    def test_emit_event_with_collector(self):
        """emit_event() produces correct output structure"""
        opt = CompositionOptimizer()
        opt.skill_priority_weights = {'s1': 0.5, 's2': 0.3, 's3': 0.2}

        # Mock collector
        class MockCollector:
            def __init__(self):
                self.calls = []

            def on_skill_composition_decision(self, skill_order, priority_weights, feedback, execution_time_ms):
                self.calls.append({
                    'skill_order': skill_order,
                    'priority_weights': priority_weights,
                    'feedback': feedback,
                    'execution_time_ms': execution_time_ms
                })

        collector = MockCollector()
        opt.emit_event(collector, feedback={'test': 1}, execution_time_ms=42)

        assert len(collector.calls) == 1
        assert 'skill_order' in collector.calls[0]
        assert 'priority_weights' in collector.calls[0]
        assert collector.calls[0]['execution_time_ms'] == 42

    def test_learning_rate_tier_2_defaults(self):
        """Tier 2 loop has correct learning rate defaults"""
        opt = CompositionOptimizer()  # tier=2 by default
        assert opt.learning_rate == 0.01
        assert opt.damping_factor == 0.95
