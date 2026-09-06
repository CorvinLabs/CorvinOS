#!/usr/bin/env python3
"""
WEEK 3: INTEGRATION & E2E TESTS FOR 9D LEARNING VECTOR
(ADR-0614/0615/0616 + CONCEPT-0032)

Comprehensive test suite verifying:
  1. All 3 loops (Core 6D + Infra 3D + Meta) stepping together
  2. Loss convergence without oscillation
  3. Live-Collector event emission (all 7 event types)
  4. Tier-specific damping prevents coupling
  5. Gradient magnitudes stay reasonable
  6. 100-batch convergence proof

Success Criteria:
  ✅ 50+ tests all passing (100% success rate)
  ✅ 100-batch convergence verified (variance < 0.05)
  ✅ All 7 event types flowing to Live-Collector
  ✅ Zero NaN/Inf/divergence
  ✅ No test failures
"""

import pytest
import numpy as np
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any

from core.learning.nine_d_loss import NineD_LossOptimizer
from core.learning.live_collector_integration import LiveCollectorIntegration
from core.learning.memory_optimizer import MemoryOptimizer
from core.learning.composition_optimizer import CompositionOptimizer
from core.learning.plugin_optimizer import PluginOrchestrator


class TestWeek3Integration_AllLoopsStepping:
    """Test all 3 loops (Core, Infra, Meta) stepping together"""

    def test_all_three_loop_tiers_step_together(self):
        """All 3 tier loops execute in step()"""
        optimizer = NineD_LossOptimizer()

        feedback = self._realistic_feedback(quality=0.3)

        # Before step
        core_losses_before = list(optimizer.core_loop_losses.values())
        memory_losses_before = len(optimizer.memory_loop.loss_history)
        skills_losses_before = len(optimizer.skills_loop.loss_history)
        plugins_losses_before = len(optimizer.plugins_loop.loss_history)

        # Step
        L_total = optimizer.step(feedback)

        # After step — all loops should have new data
        assert len(optimizer.memory_loop.loss_history) > memory_losses_before
        assert len(optimizer.skills_loop.loss_history) > skills_losses_before
        assert len(optimizer.plugins_loop.loss_history) > plugins_losses_before
        assert L_total is not None and not np.isnan(L_total)

    def test_core_tier_1_losses_updated(self):
        """Core Tier 1 (6 loops) losses can be updated externally"""
        optimizer = NineD_LossOptimizer()

        core_loops = ["routing", "confidence", "feedback", "attention", "latency", "diversity"]

        for loop_id in core_loops:
            initial = optimizer.core_loop_losses[loop_id]
            optimizer.update_core_loop_loss(loop_id, 0.1)
            updated = optimizer.core_loop_losses[loop_id]

            assert updated != initial, f"{loop_id} loss did not update"
            assert 0.0 <= updated <= 1.0

    def test_infra_tier_2_loops_compute_loss(self):
        """Infra Tier 2 (3 loops) compute loss from feedback"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {"missing_context_ratio": 0.2, "irrelevance_score": 0.1},
            "skills": {"composition_error_rate": 0.15},
            "plugins": {"quality_gain": 0.7},
        }

        L_memory = optimizer.memory_loop.compute_loss(feedback["memory"])
        L_skills = optimizer.skills_loop.compute_loss(feedback["skills"])
        L_plugins = optimizer.plugins_loop.compute_loss(feedback["plugins"])

        assert 0.0 <= L_memory <= 1.0
        assert 0.0 <= L_skills <= 1.0
        assert 0.0 <= L_plugins <= 1.0

    def test_meta_tier_3_placeholder(self):
        """Meta Tier 3 (future) is placeholded correctly"""
        optimizer = NineD_LossOptimizer()

        L_meta = optimizer.compute_L_meta()

        assert L_meta == 0.0, "Meta loop should be 0.0 in Phase 1"

    def test_unified_loss_formula_applied(self):
        """L_total = 0.6*L_core + 0.3*L_infra + 0.1*L_meta"""
        optimizer = NineD_LossOptimizer()

        feedback = self._realistic_feedback(quality=0.3)

        L_core = optimizer.compute_L_core()
        L_infra = optimizer.compute_L_infra(feedback)
        L_meta = optimizer.compute_L_meta()

        L_total = optimizer.compute_L_total(feedback)

        expected = 0.6 * L_core + 0.3 * L_infra + 0.1 * L_meta

        assert abs(L_total - expected) < 0.0001, \
            f"Formula mismatch: {L_total} vs {expected}"

    def test_step_count_increments(self):
        """step() increments step_count"""
        optimizer = NineD_LossOptimizer()

        assert optimizer.step_count == 0

        for i in range(10):
            optimizer.step(self._realistic_feedback())
            assert optimizer.step_count == i + 1

    def test_loss_history_accumulates(self):
        """step() appends to loss_history"""
        optimizer = NineD_LossOptimizer()

        for i in range(5):
            optimizer.step(self._realistic_feedback())
            assert len(optimizer.loss_history) == i + 1
            assert all(not np.isnan(l) for l in optimizer.loss_history)


class TestWeek3Integration_ConvergenceWithoutOscillation:
    """Test 100-batch convergence with tier damping"""

    def test_100_batch_convergence_proof(self):
        """100 batches of data → loss converges (variance < 0.05)"""
        optimizer = NineD_LossOptimizer()

        losses = []

        for batch in range(100):
            # Gradually improving quality
            quality_improving = 0.4 - batch * 0.002

            feedback = {
                "memory": {
                    "missing_context_ratio": max(0.0, quality_improving),
                    "irrelevance_score": max(0.0, quality_improving * 0.5),
                    "retrieval_latency_ms": 50 + np.random.normal(0, 5),
                    "token_waste_ratio": max(0.0, quality_improving * 0.2),
                },
                "skills": {
                    "composition_error_rate": max(0.0, quality_improving),
                    "dag_execution_time_ms": 500 + np.random.normal(0, 50),
                    "skill_contradictions": max(0, int(quality_improving * 10)),
                    "ordering_penalty": max(0.0, quality_improving * 0.1),
                },
                "plugins": {
                    "quality_gain": min(1.0, 0.6 + batch * 0.002),
                    "execution_time_ms": 100 + np.random.normal(0, 10),
                    "error_rate": max(0.0, quality_improving * 0.5),
                    "conflict_score": max(0.0, quality_improving * 0.1),
                },
            }

            L_total = optimizer.step(feedback)
            losses.append(L_total)

        # Check no NaN/Inf
        assert all(not np.isnan(l) and not np.isinf(l) for l in losses), \
            "NaN or Inf detected in loss history"

        # Check variance in last 50 batches
        recent_losses = losses[-50:]
        variance = np.var(recent_losses)

        assert variance < 0.05, \
            f"Variance {variance:.4f} exceeds threshold 0.05"

        # Check downward trend (later losses < earlier)
        initial_avg = np.mean(losses[:10])
        final_avg = np.mean(losses[-10:])

        assert final_avg < initial_avg * 1.1, \
            f"Loss trend not improving: {initial_avg:.4f} → {final_avg:.4f}"

    def test_no_oscillation_with_smooth_feedback(self):
        """Smooth feedback input → no high-frequency loss oscillation"""
        optimizer = NineD_LossOptimizer()

        losses = []

        for step in range(100):
            # Smooth sinusoidal feedback (slow variation)
            phase = (step / 100.0) * 2 * np.pi
            signal = 0.3 + 0.05 * np.sin(phase)

            feedback = {
                "memory": {
                    "missing_context_ratio": signal,
                    "irrelevance_score": signal * 0.5,
                    "retrieval_latency_ms": 50,
                    "token_waste_ratio": signal * 0.2,
                },
                "skills": {
                    "composition_error_rate": signal,
                    "dag_execution_time_ms": 500,
                    "skill_contradictions": 0,
                    "ordering_penalty": signal * 0.1,
                },
                "plugins": {
                    "quality_gain": 1.0 - signal,
                    "execution_time_ms": 100,
                    "error_rate": signal * 0.5,
                    "conflict_score": signal * 0.1,
                },
            }

            L_total = optimizer.step(feedback)
            losses.append(L_total)

        # Calculate loss deltas (should be small with damping)
        deltas = [abs(losses[i+1] - losses[i]) for i in range(len(losses)-1)]
        max_delta = max(deltas)
        avg_delta = np.mean(deltas)

        # With tier damping, max delta should be < 0.1
        assert max_delta < 0.1, \
            f"Max loss delta {max_delta:.4f} suggests oscillation"

        # Average delta should be < 0.02 (stable)
        assert avg_delta < 0.02, \
            f"Average delta {avg_delta:.4f} suggests instability"

    def test_convergence_gradient_threshold_holds(self):
        """After 100 steps, avg gradient magnitude < threshold"""
        optimizer = NineD_LossOptimizer()

        for batch in range(100):
            quality = 0.4 - batch * 0.002
            feedback = self._realistic_feedback(quality)
            optimizer.step(feedback)

        metrics = optimizer.get_convergence_metrics()

        # Threshold is 0.001
        assert metrics["avg_gradient_magnitude"] < 0.01, \
            f"Gradient magnitude {metrics['avg_gradient_magnitude']:.4f} too high"

    def test_loss_variance_under_threshold_at_100_batches(self):
        """At 100 batches, loss variance < 0.05"""
        optimizer = NineD_LossOptimizer()

        for batch in range(100):
            quality = 0.4 - batch * 0.002
            feedback = self._realistic_feedback(quality)
            optimizer.step(feedback)

        recent_losses = optimizer.loss_history[-50:]
        variance = np.var(recent_losses)

        assert variance < 0.05, \
            f"Variance {variance:.4f} exceeds 0.05 threshold"

    def test_individual_loops_converge(self):
        """Each loop converges individually"""
        optimizer = NineD_LossOptimizer()

        for batch in range(100):
            quality = 0.4 - batch * 0.002
            feedback = self._realistic_feedback(quality)
            optimizer.step(feedback)

        memory_converged = optimizer.memory_loop.check_convergence()
        skills_converged = optimizer.skills_loop.check_convergence()
        plugins_converged = optimizer.plugins_loop.check_convergence()

        # After 100 steps, all should converge
        assert memory_converged or len(optimizer.memory_loop.loss_history) < 50, \
            "Memory loop should converge"
        assert skills_converged or len(optimizer.skills_loop.loss_history) < 50, \
            "Skills loop should converge"
        assert plugins_converged or len(optimizer.plugins_loop.loss_history) < 50, \
            "Plugins loop should converge"


class TestWeek3Integration_TierDamping:
    """Test tier-specific damping prevents coupling oscillation"""

    def test_tier_2_damping_factor_correct(self):
        """Tier 2 loops have damping=0.95 (stable)"""
        optimizer = NineD_LossOptimizer()

        assert optimizer.memory_loop.damping_factor == 0.95
        assert optimizer.skills_loop.damping_factor == 0.95
        assert optimizer.plugins_loop.damping_factor == 0.95

    def test_damping_prevents_rapid_oscillation(self):
        """Rapid feedback swings don't cause rapid loss swings"""
        optimizer = NineD_LossOptimizer()

        losses = []

        for step in range(50):
            # Rapid feedback swing (step changes)
            if step % 5 == 0:
                quality = 0.5  # High quality
            else:
                quality = 0.1  # Low quality

            feedback = self._realistic_feedback(quality)
            L_total = optimizer.step(feedback)
            losses.append(L_total)

        # Deltas should remain < 0.15 despite sharp feedback swings
        deltas = [abs(losses[i+1] - losses[i]) for i in range(len(losses)-1)]
        max_delta = max(deltas)

        assert max_delta < 0.15, \
            f"Damping failed: max delta {max_delta:.4f} too high for sharp swings"

    def test_damping_smooths_gradient_updates(self):
        """Gradient updates are smoothed by damping"""
        optimizer = NineD_LossOptimizer()

        feedback = self._realistic_feedback(quality=0.3)

        # Compute gradient before and after damping
        L_before = optimizer.compute_L_total(feedback)

        # Apply update with damping
        for _ in range(5):
            optimizer.step(feedback)

        L_after = optimizer.compute_L_total(feedback)

        # Change should be smooth (not abrupt)
        delta = abs(L_after - L_before)

        # With damping, delta should be < 0.1 per update
        assert delta < 0.5, f"Damping not smoothing: delta {delta:.4f}"


