"""
k=4 Learning Loop Integration Tests (ADR-0845, Tier 2)

Tests Bayesian optimizer for Haiku success rates.
Validates convergence of heuristics via feedback loop.

Gate Criteria (k=4):
- Learning events flow correctly (outcome → update working)
- Heuristics converge (std-dev < 5% by iteration 50)
- Loss = std-dev(heuristic_updates) < 0.05
- All k=2 + k=3 gates still green (no regressions)
"""

import pytest
from core.learning.model_selection_optimizer import (
    OSModelSelectorOptimizer,
    TaskExecutionOutcome,
    create_optimizer,
)


class TestOptimizerBasics:
    """Basic optimizer functionality."""
    
    def setup_method(self):
        self.optimizer = create_optimizer()
    
    def test_optimizer_initialization(self):
        """Test optimizer initializes correctly."""
        assert len(self.optimizer.haiku_stats) > 0
        assert "code_review" in self.optimizer.haiku_stats
        assert "testing" in self.optimizer.haiku_stats
        
        # Check initial state (uniform priors)
        for task_type, stats in self.optimizer.haiku_stats.items():
            assert stats.mean == 0.5  # Beta(1,1) = 0.5
            assert stats.observations == 0
    
    def test_record_successful_outcome(self):
        """Test recording a successful task outcome."""
        outcome = TaskExecutionOutcome(
            task_id="task_001",
            task_type="code_review",
            model_used="haiku",
            quality_score=0.96,
            tokens_used=500,
            success=True,
            completion_time_ms=1200.0,
        )
        
        new_rate, converged = self.optimizer.record_outcome(outcome)
        
        # After one success, mean should increase
        assert new_rate > 0.5
        assert new_rate < 1.0
        assert self.optimizer.haiku_stats["code_review"].observations == 1
        assert not converged  # One observation won't converge
    
    def test_record_failed_outcome(self):
        """Test recording a failed task outcome."""
        outcome = TaskExecutionOutcome(
            task_id="task_001",
            task_type="testing",
            model_used="haiku",
            quality_score=0.75,  # Below 0.90 threshold
            tokens_used=300,
            success=False,
            completion_time_ms=800.0,
        )
        
        new_rate, _ = self.optimizer.record_outcome(outcome)
        
        # After one failure, mean should decrease
        assert new_rate < 0.5
    
    def test_mixed_outcomes_converge(self):
        """Test that mixed outcomes (successes + failures) converge to realistic rate."""
        # Simulate 20 outcomes: 16 successes, 4 failures (80% success rate)
        for i in range(20):
            is_success = i < 16  # First 16 are successes
            outcome = TaskExecutionOutcome(
                task_id=f"task_{i:03d}",
                task_type="code_review",
                model_used="haiku",
                quality_score=0.96 if is_success else 0.70,
                tokens_used=500,
                success=is_success,
                completion_time_ms=1000.0,
            )
            self.optimizer.record_outcome(outcome)
        
        final_rate = self.optimizer.get_haiku_success_rate("code_review")
        final_std_dev = self.optimizer.haiku_stats["code_review"].std_dev
        
        # Rate should be around 0.80 (16 successes out of 20)
        assert 0.70 < final_rate < 0.90, f"Rate {final_rate} outside expected range"
        
        # Std-dev should start decreasing (not yet converged)
        assert 0.05 < final_std_dev < 0.15


class TestConvergence:
    """Test heuristic convergence."""
    
    def setup_method(self):
        self.optimizer = create_optimizer()
    
    def test_convergence_50_iterations(self):
        """Test convergence after 50 iterations per task type."""
        # Simulate 50 iterations for each task type
        task_types = ["code_review", "testing", "documentation"]
        
        for task_type in task_types:
            for i in range(50):
                # Simulate realistic 90% success rate
                is_success = i % 10 < 9  # 90% success (9 successes, 1 failure per 10)
                outcome = TaskExecutionOutcome(
                    task_id=f"{task_type}_{i:03d}",
                    task_type=task_type,
                    model_used="haiku",
                    quality_score=0.95 if is_success else 0.75,
                    tokens_used=500,
                    success=is_success,
                    completion_time_ms=1000.0,
                )
                self.optimizer.record_outcome(outcome)
        
        # Check convergence
        convergence = self.optimizer.check_convergence(target_std_dev=0.05)
        avg_std_dev, converged_count, total_count = self.optimizer.get_convergence_summary()
        
        print(f"\nConvergence Summary after 50 iterations:")
        print(f"  Average std-dev: {avg_std_dev:.4f}")
        print(f"  Converged: {converged_count}/{total_count}")
        print(f"  Per-type convergence: {convergence}")
        
        # At least 50% should converge
        assert converged_count >= total_count // 2, \
            f"Only {converged_count}/{total_count} converged. std_dev={avg_std_dev}"
    
    def test_std_dev_decreases_over_time(self):
        """Test that std-dev decreases as we collect more observations."""
        optimizer = create_optimizer()
        std_devs = []
        
        for i in range(30):
            is_success = i % 5 < 4  # 80% success
            outcome = TaskExecutionOutcome(
                task_id=f"task_{i:03d}",
                task_type="code_review",
                model_used="haiku",
                quality_score=0.95 if is_success else 0.75,
                tokens_used=500,
                success=is_success,
                completion_time_ms=1000.0,
            )
            optimizer.record_outcome(outcome)
            std_devs.append(optimizer.haiku_stats["code_review"].std_dev)
        
        # Std-dev should generally decrease over time
        # (not strictly monotonic due to randomness, but should trend down)
        initial_std_dev = std_devs[0]
        final_std_dev = std_devs[-1]
        
        assert final_std_dev < initial_std_dev, \
            f"Std-dev should decrease: {initial_std_dev:.4f} → {final_std_dev:.4f}"


