"""Test SessionManager goal integration (Phase 1: Task Context Drift).

Tests:
- SessionManager.initialize_task(goal)
- SessionManager.resume_from_checkpoint()
- Goal context persisted and restored
- Audit events logged (GDPR Art. 30)
"""

import pytest
import tempfile
import json
from pathlib import Path
from unittest.mock import Mock, patch

# Import the orchestration-level SessionManager
from core.orchestration.subsystems.session_manager import SessionManager
from core.session_manager.goal_context import GoalContext
from core.session_manager.checkpoint import SessionCheckpoint


class MockExecutionContext:
    """Mock ExecutionContext for testing."""

    def __init__(self, tenant_id="tenant-default"):
        self.tenant_id = tenant_id


class TestSessionManagerInitializeTask:
    """Test SessionManager.initialize_task() method."""

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_initialize_task(self, mock_tenant_dir):
        """Test initializing task with goal."""
        # Setup
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)

            # Create session first
            session = manager.create_session(
                session_id="sess-123",
                channel_id="discord",
                metadata={"user": "test"},
            )

            assert session is not None

            # Initialize task with goal
            goal = "Build task context drift prevention system"
            result = manager.initialize_task(
                session_id="sess-123",
                goal=goal,
                task_id="task-456",
            )

            # Verify result
            assert result["session_id"] == "sess-123"
            assert result["task_id"] == "task-456"
            assert result["goal_context"] is not None
            assert result["goal_context"]["goal"] == goal

            # Verify session was updated
            updated_session = manager.get_session("sess-123")
            assert updated_session is not None
            assert "goal_context" in updated_session
            assert updated_session["task_id"] == "task-456"

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_initialize_task_with_empty_goal_raises(self, mock_tenant_dir):
        """Test that empty goal raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            with pytest.raises(ValueError, match="Goal cannot be empty"):
                manager.initialize_task(
                    session_id="sess-123",
                    goal="",
                )

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_initialize_task_nonexistent_session_raises(self, mock_tenant_dir):
        """Test that initializing task for nonexistent session raises."""
        context = MockExecutionContext()
        manager = SessionManager(context)

        with pytest.raises(ValueError, match="not found"):
            manager.initialize_task(
                session_id="nonexistent",
                goal="Some goal",
            )

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_initialize_task_generates_task_id(self, mock_tenant_dir):
        """Test that task_id is generated if not provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            result = manager.initialize_task(
                session_id="sess-123",
                goal="Auto-generated task ID goal",
            )

            assert result["task_id"] is not None
            assert len(result["task_id"]) > 0