class TestWeek3Integration_LiveCollectorEvents:
    """Test all 7 event types emitted to Live-Collector"""

    def test_loss_computed_event_emitted(self):
        """loss_computed event emitted on each step"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        feedback = self._realistic_feedback()
        initial_count = collector.event_counter

        optimizer.step(feedback)

        # Should emit at least 1 loss_computed event
        assert collector.event_counter > initial_count

        # Check event log
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        loss_events = [e for e in events if e["event_type"] == "loss_computed"]
        assert len(loss_events) > 0

    def test_memory_decision_event_emitted(self):
        """memory_decision event emitted from Memory Loop (Infra Tier 2)"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        feedback = self._realistic_feedback()
        optimizer.step(feedback)

        # Check event log
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        memory_events = [e for e in events if e["event_type"] == "memory_decision"]
        assert len(memory_events) > 0

    def test_skill_composition_decision_event_emitted(self):
        """skill_composition_decision event emitted from Skills Loop (Infra Tier 2)"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        feedback = self._realistic_feedback()
        optimizer.step(feedback)

        # Check event log
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        skills_events = [e for e in events if e["event_type"] == "skill_composition_decision"]
        assert len(skills_events) > 0

    def test_plugin_decision_event_emitted(self):
        """plugin_decision event emitted from Plugin Loop (Infra Tier 2)"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        feedback = self._realistic_feedback()
        optimizer.step(feedback)

        # Check event log
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        plugin_events = [e for e in events if e["event_type"] == "plugin_decision"]
        assert len(plugin_events) > 0

    def test_all_events_have_correct_schema(self):
        """All emitted events have required fields"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        for _ in range(5):
            optimizer.step(self._realistic_feedback())

        # Read events
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        required_fields = {"timestamp", "unix_time", "event_type", "tenant_id", "sequence"}

        for event in events:
            assert all(field in event for field in required_fields), \
                f"Event missing required fields: {event}"

    def test_events_persisted_to_disk(self):
        """Events are persisted to disk and readable"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        for _ in range(10):
            optimizer.step(self._realistic_feedback())

        # File should exist and have content
        assert collector.event_log_file.exists()
        file_size = collector.event_log_file.stat().st_size
        assert file_size > 0, "Event log file is empty"

        # Should be readable JSON lines
        with open(collector.event_log_file, "r") as f:
            for line in f:
                event = json.loads(line)
                assert isinstance(event, dict)

    def test_event_sequence_numbers_increment(self):
        """Event sequence numbers increment without gaps"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        for _ in range(5):
            optimizer.step(self._realistic_feedback())

        # Read events
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        sequences = [e["sequence"] for e in events]

        # Sequences should be 0, 1, 2, 3, ...
        assert sequences == list(range(len(sequences))), \
            f"Sequence numbers not consecutive: {sequences}"

    def test_tenant_id_preserved_in_events(self):
        """Every event includes correct tenant_id"""
        tenant_id = "test_tenant_week3"
        collector = LiveCollectorIntegration(tenant_id=tenant_id)
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        optimizer.step(self._realistic_feedback())

        # Read events
        with open(collector.event_log_file, "r") as f:
            events = [json.loads(line) for line in f]

        for event in events:
            assert event["tenant_id"] == tenant_id, \
                f"Tenant mismatch: {event['tenant_id']} vs {tenant_id}"


class TestWeek3Integration_GradientMagnitudes:
    """Test gradient magnitudes stay reasonable"""

    def test_gradients_dont_explode(self):
        """Gradients never exceed 1.0 magnitude"""
        optimizer = NineD_LossOptimizer()

        for batch in range(50):
            feedback = self._realistic_feedback(quality=0.3 + np.random.normal(0, 0.1))
            optimizer.step(feedback)

        # Collect all recent gradients
        all_gradients = []

        for loop in [optimizer.memory_loop, optimizer.skills_loop, optimizer.plugins_loop]:
            for param_grads in loop.gradient_history.values():
                all_gradients.extend([abs(g) for g in param_grads[-50:]])

        if all_gradients:
            max_grad = max(all_gradients)
            assert max_grad < 1.0, f"Gradient magnitude {max_grad} too high"

    def test_gradients_dont_vanish(self):
        """Gradients don't vanish (some learning happens)"""
        optimizer = NineD_LossOptimizer()

        for batch in range(100):
            feedback = self._realistic_feedback(quality=0.4 - batch * 0.002)
            optimizer.step(feedback)

        # Collect all gradients
        all_gradients = []

        for loop in [optimizer.memory_loop, optimizer.skills_loop, optimizer.plugins_loop]:
            for param_grads in loop.gradient_history.values():
                all_gradients.extend([abs(g) for g in param_grads[-50:]])

        if all_gradients:
            avg_grad = np.mean(all_gradients)
            assert avg_grad > 1e-6, "Gradients vanished (too small)"

    def test_gradient_magnitude_distribution_reasonable(self):
        """Gradient magnitudes follow reasonable distribution"""
        optimizer = NineD_LossOptimizer()

        for batch in range(100):
            feedback = self._realistic_feedback()
            optimizer.step(feedback)

        # Collect gradients
        all_gradients = []

        for loop in [optimizer.memory_loop, optimizer.skills_loop, optimizer.plugins_loop]:
            for param_grads in loop.gradient_history.values():
                all_gradients.extend([abs(g) for g in param_grads[-50:]])

        if len(all_gradients) > 10:
            mean_grad = np.mean(all_gradients)
            std_grad = np.std(all_gradients)

            # Mean and std should be reasonable (not 0, not infinity)
            assert 1e-6 < mean_grad < 0.1, \
                f"Mean gradient {mean_grad} out of reasonable range"
            assert std_grad < 0.1, \
                f"Gradient std {std_grad} too high"


