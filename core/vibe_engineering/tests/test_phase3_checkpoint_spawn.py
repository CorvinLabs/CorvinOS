"""Phase 3: Checkpoint/Spawn E2E Tests (CorvinOS Integration)."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from ..vibe_engine import VibeEngine
from ..state_contract import (
    InMemoryStateStore, SerializableTaskContext, CheckpointState,
    serialize_for_spawn, deserialize_from_spawn
)
from ..brain import Brain


@pytest.fixture
def state_store():
    """Create test state store."""
    return InMemoryStateStore()

@pytest.fixture
def vibe_engine(state_store):
    """Create test engine with state store."""
    engine = VibeEngine(state_store=state_store)
    return engine


@pytest.mark.asyncio
async def test_checkpoint_save_and_load(vibe_engine, state_store):
    """Test: Checkpoint saves and loads correctly."""
    task = {
        "id": "test_001",
        "goal": "Test checkpoint",
        "type": "test",
        "item_count": 10,
        "max_iterations": 5
    }

    # Manually create and save checkpoint
    checkpoint = CheckpointState(
        checkpoint_id="ckpt_001",
        task_id="test_001",
        iteration_num=2,
        timestamp_iso="2026-08-24T00:00:00",
        context_state={
            "task_id": "test_001",
            "goal": "Test checkpoint",
            "persona_id": "default",
            "progress": {
                "items_completed": 5,
                "total_items": 10,
                "error_count": 0
            }
        }
    )

    checkpoint_id = await state_store.save_checkpoint(checkpoint)
    assert checkpoint_id == "ckpt_001"

    # Load checkpoint
    loaded = await state_store.load_checkpoint("ckpt_001")
    assert loaded is not None
    assert loaded.iteration_num == 2
    assert loaded.context_state["progress"]["items_completed"] == 5


@pytest.mark.asyncio
async def test_serialization_safety(vibe_engine):
    """Test: Context serializes without lambdas/async."""
    context = SerializableTaskContext(
        task_id="test_001",
        goal="Serialize test",
        persona_id="default",
        task_type="test",
        item_count=10,
        created_at_iso="2026-08-24T00:00:00"
    )

    # Serialize to JSON
    json_str = serialize_for_spawn(context)
    data = json.loads(json_str)

    # Deserialize
    restored = deserialize_from_spawn(json_str)

    assert restored.task_id == context.task_id
    assert restored.goal == context.goal
    assert isinstance(restored, SerializableTaskContext)


@pytest.mark.asyncio
async def test_checkpoint_gc(state_store):
    """Test: Old checkpoints can be garbage-collected."""
    task_id = "gc_test"

    # Create 10 checkpoints
    for i in range(10):
        ckpt = CheckpointState(
            checkpoint_id=f"ckpt_{i}",
            task_id=task_id,
            iteration_num=i,
            timestamp_iso="2026-08-24T00:00:00",
            context_state={}
        )
        await state_store.save_checkpoint(ckpt)

    # List all
    all_ckpts = await state_store.list_checkpoints(task_id)
    assert len(all_ckpts) == 10

    # GC: keep only last 5
    old_ckpts = all_ckpts[:-5]
    for ckpt in old_ckpts:
        await state_store.delete_checkpoint(ckpt.checkpoint_id)

    remaining = await state_store.list_checkpoints(task_id)
    assert len(remaining) == 5


@pytest.mark.asyncio
async def test_brain_decompose_spawn_aware(vibe_engine):
    """Test: Brain.decompose() creates spawn-ready subtasks."""
    task = {
        "id": "spawn_test",
        "goal": "Process 20 items",
        "type": "batch",
        "item_count": 20
    }

    # Decompose with spawning
    subtasks = await vibe_engine.brain.decompose(task, use_spawn=True)

    assert len(subtasks) > 0
    # Last one is merge
    assert subtasks[-1].type == "merge"
    # Others are work
    work_tasks = [s for s in subtasks if s.type == "work"]
    assert len(work_tasks) > 0
    assert work_tasks[0].item_indices == list(range(0, 10))  # first 10


@pytest.mark.asyncio
async def test_should_spawn_threshold(vibe_engine):
    """Test: should_spawn() respects threshold."""
    small_task = {"id": "small", "item_count": 5}
    large_task = {"id": "large", "item_count": 50}

    # Small task: no spawn
    assert not await vibe_engine.brain.should_spawn(small_task)

    # Large task: spawn
    assert await vibe_engine.brain.should_spawn(large_task)


@pytest.mark.asyncio
async def test_checkpoint_contains_serializable_state(vibe_engine, state_store):
    """Test: Checkpoints contain only serializable fields."""
    checkpoint = CheckpointState(
        checkpoint_id="ser_test",
        task_id="test",
        iteration_num=1,
        timestamp_iso="2026-08-24T00:00:00",
        context_state={
            "task_id": "test",
            "goal": "Serialize test",
            "persona_id": "default",
            "progress": {
                "items_completed": 1,
                "total_items": 10,
                "error_count": 0
            }
        },
        last_skill_result={
            "status": "success",
            "output": {"result": "ok"},
            "cost": 1.0
        }
    )

    # Should be JSON-serializable
    checkpoint_id = await state_store.save_checkpoint(checkpoint)
    loaded = await state_store.load_checkpoint(checkpoint_id)

    assert json.dumps(loaded.to_dict())  # Should not raise


@pytest.mark.asyncio
async def test_recover_with_checkpoint(vibe_engine, state_store):
    """Test: Checkpoint metadata preserved for recovery."""
    checkpoint = CheckpointState(
        checkpoint_id="recovery_test",
        task_id="test_recover",
        iteration_num=5,
        timestamp_iso="2026-08-24T00:00:00",
        context_state={"task_id": "test_recover", "progress": {"items_completed": 5}},
        recovery_reason="Timeout — retry from checkpoint"
    )

    await state_store.save_checkpoint(checkpoint)
    loaded = await state_store.load_checkpoint("recovery_test")

    assert loaded.recovery_reason == "Timeout — retry from checkpoint"
    assert loaded.iteration_num == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
