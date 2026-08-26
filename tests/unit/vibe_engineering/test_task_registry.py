"""Tests for TaskRegistry persistence layer (ADR-0402)."""

import pytest
import asyncio
import json
import tempfile
from pathlib import Path
from datetime import datetime

from core.vibe_engineering.task_registry import (
    TaskMetadata,
    PhaseMetadata,
    PhaseStatus,
    TaskStatus,
    TaskRegistryPersistence,
)


@pytest.fixture
def temp_registry_path():
    """Create temporary registry file for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        registry_path = Path(tmpdir) / "registry.jsonl"
        yield str(registry_path)


@pytest.mark.asyncio
async def test_task_metadata_content_hash():
    """Test that content hash is deterministic."""
    task1 = TaskMetadata(
        task_id="task-1",
        title="Test Task",
        status=TaskStatus.RUNNING,
        phases={},
        tenant_id="test-tenant",
    )
    task2 = TaskMetadata(
        task_id="task-1",
        title="Test Task",
        status=TaskStatus.RUNNING,
        phases={},
        tenant_id="test-tenant",
    )

    assert task1.content_hash() == task2.content_hash()


@pytest.mark.asyncio
async def test_append_and_retrieve_task(temp_registry_path):
    """Test appending and retrieving a task."""
    registry = TaskRegistryPersistence(temp_registry_path)

    # Create and append a task
    task = TaskMetadata(
        task_id="task-1",
        title="Test Task",
        status=TaskStatus.RUNNING,
        phases={},
        tenant_id="test-tenant",
    )

    await registry.append_task(task)

    # Retrieve and verify
    retrieved = await registry.get_task("task-1", "test-tenant")
    assert retrieved is not None
    assert retrieved.task_id == "task-1"
    assert retrieved.title == "Test Task"
    assert retrieved.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_append_with_phases(temp_registry_path):
    """Test appending a task with phases."""
    registry = TaskRegistryPersistence(temp_registry_path)

    phases = {
        "gather": PhaseMetadata(
            phase_id="gather",
            status=PhaseStatus.COMPLETED,
            started_at=datetime.now(),
            completed_at=datetime.now(),
            retry_count=0,
            result={"papers_found": 24},
        ),
        "analyze": PhaseMetadata(
            phase_id="analyze",
            status=PhaseStatus.RUNNING,
            started_at=datetime.now(),
            retry_count=0,
        ),
    }

    task = TaskMetadata(
        task_id="task-research",
        title="Research Task",
        status=TaskStatus.RUNNING,
        phases=phases,
        tenant_id="test-tenant",
    )

    await registry.append_task(task)

    retrieved = await registry.get_task("task-research", "test-tenant")
    assert len(retrieved.phases) == 2
    assert retrieved.phases["gather"].result == {"papers_found": 24}
    assert retrieved.phases["gather"].status == PhaseStatus.COMPLETED
    assert retrieved.phases["analyze"].status == PhaseStatus.RUNNING


@pytest.mark.asyncio
async def test_list_tasks(temp_registry_path):
    """Test listing all tasks for a tenant."""
    registry = TaskRegistryPersistence(temp_registry_path)

    # Append multiple tasks
    for i in range(3):
        task = TaskMetadata(
            task_id=f"task-{i}",
            title=f"Task {i}",
            status=TaskStatus.RUNNING,
            phases={},
            tenant_id="test-tenant",
        )
        await registry.append_task(task)

    # List and verify
    tasks = await registry.list_tasks("test-tenant")
    assert len(tasks) == 3
    assert all(t.tenant_id == "test-tenant" for t in tasks)


@pytest.mark.asyncio
async def test_latest_version_retrieval(temp_registry_path):
    """Test that get_task returns the LATEST version of a task."""
    registry = TaskRegistryPersistence(temp_registry_path)

    # Append version 1
    task_v1 = TaskMetadata(
        task_id="task-1",
        title="Task (v1)",
        status=TaskStatus.RUNNING,
        phases={},
        tenant_id="test-tenant",
    )
    await registry.append_task(task_v1)

    # Append version 2 (same task_id, different status)
    task_v2 = TaskMetadata(
        task_id="task-1",
        title="Task (v2)",
        status=TaskStatus.COMPLETED,
        phases={},
        tenant_id="test-tenant",
    )
    await registry.append_task(task_v2)

    # Retrieve should return v2
    retrieved = await registry.get_task("task-1", "test-tenant")
    assert retrieved.title == "Task (v2)"
    assert retrieved.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_tenant_isolation(temp_registry_path):
    """Test that tasks are isolated by tenant_id."""
    registry = TaskRegistryPersistence(temp_registry_path)

    task_t1 = TaskMetadata(
        task_id="task-1",
        title="Task in tenant-1",
        status=TaskStatus.RUNNING,
        phases={},
        tenant_id="tenant-1",
    )

    task_t2 = TaskMetadata(
        task_id="task-1",  # Same ID, different tenant
        title="Task in tenant-2",
        status=TaskStatus.RUNNING,
        phases={},
        tenant_id="tenant-2",
    )

    await registry.append_task(task_t1)
    await registry.append_task(task_t2)

    # Retrieve from tenant-1 should get t1
    retrieved_t1 = await registry.get_task("task-1", "tenant-1")
    assert retrieved_t1.title == "Task in tenant-1"

    # Retrieve from tenant-2 should get t2
    retrieved_t2 = await registry.get_task("task-1", "tenant-2")
    assert retrieved_t2.title == "Task in tenant-2"

    # List from tenant-1 should have 1 task
    tasks_t1 = await registry.list_tasks("tenant-1")
    assert len(tasks_t1) == 1


@pytest.mark.asyncio
async def test_concurrent_writes_are_serialized(temp_registry_path):
    """Test that concurrent writes are serialized via lock."""
    registry = TaskRegistryPersistence(temp_registry_path)

    async def append_task(task_id):
        task = TaskMetadata(
            task_id=task_id,
            title=f"Task {task_id}",
            status=TaskStatus.RUNNING,
            phases={},
            tenant_id="test-tenant",
        )
        await registry.append_task(task)

    # Launch concurrent appends
    await asyncio.gather(append_task("task-1"), append_task("task-2"), append_task("task-3"))

    # Verify all were written
    tasks = await registry.list_tasks("test-tenant")
    assert len(tasks) == 3


@pytest.mark.asyncio
async def test_registry_file_format_is_jsonl(temp_registry_path):
    """Test that registry file is valid JSONL (one JSON per line)."""
    registry = TaskRegistryPersistence(temp_registry_path)

    # Append a few tasks
    for i in range(3):
        task = TaskMetadata(
            task_id=f"task-{i}",
            title=f"Task {i}",
            status=TaskStatus.RUNNING,
            phases={},
            tenant_id="test-tenant",
        )
        await registry.append_task(task)

    # Read and verify JSONL format
    with open(temp_registry_path, "r") as f:
        lines = f.readlines()

    assert len(lines) == 3
    for line in lines:
        record = json.loads(line)  # Should parse without error
        assert "task_id" in record
        assert "content_hash" in record