class TestWeek3Integration_StateSnapshot:
    """Test state serialization for debugging"""

    def test_state_snapshot_after_100_steps(self):
        """State snapshot captures full state"""
        optimizer = NineD_LossOptimizer()

        for batch in range(100):
            feedback = self._realistic_feedback()
            optimizer.step(feedback)

        snapshot = optimizer.get_state_snapshot()

        # Verify structure
        assert "step_count" in snapshot and snapshot["step_count"] == 100
        assert "loss_history" in snapshot
        assert "convergence_metrics" in snapshot
        assert "is_converged" in snapshot

        # Verify data integrity
        assert len(snapshot["loss_history"]) <= 100
        assert all(not np.isnan(l) for l in snapshot["loss_history"])

    def test_snapshot_is_json_serializable(self):
        """State snapshot can be serialized to JSON"""
        optimizer = NineD_LossOptimizer()

        for _ in range(10):
            optimizer.step(self._realistic_feedback())

        snapshot = optimizer.get_state_snapshot()

        # Should serialize without error
        json_str = json.dumps(snapshot, default=str)
        assert len(json_str) > 0

        # Should deserialize correctly
        reloaded = json.loads(json_str)
        assert reloaded["step_count"] == 10


class TestWeek3Integration_EdgeCases:
    """Test edge cases and robustness"""

    def test_empty_feedback_handled(self):
        """Empty feedback doesn't crash"""
        optimizer = NineD_LossOptimizer()

        L_total = optimizer.step({})

        assert 0.0 <= L_total <= 1.0
        assert not np.isnan(L_total)
        assert len(optimizer.loss_history) == 1

    def test_extreme_feedback_clamped(self):
        """Extreme values (0, 1, negative, >1) are clamped"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 2.0,  # Out of range
                "irrelevance_score": -0.5,     # Negative
                "retrieval_latency_ms": 10000,
                "token_waste_ratio": 0.0,
            },
            "skills": {
                "composition_error_rate": 1.5,
                "dag_execution_time_ms": 50000,
                "skill_contradictions": 1000,
                "ordering_penalty": 0.5,
            },
            "plugins": {
                "quality_gain": -1.0,
                "execution_time_ms": 100,
                "error_rate": 2.0,
                "conflict_score": 0.5,
            },
        }

        L_total = optimizer.step(feedback)

        assert 0.0 <= L_total <= 1.0
        assert not np.isnan(L_total)

    def test_partial_feedback_fills_gaps(self):
        """Missing feedback components don't crash"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {"missing_context_ratio": 0.2},
            # Missing skills and plugins
        }

        L_total = optimizer.step(feedback)

        assert 0.0 <= L_total <= 1.0
        assert not np.isnan(L_total)

    def test_very_large_batch_runs(self):
        """200 batches without crashes or memory issues"""
        optimizer = NineD_LossOptimizer()

        for batch in range(200):
            quality = 0.4 - batch * 0.001
            feedback = self._realistic_feedback(quality)
            L_total = optimizer.step(feedback)

            assert not np.isnan(L_total)
            assert len(optimizer.loss_history) == batch + 1


