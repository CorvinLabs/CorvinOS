#!/usr/bin/env python3
"""Integration tests for NineD_LossOptimizer (ADR-0614/0615/0616)

Tests cover:
  - 50 batch convergence simulation
  - No NaN/Inf values in losses or gradients
  - Live-Collector event emission
  - Loss converges downward
  - Tier-specific damping prevents oscillation
  - MemoryOptimizer mitigations verified
  - All 9 loops compute correctly
"""

import pytest
import numpy as np
import json
from pathlib import Path
from datetime import datetime

from core.learning.nine_d_loss import NineD_LossOptimizer
from core.learning.live_collector_integration import LiveCollectorIntegration


class TestNineDOptimizerInitialization:
    """Test initialization and basic structure"""

    def test_initialization(self):
        """NineD optimizer initializes with correct loops"""
        optimizer = NineD_LossOptimizer()

        assert optimizer.memory_loop is not None
        assert optimizer.skills_loop is not None
        assert optimizer.plugins_loop is not None
        assert optimizer.core_weight == 0.6
        assert optimizer.infra_weight == 0.3
        assert optimizer.meta_weight == 0.1

    def test_core_loops_initialized(self):
        """All 6 core loop losses are initialized"""
        optimizer = NineD_LossOptimizer()

        expected_core_loops = [
            "routing",
            "confidence",
            "feedback",
            "attention",
            "latency",
            "diversity",
        ]

        for loop_id in expected_core_loops:
            assert loop_id in optimizer.core_loop_losses
            assert 0.0 <= optimizer.core_loop_losses[loop_id] <= 1.0

    def test_with_collector_integration(self):
        """NineD optimizer integrates with Live-Collector"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        assert optimizer.collector is not None
        assert optimizer.collector == collector


class TestNineDLossComputation:
    """Test loss computation across all dimensions"""

    def test_compute_L_core(self):
        """L_core computes correctly as mean of 6 loops"""
        optimizer = NineD_LossOptimizer()

        L_core = optimizer.compute_L_core()

        assert 0.0 <= L_core <= 1.0
        expected = np.mean(list(optimizer.core_loop_losses.values()))
        assert abs(L_core - expected) < 0.01

    def test_compute_L_infra_no_feedback(self):
        """L_infra computes with empty feedback (defaults to 0)"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {},
            "skills": {},
            "plugins": {},
        }

        L_infra = optimizer.compute_L_infra(feedback)

        assert 0.0 <= L_infra <= 1.0
        assert not np.isnan(L_infra)
        assert not np.isinf(L_infra)

    def test_compute_L_infra_with_feedback(self):
        """L_infra computes correctly with realistic feedback"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        L_infra = optimizer.compute_L_infra(feedback)

        assert 0.0 <= L_infra <= 1.0
        assert not np.isnan(L_infra)
        assert not np.isinf(L_infra)

    def test_compute_L_total_structure(self):
        """L_total combines L_core, L_infra, L_meta with correct weights"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        L_core = optimizer.compute_L_core()
        L_infra = optimizer.compute_L_infra(feedback)
        L_meta = optimizer.compute_L_meta()

        L_total = optimizer.compute_L_total(feedback)

        expected = (
            0.6 * L_core + 0.3 * L_infra + 0.1 * L_meta
        )

        assert abs(L_total - expected) < 0.001
        assert 0.0 <= L_total <= 1.0

    def test_L_total_no_nan_inf(self):
        """L_total never produces NaN or Inf"""
        optimizer = NineD_LossOptimizer()

        # Test with various feedback patterns
        test_cases = [
            {},  # empty
            {"memory": {}},  # partial
            {
                "memory": {"missing_context_ratio": 0.5},
                "skills": {"dag_execution_time_ms": 1000},
                "plugins": {"error_rate": 1.0},
            },  # edge cases
        ]

        for feedback in test_cases:
            L_total = optimizer.compute_L_total(feedback)

            assert not np.isnan(L_total), f"NaN produced for feedback: {feedback}"
            assert not np.isinf(L_total), f"Inf produced for feedback: {feedback}"
            assert 0.0 <= L_total <= 1.0


class TestNineDStep:
    """Test the main optimization step"""

    def test_step_updates_loops(self):
        """step() updates all loops and records loss"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        L1 = optimizer.step(feedback)

        assert L1 is not None
        assert 0.0 <= L1 <= 1.0
        assert not np.isnan(L1)
        assert len(optimizer.loss_history) == 1
        assert optimizer.step_count == 1

    def test_step_no_nan_inf_in_losses(self):
        """step() never produces NaN or Inf in any loss value"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        for step_num in range(10):
            L_total = optimizer.step(feedback)

            assert not np.isnan(L_total)
            assert not np.isinf(L_total)
            assert 0.0 <= L_total <= 1.0

            # Check loop histories
            for loop_loss in optimizer.memory_loop.loss_history:
                assert not np.isnan(loop_loss)
                assert not np.isinf(loop_loss)


