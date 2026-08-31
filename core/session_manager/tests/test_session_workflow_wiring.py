"""Integration tests for k=4 Session Manager Wiring — checkpoint + workflow + goal alignment.

Tests:
1. Checkpoint captures workflow execution state
2. Checkpoint persists goal and alignment score
3. Session split creates checkpoint with all state
4. Session restore from checkpoint recreates metadata + metrics
5. Goal alignment monitor state is restored post-split
6. Multi-session workflow preserves state across splits
"""

import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from core.session_manager.checkpoint import (
    SessionCheckpoint,
    CheckpointManager,
    TaskState,
)
from core.session_manager.lifecycle import (
    SessionLifecycleManager,
    SessionSplitTrigger,
)
from core.session_manager.monitors.goal_alignment import GoalAlignmentMonitor
from core.workflows.execution_engine import WorkflowExecutionState


class TestCheckpointWorkflowState:
    """Test checkpoint captures workflow execution state."""

    def test_checkpoint_stores_workflow_state(self):
        """Test SessionCheckpoint stores workflow execution state."""
        workflow_state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id="run-123",
            status="running",
            started_at=1234567890.0,
            nodes_executed=["node-1", "node-2"],
            errors=[],
            events=[],
        )

        checkpoint = SessionCheckpoint(
            session_id="session-123",
            task_id="task-456",
            phase="execution",
            tenant_id="test-tenant",
            workflow_execution_state=workflow_state,
        )

        # Verify state is stored
        assert checkpoint.workflow_execution_state is not None
        assert checkpoint.workflow_execution_state.workflow_id == "test-workflow"
        assert checkpoint.workflow_execution_state.run_id == "run-123"
        assert len(checkpoint.workflow_execution_state.nodes_executed) == 2

    def test_checkpoint_serializes_workflow_state(self):
        """Test checkpoint serializes workflow state to JSON."""
        workflow_state = WorkflowExecutionState(
            workflow_id="wf-xyz",
            run_id="run-789",
            status="completed",
            started_at=1700000000.0,
            nodes_executed=["node-1", "node-2", "node-3"],
            errors=[],
            events=[],
        )

        checkpoint = SessionCheckpoint(
            session_id="s-abc",
            task_id="t-def",
            phase="validation",
            tenant_id="tenant-ghi",
            workflow_execution_state=workflow_state,
        )

        # Serialize
        data = checkpoint.to_dict()

        # Verify workflow state is serialized
        assert "workflow_execution_state" in data
        assert data["workflow_execution_state"]["workflow_id"] == "wf-xyz"
        assert len(data["workflow_execution_state"]["nodes_executed"]) == 3

        # Deserialize and verify
        restored = SessionCheckpoint.from_dict(data)
        assert restored.workflow_execution_state is not None
        assert restored.workflow_execution_state["workflow_id"] == "wf-xyz"


class TestCheckpointGoalAlignment:
    """Test checkpoint goal and alignment score persistence."""

    def test_checkpoint_goal_alignment(self):
        """Test checkpoint stores goal and alignment score."""
        checkpoint = SessionCheckpoint(
            session_id="session-abc",
            task_id="task-xyz",
            phase="planning",
            tenant_id="tenant-123",
            goal="Implement user authentication system",
            goal_alignment_score=0.93,
        )

        # Verify fields are stored
        assert checkpoint.goal == "Implement user authentication system"
        assert checkpoint.goal_alignment_score == 0.93

    def test_checkpoint_goal_round_trip(self):
        """Test goal and alignment persists through serialization."""
        original = SessionCheckpoint(
            session_id="s-1",
            task_id="t-2",
            phase="phase-3",
            tenant_id="tenant-4",
            goal="Build a secure REST API",
            goal_alignment_score=0.87,
        )

        # Serialize and deserialize
        data = original.to_dict()
        restored = SessionCheckpoint.from_dict(data)

        # Verify restoration
        assert restored.goal == "Build a secure REST API"
        assert restored.goal_alignment_score == 0.87