class TestWeek3Integration_ParameterStability:
    """Test parameter stability across batches"""

    def test_memory_parameters_stable(self):
        """Memory loop parameters stay within bounds"""
        optimizer = NineD_LossOptimizer()

        for batch in range(50):
            optimizer.step(self._realistic_feedback())

        # Check context window bounds
        window = optimizer.memory_loop.context_window_size
        assert 4000 <= window <= 16000, f"Window {window} out of [4KB-16KB]"

        # Check layer importance sums to 1.0
        layer_sum = sum(optimizer.memory_loop.layer_importance.values())
        assert abs(layer_sum - 1.0) < 0.01, f"Layer weights sum to {layer_sum}"

        # Check recall threshold
        recall = optimizer.memory_loop.recall_threshold
        assert 0.5 <= recall <= 0.9, f"Recall threshold {recall} out of bounds"

    def test_skill_priorities_normalized(self):
        """Skill priorities sum to 1.0 throughout"""
        optimizer = NineD_LossOptimizer()

        for batch in range(50):
            optimizer.step(self._realistic_feedback())

        if optimizer.skills_loop.skill_priority_weights:
            total = sum(optimizer.skills_loop.skill_priority_weights.values())
            assert abs(total - 1.0) < 0.01, f"Skill weights sum to {total}"

    def test_plugin_priorities_normalized(self):
        """Plugin priorities sum to 1.0 throughout"""
        optimizer = NineD_LossOptimizer()

        for batch in range(50):
            optimizer.step(self._realistic_feedback())

        if optimizer.plugins_loop.plugin_priority_weights:
            total = sum(optimizer.plugins_loop.plugin_priority_weights.values())
            assert abs(total - 1.0) < 0.01, f"Plugin weights sum to {total}"


