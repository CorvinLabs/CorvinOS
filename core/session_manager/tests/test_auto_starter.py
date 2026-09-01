"""E2E tests for SessionAutoStarter (ADR-0472)."""

import pytest
from datetime import datetime

from ..auto_starter import SessionAutoStarter
from ..lifecycle_manager import SessionLifecycleManager
from ..checkpoint_manager import CheckpointManager
import tempfile


@pytest.fixture
def lifecycle_manager():
    return SessionLifecycleManager()


@pytest.fixture
def checkpoint_manager():
    return CheckpointManager(checkpoint_dir=tempfile.mkdtemp())


@pytest.fixture
def auto_starter(lifecycle_manager, checkpoint_manager):
    return SessionAutoStarter(lifecycle_manager, checkpoint_manager)


@pytest.mark.asyncio
async def test_task_start_initializes_state(auto_starter):
    """Test: on_task_start creates initial task state."""
    session_id = await auto_starter.on_task_start(
        task_id="audit_123",
        goal="Audit entire codebase",
        tenant_id="_default",
    )

    assert session_id.startswith("session_audit_123")
    assert auto_starter.get_task_state("audit_123") is not None
    assert auto_starter.get_task_state("audit_123")["goal"] == "Audit entire codebase"


@pytest.mark.asyncio
async def test_split_on_context_limit(auto_starter):
    """Test: auto-splits when context reaches 85%."""
    # Start task
    await auto_starter.on_task_start(
        task_id="task_1",
        goal="Long analysis",
        tenant_id="_default",
    )

    # Progress until context limit
    new_session_id = await auto_starter.on_task_progress(
        task_id="task_1",
        context_usage_pct=0.85,  # Trigger split
        iterations=10,
        context={"tokens_used": 50000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_123",
    )

    assert new_session_id is not None
    assert new_session_id != auto_starter.get_task_state("task_1")["session_id"]
    assert auto_starter.get_task_state("task_1")["split_count"] == 1


@pytest.mark.asyncio
async def test_no_split_when_context_ok(auto_starter):
    """Test: no split when context usage is low."""
    await auto_starter.on_task_start(
        task_id="task_2",
        goal="Quick task",
        tenant_id="_default",
    )

    new_session_id = await auto_starter.on_task_progress(
        task_id="task_2",
        context_usage_pct=0.50,  # Well below 85%
        iterations=5,
        context={"tokens_used": 30000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_456",
    )

    assert new_session_id is None
    assert auto_starter.get_task_state("task_2")["split_count"] == 0


@pytest.mark.asyncio
async def test_goal_drift_prevents_split(auto_starter):
    """Test: goal drift is detected and split refused (fail-closed)."""
    await auto_starter.on_task_start(
        task_id="task_3",
        goal="Audit codebase",  # Original goal
        tenant_id="_default",
    )

    # Simulate goal change (drift)
    state = auto_starter.get_task_state("task_3")
    state["goal"] = "Deploy to production"  # CHANGED GOAL

    # Try to split with new goal
    new_session_id = await auto_starter.on_task_progress(
        task_id="task_3",
        context_usage_pct=0.85,  # Would trigger split
        iterations=10,
        context={"tokens_used": 50000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_789",
    )

    # Split should be refused (goal mismatch)
    assert new_session_id is None


@pytest.mark.asyncio
async def test_task_completion_reports_metadata(auto_starter):
    """Test: on_task_complete returns correct metadata."""
    await auto_starter.on_task_start(
        task_id="task_4",
        goal="Test task",
        tenant_id="_default",
    )

    # Simulate some splits
    await auto_starter.on_task_progress(
        task_id="task_4",
        context_usage_pct=0.85,
        iterations=10,
        context={"tokens_used": 50000, "tokens_available": 100000},
        audit_trail_hash="audit_1",
    )

    # Complete task
    metadata = await auto_starter.on_task_complete("task_4")

    assert metadata["task_id"] == "task_4"
    assert metadata["splits"] == 1
    assert metadata["iterations"] == 10
    assert "duration_seconds" in metadata


@pytest.mark.asyncio
async def test_multiple_sequential_splits(auto_starter):
    """Test: task can split multiple times in sequence."""
    await auto_starter.on_task_start(
        task_id="task_5",
        goal="Multi-phase task",
        tenant_id="_default",
    )

    # First split
    session_1 = auto_starter.get_task_state("task_5")["session_id"]
    new_session_2 = await auto_starter.on_task_progress(
        task_id="task_5",
        context_usage_pct=0.85,
        iterations=10,
        context={"tokens_used": 50000, "tokens_available": 100000},
        audit_trail_hash="audit_1",
    )
    assert new_session_2 is not None
    assert new_session_2 != session_1

    # Second split
    session_2 = auto_starter.get_task_state("task_5")["session_id"]
    assert session_2 == new_session_2
    new_session_3 = await auto_starter.on_task_progress(
        task_id="task_5",
        context_usage_pct=0.85,
        iterations=20,
        context={"tokens_used": 50000, "tokens_available": 100000},
        audit_trail_hash="audit_2",
    )
    assert new_session_3 is not None
    assert new_session_3 != session_2

    # Verify split count
    assert auto_starter.get_task_state("task_5")["split_count"] == 2
