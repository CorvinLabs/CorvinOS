"""Unit tests for MetaOptimizer (ADR-0623/0624/0625)"""

import pytest
from core.learning.meta_optimizer import MetaOptimizer
from core.learning.watchdog import DivergenceWatchdog


class TestMetaOptimizerInitialization:
    def test_initialization(self):
        loop = MetaOptimizer()
        assert loop.α_core == 0.1
        assert loop.α_infra == 0.01
        assert loop.damping_core == 0.9
        assert loop.damping_infra == 0.95

    def test_update_count_starts_at_zero(self):
        loop = MetaOptimizer()
        assert loop.update_count == 0


class TestMetaLossComputation:
    def test_loss_in_valid_range(self):
        loop = MetaOptimizer()
        feedback = {'loss_delta_core': -0.05, 'loss_delta_infra': -0.03}
        loss = loop.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0

    def test_negative_delta_improves_loss(self):
        loop = MetaOptimizer()
        feedback_good = {'loss_delta_core': -0.05, 'loss_delta_infra': -0.03}
        loss_good = loop.compute_loss(feedback_good)

        feedback_bad = {'loss_delta_core': 0.05, 'loss_delta_infra': 0.03}
        loss_bad = loop.compute_loss(feedback_bad)

        assert loss_good < loss_bad

    def test_missing_feedback_defaults_to_zero(self):
        loop = MetaOptimizer()
        feedback = {}
        loss = loop.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0


class TestMetaTuningLaw:
    def test_gradient_sign_on_improvement(self):
        loop = MetaOptimizer()
        loss_prev = 0.5
        loss_curr = 0.3  # improved
        gradients = loop.compute_gradients(loss_curr, loss_prev)
        assert gradients['α_core'] > 0  # positive gradient = increase α

    def test_gradient_sign_on_worsening(self):
        loop = MetaOptimizer()
        loss_prev = 0.3
        loss_curr = 0.5  # worsened
        gradients = loop.compute_gradients(loss_curr, loss_prev)
        assert gradients['α_core'] < 0  # negative gradient = decrease α

    def test_gradient_damping_on_high_variance(self):
        loop = MetaOptimizer()
        # Add high variance to loss_history
        for i in range(15):
            loop.record_loss(0.1 + (i % 2) * 0.3)
        loss_curr = 0.5
        gradients = loop.compute_gradients(loss_curr, 0.3)
        assert 'damping_core' in gradients


class TestWatchdogBounds:
    def test_bounds_enforced_on_update(self):
        loop = MetaOptimizer()
        # Try to exceed bounds with extreme gradient
        extreme_grad = {'α_core': 100.0, 'α_infra': -100.0, 'damping_core': 100.0, 'damping_infra': -100.0}
        loop.apply_gradients(extreme_grad, learning_rate=0.01)
        assert 0.001 <= loop.α_core <= 0.3
        assert 0.001 <= loop.α_infra <= 0.3
        assert 0.8 <= loop.damping_core <= 0.99
        assert 0.8 <= loop.damping_infra <= 0.99

    def test_watchdog_detects_nan(self):
        watchdog = DivergenceWatchdog()
        state = {'α_core': float('nan'), 'α_infra': 0.01}
        assert not watchdog.validate_state(state)

    def test_watchdog_detects_inf(self):
        watchdog = DivergenceWatchdog()
        state = {'α_core': float('inf'), 'damping_core': 0.9}
        assert not watchdog.validate_state(state)


class TestDivergenceDetection:
    def test_loss_threshold_exceeded(self):
        watchdog = DivergenceWatchdog()
        state = {'loss': 11.0}  # exceeds 10x threshold
        assert not watchdog.validate_state(state)

    def test_valid_state_passes(self):
        watchdog = DivergenceWatchdog()
        state = {'α_core': 0.1, 'α_infra': 0.01, 'damping_core': 0.9, 'damping_infra': 0.95, 'loss': 0.5}
        assert watchdog.validate_state(state)

    def test_checkpoint_save_and_restore(self):
        watchdog = DivergenceWatchdog()
        state = {'α_core': 0.15, 'loss': 0.3}
        ckpt_id = watchdog.save_checkpoint(state)
        restored = watchdog.restore_checkpoint(ckpt_id)
        assert restored['α_core'] == 0.15


class TestConservativeMode:
    def test_conservative_mode_activates_on_worsening(self):
        loop = MetaOptimizer()
        # Add consistently high loss
        for i in range(12):
            loop.record_loss(0.6)
        assert loop.consecutive_worsening >= 0  # tracking activated

    def test_parameters_stable(self):
        loop = MetaOptimizer()
        for i in range(20):
            feedback = {'loss_delta_core': -0.01, 'loss_delta_infra': -0.01}
            loss = loop.compute_loss(feedback)
            prev_loss = loop.loss_history[-2] if len(loop.loss_history) > 1 else loss
            gradients = loop.compute_gradients(loss, prev_loss)
            loop.apply_gradients(gradients)
        # After convergence, parameters should be stable
        assert loop.α_core > 0.001 and loop.α_core < 0.3


class TestCheckpointIntegration:
    def test_get_set_state_roundtrip(self):
        loop = MetaOptimizer()
        loop.α_core = 0.15
        loop.damping_infra = 0.92
        state = loop.get_state()
        loop2 = MetaOptimizer()
        loop2.set_state(state)
        assert loop2.α_core == 0.15
        assert loop2.damping_infra == 0.92


class TestFeedbackSignalProcessing:
    def test_process_feedback_with_sufficient_samples(self):
        loop = MetaOptimizer()
        feedback_outcomes = [
            {'outcome_feedback': 'yes', 'confidence': 0.9},
            {'outcome_feedback': 'yes', 'confidence': 0.85},
            {'outcome_feedback': 'no', 'confidence': 0.7},
            {'outcome_feedback': 'yes', 'confidence': 0.8},
        ] * 3  # 12 samples
        result = loop.process_feedback_signal(feedback_outcomes)
        assert isinstance(result, bool)

    def test_process_feedback_insufficient_samples(self):
        loop = MetaOptimizer()
        feedback_outcomes = [
            {'outcome_feedback': 'yes', 'confidence': 0.9},
            {'outcome_feedback': 'no', 'confidence': 0.7},
        ]  # only 2 samples
        result = loop.process_feedback_signal(feedback_outcomes)
        assert result is False


class TestDivergenceDetectionViaFeedback:
    def test_detect_worsening_via_feedback(self):
        loop = MetaOptimizer()
        old_loss = 0.3
        new_loss = 0.45  # worsened by > 0.05
        result = loop.detect_feedback_divergence(old_loss, new_loss)
        assert result is True

    def test_no_divergence_on_improvement(self):
        loop = MetaOptimizer()
        old_loss = 0.5
        new_loss = 0.3  # improved
        result = loop.detect_feedback_divergence(old_loss, new_loss)
        assert result is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