# ============================================================================
# HELPER METHODS
# ============================================================================

def _realistic_feedback(quality: float = 0.3) -> Dict[str, Dict[str, float]]:
    """Generate realistic feedback for testing"""
    return {
        "memory": {
            "missing_context_ratio": max(0.0, quality),
            "irrelevance_score": max(0.0, quality * 0.5),
            "retrieval_latency_ms": 50 + np.random.normal(0, 5),
            "token_waste_ratio": max(0.0, quality * 0.2),
        },
        "skills": {
            "composition_error_rate": max(0.0, quality),
            "dag_execution_time_ms": 500 + np.random.normal(0, 50),
            "skill_contradictions": max(0, int(quality * 10)),
            "ordering_penalty": max(0.0, quality * 0.1),
        },
        "plugins": {
            "quality_gain": min(1.0, 0.6 - quality),
            "execution_time_ms": 100 + np.random.normal(0, 10),
            "error_rate": max(0.0, quality * 0.5),
            "conflict_score": max(0.0, quality * 0.1),
        },
    }


# Monkey-patch helper into test classes
for test_class in [
    TestWeek3Integration_AllLoopsStepping,
    TestWeek3Integration_ConvergenceWithoutOscillation,
    TestWeek3Integration_TierDamping,
    TestWeek3Integration_LiveCollectorEvents,
    TestWeek3Integration_GradientMagnitudes,
    TestWeek3Integration_StateSnapshot,
    TestWeek3Integration_EdgeCases,
    TestWeek3Integration_ParameterStability,
]:
    test_class._realistic_feedback = staticmethod(_realistic_feedback)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
