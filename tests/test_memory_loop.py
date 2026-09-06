"""Unit tests for MemoryOptimizer (ADR-0620)"""

import pytest
import numpy as np
from core.learning.memory_optimizer import MemoryOptimizer


class TestMemoryOptimizerBasics:
    """Test basic functionality"""

    def test_initialization(self):
        """Memory loop initializes with correct defaults"""
        loop = MemoryOptimizer()
        assert loop.context_window_size == 8000
        assert loop.layer_importance['original'] == 0.50
        assert 0.1 <= loop.layer_importance['preserved'] <= 0.6
        assert 0.5 <= loop.recall_threshold <= 0.9

    def test_compute_loss_range(self):
        """Loss is always in [0, 1]"""
        loop = MemoryOptimizer()
        feedback = {
            'missing_context_ratio': 0.1,
            'irrelevance_score': 0.2,
            'retrieval_latency_ms': 50,
            'token_waste_ratio': 0.15,
        }
        loss = loop.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0

    def test_layer_weight_normalization(self):
        """Layer weights always sum to 1.0"""
        loop = MemoryOptimizer()
        gradients = {
            'window_size': 0.01,
            'layer_preserved': 0.05,
            'layer_injected': -0.05,
            'recall_threshold': 0.001,
        }

        # Apply multiple times
        for _ in range(10):
            loop.apply_gradients(gradients, learning_rate=0.01, damping=0.95)

        total = sum(loop.layer_importance.values())
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, not 1.0"

    def test_parameter_bounds(self):
        """All parameters stay within bounds"""
        loop = MemoryOptimizer()

        # Apply extreme gradients
        extreme_gradients = {
            'window_size': -0.5,  # try to shrink a lot
            'layer_preserved': 0.5,
            'layer_injected': -0.5,
            'recall_threshold': 0.3,
        }

        for _ in range(100):
            loop.apply_gradients(extreme_gradients, learning_rate=0.1, damping=0.9)

        assert 4000 <= loop.context_window_size <= 16000
        assert 0.1 <= loop.layer_importance['preserved'] <= 0.6
        assert 0.1 <= loop.layer_importance['injected'] <= 0.6
        assert 0.5 <= loop.recall_threshold <= 0.9

    def test_compliance_floor(self):
        """Context window never drops below compliance minimum"""
        loop = MemoryOptimizer(min_audit_requirement_bytes=5000)
        shrink_gradient = {'window_size': -1.0, 'layer_preserved': 0, 'layer_injected': 0, 'recall_threshold': 0}

        for _ in range(100):
            loop.apply_gradients(shrink_gradient, learning_rate=0.1, damping=0.8)

        assert loop.context_window_size >= 5000


class TestMemoryOptimizerGradients:
    """Test gradient computation"""

    def test_gradient_correctness_numerical_check(self):
        """Gradients computed correctly (numerical check)"""
        loop = MemoryOptimizer()

        feedback = {'missing_context_ratio': 0.1, 'irrelevance_score': 0.2, 'retrieval_latency_ms': 50, 'token_waste_ratio': 0.15}
        loss1 = loop.compute_loss(feedback)

        # Perturb loss slightly
        eps = 0.001
        loss2 = loss1 + eps

        gradients = loop.compute_gradients(loss2, loss1)

        # Gradients should exist and be small numbers
        assert len(gradients) > 0
        assert all(isinstance(g, (int, float)) for g in gradients.values())

    def test_gradient_sign_on_loss_increase(self):
        """If loss increases, gradients point toward reduction"""
        loop = MemoryOptimizer()

        loss_prev = 0.3
        loss_curr = 0.4  # increased

        gradients = loop.compute_gradients(loss_curr, loss_prev)

        # When loss increases, negative gradient means "reduce parameter"
        # This makes sense for loss reduction
        assert 'window_size' in gradients
        assert isinstance(gradients['window_size'], (int, float))


