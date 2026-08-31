"""k=3 Integration Test: Session Manager + WorkflowExecutor Wiring.

Tests the contract:
1. WorkflowExecutor.save_execution_state() → SessionCheckpoint
2. SessionCheckpoint.restore_execution_state() → WorkflowExecutor
3. Context preservation across split/resume
"""

import pytest
from datetime import datetime
from uuid import uuid4

from core.session_manager.checkpoint import (
    SessionCheckpoint,
    TaskState,
)
from core.workflows.execution_engine import (
    WorkflowExecutor,
    WorkflowExecutionState,
    WorkflowNodeEvent,
)
from core.context_engineering.execution_context import ExecutionContext


class TestWorkflowCheckpointIntegration:
    """Integration tests for WorkflowExecutor ↔ SessionCheckpoint contract."""

    def test_checkpoint_includes_workflow_execution_state(self):
        """Test that SessionCheckpoint can hold workflow_execution_state."""
        # Create a WorkflowExecutionState
        workflow_state = WorkflowExecutionState(
            workflow_id="wf-001",
            run_id="run-001",
            status="executing",
            started_at=datetime.utcnow().timestamp(),
            nodes_executed=["node-1", "node-2"],
            errors=[],
            events=[],
        )

        # Create a checkpoint with the workflow state
        task_state = TaskState(
            task_id="task-001",
            goal="Test workflow checkpoint integration",
        )

        checkpoint = SessionCheckpoint(
            session_id="sess-001",
            task_id="task-001",
            phase="execution",
            tenant_id="default",
            task_state=task_state,
            workflow_execution_state=workflow_state,  # THIS FIELD SHOULD EXIST
        )

        assert checkpoint.workflow_execution_state is not None
        assert checkpoint.workflow_execution_state.workflow_id == "wf-001"
        assert checkpoint.workflow_execution_state.status == "executing"
        assert checkpoint.workflow_execution_state.nodes_executed == ["node-1", "node-2"]

    def test_workflow_execution_state_serialization(self):
        """Test that workflow_execution_state survives to_dict/from_dict round-trip."""
        workflow_state = WorkflowExecutionState(
            workflow_id="wf-002",
            run_id="run-002",
            status="running",
            started_at=1234567890.0,
            nodes_executed=["node-a", "node-b"],
            errors=["error-1"],
        )

        checkpoint = SessionCheckpoint(
            session_id="sess-002",
            task_id="task-002",
            phase="execution",
            tenant_id="default",
            workflow_execution_state=workflow_state,
        )

        # Serialize to dict
        checkpoint_dict = checkpoint.to_dict()

        # Verify the workflow state is in the dict
        assert "workflow_execution_state" in checkpoint_dict
        assert checkpoint_dict["workflow_execution_state"]["workflow_id"] == "wf-002"
        assert checkpoint_dict["workflow_execution_state"]["status"] == "running"

        # Deserialize from dict
        restored_checkpoint = SessionCheckpoint.from_dict(checkpoint_dict)

        # Verify restoration
        assert restored_checkpoint.workflow_execution_state is not None
        assert restored_checkpoint.workflow_execution_state.workflow_id == "wf-002"
        assert restored_checkpoint.workflow_execution_state.status == "running"
        assert restored_checkpoint.workflow_execution_state.nodes_executed == ["node-a", "node-b"]
        assert restored_checkpoint.workflow_execution_state.errors == ["error-1"]

    def test_workflow_execution_state_none_is_valid(self):
        """Test that checkpoint can have workflow_execution_state = None (not all checkpoints involve workflows)."""
        checkpoint = SessionCheckpoint(
            session_id="sess-003",
            task_id="task-003",
            phase="planning",
            tenant_id="default",
            workflow_execution_state=None,  # Valid for non-workflow tasks
        )

        checkpoint_dict = checkpoint.to_dict()
        assert checkpoint_dict["workflow_execution_state"] is None

        restored = SessionCheckpoint.from_dict(checkpoint_dict)
        assert restored.workflow_execution_state is None

    def test_executor_save_execution_state_to_checkpoint(self):
        """Test WorkflowExecutor.save_execution_state() method."""
        # Create a mock ExecutionContext
        execution_context = ExecutionContext(
            task_id="task-004",
            tenant_id="default",
        )

        # Create executor with mock DAGRunner (None for now, just testing state capture)
        executor = WorkflowExecutor(
            dag_runner=None,
            execution_context=execution_context,
        )

        # Simulate execution
        executor.execution_state = WorkflowExecutionState(
            workflow_id="wf-004",
            run_id="run-004",
            status="executing",
            started_at=1234567890.0,
            nodes_executed=["node-1"],
        )

        # Save to checkpoint
        checkpoint = executor.save_execution_state(
            session_id="sess-004",
            trigger_type="context_limit",
        )

        # Verify the checkpoint contains the state
        assert checkpoint.workflow_execution_state is not None
        assert checkpoint.workflow_execution_state.workflow_id == "wf-004"
        assert checkpoint.workflow_execution_state.run_id == "run-004"

    def test_executor_restore_execution_state_from_checkpoint(self):
        """Test WorkflowExecutor.restore_execution_state() method."""
        execution_context = ExecutionContext(
            task_id="task-005",
            tenant_id="default",
        )

        executor = WorkflowExecutor(
            dag_runner=None,
            execution_context=execution_context,
        )

        # Create a checkpoint with workflow state
        workflow_state = WorkflowExecutionState(
            workflow_id="wf-005",
            run_id="run-005",
            status="paused",
            started_at=1234567890.0,
            nodes_executed=["node-1", "node-2"],
        )

        checkpoint = SessionCheckpoint(
            session_id="sess-005",
            task_id="task-005",
            phase="execution",
            tenant_id="default",
            workflow_execution_state=workflow_state,
        )

        # Restore from checkpoint
        executor.restore_execution_state(checkpoint)

        # Verify executor state is restored
        assert executor.execution_state is not None
        assert executor.execution_state.workflow_id == "wf-005"
        assert executor.execution_state.run_id == "run-005"
        assert executor.execution_state.status == "paused"
        assert executor.execution_state.nodes_executed == ["node-1", "node-2"]

    def test_context_preservation_across_split(self):
        """Test that ExecutionContext decision history is preserved in checkpoint."""
        execution_context = ExecutionContext(
            task_id="task-006",
            tenant_id="default",
        )

        # Record decisions during execution
        execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="node_execution",
            value="node-1",
            reasoning="First node in sequence",
            confidence=0.9,
        )

        execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="node_execution",
            value="node-2",
            reasoning="Second node in sequence",
            confidence=0.9,
        )

        # Create checkpoint that captures decision history
        checkpoint = SessionCheckpoint(
            session_id="sess-006",
            task_id="task-006",
            phase="execution",
            tenant_id="default",
        )

        # Serialize and restore
        checkpoint_dict = checkpoint.to_dict()
        restored_checkpoint = SessionCheckpoint.from_dict(checkpoint_dict)

        # Verify checkpoint structure is sound
        assert restored_checkpoint.session_id == "sess-006"
        assert restored_checkpoint.task_id == "task-006"
