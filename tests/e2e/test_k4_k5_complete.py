import pytest
import asyncio
from core.voice.natural_voice_integration import NaturalVoiceIntegration, VoiceContext
from core.learning.ldd_learning_loop import LddLearningLoop, StrategyDecision, ModelStrategy
from core.orchestration.subsystems.task_manager import TaskManager, TaskState


@pytest.mark.asyncio
async def test_voice_streaming():
    """k=4: Voice streaming works."""
    voice = NaturalVoiceIntegration()
    chunks = []
    async for chunk in voice.stream_response("Hello world", "chat1"):
        chunks.append(chunk)
    assert len(chunks) > 0
    assert any(c.get("type") == "tts_chunk" for c in chunks)


@pytest.mark.asyncio
async def test_voice_interruption():
    """k=4: Voice interruption works."""
    voice = NaturalVoiceIntegration()
    voice.streaming_active = True
    result = []
    async for event in voice.interrupt("chat1"):
        result.append(event)
    assert result[0]["type"] == "interrupt"


@pytest.mark.asyncio
async def test_voice_context():
    """k=4: Voice context tracking works."""
    voice = NaturalVoiceIntegration()
    await voice.record_user_turn("hello", "chat1", tone="friendly")
    context = voice.get_context("chat1")
    assert context is not None
    assert len(context.last_3_turns) == 1


@pytest.mark.asyncio
async def test_learning_decision_recording():
    """k=4: Learning loop records decisions."""
    loop = LddLearningLoop()
    decision = StrategyDecision(
        model=ModelStrategy.HAIKU,
        confidence=0.5,
        quality_score=0.85,
        cost=0.05,
        latency_ms=150
    )
    await loop.record_decision(decision)
    assert len(loop.decisions) == 1
    assert loop.model_confidence["haiku"] > 0.5


@pytest.mark.asyncio
async def test_learning_bayesian_update():
    """k=4: Bayesian confidence updates."""
    loop = LddLearningLoop()
    await loop.update_confidence(ModelStrategy.OPUS, 0.9)
    assert loop.model_confidence["opus"] > 0.5


@pytest.mark.asyncio
async def test_learning_strategy_optimization():
    """k=4: Strategy optimization based on history."""
    loop = LddLearningLoop()
    # Record good Haiku performance
    decision = StrategyDecision(
        model=ModelStrategy.HAIKU,
        confidence=0.5,
        quality_score=0.9,
        cost=0.02,
        latency_ms=100
    )
    await loop.record_decision(decision)
    
    # Next strategy should prefer Haiku
    strategy = await loop.optimize_next_strategy(task_difficulty="simple")
    assert strategy == ModelStrategy.HAIKU


@pytest.mark.asyncio
async def test_task_creation():
    """k=5: Task creation works."""
    manager = TaskManager()
    task = await manager.create_task("task1", cost_budget=5.0)
    assert task.task_id == "task1"
    assert task.state == TaskState.QUEUED


@pytest.mark.asyncio
async def test_task_state_transitions():
    """k=5: Task state transitions."""
    manager = TaskManager()
    await manager.create_task("task1")
    await manager.update_state("task1", TaskState.RUNNING)
    assert manager.get_task("task1").state == TaskState.RUNNING


@pytest.mark.asyncio
async def test_task_dependencies():
    """k=5: Task dependency resolution."""
    manager = TaskManager()
    await manager.create_task("task1")
    await manager.create_task("task2", dependencies={"task1"})
    
    # task2 waits for task1
    result = await asyncio.wait_for(
        manager.wait_for_dependencies("task2", timeout_s=1),
        timeout=2.0
    )
    # Should timeout (task1 never completes)
    assert not result


@pytest.mark.asyncio
async def test_task_cost_tracking():
    """k=5: Task cost tracking and budget."""
    manager = TaskManager()
    await manager.create_task("task1", cost_budget=1.0)
    
    # Deduct cost
    success = await manager.deduct_cost("task1", 0.5)
    assert success
    
    # Deduct more (should fail, exceeds budget)
    success = await manager.deduct_cost("task1", 0.6)
    assert not success
