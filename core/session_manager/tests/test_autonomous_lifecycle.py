"""E2E tests for autonomous session lifecycle (ADR-0471)."""

import pytest
import tempfile
from datetime import datetime

from ..lifecycle_manager import SessionLifecycleManager, SplitTrigger, Checkpoint
from ..checkpoint_manager import CheckpointManager


@pytest.fixture
def lifecycle_manager():
    return SessionLifecycleManager()


@pytest.fixture
def checkpoint_manager():
    return CheckpointManager(checkpoint_dir=tempfile.mkdtemp())


@pytest.mark.asyncio
async def test_split_decision_context_limit(lifecycle_manager):
    """Test: context at 85% triggers split."""
    decision = lifecycle_manager.should_split_session(
        session_id="session_1",
        context_usage_pct=0.85,
        phase="execution",
        total_tokens_used=100000,
        iterations=10,
        last_progress_ts=datetime.now().timestamp(),
    )

    assert decision.should_split is True
    assert decision.trigger == SplitTrigger.CONTEXT_LIMIT


@pytest.mark.asyncio
async def test_split_decision_token_budget(lifecycle_manager):
    """Test: token budget exceeded triggers split."""
    decision = lifecycle_manager.should_split_session(
        session_id="session_1",
        context_usage_pct=0.50,
        phase="execution",
        total_tokens_used=500000,  # Daily budget
        iterations=10,
        last_progress_ts=datetime.now().timestamp(),
    )

    assert decision.should_split is True
    assert decision.trigger == SplitTrigger.TOKEN_BUDGET


@pytest.mark.asyncio
async def test_split_decision_iteration_cap(lifecycle_manager):
    """Test: iteration cap triggers split."""
    decision = lifecycle_manager.should_split_session(
        session_id="session_1",
        context_usage_pct=0.50,
        phase="execution",
        total_tokens_used=100000,
        iterations=50,  # At cap
        last_progress_ts=datetime.now().timestamp(),
    )

    assert decision.should_split is True
    assert decision.trigger == SplitTrigger.ITERATION_CAP


@pytest.mark.asyncio
async def test_no_split_needed(lifecycle_manager):
    """Test: no split when all metrics are green."""
    decision = lifecycle_manager.should_split_session(
        session_id="session_1",
        context_usage_pct=0.50,
        phase="execution",
        total_tokens_used=100000,
        iterations=10,
        last_progress_ts=datetime.now().timestamp(),
    )

    assert decision.should_split is False
    assert decision.trigger == SplitTrigger.NONE


@pytest.mark.asyncio
async def test_create_checkpoint(lifecycle_manager):
    """Test: checkpoint creation with goal hash."""
    checkpoint = await lifecycle_manager.create_checkpoint(
        session_id="session_1",
        goal="Audit entire codebase",
        context={"tokens_used": 50000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_123",
        phase="execution",
    )

    assert checkpoint.session_id == "session_1"
    assert checkpoint.goal == "Audit entire codebase"
    assert checkpoint.goal_hash is not None
    assert checkpoint.checkpoint_hash is not None
    assert checkpoint.context_reduction_pct == 50.0


@pytest.mark.asyncio
async def test_checkpoint_storage(lifecycle_manager, checkpoint_manager):
    """Test: checkpoint save and load."""
    checkpoint = await lifecycle_manager.create_checkpoint(
        session_id="session_2",
        goal="Implement feature",
        context={"tokens_used": 30000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_456",
    )

    # Save
    saved = await checkpoint_manager.save_checkpoint(checkpoint)
    assert saved is True

    # Load
    loaded = await checkpoint_manager.load_checkpoint("session_2")
    assert loaded is not None
    assert loaded.session_id == "session_2"
    assert loaded.goal == "Implement feature"
    assert loaded.goal_hash == checkpoint.goal_hash


@pytest.mark.asyncio
async def test_goal_continuity_verified(lifecycle_manager):
    """Test: goal continuity check passes on same goal."""
    checkpoint = await lifecycle_manager.create_checkpoint(
        session_id="session_3",
        goal="Deploy to production",
        context={"tokens_used": 20000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_789",
    )

    # Verify continuity with same goal
    is_continuous = await lifecycle_manager.verify_continuity(
        checkpoint,
        new_session_goal="Deploy to production",
    )

    assert is_continuous is True


@pytest.mark.asyncio
async def test_goal_drift_fails_closed(lifecycle_manager):
    """Test: goal drift is detected and fails-closed (ADR-0471)."""
    checkpoint = await lifecycle_manager.create_checkpoint(
        session_id="session_4",
        goal="Audit codebase",
        context={"tokens_used": 40000, "tokens_available": 100000},
        audit_trail_hash="audit_hash_999",
    )

    # Try resume with different goal
    is_continuous = await lifecycle_manager.verify_continuity(
        checkpoint,
        new_session_goal="Deploy to production",  # DIFFERENT GOAL
    )

    assert is_continuous is False  # Fail-closed


@pytest.mark.asyncio
async def test_phase_exit_signal(lifecycle_manager):
    """Test: operator can signal phase exit."""
    decision = lifecycle_manager.signal_phase_exit(
        session_id="session_5",
        next_phase="validation",
    )

    assert decision.should_split is True
    assert decision.trigger == SplitTrigger.PHASE_EXIT
    assert decision.new_phase == "validation"