class TestNineDConvergence50Batch:
    """Test 50-batch convergence simulation (core success criteria)"""

    def test_50_batch_convergence_with_improving_feedback(self):
        """50 batches with improving quality → loss converges downward"""
        optimizer = NineD_LossOptimizer()

        losses = []

        for batch in range(50):
            # Simulate improving quality over time
            quality_improving = 0.4 - batch * 0.004  # gradually improve

            feedback = {
                "memory": {
                    "missing_context_ratio": max(0.0, quality_improving),
                    "irrelevance_score": max(0.0, quality_improving * 0.5),
                    "retrieval_latency_ms": 50
                    + np.random.normal(0, 5),
                    "token_waste_ratio": max(0.0, quality_improving * 0.2),
                },
                "skills": {
                    "composition_error_rate": max(0.0, quality_improving),
                    "dag_execution_time_ms": 500 + np.random.normal(0, 50),
                    "skill_contradictions": max(0, int(quality_improving * 10)),
                    "ordering_penalty": max(0.0, quality_improving * 0.1),
                },
                "plugins": {
                    "quality_gain": min(1.0, 0.6 + batch * 0.004),  # improving
                    "execution_time_ms": 100 + np.random.normal(0, 10),
                    "error_rate": max(0.0, quality_improving * 0.5),
                    "conflict_score": max(0.0, quality_improving * 0.1),
                },
            }

            L_total = optimizer.step(feedback)
            losses.append(L_total)

        # Verify no NaN/Inf
        assert all(not np.isnan(l) and not np.isinf(l) for l in losses)

        # Verify loss converges downward
        initial_loss = np.mean(losses[:5])
        final_loss = np.mean(losses[-5:])

        print(f"50-batch convergence: {initial_loss:.3f} → {final_loss:.3f}")
        assert final_loss < initial_loss, "Loss should improve over 50 batches"

    def test_100_batch_convergence_variance(self):
        """100 batches → loss variance < 0.05 (stability criterion)"""
        optimizer = NineD_LossOptimizer()

        losses = []

        for batch in range(100):
            quality_improving = 0.4 - batch * 0.002  # gradually improve

            feedback = {
                "memory": {
                    "missing_context_ratio": max(0.0, quality_improving),
                    "irrelevance_score": max(0.0, quality_improving * 0.5),
                    "retrieval_latency_ms": 50
                    + np.random.normal(0, 5),
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

        # Check variance in last 50 batches
        recent_losses = losses[-50:]
        variance = np.var(recent_losses)

        print(f"Loss variance (last 50 batches): {variance:.4f}")
        assert variance < 0.05, f"Loss variance {variance} exceeds threshold"


class TestNineDTierDamping:
    """Test tier-specific damping prevents oscillation"""

    def test_tier_2_damping_stability(self):
        """Tier 2 loops use damping=0.95 for stability"""
        optimizer = NineD_LossOptimizer()

        # Verify damping factors
        assert optimizer.memory_loop.damping_factor == 0.95
        assert optimizer.skills_loop.damping_factor == 0.95
        assert optimizer.plugins_loop.damping_factor == 0.95

    def test_no_oscillation_with_gradual_feedback(self):
        """Gradual feedback changes don't cause oscillation"""
        optimizer = NineD_LossOptimizer()

        # Slowly vary feedback
        losses = []
        for step in range(100):
            phase = (step / 100.0) * 2 * np.pi
            signal = 0.3 + 0.05 * np.sin(phase)  # smooth oscillation

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

        # Check for high-frequency oscillation (shouldn't exist)
        differences = [abs(losses[i + 1] - losses[i]) for i in range(len(losses) - 1)]
        max_delta = max(differences)

        print(f"Max loss delta (smooth input): {max_delta:.4f}")
        assert (
            max_delta < 0.1
        ), "Large jumps suggest insufficient damping"


class TestNineDMemoryMitigations:
    """Verify MemoryOptimizer mitigations work within 9D context"""

    def test_exponential_smoothing_applied(self):
        """MemoryOptimizer uses exponential smoothing (α=0.95)"""
        optimizer = NineD_LossOptimizer()

        assert optimizer.memory_loop.smoothing_alpha == 0.95

    def test_layer_weight_normalization_held(self):
        """Layer weights always sum to 1.0 throughout 50 batches"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        for batch in range(50):
            optimizer.step(feedback)

            # Check layer weights sum to 1.0
            total = sum(optimizer.memory_loop.layer_importance.values())
            assert abs(total - 1.0) < 0.01, f"Layer weights sum to {total}"

    def test_context_window_stays_in_bounds(self):
        """Context window [4KB–16KB] throughout"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.5,
                "irrelevance_score": 0.5,
                "retrieval_latency_ms": 100,
                "token_waste_ratio": 0.5,
            },
            "skills": {
                "composition_error_rate": 0.5,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 5,
                "ordering_penalty": 0.5,
            },
            "plugins": {
                "quality_gain": 0.5,
                "execution_time_ms": 100,
                "error_rate": 0.5,
                "conflict_score": 0.5,
            },
        }

        for batch in range(50):
            optimizer.step(feedback)

            window = optimizer.memory_loop.context_window_size
            assert (
                4000 <= window <= 16000
            ), f"Window {window} out of bounds at batch {batch}"


