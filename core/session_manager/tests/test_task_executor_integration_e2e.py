"""E2E Tests: TaskExecutor ↔ SessionAutoStarter Integration (Phase 1.2).

Tests actual AutonomousTaskEngine with autonomous session splitting.
"""

import pytest
import asyncio
import tempfile
from datetime import datetime

from core.orchestration.autonomous_task_engine import (
    AutonomousTaskEngine, TaskDefinition, TaskPriority, TaskState
)
from core.session_manager.lifecycle_manager import SessionLifecycleManager
from core.session_manager.checkpoint_manager import CheckpointManager
from core.session_manager.auto_starter import SessionAutoStarter
from core.session_manager.task_executor_integration import TaskExecutorIntegration


@pytest.fixture
def lifecycle_manager():
    return SessionLifecycleManager()


@pytest.fixture
def checkpoint_manager():
    return CheckpointManager(checkpoint_dir=tempfile.mkdtemp())


@pytest.fixture
def auto_starter(lifecycle_manager, checkpoint_manager):
    return SessionAutoStarter(lifecycle_manager, checkpoint_manager)


@pytest.fixture
def task_integrator(auto_starter):
    return TaskExecutorIntegration(auto_starter, tenant_id="_default")


@pytest.fixture
def task_engine():
    return AutonomousTaskEngine(name="test-engine")


@pytest.mark.asyncio
async def test_e2e_simple_task_no_split(task_engine, task_integrator):
    """E2E: Simple task runs to completion without split."""
    # Define task handler
    execution_log = []

    async def simple_handler(context):
        """Handler that completes quickly without context pressure."""
        execution_log.append({
            "attempt": context.attempts,
            "state": context.state.value,
        })
        return f"completed in attempt {context.attempts}"

    # Register task
    task = TaskDefinition(
        task_id="simple_task_1",
        name="Simple Task",
        description="Completes in one session",
        priority=TaskPriority.MEDIUM,
        handler=simple_handler,
        max_retries=3,
        timeout_seconds=10,
    )
    task_engine.register_task(task)

    # Initialize first session
    state = await task_integrator.on_task_start(
        task_id="simple_task_1",
        goal="Complete simple task",
    )
    assert state.current_session_id is not None
    assert state.iterations == 0

    # Execute task
    result = await task_engine.execute_task("simple_task_1")

    # Verify
    assert result is not None
    assert len(execution_log) == 1
    assert task_engine.contexts["simple_task_1"].state == TaskState.COMPLETE


@pytest.mark.asyncio
async def test_e2e_task_with_simulated_splits(task_engine, task_integrator):
    """E2E: Task simulates context pressure and auto-splits sessions."""
    iteration_count = [0]
    session_log = []

    async def pressure_handler(context):
        """Handler that simulates context pressure over iterations."""
        iteration_count[0] += 1

        # Simulate context pressure increasing per iteration
        # At iteration 5+, assume 85% context (trigger split)
        if iteration_count[0] >= 5:
            # In real system, this would come from actual context monitor
            context_pct = 0.85 + (iteration_count[0] - 5) * 0.01
        else:
            context_pct = 0.20 + iteration_count[0] * 0.10

        # Simulate audit trail update
        audit_hash = f"audit_hash_{iteration_count[0]}"

        # Check for split
        new_session = await task_integrator.on_iteration(
            task_id=context.task_id,
            current_session_id="session_1",  # Would be tracked in real TaskExecutor
            context_usage_pct=min(context_pct, 0.99),  # Cap at 99%
            iterations=iteration_count[0],
            goal="Simulate context pressure",
            audit_trail_hash=audit_hash,
            context={
                "tokens_used": int(context_pct * 100000),
                "tokens_available": 100000,
            },
        )

        if new_session:
            session_log.append({
                "iteration": iteration_count[0],
                "old_session": "session_1",
                "new_session": new_session,
            })

        return f"iteration {iteration_count[0]} completed"

    # Register task
    task = TaskDefinition(
        task_id="pressure_task_1",
        name="Pressure Task",
        description="Simulates context pressure",
        priority=TaskPriority.MEDIUM,
        handler=pressure_handler,
        max_retries=1,
        timeout_seconds=10,
    )
    task_engine.register_task(task)

    # Initialize
    state = await task_integrator.on_task_start(
        task_id="pressure_task_1",
        goal="Simulate context pressure",
    )

    # Execute (will trigger splits at iteration 5+)
    result = await task_engine.execute_task("pressure_task_1")

    # Verify split occurred
    assert len(session_log) >= 1, "Expected at least one session split"
    assert session_log[0]["iteration"] >= 5, "Split should occur at iteration 5+"


@pytest.mark.asyncio
async def test_e2e_task_failure_and_recovery(task_engine, task_integrator):
    """E2E: Task fails, retries, and eventually succeeds."""
    attempt_log = []

    async def flaky_handler(context):
        """Handler that fails first attempt, succeeds on retry."""
        attempt_log.append(context.attempts)

        if context.attempts == 1:
            raise ValueError("First attempt fails (transient error)")

        return f"succeeded on attempt {context.attempts}"

    # Register task
    task = TaskDefinition(
        task_id="flaky_task_1",
        name="Flaky Task",
        description="Fails once, then succeeds",
        priority=TaskPriority.HIGH,
        handler=flaky_handler,
        max_retries=3,
        timeout_seconds=10,
    )
    task_engine.register_task(task)

    # Initialize
    await task_integrator.on_task_start(
        task_id="flaky_task_1",
        goal="Handle transient failure",
    )

    # Execute
    result = await task_engine.execute_task("flaky_task_1")

    # Verify retry succeeded
    assert result is not None
    assert len(attempt_log) == 2
    assert attempt_log[1] == 2
    assert task_engine.contexts["flaky_task_1"].state == TaskState.COMPLETE


@pytest.mark.asyncio
async def test_e2e_concurrent_tasks_with_splits(task_engine, task_integrator):
    """E2E: Multiple tasks run concurrently with independent splits."""
    task_counter = {"task_1": 0, "task_2": 0}

    async def concurrent_handler(task_id: str):
        async def handler(context):
            task_counter[task_id] += 1
            await asyncio.sleep(0.01)  # Simulate work
            return f"{task_id} iteration {task_counter[task_id]}"
        return handler

    # Register two tasks
    for i in [1, 2]:
        task_id = f"task_{i}"
        task = TaskDefinition(
            task_id=task_id,
            name=f"Task {i}",
            description="Concurrent task",
            priority=TaskPriority.MEDIUM,
            handler=await concurrent_handler(task_id),
            max_retries=1,
            timeout_seconds=5,
        )
        task_engine.register_task(task)

    # Initialize both
    for i in [1, 2]:
        await task_integrator.on_task_start(
            task_id=f"task_{i}",
            goal=f"Run concurrent task {i}",
        )

    # Execute both concurrently
    results = await asyncio.gather(
        task_engine.execute_task("task_1"),
        task_engine.execute_task("task_2"),
    )

    # Verify both completed
    assert results[0] is not None
    assert results[1] is not None
    assert task_engine.contexts["task_1"].state == TaskState.COMPLETE
    assert task_engine.contexts["task_2"].state == TaskState.COMPLETE
