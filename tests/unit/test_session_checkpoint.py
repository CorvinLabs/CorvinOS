"""Unit tests for session_checkpoint module (ADR-0367).

Tests SessionCheckpoint dataclass and SessionContinuationManager persistence.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.context_engineering.session_checkpoint import (
    SessionCheckpoint,
    SessionContinuationManager,
    CheckpointNotFoundError,
    CheckpointPersistenceError,
)
from core.context_engineering.execution_context import ExecutionContext, ContextStack


@pytest.fixture
def temp_corvin_home():
    """Create temporary CORVIN_HOME for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_execution_context():
    """Create a mock ExecutionContext for testing."""
    stack = ContextStack()
    stack.push("task", "test_task_1", complexity="high")

    ctx = ExecutionContext(
        task_id="test_task_1",
        tenant_id="_default",
        task_template={"task_type": "code_fix", "typical_duration_min": 30},
        context_stack=stack,
        budget_remaining=500.0,
        time_remaining=1800,
        model="claude-3-sonnet",
        strategy="direct_fix",
        strategy_confidence=0.8,
        guidance_overrides={"prefer_simple": True},
    )

    # Add decision history
    ctx.record_decision(
        subsystem="LoopEngineer",
        decision_type="strategy_selection",
        value="direct_fix",
        reasoning="Task appears straightforward",
        confidence=0.8,
    )

    return ctx


class TestSessionCheckpoint:
    """Tests for SessionCheckpoint dataclass."""

    def test_create_checkpoint(self, mock_execution_context):
        """Test creating a SessionCheckpoint."""
        checkpoint = SessionCheckpoint(
            checkpoint_id="cp_123",
            task_id="test_task_1",
            session_id="sess_abc",
            tenant_id="_default",
            context_state={
                "task_id": "test_task_1",
                "budget_remaining": 500.0,
                "model": "claude-3-sonnet",
            },
            turn_number=5,
            tokens_consumed=2000,
            cost_consumed_cents=50,
        )

        assert checkpoint.checkpoint_id == "cp_123"
        assert checkpoint.task_id == "test_task_1"
        assert checkpoint.turn_number == 5
        assert checkpoint.tokens_consumed == 2000

    def test_to_json(self, mock_execution_context):
        """Test serializing checkpoint to JSON."""
        checkpoint = SessionCheckpoint(
            checkpoint_id="cp_123",
            task_id="test_task_1",
            session_id="sess_abc",
            tenant_id="_default",
            context_state={"task_id": "test_task_1"},
            turn_number=5,
        )

        json_str = checkpoint.to_json()
        assert isinstance(json_str, str)
        data = json.loads(json_str)
        assert data["checkpoint_id"] == "cp_123"
        assert data["task_id"] == "test_task_1"

    def test_from_json(self, mock_execution_context):
        """Test deserializing checkpoint from JSON."""
        original = SessionCheckpoint(
            checkpoint_id="cp_123",
            task_id="test_task_1",
            session_id="sess_abc",
            tenant_id="_default",
            context_state={"task_id": "test_task_1"},
            turn_number=5,
        )

        json_str = original.to_json()
        restored = SessionCheckpoint.from_json(json_str)

        assert restored.checkpoint_id == original.checkpoint_id
        assert restored.task_id == original.task_id
        assert restored.turn_number == original.turn_number