class TestNineDLiveCollectorIntegration:
    """Test Live-Collector event emission"""

    def test_events_emitted_on_step(self):
        """step() emits events to Live-Collector"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        initial_count = collector.event_counter
        optimizer.step(feedback)

        # Should have emitted multiple events (loss_computed, memory, skills, plugins)
        assert collector.event_counter > initial_count

    def test_event_log_created(self):
        """Live-Collector creates event log file"""
        collector = LiveCollectorIntegration()
        optimizer = NineD_LossOptimizer(collector_integration=collector)

        feedback = {
            "memory": {"missing_context_ratio": 0.1, "irrelevance_score": 0.2, "retrieval_latency_ms": 50, "token_waste_ratio": 0.15},
            "skills": {"composition_error_rate": 0.1, "dag_execution_time_ms": 500, "skill_contradictions": 0, "ordering_penalty": 0.05},
            "plugins": {"quality_gain": 0.8, "execution_time_ms": 100, "error_rate": 0.05, "conflict_score": 0.1},
        }

        optimizer.step(feedback)

        assert collector.event_log_file.exists()
        assert collector.event_log_file.stat().st_size > 0


class TestNineDStateSnapshot:
    """Test state serialization"""

    def test_state_snapshot_structure(self):
        """get_state_snapshot() returns complete structure"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        optimizer.step(feedback)
        snapshot = optimizer.get_state_snapshot()

        assert "step_count" in snapshot
        assert "loss_history" in snapshot
        assert "core_loop_losses" in snapshot
        assert "memory_loop" in snapshot
        assert "skills_loop" in snapshot
        assert "plugins_loop" in snapshot
        assert "convergence_metrics" in snapshot
        assert "is_converged" in snapshot

    def test_snapshot_is_json_serializable(self):
        """State snapshot can be JSON serialized"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 0.1,
                "irrelevance_score": 0.2,
                "retrieval_latency_ms": 50,
                "token_waste_ratio": 0.15,
            },
            "skills": {
                "composition_error_rate": 0.1,
                "dag_execution_time_ms": 500,
                "skill_contradictions": 0,
                "ordering_penalty": 0.05,
            },
            "plugins": {
                "quality_gain": 0.8,
                "execution_time_ms": 100,
                "error_rate": 0.05,
                "conflict_score": 0.1,
            },
        }

        optimizer.step(feedback)
        snapshot = optimizer.get_state_snapshot()

        # Should not raise
        json_str = json.dumps(snapshot, default=str)
        assert len(json_str) > 0


class TestNineDCoreLoopUpdates:
    """Test updating core loop losses externally"""

    def test_update_core_loop_loss(self):
        """update_core_loop_loss() updates loss with smoothing"""
        optimizer = NineD_LossOptimizer()

        optimizer.update_core_loop_loss("routing", 0.2)

        # Should apply exponential smoothing
        expected = 0.9 * 0.3 + 0.1 * 0.2  # smoothing_alpha=0.9
        assert abs(optimizer.core_loop_losses["routing"] - expected) < 0.001

    def test_core_loop_loss_clipping(self):
        """Core loop losses are clipped to [0, 1]"""
        optimizer = NineD_LossOptimizer()

        optimizer.update_core_loop_loss("routing", 1.5)
        assert optimizer.core_loop_losses["routing"] <= 1.0

        optimizer.update_core_loop_loss("confidence", -0.5)
        assert optimizer.core_loop_losses["confidence"] >= 0.0


class TestNineDEdgeCases:
    """Test edge cases and error handling"""

    def test_empty_feedback(self):
        """Empty feedback doesn't crash"""
        optimizer = NineD_LossOptimizer()

        L_total = optimizer.step({})

        assert 0.0 <= L_total <= 1.0
        assert not np.isnan(L_total)

    def test_partial_feedback(self):
        """Partial feedback (missing some components) is handled"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {"missing_context_ratio": 0.1},
            # Missing 'skills' and 'plugins'
        }

        L_total = optimizer.step(feedback)

        assert 0.0 <= L_total <= 1.0
        assert not np.isnan(L_total)

    def test_extreme_feedback_values(self):
        """Extreme feedback values (0 or 1) are handled"""
        optimizer = NineD_LossOptimizer()

        feedback = {
            "memory": {
                "missing_context_ratio": 1.0,
                "irrelevance_score": 0.0,
                "retrieval_latency_ms": 1000,
                "token_waste_ratio": 1.0,
            },
            "skills": {
                "composition_error_rate": 1.0,
                "dag_execution_time_ms": 10000,
                "skill_contradictions": 1000,
                "ordering_penalty": 1.0,
            },
            "plugins": {
                "quality_gain": 0.0,
                "execution_time_ms": 10000,
                "error_rate": 1.0,
                "conflict_score": 1.0,
            },
        }

        L_total = optimizer.step(feedback)

        assert 0.0 <= L_total <= 1.0
        assert not np.isnan(L_total)
        assert not np.isinf(L_total)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
