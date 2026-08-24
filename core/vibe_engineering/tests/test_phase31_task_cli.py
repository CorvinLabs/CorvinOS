"""Phase 3.1a: TaskCLI Unit Tests (list, resume, status, monitor, auto_resume)."""

import pytest
import json
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from ..task_cli import TaskCLI
from ..status_snapshot import StatusSnapshot, TaskState, StatusPublisher


@pytest.fixture
def publisher():
    """Create test publisher."""
    return StatusPublisher()


@pytest.fixture
def state_store():
    """Mock state store."""
    store = MagicMock()
    store.load_checkpoint = AsyncMock()
    return store


@pytest.fixture
def vibe_engine():
    """Mock vibe engine."""
    engine = MagicMock()
    engine.execute_task = AsyncMock()
    return engine


@pytest.fixture
def cli(state_store, vibe_engine, publisher):
    """Create test CLI instance."""
    cli = TaskCLI(state_store, vibe_engine)
    cli.publisher = publisher
    return cli


@pytest.mark.asyncio
async def test_list_tasks_empty(cli):
    """Test: list_tasks returns empty list when no tasks."""
    tasks = await cli.list_tasks()
    assert tasks == []


@pytest.mark.asyncio
async def test_list_tasks_pending_only(cli):
    """Test: list_tasks returns only pending (non-terminal) tasks."""
    # Add running task
    snap1 = StatusSnapshot(task_id="task_1", session_id="s1", state=TaskState.RUNNING)
    # Add completed task (should be filtered)
    snap2 = StatusSnapshot(task_id="task_2", session_id="s1", state=TaskState.COMPLETED)
    # Add awaiting-input task
    snap3 = StatusSnapshot(task_id="task_3", session_id="s1", state=TaskState.AWAITING_INPUT)

    await cli.publisher.publish(snap1)
    await cli.publisher.publish(snap2)
    await cli.publisher.publish(snap3)

    tasks = await cli.list_tasks()
    # Should return task_1 and task_3, not task_2 (completed)
    assert "task_1" in tasks
    assert "task_3" in tasks
    assert "task_2" not in tasks


@pytest.mark.asyncio
async def test_status_task_not_found(cli):
    """Test: status returns not_found for unknown task."""
    result = await cli.status("unknown_task")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_status_task_found(cli):
    """Test: status returns snapshot dict for known task."""
    snap = StatusSnapshot(task_id="test_001", session_id="s1", state=TaskState.RUNNING)
    await cli.publisher.publish(snap)

    result = await cli.status("test_001")
    assert result["task_id"] == "test_001"
    assert result["state"] == "running"


@pytest.mark.asyncio
async def test_resume_no_checkpoint_found(cli):
    """Test: resume returns error when no checkpoint exists."""
    with TemporaryDirectory() as tmpdir:
        with patch('pathlib.Path.expanduser') as mock_expand:
            mock_expand.return_value = Path(tmpdir)
            result = await cli.resume("nonexistent_task")
            assert result["status"] == "error"
            assert "No checkpoint found" in result["reason"]


@pytest.mark.asyncio
async def test_resume_loads_checkpoint(cli):
    """Test: resume loads checkpoint JSON and parses context."""
    with TemporaryDirectory() as tmpdir:
        checkpoint_dir = Path(tmpdir)
        checkpoint_file = checkpoint_dir / "test_001_ckpt.json"

        checkpoint_data = {
            "checkpoint_id": "ckpt_123",
            "task_id": "test_001",
            "iteration_num": 5,
            "context_state": {
                "goal": "Test task",
                "progress": {
                    "items_completed": 2,
                    "total_items": 10
                }
            }
        }

        checkpoint_file.write_text(json.dumps(checkpoint_data))

        with patch('pathlib.Path.expanduser') as mock_expand:
            mock_expand.return_value = checkpoint_dir
            result = await cli.resume("test_001")

            assert result["status"] == "loaded"
            assert result["checkpoint_id"] == "ckpt_123"
            assert result["iteration"] == 5
            assert result["context_summary"]["goal"] == "Test task"


@pytest.mark.asyncio
async def test_resume_invalid_json(cli):
    """Test: resume handles invalid JSON gracefully."""
    with TemporaryDirectory() as tmpdir:
        checkpoint_dir = Path(tmpdir)
        checkpoint_file = checkpoint_dir / "test_001_ckpt.json"
        checkpoint_file.write_text("{ invalid json }")

        with patch('pathlib.Path.expanduser') as mock_expand:
            mock_expand.return_value = checkpoint_dir
            result = await cli.resume("test_001")

            assert result["status"] == "error"
            assert "JSON parse error" in result["reason"]


@pytest.mark.asyncio
async def test_auto_resume_no_unfinished_tasks(cli):
    """Test: auto_resume returns not_found when no unfinished tasks."""
    # Only completed tasks
    snap = StatusSnapshot(task_id="task_1", session_id="s1", state=TaskState.COMPLETED)
    await cli.publisher.publish(snap)

    result = await cli.auto_resume_last_unfinished()
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_auto_resume_finds_unfinished_task(cli):
    """Test: auto_resume finds and attempts to resume last unfinished task."""
    snap1 = StatusSnapshot(
        task_id="task_1",
        session_id="s1",
        state=TaskState.RUNNING,
        can_resume=True
    )
    snap2 = StatusSnapshot(
        task_id="task_2",
        session_id="s1",
        state=TaskState.AWAITING_INPUT,
        can_resume=True
    )

    await cli.publisher.publish(snap1)
    await cli.publisher.publish(snap2)

    with patch.object(cli, 'resume', new_callable=AsyncMock) as mock_resume:
        mock_resume.return_value = {"status": "loaded", "checkpoint_id": "ckpt_123"}
        result = await cli.auto_resume_last_unfinished()

        # Should have called resume with task_2 (last unfinished)
        mock_resume.assert_called_once()
        args, _ = mock_resume.call_args
        assert args[0] == "task_2"


@pytest.mark.asyncio
async def test_monitor_task_completed(cli):
    """Test: monitor exits when task completes."""
    snap = StatusSnapshot(task_id="test_001", session_id="s1", state=TaskState.RUNNING)
    await cli.publisher.publish(snap)

    # Simulate task completion
    async def complete_after_delay():
        await asyncio.sleep(0.1)
        snap.state = TaskState.COMPLETED
        await cli.publisher.publish(snap)

    asyncio.create_task(complete_after_delay())

    # Monitor should exit once task is completed
    with patch('builtins.print'):
        await cli.monitor("test_001", poll_interval=0.05, max_iterations=100)


@pytest.mark.asyncio
async def test_monitor_task_not_found(cli):
    """Test: monitor handles task-not-found gracefully."""
    with patch('builtins.print'):
        await cli.monitor("nonexistent_task", poll_interval=0.05, max_iterations=1)


@pytest.mark.asyncio
async def test_monitor_timeout(cli):
    """Test: monitor respects max_iterations timeout."""
    snap = StatusSnapshot(task_id="test_001", session_id="s1", state=TaskState.RUNNING)
    await cli.publisher.publish(snap)

    with patch('builtins.print'):
        # With poll_interval=0.01 and max_iterations=5, should timeout quickly
        await cli.monitor("test_001", poll_interval=0.01, max_iterations=5)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