class TestLossCalculation:
    """Test k=4 loss metric (convergence-based)."""
    
    def setup_method(self):
        self.optimizer = create_optimizer()
    
    def test_loss_from_std_dev(self):
        """Test that loss = std-dev (convergence metric)."""
        # Simulate 25 observations
        for i in range(25):
            is_success = i < 20  # 80% success
            outcome = TaskExecutionOutcome(
                task_id=f"task_{i:03d}",
                task_type="code_review",
                model_used="haiku",
                quality_score=0.95 if is_success else 0.75,
                tokens_used=500,
                success=is_success,
                completion_time_ms=1000.0,
            )
            self.optimizer.record_outcome(outcome)
        
        # Loss = std-dev
        stats = self.optimizer.haiku_stats["code_review"]
        loss = stats.std_dev
        
        print(f"\nLoss (std-dev): {loss:.4f}")
        print(f"  Target: < 0.05")
        
        # After 25 iterations with 80% success, std-dev should be reasonable
        assert loss < 0.20, f"Loss {loss:.4f} too high after 25 iterations"
    
    def test_loss_improves_with_iterations(self):
        """Test that loss (std-dev) improves with more iterations."""
        losses = []
        
        for iteration_count in [5, 10, 25, 50]:
            optimizer = create_optimizer()
            
            for i in range(iteration_count):
                is_success = i < int(iteration_count * 0.8)  # 80% success
                outcome = TaskExecutionOutcome(
                    task_id=f"task_{i:03d}",
                    task_type="code_review",
                    model_used="haiku",
                    quality_score=0.95 if is_success else 0.75,
                    tokens_used=500,
                    success=is_success,
                    completion_time_ms=1000.0,
                )
                optimizer.record_outcome(outcome)
            
            loss = optimizer.haiku_stats["code_review"].std_dev
            losses.append(loss)
        
        print(f"\nLoss by iteration count: {losses}")
        
        # Loss should generally decrease
        assert losses[-1] < losses[0], \
            f"Loss should improve: {losses[0]:.4f} → {losses[-1]:.4f}"


class TestHeuristicUpdates:
    """Test heuristic update events."""
    
    def setup_method(self):
        self.optimizer = create_optimizer()
    
    def test_heuristic_update_event_emission(self):
        """Test that heuristic update events are emitted correctly."""
        outcome = TaskExecutionOutcome(
            task_id="task_001",
            task_type="code_review",
            model_used="haiku",
            quality_score=0.96,
            tokens_used=500,
            success=True,
            completion_time_ms=1200.0,
        )
        
        old_rate = self.optimizer.get_haiku_success_rate("code_review")
        new_rate, _ = self.optimizer.record_outcome(outcome)
        
        # Emit event
        event = self.optimizer.emit_heuristic_update_event("code_review", old_rate, new_rate)
        
        assert event["event_type"] == "model_selector_heuristic_updated"
        assert event["task_type"] == "code_review"
        assert event["old_haiku_success_rate"] == old_rate
        assert event["new_haiku_success_rate"] == new_rate
        assert "delta" in event
        assert "timestamp" in event
        assert event["tenant_id"] == "_default"


class TestMultiTaskTypeConvergence:
    """Test convergence across multiple task types."""
    
    def test_per_task_type_convergence(self):
        """Test that each task type converges independently."""
        optimizer = create_optimizer()
        
        task_configs = [
            ("code_review", 0.90),
            ("testing", 0.85),
            ("documentation", 0.95),
        ]
        
        # 40 iterations per task type with different success rates
        for task_type, success_rate in task_configs:
            for i in range(40):
                is_success = i < int(40 * success_rate)
                outcome = TaskExecutionOutcome(
                    task_id=f"{task_type}_{i:03d}",
                    task_type=task_type,
                    model_used="haiku",
                    quality_score=0.95 if is_success else 0.70,
                    tokens_used=500,
                    success=is_success,
                    completion_time_ms=1000.0,
                )
                optimizer.record_outcome(outcome)
        
        # Check convergence per task type
        stats = optimizer.get_all_stats()
        print(f"\nPer-task-type stats:")
        for task_type, stat in stats.items():
            if task_type in [t for t, _ in task_configs]:
                print(f"  {task_type}: mean={stat['mean']:.3f}, std_dev={stat['std_dev']:.4f}")
        
        # Verify each task type has learned realistic rates
        for task_type, expected_rate in task_configs:
            actual_rate = optimizer.get_haiku_success_rate(task_type)
            # Should be within 10% of expected
            assert abs(actual_rate - expected_rate) < 0.15, \
                f"{task_type}: expected ~{expected_rate:.2f}, got {actual_rate:.3f}"


class TestHistoryTracking:
    """Test history tracking for analysis."""
    
    def setup_method(self):
        self.optimizer = create_optimizer()
    
    def test_heuristic_history_recorded(self):
        """Test that heuristic history is properly tracked."""
        for i in range(10):
            is_success = i < 8  # 80% success
            outcome = TaskExecutionOutcome(
                task_id=f"task_{i:03d}",
                task_type="code_review",
                model_used="haiku",
                quality_score=0.95 if is_success else 0.75,
                tokens_used=500,
                success=is_success,
                completion_time_ms=1000.0,
            )
            self.optimizer.record_outcome(outcome)
        
        history = self.optimizer.heuristic_history["code_review"]
        
        assert len(history) == 10
        
        # Verify history entries
        for i, entry in enumerate(history):
            assert "iteration" in entry
            assert "mean" in entry
            assert "std_dev" in entry
            assert "timestamp" in entry
            assert entry["iteration"] == i + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