class TestSessionManagerResumeFromCheckpoint:
    """Test SessionManager.resume_from_checkpoint() method."""

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_resume_from_checkpoint_with_goal_context(self, mock_tenant_dir):
        """Test resuming from checkpoint with goal_context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            # Create checkpoint with goal_context
            goal = "Resume from checkpoint goal"
            goal_ctx = GoalContext.create(goal)
            checkpoint_data = {
                "session_id": "sess-123",
                "task_id": "task-456",
                "goal_context": goal_ctx.to_dict(),
            }

            # Resume from checkpoint
            result = manager.resume_from_checkpoint(
                session_id="sess-123",
                checkpoint_data=checkpoint_data,
            )

            # Verify result
            assert result["session_id"] == "sess-123"
            assert result["task_id"] == "task-456"
            assert result["integrity_verified"] is True
            assert result["goal_context"] is not None
            assert result["goal_context"]["goal"] == goal

            # Verify session was updated
            updated_session = manager.get_session("sess-123")
            assert updated_session is not None
            assert "goal_context" in updated_session

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_resume_from_checkpoint_corrupted_hash_fails(self, mock_tenant_dir):
        """Test that corrupted goal hash fails on resume (fail-closed)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            # Create checkpoint with corrupted goal_context
            goal_ctx = GoalContext.create("Original goal")
            checkpoint_data = {
                "session_id": "sess-123",
                "task_id": "task-456",
                "goal_context": {
                    "goal": "Modified goal",
                    "goal_hash": goal_ctx.goal_hash,  # Hash of original goal
                    "created_at": goal_ctx.created_at,
                },
            }

            # Should fail due to hash mismatch (fail-closed)
            with pytest.raises(AssertionError, match="Goal integrity check failed"):
                manager.resume_from_checkpoint(
                    session_id="sess-123",
                    checkpoint_data=checkpoint_data,
                )

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_resume_from_checkpoint_backward_compat_no_goal_context(self, mock_tenant_dir):
        """Test backward compatibility with checkpoints without goal_context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            # Old checkpoint without goal_context
            checkpoint_data = {
                "session_id": "sess-123",
                "task_id": "task-456",
            }

            result = manager.resume_from_checkpoint(
                session_id="sess-123",
                checkpoint_data=checkpoint_data,
            )

            # Should succeed with integrity_verified=False
            assert result["session_id"] == "sess-123"
            assert result["integrity_verified"] is False
            assert result["goal_context"] is None

    def test_resume_from_checkpoint_no_checkpoint_data_raises(self):
        """Test that missing checkpoint_data raises ValueError."""
        context = MockExecutionContext()
        manager = SessionManager(context)

        with pytest.raises(ValueError, match="checkpoint_data is required"):
            manager.resume_from_checkpoint(
                session_id="sess-123",
                checkpoint_data={},  # Empty
            )


class TestSessionManagerAuditLogging:
    """Test audit logging for goal context events."""

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_audit_log_goal_initialized(self, mock_tenant_dir):
        """Test that goal initialization is audit-logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext("tenant-xyz")
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            # Mock hub for audit events
            mock_hub = Mock()
            manager.hub = mock_hub

            # Initialize task
            goal = "Test audit logging"
            manager.initialize_task(
                session_id="sess-123",
                goal=goal,
                task_id="task-456",
            )

            # Verify audit event was published
            mock_hub.publish_event.assert_called()
            call_args = mock_hub.publish_event.call_args

            assert call_args[0][0] == "goal_context.initialized"
            audit_data = call_args[0][1]
            assert audit_data["event_type"] == "goal_context.initialized"
            assert audit_data["tenant_id"] == "tenant-xyz"
            assert audit_data["session_id"] == "sess-123"
            assert audit_data["task_id"] == "task-456"
            assert "goal_hash" in audit_data

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_audit_log_goal_restored(self, mock_tenant_dir):
        """Test that goal restoration is audit-logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext("tenant-xyz")
            manager = SessionManager(context)
            manager.create_session(session_id="sess-123")

            # Mock hub for audit events
            mock_hub = Mock()
            manager.hub = mock_hub

            # Resume from checkpoint
            goal = "Test restore audit"
            goal_ctx = GoalContext.create(goal)
            checkpoint_data = {
                "session_id": "sess-123",
                "task_id": "task-456",
                "goal_context": goal_ctx.to_dict(),
            }

            manager.resume_from_checkpoint("sess-123", checkpoint_data)

            # Verify audit event was published
            mock_hub.publish_event.assert_called()

            # Find the restore event
            restore_called = False
            for call in mock_hub.publish_event.call_args_list:
                if call[0][0] == "goal_context.restored":
                    restore_called = True
                    audit_data = call[0][1]
                    assert audit_data["event_type"] == "goal_context.restored"
                    assert audit_data["tenant_id"] == "tenant-xyz"
                    assert "goal_hash" in audit_data

            assert restore_called


class TestSessionManagerEndToEnd:
    """End-to-end tests for goal context workflow."""

    @patch("core.orchestration.subsystems.session_manager.tenant_session_dir")
    def test_e2e_initialize_and_resume(self, mock_tenant_dir):
        """Test full workflow: initialize -> checkpoint -> resume."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session_dir = Path(tmpdir) / "sessions" / "sess-123"
            session_dir.mkdir(parents=True)
            mock_tenant_dir.return_value = session_dir

            context = MockExecutionContext()
            manager = SessionManager(context)

            # 1. Create session
            manager.create_session(session_id="sess-123")

            # 2. Initialize task with goal
            goal = "End-to-end test goal"
            init_result = manager.initialize_task(
                session_id="sess-123",
                goal=goal,
                task_id="task-789",
            )

            initial_goal_hash = init_result["goal_context"]["goal_hash"]

            # 3. Simulate checkpoint creation
            session = manager.get_session("sess-123")
            checkpoint_data = {
                "session_id": "sess-123",
                "task_id": "task-789",
                "goal_context": session["goal_context"],
            }

            # 4. Resume from checkpoint
            resume_result = manager.resume_from_checkpoint(
                session_id="sess-123",
                checkpoint_data=checkpoint_data,
            )

            # Verify goal unchanged
            assert resume_result["goal_context"]["goal"] == goal
            assert resume_result["goal_context"]["goal_hash"] == initial_goal_hash
            assert resume_result["integrity_verified"] is True
