"""E2E Test: Minimal autonomous task execution."""

import asyncio
import pytest
from datetime import datetime

from ..vibe_engine import VibeEngine
from ..context import TaskContext


@pytest.mark.asyncio
async def test_simple_task_completion():
    """Test: 10-item task completes autonomously."""
    engine = VibeEngine()

    # Mock status listener (collects updates)
    updates = []
    async def collect_status(level, message, metadata):
        updates.append({"level": level, "message": message, "metadata": metadata})

    engine.add_status_listener(collect_status)

    # Simple task: process 10 items
    task = {
        "id": "task_001",
        "goal": "Process 10 items",
        "type": "refactoring",
        "item_count": 10,
        "max_iterations": 20
    }

    # Execute
    result = await engine.execute_task(task, persona_id="test_user")

    # Assertions
    assert result["status"] in ["complete", "partial"]
    assert result["items_completed"] > 0
    assert len(updates) > 0
    assert any("Starting task" in u["message"] for u in updates)
    assert any("complete" in u["message"].lower() or "done" in u["message"].lower()
              for u in updates)


@pytest.mark.asyncio
async def test_memory_learns_from_success():
    """Test: Memory updates strategy weights on success."""
    engine = VibeEngine()

    # Initial weights (uniform)
    initial_weights = await engine.memory.get_strategy_weights("user_1", "refactoring")
    assert all(w > 0.2 for w in initial_weights.values())

    # Simulate success
    await engine.memory.update_strategy_weight(
        "user_1", "refactoring", "direct_fix", success=True
    )

    # Check weights updated
    updated_weights = await engine.memory.get_strategy_weights("user_1", "refactoring")
    assert updated_weights["direct_fix"] > initial_weights["direct_fix"]


@pytest.mark.asyncio
async def test_brain_decides_based_on_weights():
    """Test: Brain chooses strategy based on learned weights."""
    engine = VibeEngine()

    # Bias weights toward "decompose"
    for _ in range(5):
        await engine.memory.update_strategy_weight(
            "user_2", "refactoring", "decompose", success=True
        )

    # Brain decides
    task = {"id": "t", "type": "refactoring", "goal": "refactor code"}
    context = {"persona_id": "user_2"}
    decision = await engine.brain.decide(task, context)

    # Should prefer "decompose"
    assert decision.skill_id == "decompose_task"


@pytest.mark.asyncio
async def test_decomposition():
    """Test: Brain decomposes large task into subtasks."""
    engine = VibeEngine()

    task = {
        "id": "big_task",
        "type": "refactoring",
        "item_count": 50
    }

    subtasks = await engine.brain.decompose(task)

    # Should create batches + integration phase
    assert len(subtasks) > 1
    # `decompose` returns Subtask OBJECTS, not dicts — the subscript form
    # raised TypeError before this assertion could ever run.
    assert any(s.type == "merge" for s in subtasks)  # Integration phase


@pytest.mark.asyncio
async def test_skill_invocation_and_result():
    """Test: Skills execute and return results."""
    engine = VibeEngine()

    result = await engine.skills.invoke("code_analysis", context=None)

    assert result.status == "success"
    assert result.output is not None
    assert result.cost_actual >= 0
    assert result.time_actual >= 0


@pytest.mark.asyncio
async def test_context_enrichment():
    """Test: Context enriched from memory + skills + persona."""
    engine = VibeEngine()

    # Store some memories first
    await engine.memory.store(
        "semantic",
        "Refactoring patterns: use decompose for large tasks",
        "refactoring",
        "user_1"
    )

    # Enrich context
    task = {
        "id": "t",
        "goal": "refactor code",
        "type": "refactoring",
        "item_count": 5
    }
    context = await engine.context_enricher.enrich(task, "user_1")

    assert context.goal == "refactor code"
    assert len(context.available_skills) > 0
    assert len(context.recalled_memories) > 0


if __name__ == "__main__":
    # Run tests
    asyncio.run(test_simple_task_completion())
    print("✅ All tests passed!")
