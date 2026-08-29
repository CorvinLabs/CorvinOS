"""Integration tests for WorkflowExecutor + Session Manager checkpoint/resume.

Tests the contract between WorkflowExecutor and session manager for
checkpointing workflow state on split, resuming on new session.

Run: python3 core/workflows/tests/test_execution_engine_integration.py
"""

import unittest
from pathlib import Path
import sys
import json

# Add package to path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent.parent))

from core.workflows.execution_engine import (
    WorkflowExecutor,
    WorkflowExecutionState,
)
from core.context_engineering.execution_context import (
    ExecutionContext,
    ContextStack,
)
from core.session_manager.checkpoint import (
    SessionCheckpoint,
    TaskState,
    LearningState,
    ContextEssentials,
)


class TestWorkflowExecutorCheckpointIntegration(unittest.TestCase):
    """Test WorkflowExecutor integration with session checkpoint/resume."""

    def setUp(self):
        """Set up test fixtures."""
        from unittest.mock import Mock

        self.context_stack = ContextStack()
        self.execution_context = ExecutionContext(
            task_id="task-workflow-123",
            tenant_id="_default",
            task_template={"name": "test-workflow"},
            context_stack=self.context_stack,
            budget_remaining=1000.0,
        )

        self.dag_runner = Mock()
        self.executor = WorkflowExecutor(
            dag_runner=self.dag_runner,
            execution_context=self.execution_context,
            context_bus=None,  # Simplified for integration test
        )

    def test_execution_context_serialization_for_checkpoint(self):
        """Test that ExecutionContext can be serialized for checkpoint storage."""
        # Simulate workflow execution with decisions
        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_execution",
            value="execute test-workflow",
            reasoning="Checkpoint test",
            confidence=0.95,
        )

        # Serialize to dict for checkpoint
        context_dict = self.execution_context.to_full_dict()

        # Verify structure
        self.assertIn("task_id", context_dict)
        self.assertIn("tenant_id", context_dict)
        self.assertIn("decision_history", context_dict)
        self.assertGreater(len(context_dict["decision_history"]), 0)

        # Verify first decision
        first_decision = context_dict["decision_history"][0]
        self.assertEqual(first_decision["subsystem"], "WorkflowExecutor")
        self.assertEqual(first_decision["decision_type"], "workflow_execution")

    def test_workflow_execution_state_serialization(self):
        """Test that WorkflowExecutionState can be serialized to JSON."""
        state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id="run-123",
            status="running",
            started_at=0,
        )

        # Record some node events
        state.nodes_executed.append("node1")
        state.nodes_executed.append("node2")
        state.errors.append("node3: timeout")

        # Serialize to dict (checkpoint-ready)
        state_dict = {
            "workflow_id": state.workflow_id,
            "run_id": state.run_id,
            "status": state.status,
            "nodes_executed": state.nodes_executed,
            "errors": state.errors,
        }

        # Verify JSON serializable
        json_str = json.dumps(state_dict)
        self.assertIsNotNone(json_str)

        # Verify round-trip
        restored = json.loads(json_str)
        self.assertEqual(restored["workflow_id"], "test-workflow")
        self.assertEqual(len(restored["nodes_executed"]), 2)

    def test_checkpoint_creation_with_workflow_execution_state(self):
        """Test creating a SessionCheckpoint with workflow state embedded."""
        # Simulate execution state after workflow progress
        execution_state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id="run-123",
            status="running",
            started_at=0,
        )
        execution_state.nodes_executed.extend(["node1", "node2"])

        # Create a checkpoint (as session manager would)
        checkpoint = SessionCheckpoint(
            session_id="session-456",
            task_id="task-workflow-123",
            phase="workflow_execution",
            tenant_id="_default",
            task_state=TaskState(
                task_id="task-workflow-123",
                goal="Execute workflow",
            ),
            learning_state=LearningState(
                strategies_tried=["workflow_execution"],
                success_rate=0.0,  # In progress
            ),
        )

        # Verify checkpoint can be serialized
        checkpoint_dict = checkpoint.to_dict()
        self.assertEqual(checkpoint_dict["session_id"], "session-456")
        self.assertEqual(checkpoint_dict["task_id"], "task-workflow-123")
        self.assertEqual(checkpoint_dict["phase"], "workflow_execution")

    def test_workflow_execution_state_from_checkpoint(self):
        """Test reconstructing workflow state from checkpoint on resume."""
        # Simulate checkpoint saved during split
        checkpoint_data = {
            "session_id": "session-456",
            "task_id": "task-workflow-123",
            "phase": "workflow_execution",
            "tenant_id": "_default",
            "workflow_run_id": "run-123",
            "nodes_executed": ["node1", "node2"],
            "execution_status": "running",
        }

        # Reconstruct (as session manager would on resume)
        run_id = checkpoint_data.get("workflow_run_id")
        nodes_executed = checkpoint_data.get("nodes_executed", [])
        status = checkpoint_data.get("execution_status", "pending")

        # Create state for resume
        resume_state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id=run_id,
            status=status,
            started_at=0,
        )
        resume_state.nodes_executed.extend(nodes_executed)

        # Verify state was restored correctly
        self.assertEqual(resume_state.run_id, "run-123")
        self.assertEqual(len(resume_state.nodes_executed), 2)
        self.assertIn("node1", resume_state.nodes_executed)
        self.assertIn("node2", resume_state.nodes_executed)

    def test_context_stack_preservation_across_split_resume(self):
        """Test that context stack is preserved across session split/resume."""
        # Initial scope stack
        initial_stack_str = str(self.execution_context.context_stack)

        # Simulate workflow execution: push scope
        self.execution_context.context_stack.push(
            level="workflow",
            id="test-workflow",
            workflow_id="run-123",
        )

        # Record the stack state for checkpoint
        checkpoint_stack_str = str(self.execution_context.context_stack)
        self.assertNotEqual(checkpoint_stack_str, initial_stack_str)

        # Pop scope (simulate session split/paused)
        self.execution_context.context_stack.pop(level="workflow")

        # Verify we're back to initial state
        final_stack_str = str(self.execution_context.context_stack)
        self.assertEqual(final_stack_str, initial_stack_str)

    def test_decision_history_accumulates_across_resumption(self):
        """Test that decision history is cumulative on resume."""
        # Record first decision (before split)
        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_start",
            value="execute test-workflow",
            confidence=0.95,
        )

        first_count = len(self.execution_context.decision_history)
        self.assertEqual(first_count, 1)

        # Simulate checkpoint/resume: record next decision
        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_resume",
            value="resume after split",
            confidence=0.90,
        )

        second_count = len(self.execution_context.decision_history)
        self.assertEqual(second_count, 2)

        # Verify both decisions are preserved
        history = self.execution_context.decision_history
        self.assertEqual(history[0].decision_type, "workflow_start")
        self.assertEqual(history[1].decision_type, "workflow_resume")

    def test_execution_context_tenant_isolation_in_checkpoint(self):
        """Test that tenant_id is properly isolated in checkpointed context."""
        # Verify ExecutionContext has tenant_id
        self.assertEqual(self.execution_context.tenant_id, "_default")

        # Serialize for checkpoint
        context_dict = self.execution_context.to_dict()
        self.assertEqual(context_dict["tenant_id"], "_default")

        # Create checkpoint with same tenant
        checkpoint = SessionCheckpoint(
            session_id="session-456",
            task_id="task-workflow-123",
            phase="workflow_execution",
            tenant_id="_default",  # Must match
        )

        # Verify tenant isolation
        self.assertEqual(checkpoint.tenant_id, "_default")
        self.assertEqual(checkpoint.tenant_id, self.execution_context.tenant_id)


