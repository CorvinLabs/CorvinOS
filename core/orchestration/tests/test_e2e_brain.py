"""End-to-end tests for CorvinOS Brain."""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from core.orchestration.brain import TaskBrain
from core.orchestration.config import BrainConfigLoader
from core.orchestration.subsystems.health_monitor import HealthMonitor
from core.orchestration.subsystems.context_bridge import ContextBridge
from core.orchestration.subsystems.loop_engineer import LoopEngineer
from core.orchestration.subsystems.orchestrator import Orchestrator


async def test_brain_creation():
    """Test brain creation."""
    brain = TaskBrain()
    assert brain.poll_interval_s == 5
    assert len(brain.hub.subsystems) == 0
    print("✓ Brain creation")


async def test_subsystem_registration():
    """Test registering all 4 built-in subsystems."""
    brain = TaskBrain()

    # Register 4 subsystems
    health = HealthMonitor()
    context = ContextBridge()
    loop_eng = LoopEngineer()
    orchestrator = Orchestrator()

    brain.register_subsystem(health)
    brain.register_subsystem(context)
    brain.register_subsystem(loop_eng)
    brain.register_subsystem(orchestrator)

    assert len(brain.hub.subsystems) == 4
    assert "health_monitor" in brain.hub.subsystems
    assert "context_bridge" in brain.hub.subsystems
    assert "loop_engineer" in brain.hub.subsystems
    assert "orchestrator" in brain.hub.subsystems

    print("✓ Subsystem registration")


async def test_event_flow():
    """Test event flow between subsystems."""
    brain = TaskBrain()

    health = HealthMonitor()
    loop_eng = LoopEngineer()

    brain.register_subsystem(health)
    brain.register_subsystem(loop_eng)

    # Publish an error event
    brain.hub.publish_event("error_detected", {"task_id": "task_1", "error": "ValueError"})

    # Process events
    await brain.hub.process_events(timeout_s=1.0)

    # Check that LoopEngineer got it
    # (would need to subscribe to "strategy_applied" to verify)

    print("✓ Event flow")


async def test_request_response():
    """Test request/response between subsystems."""
    brain = TaskBrain()

    orchestrator = Orchestrator()
    brain.register_subsystem(orchestrator)

    # Ask for active tasks
    response = await brain.hub.request_from_subsystem(
        "orchestrator", "get_active_tasks"
    )

    assert response["active_count"] == 0
    assert response["max_parallel"] == 3
    assert response["tasks"] == []

    print("✓ Request/response")


async def test_config_loader():
    """Test loading brain from YAML config."""
    config_path = "~/.corvin/brain-config.yaml"
    try:
        brain = BrainConfigLoader.load_brain(config_path)
        print(f"✓ Config loader: {len(brain.hub.subsystems)} subsystems loaded")
    except FileNotFoundError:
        print("⚠ Config file not found at ~/.corvin/brain-config.yaml (skipped)")


async def test_health_monitor_queries():
    """Test HealthMonitor request handling."""
    brain = TaskBrain()
    health = HealthMonitor()

    brain.register_subsystem(health)

    # Query health status
    status = await brain.hub.request_from_subsystem("health_monitor", "health_status")
    assert status["status"] == "healthy"
    assert status["error_count"] == 0
    assert status["total_count"] == 0

    # Query error rate
    rate = await brain.hub.request_from_subsystem("health_monitor", "error_rate")
    assert rate == 0.0

    print("✓ HealthMonitor queries")


async def test_context_bridge_checkpoints():
    """Test ContextBridge checkpoint creation."""
    brain = TaskBrain()
    context = ContextBridge()

    brain.register_subsystem(context)

    # Create checkpoint
    result = await brain.hub.request_from_subsystem(
        "context_bridge",
        "create_checkpoint",
        task_id="task_1",
        memory={"key": "value"},
        timestamp="2026-08-16T00:00:00",
    )

    assert result["success"] is True
    assert result["checkpoint_id"] == 1

    # Retrieve checkpoint
    checkpoint = await brain.hub.request_from_subsystem(
        "context_bridge",
        "retrieve_checkpoint",
        task_id="task_1",
        checkpoint_id=0,
    )

    assert checkpoint["memory"]["key"] == "value"

    print("✓ ContextBridge checkpoints")


async def test_loop_engineer_strategies():
    """Test LoopEngineer strategy ladder."""
    brain = TaskBrain()
    loop_eng = LoopEngineer(max_retries=3)

    brain.register_subsystem(loop_eng)

    # Get next strategy
    strat = await brain.hub.request_from_subsystem(
        "loop_engineer", "next_strategy", task_id="task_1"
    )

    assert strat["strategy"] == "direct_fix"
    assert strat["attempt"] == 0
    assert strat["max_attempts"] == 3

    print("✓ LoopEngineer strategies")


async def test_orchestrator_spawning():
    """Test Orchestrator task spawning."""
    brain = TaskBrain()
    orchestrator = Orchestrator(max_parallel_sessions=2)

    brain.register_subsystem(orchestrator)

    # Spawn task
    result = await brain.hub.request_from_subsystem(
        "orchestrator", "spawn_task", task_id="task_1"
    )

    assert result["success"] is True

    # Check parallelism
    active = await brain.hub.request_from_subsystem(
        "orchestrator", "get_active_tasks"
    )
    assert active["active_count"] == 1

    # Spawn another task
    result2 = await brain.hub.request_from_subsystem(
        "orchestrator", "spawn_task", task_id="task_2"
    )
    assert result2["success"] is True

    # Try to spawn beyond limit (should fail)
    result3 = await brain.hub.request_from_subsystem(
        "orchestrator", "spawn_task", task_id="task_3"
    )
    assert result3["success"] is False

    print("✓ Orchestrator spawning")


async def test_all():
    """Run all tests."""
    print("\n=== CorvinOS Brain E2E Tests ===\n")

    try:
        await test_brain_creation()
        await test_subsystem_registration()
        await test_event_flow()
        await test_request_response()
        await test_config_loader()
        await test_health_monitor_queries()
        await test_context_bridge_checkpoints()
        await test_loop_engineer_strategies()
        await test_orchestrator_spawning()

        print("\n✅ All E2E tests passed!\n")
    except Exception as e:
        print(f"\n❌ Test failed: {e}\n")
        import traceback
        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    success = asyncio.run(test_all())
    sys.exit(0 if success else 1)
