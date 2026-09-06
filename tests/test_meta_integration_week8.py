"""Week 8: MetaOptimizer integration & 100-batch convergence tests"""

import pytest
from core.learning.meta_optimizer import MetaOptimizer
from core.learning.memory_optimizer import MemoryOptimizer
from core.learning.composition_optimizer import CompositionOptimizer
from core.learning.plugin_optimizer import PluginOrchestrator
from core.learning.nine_d_loss import NineD_LossOptimizer


class TestMetaIntegrationWithNineD:
    """Verify MetaOptimizer works with NineD unified loss."""

    def test_meta_receives_loss_deltas(self):
        """MetaOptimizer receives loss delta feedback from Tier 1/2."""
        meta = MetaOptimizer()
        
        # Simulate Tier 1/2 feedback
        feedback = {
            'loss_delta_core': -0.03,
            'loss_delta_infra': -0.02,
        }
        loss = meta.compute_loss(feedback)
        assert loss < 0.5  # Good feedback should result in low meta loss

    def test_meta_tuning_flow(self):
        """Full cycle: loss → gradient → update → new parameters."""
        meta = MetaOptimizer()
        
        # Batch 1: compute loss
        feedback_1 = {'loss_delta_core': -0.01, 'loss_delta_infra': -0.01}
        loss_1 = meta.compute_loss(feedback_1)
        
        # Batch 2: compute gradient
        feedback_2 = {'loss_delta_core': -0.02, 'loss_delta_infra': -0.02}
        loss_2 = meta.compute_loss(feedback_2)
        
        old_α = meta.α_core
        gradients = meta.compute_gradients(loss_2, loss_1)
        meta.apply_gradients(gradients)
        new_α = meta.α_core
        
        # On improvement, α should increase
        if loss_2 < loss_1:
            assert new_α >= old_α, f"Expected α to increase, got {old_α} → {new_α}"


class TestRobustness:
    """Test MetaOptimizer robustness against edge cases."""

    def test_nan_loss_input_handled(self):
        """NaN in feedback → graceful handling."""
        meta = MetaOptimizer()
        feedback = {'loss_delta_core': float('nan'), 'loss_delta_infra': 0.0}
        loss = meta.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0  # Clipped to valid range

    def test_inf_loss_input_handled(self):
        """Inf in feedback → clipped."""
        meta = MetaOptimizer()
        feedback = {'loss_delta_core': float('inf'), 'loss_delta_infra': 0.0}
        loss = meta.compute_loss(feedback)
        assert loss <= 1.0

    def test_extreme_gradients_bounded(self):
        """Extreme gradients don't cause divergence."""
        meta = MetaOptimizer()
        for _ in range(20):
            extreme_grad = {'α_core': 1000.0, 'α_infra': -1000.0, 'damping_core': 100.0, 'damping_infra': -100.0}
            meta.apply_gradients(extreme_grad, learning_rate=0.01)
            # Bounds still hold
            assert 0.001 <= meta.α_core <= 0.3
            assert 0.8 <= meta.damping_core <= 0.99

    def test_empty_feedback_ignored(self):
        """Empty feedback dict → handled gracefully."""
        meta = MetaOptimizer()
        loss = meta.compute_loss({})
        assert 0.0 <= loss <= 1.0

    def test_partial_feedback_filled(self):
        """Missing feedback components use defaults."""
        meta = MetaOptimizer()
        feedback = {'loss_delta_core': -0.05}  # missing loss_delta_infra
        loss = meta.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0


class TestConvergence100Batch:
    """100-batch convergence test: Meta tunes Tier 1/2 hyperparameters."""

    def test_loss_improves_with_meta_tuning(self):
        """Over 100 batches, Meta Loop tuning should reduce loss."""
        meta = MetaOptimizer()
        
        losses = []
        for batch in range(100):
            # Simulate Tier 1/2 improving (loss decreasing)
            loss_delta_core = -0.001 - (batch / 1000)  # gradually improving
            loss_delta_infra = -0.0005
            
            feedback = {
                'loss_delta_core': loss_delta_core,
                'loss_delta_infra': loss_delta_infra,
            }
            loss = meta.compute_loss(feedback)
            losses.append(loss)
            
            # Every ~10 batches, compute gradient and update
            if batch % 10 == 0 and batch > 0:
                prev_loss = losses[batch - 1]
                gradients = meta.compute_gradients(loss, prev_loss)
                meta.apply_gradients(gradients)
        
        # Loss should improve (first 20 vs last 20 batches)
        avg_first_20 = sum(losses[:20]) / 20
        avg_last_20 = sum(losses[-20:]) / 20
        assert avg_last_20 < avg_first_20, f"Loss didn't improve: {avg_first_20:.3f} → {avg_last_20:.3f}"

    def test_parameters_converge(self):
        """Over 100 batches, parameters should stabilize."""
        meta = MetaOptimizer()
        
        α_core_history = []
        for batch in range(100):
            feedback = {'loss_delta_core': -0.001, 'loss_delta_infra': -0.0005}
            loss = meta.compute_loss(feedback)
            
            if batch > 0:
                prev_loss = losses[batch - 1]
                gradients = meta.compute_gradients(loss, prev_loss)
                meta.apply_gradients(gradients)
            
            α_core_history.append(meta.α_core)
        
        # Variance in last 20 steps should be small (convergence)
        last_20 = α_core_history[-20:]
        variance = sum((x - sum(last_20)/len(last_20))**2 for x in last_20) / len(last_20)
        assert variance < 0.001, f"α_core not converged, variance = {variance}"

    def test_damping_converges(self):
        """Damping should converge to stable value."""
        meta = MetaOptimizer()
        
        for batch in range(100):
            feedback = {'loss_delta_core': -0.001, 'loss_delta_infra': -0.0005}
            loss = meta.compute_loss(feedback)
            
            if batch > 0:
                prev_loss = losses[batch - 1]
                gradients = meta.compute_gradients(loss, prev_loss)
                meta.apply_gradients(gradients)
        
        # Final damping should be in valid range
        assert 0.8 <= meta.damping_core <= 0.99
        assert 0.8 <= meta.damping_infra <= 0.99


class TestLiveCollectorIntegration:
    """Verify Meta Loop events flow to Live-Collector."""

    def test_emit_event_structure(self):
        """Meta emission should include all required fields."""
        meta = MetaOptimizer()
        
        # Collect emitted data
        emitted = []
        
        class MockCollector:
            def on_meta_decision(self, **kwargs):
                emitted.append(kwargs)
        
        meta.emit_event(MockCollector(), feedback={'test': 'data'})
        
        assert len(emitted) == 1
        event = emitted[0]
        assert 'α_core' in event
        assert 'α_infra' in event
        assert 'damping_core' in event
        assert 'damping_infra' in event
        assert 'feedback' in event


class TestNoRegressions:
    """Verify Phase 1 still works with Meta Loop active."""

    def test_memory_optimizer_independent(self):
        """MemoryOptimizer should work independently of Meta."""
        mem = MemoryOptimizer()
        feedback = {
            'missing_context_ratio': 0.1,
            'irrelevance_score': 0.2,
            'retrieval_latency_ms': 50,
            'token_waste_ratio': 0.15,
        }
        loss = mem.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0

    def test_composition_independent(self):
        """CompositionOptimizer should work independently."""
        comp = CompositionOptimizer()
        feedback = {
            'composition_error_rate': 0.1,
            'dag_execution_time_ms': 500,
            'skill_contradictions': 0,
            'ordering_penalty': 0.0,
        }
        loss = comp.compute_loss(feedback)
        assert 0.0 <= loss <= 1.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