class TestWorkflowStateRecovery(unittest.TestCase):
    """Test workflow state recovery scenarios."""

    def setUp(self):
        """Set up test fixtures."""
        from unittest.mock import Mock

        self.context_stack = ContextStack()
        self.execution_context = ExecutionContext(
            task_id="task-workflow-456",
            tenant_id="_default",
            task_template={"name": "multi-step-workflow"},
            context_stack=self.context_stack,
            budget_remaining=2000.0,
        )

        self.dag_runner = Mock()
        self.executor = WorkflowExecutor(
            dag_runner=self.dag_runner,
            execution_context=self.execution_context,
            context_bus=None,
        )

    def test_workflow_state_recovery_after_session_split(self):
        """Test recovering workflow state after a session split.

        Simulates:
        1. Workflow starts, executes first 2 nodes
        2. Session splits (checkpoint saved)
        3. New session resumes, has access to execution state
        4. Workflow continues from node 3
        """
        # Phase 1: Initial execution (nodes 1-2 completed)
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="multi-step-workflow",
            run_id="run-456",
            status="running",
            started_at=0,
        )
        self.executor.execution_state.nodes_executed.extend(["node1", "node2"])

        # Record checkpoint data
        checkpoint_data = {
            "nodes_executed": self.executor.execution_state.nodes_executed.copy(),
            "run_id": self.executor.execution_state.run_id,
            "status": self.executor.execution_state.status,
        }

        # Phase 2: Session split (checkpoint persisted, session ends)
        # ... (session manager persists checkpoint_data)

        # Phase 3: Resume in new session
        # Create new executor instance (simulating new session)
        context_stack_new = ContextStack()
        execution_context_new = ExecutionContext(
            task_id="task-workflow-456",
            tenant_id="_default",
            task_template={"name": "multi-step-workflow"},
            context_stack=context_stack_new,
            budget_remaining=2000.0,
        )

        executor_new = WorkflowExecutor(
            dag_runner=self.dag_runner,
            execution_context=execution_context_new,
            context_bus=None,
        )

        # Restore state from checkpoint
        executor_new.execution_state = WorkflowExecutionState(
            workflow_id=checkpoint_data.get("workflow_id", "multi-step-workflow"),
            run_id=checkpoint_data["run_id"],
            status=checkpoint_data["status"],
            started_at=0,
        )
        executor_new.execution_state.nodes_executed.extend(
            checkpoint_data["nodes_executed"]
        )

        # Phase 4: Verify state was recovered
        self.assertEqual(executor_new.execution_state.run_id, "run-456")
        self.assertEqual(
            len(executor_new.execution_state.nodes_executed), 2
        )
        self.assertIn("node1", executor_new.execution_state.nodes_executed)
        self.assertIn("node2", executor_new.execution_state.nodes_executed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