class TestLifecycleCheckpointCreation:
    """Test SessionLifecycleManager creates checkpoint with full state."""

    def test_create_checkpoint_for_split_with_workflow_state(self):
        """Test checkpoint creation captures workflow state."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            manager = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager(checkpoint_dir)

            # Create session
            session = manager.create_session(
                task_id="task-001",
                phase="phase-1",
                tenant_id="test-tenant",
            )

            # Update metrics
            manager.record_iteration(session.session_id)
            manager.record_iteration(session.session_id)
            manager.update_context_size(session.session_id, 50000)

            # Simulate workflow state
            workflow_state = WorkflowExecutionState(
                workflow_id="wf-test",
                run_id="run-test",
                status="running",
                started_at=datetime.utcnow().timestamp(),
                nodes_executed=["node-a", "node-b"],
                errors=[],
                events=[],
            )

            # Trigger split
            split_event = manager.check_split_triggers(session.session_id, max_context_tokens=100000)
            # Force split via iteration cap
            manager.session_metrics[session.session_id].iterations = 50

            split_event = manager.check_split_triggers(session.session_id, max_context_tokens=100000)
            assert split_event is not None

            # Create checkpoint with workflow state and goal
            checkpoint = manager.create_checkpoint_for_split(
                session_id=session.session_id,
                split_event=split_event,
                checkpoint_manager=checkpoint_mgr,
                workflow_executor=type("obj", (object,), {"execution_state": workflow_state})(),
                goal="Complete feature implementation",
                goal_alignment_score=0.92,
            )

            # Verify checkpoint
            assert checkpoint is not None
            assert checkpoint.workflow_execution_state is not None
            assert checkpoint.goal == "Complete feature implementation"
            assert checkpoint.goal_alignment_score == 0.92
            assert checkpoint.iterations_at_checkpoint == 50
            assert checkpoint.token_count_at_checkpoint == 50000

    def test_checkpoint_creation_without_workflow_executor(self):
        """Test checkpoint creation works without workflow executor."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            manager = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager(checkpoint_dir)

            # Create session
            session = manager.create_session(
                task_id="task-002",
                phase="phase-2",
                tenant_id="test-tenant",
            )

            # Update metrics
            manager.update_context_size(session.session_id, 80000)
            manager.update_token_budget(session.session_id, 0.96)

            # Check token burn trigger
            split_event = manager.check_split_triggers(session.session_id, max_context_tokens=100000)
            assert split_event is not None
            assert split_event.trigger_type == SessionSplitTrigger.TOKEN_BURN

            # Create checkpoint without workflow executor
            checkpoint = manager.create_checkpoint_for_split(
                session_id=session.session_id,
                split_event=split_event,
                checkpoint_manager=checkpoint_mgr,
                goal="Process data pipeline",
                goal_alignment_score=0.85,
            )

            # Verify checkpoint
            assert checkpoint is not None
            assert checkpoint.workflow_execution_state is None
            assert checkpoint.goal == "Process data pipeline"
            assert checkpoint.goal_alignment_score == 0.85


class TestSessionRestoration:
    """Test session restoration from checkpoint."""

    def test_restore_session_from_checkpoint_basic(self):
        """Test basic session restoration from checkpoint."""
        manager = SessionLifecycleManager()

        # Create a checkpoint with state
        checkpoint = SessionCheckpoint(
            session_id="old-session-123",
            task_id="task-restore",
            phase="phase-next",
            tenant_id="tenant-restore",
            iterations_at_checkpoint=25,
            token_count_at_checkpoint=75000,
            goal="Continue implementation",
            goal_alignment_score=0.88,
        )

        # Restore session
        new_session_id = manager.restore_session_from_checkpoint(checkpoint)

        # Verify restoration
        assert new_session_id is not None
        assert new_session_id != checkpoint.session_id

        # Verify new session exists with restored metrics
        assert new_session_id in manager.active_sessions
        assert new_session_id in manager.session_metrics

        metrics = manager.session_metrics[new_session_id]
        assert metrics.iterations == 25
        assert metrics.context_size_tokens == 75000

    def test_restore_session_with_goal_alignment_monitor(self):
        """Test session restoration restores goal alignment state."""
        manager = SessionLifecycleManager()
        goal_monitor = GoalAlignmentMonitor()

        checkpoint = SessionCheckpoint(
            session_id="old-s",
            task_id="task-gal",
            phase="exec",
            tenant_id="t-gal",
            goal="Implement error handling",
            goal_alignment_score=0.91,
        )

        # Restore with goal alignment monitor
        new_session_id = manager.restore_session_from_checkpoint(
            checkpoint,
            goal_alignment_monitor=goal_monitor,
        )

        assert new_session_id is not None

        # Verify goal alignment monitor has state
        assert new_session_id in goal_monitor.session_states
        state = goal_monitor.session_states[new_session_id]
        assert state.original_goal == "Implement error handling"


