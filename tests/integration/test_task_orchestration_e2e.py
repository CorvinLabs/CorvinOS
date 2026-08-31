"""E2E tests: TaskOrchestrator DAG execution (ADR-0402)."""

import pytest
import asyncio
import tempfile
from datetime import datetime

from core.vibe_engineering.task_orchestrator import (
    TaskOrchestrator, Phase, TaskSpec, TaskStatus, PhaseStatus
)
from core.vibe_engineering.task_registry import TaskRegistryPersistence


@pytest.fixture
def temp_registry():
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = f"{tmpdir}/registry.jsonl"
        yield TaskRegistryPersistence(registry_path)


@pytest.mark.asyncio
async def test_single_phase_task(temp_registry):
    """Test executing a task with one phase."""
    orchestrator = TaskOrchestrator(temp_registry)
    events_emitted = []

    orchestrator.on_event("phase.completed", lambda data: events_emitted.append(data))
    orchestrator.on_event("task.completed", lambda data: events_emitted.append(data))

    async def gather_phase():
        return {"papers": 24}

    phase = Phase(phase_id="gather", handler=gather_phase)
    spec = TaskSpec(task_id="task-1", title="Single Phase Task", phases=[phase])

    task = await orchestrator.execute(spec)

    assert task.status == TaskStatus.COMPLETED
    assert task.phases["gather"].status == PhaseStatus.COMPLETED
    assert task.phases["gather"].result == {"papers": 24}
    assert len(events_emitted) == 2  # phase.completed + task.completed


@pytest.mark.asyncio
async def test_two_phase_dag_with_dependency(temp_registry):
    """Test DAG execution with phase dependency."""
    orchestrator = TaskOrchestrator(temp_registry)

    async def phase1():
        await asyncio.sleep(0.01)
        return {"data": "from_phase1"}

    async def phase2():
        await asyncio.sleep(0.01)
        return {"processed": True}

    phases = [
        Phase(phase_id="gather", handler=phase1),
        Phase(phase_id="analyze", handler=phase2, depends_on=["gather"]),
    ]
    spec = TaskSpec(task_id="task-dag", title="DAG Task", phases=phases)

    task = await orchestrator.execute(spec)

    assert task.status == TaskStatus.COMPLETED
    assert task.phases["gather"].status == PhaseStatus.COMPLETED
    assert task.phases["analyze"].status == PhaseStatus.COMPLETED
    # Verify ordering: gather completed before analyze started
    assert (task.phases["gather"].completed_at <= task.phases["analyze"].started_at)


@pytest.mark.asyncio
async def test_phase_failure_with_retry(temp_registry):
    """Test phase failure and retry logic."""
    orchestrator = TaskOrchestrator(temp_registry)
    attempt_count = 0

    async def flaky_phase():
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count < 2:
            raise RuntimeError("Transient error")
        return {"recovered": True}

    phase = Phase(phase_id="flaky", handler=flaky_phase, retry_count=3)
    spec = TaskSpec(task_id="task-retry", title="Retry Task", phases=[phase])

    task = await orchestrator.execute(spec)

    assert task.status == TaskStatus.COMPLETED
    assert task.phases["flaky"].status == PhaseStatus.COMPLETED
    assert task.phases["flaky"].retry_count == 1  # Retried once, succeeded
    assert attempt_count == 2


@pytest.mark.asyncio
async def test_parallel_phases(temp_registry):
    """Test concurrent execution of independent phases."""
    orchestrator = TaskOrchestrator(temp_registry)
    start_times = {}
    end_times = {}

    async def phase_a():
        start_times["a"] = datetime.now()
        await asyncio.sleep(0.05)
        end_times["a"] = datetime.now()
        return {"result": "a"}

    async def phase_b():
        start_times["b"] = datetime.now()
        await asyncio.sleep(0.05)
        end_times["b"] = datetime.now()
        return {"result": "b"}

    phases = [
        Phase(phase_id="phase_a", handler=phase_a),
        Phase(phase_id="phase_b", handler=phase_b),
    ]
    spec = TaskSpec(task_id="task-parallel", title="Parallel Task", phases=phases)

    task = await orchestrator.execute(spec)

    assert task.status == TaskStatus.COMPLETED
    # Both phases should complete roughly at same time (parallel execution)
    time_diff = (end_times["a"] - start_times["a"]).total_seconds()
    assert time_diff < 0.1  # Both ran concurrently, not sequentially


@pytest.mark.asyncio
async def test_three_phase_complex_dag(temp_registry):
    """Test complex DAG: gather → (analyze, validate) → report."""
    orchestrator = TaskOrchestrator(temp_registry)

    async def gather():
        return {"sources": 24}

    async def analyze():
        return {"analysis": "complete"}

    async def validate():
        return {"valid": True}

    async def report():
        return {"report": "done"}

    phases = [
        Phase(phase_id="gather", handler=gather),
        Phase(phase_id="analyze", handler=analyze, depends_on=["gather"]),
        Phase(phase_id="validate", handler=validate, depends_on=["gather"]),
        Phase(phase_id="report", handler=report, depends_on=["analyze", "validate"]),
    ]
    spec = TaskSpec(task_id="task-complex", title="Complex DAG", phases=phases)

    task = await orchestrator.execute(spec)

    assert task.status == TaskStatus.COMPLETED
    assert all(p.status == PhaseStatus.COMPLETED for p in task.phases.values())
    # Verify dependency ordering
    assert task.phases["gather"].completed_at <= task.phases["analyze"].started_at
    assert task.phases["gather"].completed_at <= task.phases["validate"].started_at
    assert task.phases["analyze"].completed_at <= task.phases["report"].started_at
    assert task.phases["validate"].completed_at <= task.phases["report"].started_at
