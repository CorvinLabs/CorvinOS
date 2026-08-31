"""Unit tests for WorkflowExecutor (ADR-0423 Phase 2).

Tests ExecutionContext v2 integration, node event recording, and scope tracking.

Run: python3 core/workflows/tests/test_execution_engine.py
"""

import unittest
from pathlib import Path
import sys

# Add package to path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent.parent))

from core.workflows.execution_engine import (
    WorkflowExecutor,
    WorkflowExecutionState,
    WorkflowNodeEvent,
)
from core.context_engineering.execution_context import (
    ExecutionContext,
    ContextStack,
)


class TestWorkflowNodeEvent(unittest.TestCase):
    """Test WorkflowNodeEvent dataclass."""

    def test_node_event_creation(self):
        """Test creating a node event."""
        event = WorkflowNodeEvent(
            node_id="node1",
            node_type="agent",
            event_type="node_started",
            timestamp=1234567890.0,
            output={"key": "value"},
        )

        self.assertEqual(event.node_id, "node1")
        self.assertEqual(event.node_type, "agent")
        self.assertEqual(event.event_type, "node_started")
        self.assertEqual(event.output, {"key": "value"})
        self.assertIsNone(event.error)
        self.assertEqual(event.retry_count, 0)

    def test_node_event_with_error(self):
        """Test creating a node event with error."""
        event = WorkflowNodeEvent(
            node_id="node1",
            node_type="agent",
            event_type="node_failed",
            timestamp=1234567890.0,
            error="Connection refused",
            retry_count=2,
        )

        self.assertEqual(event.error, "Connection refused")
        self.assertEqual(event.retry_count, 2)


class TestWorkflowExecutionState(unittest.TestCase):
    """Test WorkflowExecutionState dataclass."""

    def test_execution_state_creation(self):
        """Test creating execution state."""
        state = WorkflowExecutionState(
            workflow_id="workflow1",
            run_id="run-123",
            status="running",
            started_at=1234567890.0,
        )

        self.assertEqual(state.workflow_id, "workflow1")
        self.assertEqual(state.run_id, "run-123")
        self.assertEqual(state.status, "running")
        self.assertEqual(state.nodes_executed, [])
        self.assertEqual(state.errors, [])
        self.assertEqual(state.events, [])

    def test_execution_state_tracks_progress(self):
        """Test that execution state tracks node progress."""
        state = WorkflowExecutionState(
            workflow_id="workflow1",
            run_id="run-123",
            status="running",
            started_at=0,
        )

        state.nodes_executed.append("node1")
        state.nodes_executed.append("node2")
        state.errors.append("node3: timeout")

        self.assertEqual(len(state.nodes_executed), 2)
        self.assertEqual(len(state.errors), 1)


class TestWorkflowExecutorBasics(unittest.TestCase):
    """Test WorkflowExecutor basic operations."""

    def setUp(self):
        """Set up test fixtures."""
        from unittest.mock import Mock

        self.context_stack = ContextStack()
        self.execution_context = ExecutionContext(
            task_id="task-123",
            tenant_id="_default",
            task_template={},
            context_stack=self.context_stack,
            budget_remaining=1000.0,
        )

        self.dag_runner = Mock()
        self.context_bus = Mock()

        self.executor = WorkflowExecutor(
            dag_runner=self.dag_runner,
            execution_context=self.execution_context,
            context_bus=self.context_bus,
        )

    def test_initialization(self):
        """Test WorkflowExecutor initializes correctly."""
        self.assertIsNotNone(self.executor.dag_runner)
        self.assertIsNotNone(self.executor.execution_context)
        self.assertIsNotNone(self.executor.context_bus)
        self.assertIsNone(self.executor.execution_state)

    def test_record_node_event_requires_active_execution(self):
        """Test that recording node events requires active execution."""
        with self.assertRaises(RuntimeError):
            self.executor.record_node_event(
                node_id="node1",
                node_type="agent",
                event_type="node_started",
            )

    def test_record_node_event_started(self):
        """Test recording a node_started event."""
        # Simulate active execution
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id="run-123",
            status="running",
            started_at=0,
        )

        event = self.executor.record_node_event(
            node_id="node1",
            node_type="agent",
            event_type="node_started",
        )

        self.assertEqual(event.node_id, "node1")
        self.assertEqual(event.node_type, "agent")
        self.assertEqual(event.event_type, "node_started")
        self.assertIn(event, self.executor.execution_state.events)

    def test_record_node_event_completed(self):
        """Test recording a node_completed event tracks the node."""
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id="run-123",
            status="running",
            started_at=0,
        )

        event = self.executor.record_node_event(
            node_id="node1",
            node_type="agent",
            event_type="node_completed",
            output={"result": "ok"},
        )

        self.assertEqual(event.event_type, "node_completed")
        self.assertEqual(event.output, {"result": "ok"})
        self.assertIn("node1", self.executor.execution_state.nodes_executed)

    def test_record_node_event_failed(self):
        """Test recording a node_failed event tracks the error."""
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="test-workflow",
            run_id="run-123",
            status="running",
            started_at=0,
        )

        event = self.executor.record_node_event(
            node_id="node1",
            node_type="agent",
            event_type="node_failed",
            error="Connection timeout",
        )

        self.assertEqual(event.event_type, "node_failed")
        self.assertEqual(event.error, "Connection timeout")
        self.assertTrue(
            any("node1" in e for e in self.executor.execution_state.errors)
        )

    def test_execution_context_scope_tracking(self):
        """Test that workflow scope is pushed/popped correctly."""
        initial_depth = self.execution_context.context_stack.depth
        self.assertEqual(initial_depth, 0)

        # Push scope
        self.execution_context.context_stack.push(
            level="workflow",
            id="test-workflow",
            workflow_id="run-123",
        )
        self.assertEqual(self.execution_context.context_stack.depth, 1)

        # Pop scope
        self.execution_context.context_stack.pop(level="workflow")
        self.assertEqual(self.execution_context.context_stack.depth, 0)

    def test_decision_history_recording(self):
        """Test that decisions are recorded in execution context."""
        initial_count = len(self.execution_context.decision_history)

        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_execution",
            value="execute test-workflow",
            reasoning="Test workflow",
            confidence=0.95,
        )

        self.assertEqual(
            len(self.execution_context.decision_history), initial_count + 1
        )
        last_decision = self.execution_context.decision_history[-1]
        self.assertEqual(last_decision.subsystem, "WorkflowExecutor")
        self.assertEqual(last_decision.decision_type, "workflow_execution")
        self.assertEqual(last_decision.confidence, 0.95)

    def test_get_execution_state_before_run(self):
        """Test that execution state is None before run."""
        self.assertIsNone(self.executor.get_execution_state())

    def test_get_execution_state_after_setup(self):
        """Test that execution state is available after setup."""
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="test",
            run_id="run-123",
            status="running",
            started_at=0,
        )
        state = self.executor.get_execution_state()
        self.assertIsNotNone(state)
        self.assertEqual(state.workflow_id, "test")
        self.assertEqual(state.run_id, "run-123")

    def test_get_decision_history(self):
        """Test retrieving decision history from execution context."""
        self.execution_context.record_decision(
            subsystem="TestSubsystem",
            decision_type="test_decision",
            value="test",
        )

        history = self.executor.get_decision_history()
        self.assertGreaterEqual(len(history), 1)
        self.assertTrue(any(d.subsystem == "TestSubsystem" for d in history))


if __name__ == "__main__":
    unittest.main(verbosity=2)