class TestMultiSessionWorkflow:
    """Test multi-session workflow with state preservation."""

    def test_full_split_restore_cycle(self):
        """Test complete cycle: split → checkpoint → restore."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            manager = SessionLifecycleManager()
            checkpoint_mgr = CheckpointManager(checkpoint_dir)
            goal_monitor = GoalAlignmentMonitor()

            # Session 1: Start
            session1 = manager.create_session(
                task_id="task-cycle",
                phase="phase-1",
                tenant_id="tenant-cycle",
            )

            # Set initial goal
            goal_monitor.set_goal(
                session1.session_id,
                "task-cycle",
                "tenant-cycle",
                "Build authentication layer",
            )

            # Do some work
            manager.record_iteration(session1.session_id)
            manager.record_iteration(session1.session_id)
            manager.update_context_size(session1.session_id, 60000)

            # Simulate workflow
            workflow_state_1 = WorkflowExecutionState(
                workflow_id="wf-cycle-1",
                run_id="run-1",
                status="in-progress",
                started_at=datetime.utcnow().timestamp(),
                nodes_executed=["auth-setup"],
                errors=[],
                events=[],
            )

            # Trigger split at iteration cap
            manager.session_metrics[session1.session_id].iterations = 50
            split_event = manager.check_split_triggers(session1.session_id)

            assert split_event is not None

            # Create checkpoint
            checkpoint = manager.create_checkpoint_for_split(
                session_id=session1.session_id,
                split_event=split_event,
                checkpoint_manager=checkpoint_mgr,
                workflow_executor=type("obj", (object,), {"execution_state": workflow_state_1})(),
                goal="Build authentication layer",
                goal_alignment_score=0.90,
            )

            assert checkpoint is not None

            # Close session 1
            manager.close_session(session1.session_id)
            assert session1.session_id not in manager.active_sessions

            # Restore to Session 2
            session2_id = manager.restore_session_from_checkpoint(
                checkpoint,
                goal_alignment_monitor=goal_monitor,
            )

            assert session2_id is not None
            assert session2_id in manager.active_sessions

            # Verify Session 2 has Session 1 as parent
            metadata = manager.active_sessions[session2_id]
            assert metadata.parent_session_id == session1.session_id

            # Verify metrics are restored
            metrics = manager.session_metrics[session2_id]
            assert metrics.iterations == 50
            assert metrics.context_size_tokens == 60000

            # Verify goal alignment state is restored
            assert session2_id in goal_monitor.session_states
            goal_state = goal_monitor.session_states[session2_id]
            assert goal_state.original_goal == "Build authentication layer"

    def test_checkpoint_persistence_to_disk(self):
        """Test checkpoints persist to disk and can be restored."""
        with TemporaryDirectory() as tmpdir:
            checkpoint_dir = Path(tmpdir) / "checkpoints"

            checkpoint_mgr = CheckpointManager(checkpoint_dir)

            # Create checkpoint with full state
            original = SessionCheckpoint(
                session_id="s-persist",
                task_id="t-persist",
                phase="phase-persist",
                tenant_id="tenant-persist",
                iterations_at_checkpoint=35,
                token_count_at_checkpoint=55000,
                goal="Persistent goal",
                goal_alignment_score=0.89,
            )

            # Create via manager (should persist to disk)
            checkpoint = checkpoint_mgr.create_checkpoint(
                session_id=original.session_id,
                task_id=original.task_id,
                phase=original.phase,
                tenant_id=original.tenant_id,
                iterations=35,
                token_count=55000,
                goal="Persistent goal",
                goal_alignment_score=0.89,
            )

            # Verify file was created
            checkpoint_file = checkpoint_dir / f"{checkpoint.checkpoint_id}.json"
            assert checkpoint_file.exists()

            # Load from disk
            restored = checkpoint_mgr.restore_checkpoint(checkpoint.checkpoint_id)

            # Verify restoration
            assert restored is not None
            assert restored.session_id == "s-persist"
            assert restored.goal == "Persistent goal"
            assert restored.goal_alignment_score == 0.89


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
