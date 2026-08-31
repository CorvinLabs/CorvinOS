"""Tests for TaskManager (Proposal 3 + 5, k=3)."""

import pytest
from ..task_manager import TaskPattern, LearningValidator, LDDOptimizer, TaskManager


class TestTaskPattern:
    """Test pattern representation."""

    def test_pattern_creation(self):
        """Create task pattern."""
        pattern = TaskPattern(
            task_type="refactoring",
            strategy="decompose",
            model="Opus",
            success_rate=0.95,
            sample_size=10,
            confidence=0.85,
            estimated_cost=0.50
        )
        assert pattern.task_type == "refactoring"
        assert pattern.success_rate == 0.95
        assert pattern.to_dict()["confidence"] == 0.85


class TestLearningValidator:
    """Test safety guardrails (Proposal 5)."""

    async def test_reject_dangerous_optimization(self):
        """Block: always_use_expensive_model."""
        recommendation = {"always_use_expensive_model": True}
        result = await LearningValidator.validate_recommendation(recommendation)
        assert result is None

    async def test_allow_safe_optimization(self):
        """Allow: safe recommendation."""
        recommendation = {"dimension": "cost", "action": "use_cheaper_model"}
        result = await LearningValidator.validate_recommendation(recommendation)
        assert result is not None


@pytest.mark.asyncio
class TestLDDOptimizer:
    """Test gradient-based optimization."""

    async def test_process_loss_signal_high_errors(self):
        """High error rate → switch strategy."""
        optimizer = LDDOptimizer()

        loss_event = {
            "task_id": "task_1",
            "task_type": "refactoring",
            "loss": {
                "errors": 5,  # > target (0)
                "cost": 0.05,  # < target (0.10)
                "latency": 60,
            },
        }

        result = await optimizer.process_loss_signal(loss_event)
        assert result is not None
        assert len(result["recommendations"]) > 0


@pytest.mark.asyncio
class TestTaskManager:
    """Test task-level learning."""

    async def test_recommend_task_parameters_no_history(self):
        """No history → no recommendations."""
        manager = TaskManager(tenant_id="_test")
        result = await manager.recommend_task_parameters("unknown_type")
        assert result is None

    async def test_record_and_retrieve_pattern(self):
        """Learn from task completion → recommend for next."""
        manager = TaskManager(tenant_id="_test")

        # Simulate task completion
        event_data = {
            "task_id": "task_1",
            "task_type": "refactoring",
            "strategy_used": "decompose",
            "model_used": "Opus",
            "error_count": 1,
            "item_count": 50,
            "cost_spent": 2.50,
            "items_completed": 50,
        }

        await manager._handle_task_completion(event_data)

        # Query recommendations
        result = await manager.recommend_task_parameters("refactoring")
        assert result is not None
        assert result["recommended_strategy"] == "decompose"
        assert result["recommended_model"] == "Opus"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