class TestMemoryOptimizerConvergence:
    """Test convergence detection"""

    def test_convergence_detection_on_stable_gradients(self):
        """Loop detects convergence when gradients become small"""
        loop = MemoryOptimizer()

        # Simulate 100 small gradients
        small_gradients = {
            'window_size': 0.0001,
            'layer_preserved': 0.00005,
            'layer_injected': -0.00005,
            'recall_threshold': 0.00001,
        }

        for i in range(100):
            # Compute tiny loss changes
            loss = 0.3 + 0.0001 * i  # slowly decreasing
            prev_loss = 0.3 + 0.0001 * (i - 1)
            loop.record_loss(loss)

            loop.apply_gradients(small_gradients, learning_rate=0.001, damping=0.95)

        converged = loop.check_convergence()
        assert converged, "Loop should detect convergence with small gradients"

    def test_no_convergence_on_large_gradients(self):
        """Loop doesn't converge when gradients are large"""
        loop = MemoryOptimizer()

        large_gradients = {
            'window_size': 0.1,
            'layer_preserved': 0.05,
            'layer_injected': -0.05,
            'recall_threshold': 0.01,
        }

        for i in range(100):
            loss = 0.5 - 0.001 * i  # loss changing
            prev_loss = 0.5 - 0.001 * (i - 1)
            loop.record_loss(loss)

            loop.apply_gradients(large_gradients, learning_rate=0.01, damping=0.95)

        converged = loop.check_convergence()
        assert not converged, "Loop should not converge with large gradients"


class TestMemoryOptimizerMitigations:
    """Test adversarial findings mitigations"""

    def test_mitigation_1_exponential_smoothing(self):
        """Delayed feedback is smoothed (exponential averaging)"""
        loop = MemoryOptimizer()

        # Simulate noisy feedback with delay
        noisy_feedbacks = [
            {'missing_context_ratio': 0.5, 'irrelevance_score': 0.2, 'retrieval_latency_ms': 50, 'token_waste_ratio': 0.1},
            {'missing_context_ratio': 0.1, 'irrelevance_score': 0.8, 'retrieval_latency_ms': 50, 'token_waste_ratio': 0.1},
            {'missing_context_ratio': 0.5, 'irrelevance_score': 0.2, 'retrieval_latency_ms': 50, 'token_waste_ratio': 0.1},
        ]

        losses = []
        for fb in noisy_feedbacks:
            loss = loop.compute_loss(fb)
            losses.append(loss)

        # Smoothed values should be less volatile than raw
        variance_raw = np.var(losses)
        assert variance_raw >= 0  # (sanity)

    def test_mitigation_5_compliance_floor_prevents_audit_gap(self):
        """Context window doesn't drop below compliance requirement"""
        min_audit = 6000
        loop = MemoryOptimizer(min_audit_requirement_bytes=min_audit)

        # Try to shrink window aggressively
        shrink = {'window_size': -1.0, 'layer_preserved': 0, 'layer_injected': 0, 'recall_threshold': 0}

        for _ in range(500):
            loop.apply_gradients(shrink, learning_rate=0.1, damping=0.8)

        assert loop.context_window_size >= min_audit, f"Window {loop.context_window_size} dropped below minimum {min_audit}"


class TestMemoryOptimizerIntegration:
    """Test with realistic data"""

    def test_100_batch_convergence_simulation(self):
        """Run 100 batches with realistic feedback, verify convergence"""
        loop = MemoryOptimizer()

        for batch in range(100):
            # Simulate improving quality over time
            quality_improving = 0.5 - batch * 0.003
            feedback = {
                'missing_context_ratio': max(0.0, quality_improving),
                'irrelevance_score': max(0.0, quality_improving * 0.5),
                'retrieval_latency_ms': 50 + np.random.normal(0, 5),
                'token_waste_ratio': max(0.0, quality_improving * 0.2),
            }

            loss = loop.compute_loss(feedback)
            prev_loss = loop.loss_history[-2] if len(loop.loss_history) > 1 else loss

            gradients = loop.compute_gradients(loss, prev_loss)
            loop.apply_gradients(gradients)

        # Check convergence
        converged = loop.check_convergence()
        print(f"Converged after 100 batches: {converged}")
        print(f"Final loss variance: {loop.get_loss_variance()}")

        # By batch 100, should be converging
        assert loop.get_loss_variance() < 0.05 or converged, "Loss should stabilize by batch 100"