class TestSessionContinuationManager:
    """Tests for SessionContinuationManager."""

    def test_init_creates_checkpoint_dir(self, temp_corvin_home):
        """Test that init creates checkpoint base directory."""
        manager = SessionContinuationManager(temp_corvin_home)
        assert manager._checkpoint_base.exists()

    def test_save_checkpoint(self, temp_corvin_home, mock_execution_context):
        """Test saving a checkpoint."""
        manager = SessionContinuationManager(temp_corvin_home)

        checkpoint_id = manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=mock_execution_context,
            session_id="sess_abc",
            turn_number=5,
            tokens_consumed=2000,
            cost_consumed_cents=50,
        )

        assert checkpoint_id
        assert isinstance(checkpoint_id, str)

        # Check that files were created
        task_dir = manager._checkpoint_base / "test_task_1"
        assert task_dir.exists()
        assert (task_dir / "latest.json").exists()
        assert (task_dir / "history.jsonl").exists()

    def test_load_latest_checkpoint(self, temp_corvin_home, mock_execution_context):
        """Test loading the latest checkpoint."""
        manager = SessionContinuationManager(temp_corvin_home)

        # Save a checkpoint
        checkpoint_id = manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=mock_execution_context,
            session_id="sess_abc",
            turn_number=5,
            tokens_consumed=2000,
        )

        # Load it back (without specifying ID)
        loaded = manager.load_checkpoint("test_task_1")
        assert loaded.checkpoint_id == checkpoint_id
        assert loaded.task_id == "test_task_1"
        assert loaded.turn_number == 5

    def test_load_specific_checkpoint(self, temp_corvin_home, mock_execution_context):
        """Test loading a specific checkpoint by ID."""
        manager = SessionContinuationManager(temp_corvin_home)

        # Save multiple checkpoints
        cp1 = manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=mock_execution_context,
            session_id="sess_abc",
            turn_number=5,
        )

        cp2 = manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=mock_execution_context,
            session_id="sess_abc",
            turn_number=10,
        )

        # Load specific checkpoint
        loaded = manager.load_checkpoint("test_task_1", cp1)
        assert loaded.checkpoint_id == cp1
        assert loaded.turn_number == 5

    def test_load_nonexistent_checkpoint_raises_error(self, temp_corvin_home):
        """Test that loading nonexistent checkpoint raises CheckpointNotFoundError."""
        manager = SessionContinuationManager(temp_corvin_home)

        with pytest.raises(CheckpointNotFoundError):
            manager.load_checkpoint("nonexistent_task")

    def test_get_checkpoint_metadata(self, temp_corvin_home, mock_execution_context):
        """Test retrieving checkpoint metadata."""
        manager = SessionContinuationManager(temp_corvin_home)

        # Save multiple checkpoints
        manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=mock_execution_context,
            session_id="sess_abc",
            turn_number=5,
            tokens_consumed=2000,
            cost_consumed_cents=50,
        )

        manager.save_checkpoint(
            task_id="test_task_1",
            tenant_id="_default",
            execution_context=mock_execution_context,
            session_id="sess_abc",
            turn_number=10,
            tokens_consumed=4000,
            cost_consumed_cents=100,
        )

        # Get metadata
        metadata = manager.get_checkpoint_metadata("test_task_1")
        assert len(metadata) == 2
        assert metadata[0]["turn_number"] == 5
        assert metadata[1]["turn_number"] == 10
        assert metadata[1]["tokens_consumed"] == 4000

    def test_resume_from_checkpoint(self, temp_corvin_home, mock_execution_context):
        """Test resuming ExecutionContext from checkpoint."""
        manager = SessionContinuationManager(temp_corvin_home)

        # Save checkpoint
        checkpoint = SessionCheckpoint(
            checkpoint_id="cp_test",
            task_id="test_task_1",
            session_id="sess_abc",
            tenant_id="_default",
            context_state={
                "task_id": "test_task_1",
                "tenant_id": "_default",
                "task_template": {"task_type": "code_fix"},
                "context_stack": "task:test_task_1",
                "budget_remaining": 300.0,
                "time_remaining": 900,
                "model": "claude-3-haiku",
                "strategy": "pivot",
                "strategy_confidence": 0.6,
                "guidance_overrides": {},
            },
            turn_number=5,
            tokens_consumed=2000,
        )

        # Resume from checkpoint
        resumed_ctx = manager.resume_from_checkpoint(checkpoint, ExecutionContext)

        assert resumed_ctx.task_id == "test_task_1"
        assert resumed_ctx.budget_remaining == 300.0
        assert resumed_ctx.model == "claude-3-haiku"
        assert resumed_ctx.strategy == "pivot"

    def test_checkpoint_persistence_error_on_bad_path(self):
        """Test that bad path raises CheckpointPersistenceError."""
        manager = SessionContinuationManager("/nonexistent/path")

        mock_ctx = MagicMock()
        mock_ctx.decision_history = []
        mock_ctx.checkpoints = []

        with pytest.raises(CheckpointPersistenceError):
            manager.save_checkpoint(
                task_id="test",
                tenant_id="_default",
                execution_context=mock_ctx,
                session_id="sess",
            )

    def test_serialize_execution_context(self, mock_execution_context):
        """Test serializing ExecutionContext to dict."""
        state = SessionContinuationManager._serialize_execution_context(mock_execution_context)

        assert state["task_id"] == "test_task_1"
        assert state["tenant_id"] == "_default"
        assert state["budget_remaining"] == 500.0
        assert state["model"] == "claude-3-sonnet"
        assert "context_stack" in state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
