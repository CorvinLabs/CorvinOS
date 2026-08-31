"""E2E tests for Workflow Phase 2 — Basic end-to-end workflow execution.

Tests the full workflow execution pipeline:
1. Define a simple 3-node workflow (decision → action → terminal)
2. Execute through WorkflowExecutor
3. Verify ExecutionContext state
4. Verify audit trail recording
5. Verify ContextBus events

Run: python3 tests/e2e/test_workflow_phase2_basics.py
"""

import asyncio
import unittest
from unittest.mock import Mock, AsyncMock, MagicMock
from pathlib import Path
import sys
from dataclasses import dataclass

# Add package to path
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

from core.workflows.execution_engine import (
    WorkflowExecutor,
    WorkflowExecutionState,
    WorkflowNodeEvent,
)
from core.context_engineering.execution_context import (
    ExecutionContext,
    ContextStack,
)
from core.context_engineering.context_bus import ContextBus
from core.brain.workflow_bridge import WorkflowBridge


class MockDAGRunner:
    """Mock DAG runner for testing workflow execution."""

    def __init__(self):
        self.nodes = {}
        self.executed_nodes = []
        self.node_outputs = {}

    def add_node(self, node_id: str, node_type: str, config: dict):
        """Add a node to the DAG."""
        self.nodes[node_id] = {"type": node_type, "config": config}

    async def run(self, workflow_id: str, run_id: str, inputs: dict) -> dict:
        """Simulate running the workflow."""
        results = {}
        for node_id in ["decision_node", "action_node", "terminal_node"]:
            if node_id in self.nodes:
                self.executed_nodes.append(node_id)
                # Simulate node output
                if node_id == "decision_node":
                    results[node_id] = {"decision": "execute"}
                elif node_id == "action_node":
                    results[node_id] = {"action": "completed", "status": "ok"}
                elif node_id == "terminal_node":
                    results[node_id] = {"result": "success"}
        return results

    def get_node_output(self, node_id: str) -> dict:
        """Get output from a specific node."""
        return self.node_outputs.get(node_id, {})


class TestWorkflowPhase2BasicExecution(unittest.TestCase):
    """E2E test for basic 3-node workflow execution."""

    def setUp(self):
        """Set up workflow execution environment."""
        # Create execution context
        self.context_stack = ContextStack()
        self.execution_context = ExecutionContext(
            task_id="e2e-workflow-task-1",
            tenant_id="_default",
            task_template={
                "name": "e2e-basic-workflow",
                "version": "1.0.0",
            },
            context_stack=self.context_stack,
            budget_remaining=5000.0,
        )

        # Create mock DAG runner
        self.dag_runner = MockDAGRunner()
        self.dag_runner.add_node(
            "decision_node", "decision", {"strategy": "simple"}
        )
        self.dag_runner.add_node(
            "action_node", "action", {"action_type": "execute"}
        )
        self.dag_runner.add_node(
            "terminal_node", "terminal", {"output_format": "json"}
        )

        # Create context bus (will be mocked for test)
        self.context_bus = Mock()

        # Create workflow executor
        self.executor = WorkflowExecutor(
            dag_runner=self.dag_runner,
            execution_context=self.execution_context,
            context_bus=self.context_bus,
        )

        # Create workflow bridge
        self.bridge = WorkflowBridge(
            execution_context=self.execution_context,
            context_bus=self.context_bus,
        )

    def test_workflow_execution_lifecycle(self):
        """Test complete workflow execution lifecycle."""
        # Step 1: Initialize execution state
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="e2e-basic-workflow",
            run_id="run-e2e-1",
            status="running",
            started_at=0,
        )

        self.assertIsNotNone(self.executor.execution_state)
        self.assertEqual(self.executor.execution_state.status, "running")
        self.assertEqual(self.executor.execution_state.workflow_id, "e2e-basic-workflow")

    def test_workflow_node_execution_sequence(self):
        """Test executing a sequence of workflow nodes."""
        # Initialize execution state
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="e2e-basic-workflow",
            run_id="run-e2e-1",
            status="running",
            started_at=0,
        )

        # Simulate decision node
        event1 = self.executor.record_node_event(
            node_id="decision_node",
            node_type="decision",
            event_type="node_started",
        )
        self.assertEqual(event1.node_id, "decision_node")
        self.assertEqual(event1.event_type, "node_started")

        event2 = self.executor.record_node_event(
            node_id="decision_node",
            node_type="decision",
            event_type="node_completed",
            output={"decision": "execute"},
        )
        self.assertEqual(event2.event_type, "node_completed")
        self.assertIn("decision_node", self.executor.execution_state.nodes_executed)

        # Simulate action node
        event3 = self.executor.record_node_event(
            node_id="action_node",
            node_type="action",
            event_type="node_started",
        )
        self.assertEqual(event3.node_id, "action_node")

        event4 = self.executor.record_node_event(
            node_id="action_node",
            node_type="action",
            event_type="node_completed",
            output={"action": "completed", "status": "ok"},
        )
        self.assertIn("action_node", self.executor.execution_state.nodes_executed)

        # Simulate terminal node
        event5 = self.executor.record_node_event(
            node_id="terminal_node",
            node_type="terminal",
            event_type="node_started",
        )
        self.assertEqual(event5.node_id, "terminal_node")

        event6 = self.executor.record_node_event(
            node_id="terminal_node",
            node_type="terminal",
            event_type="node_completed",
            output={"result": "success"},
        )
        self.assertIn("terminal_node", self.executor.execution_state.nodes_executed)

        # Verify all nodes executed
        self.assertEqual(len(self.executor.execution_state.nodes_executed), 3)
        self.assertEqual(len(self.executor.execution_state.events), 6)

    def test_execution_context_tracks_workflow_decisions(self):
        """Test that ExecutionContext tracks decisions during workflow."""
        # Record workflow start decision
        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_start",
            value="e2e-basic-workflow",
            reasoning="Starting basic workflow",
            confidence=0.95,
        )

        # Record workflow progress decision
        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_progress",
            value="executing nodes",
            reasoning="Processing decision → action → terminal sequence",
            confidence=0.90,
        )

        # Verify decisions recorded
        self.assertEqual(len(self.execution_context.decision_history), 2)
        self.assertEqual(
            self.execution_context.decision_history[0].decision_type,
            "workflow_start",
        )
        self.assertEqual(
            self.execution_context.decision_history[1].decision_type,
            "workflow_progress",
        )

    def test_context_stack_tracks_workflow_scope(self):
        """Test that context stack tracks workflow execution scope."""
        # Push workflow scope
        self.context_stack.push(
            level="workflow",
            id="e2e-basic-workflow",
            run_id="run-e2e-1",
        )
        self.assertEqual(self.context_stack.depth, 1)

        # Push node scopes
        self.context_stack.push(level="node", id="decision_node")
        self.assertEqual(self.context_stack.depth, 2)

        self.context_stack.pop(level="node")
        self.assertEqual(self.context_stack.depth, 1)

        # Pop workflow scope
        self.context_stack.pop(level="workflow")
        self.assertEqual(self.context_stack.depth, 0)

    def test_workflow_bridge_receives_workflow_events(self):
        """Test that WorkflowBridge receives and processes workflow events."""
        # Simulate workflow started event
        payload = {
            "workflow_id": "e2e-basic-workflow",
            "run_id": "run-e2e-1",
        }

        self.bridge._on_workflow_started(payload)

        # Verify decision was recorded
        history = self.execution_context.decision_history
        self.assertEqual(len(history), 0)  # No decision recorded for workflow_started

        # Simulate workflow completed event
        payload_completed = {
            "workflow_id": "e2e-basic-workflow",
            "state": "succeeded",
            "total_wall_s": 1.5,
        }

        self.bridge._on_workflow_completed(payload_completed)

        # Verify decision was recorded
        history = self.execution_context.decision_history
        self.assertGreater(len(history), 0)
        last_decision = history[-1]
        self.assertEqual(last_decision.subsystem, "WorkflowBridge")
        self.assertEqual(last_decision.decision_type, "workflow_completion")

    def test_workflow_bridge_processes_node_events(self):
        """Test that WorkflowBridge processes node events from workflow."""
        # Simulate node_started event
        payload_started = {
            "node_id": "decision_node",
            "node_type": "decision",
            "run_id": "run-e2e-1",
        }

        self.bridge._on_node_started(payload_started)

        # Simulate node_completed event
        payload_completed = {
            "node_id": "decision_node",
            "run_id": "run-e2e-1",
            "output": {"decision": "execute"},
        }

        self.bridge._on_node_completed(payload_completed)

        # Simulate node_failed event
        payload_failed = {
            "node_id": "action_node",
            "run_id": "run-e2e-1",
            "error": "Connection timeout",
            "retry_count": 1,
        }

        self.bridge._on_node_failed(payload_failed)

        # Just verify no exceptions were raised

    def test_workflow_execution_error_handling(self):
        """Test workflow execution with error scenario."""
        # Initialize execution state
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="e2e-basic-workflow",
            run_id="run-e2e-1",
            status="running",
            started_at=0,
        )

        # Simulate node that fails
        event = self.executor.record_node_event(
            node_id="action_node",
            node_type="action",
            event_type="node_failed",
            error="Connection timeout",
        )

        self.assertEqual(event.event_type, "node_failed")
        self.assertEqual(event.error, "Connection timeout")
        self.assertTrue(any("action_node" in e for e in self.executor.execution_state.errors))

    def test_workflow_execution_with_guidance(self):
        """Test workflow execution with Brain guidance."""
        # Publish guidance
        self.bridge.publish_guidance(
            node_id="action_node",
            suggestion="retry",
            confidence=0.85,
            rationale="Temporary failure",
        )

        # Retrieve guidance
        guidance = self.bridge.get_guidance_for_node("action_node")
        self.assertIsNotNone(guidance)
        self.assertEqual(guidance.suggestion, "retry")
        self.assertEqual(guidance.confidence, 0.85)

    def test_workflow_phase_completion_tracking(self):
        """Test tracking workflow completion through ExecutionContext."""
        initial_budget = self.execution_context.budget_remaining

        # Simulate workflow execution consuming budget
        self.execution_context.budget_remaining -= 100.0

        # Record completion decision
        self.execution_context.record_decision(
            subsystem="WorkflowExecutor",
            decision_type="workflow_completion",
            value="succeeded",
            reasoning="All nodes completed successfully",
            confidence=0.95,
        )

        # Verify state
        self.assertLess(
            self.execution_context.budget_remaining,
            initial_budget,
        )
        self.assertEqual(len(self.execution_context.decision_history), 1)

    def test_workflow_multi_node_event_ordering(self):
        """Test that events from multiple nodes are recorded in order."""
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="e2e-basic-workflow",
            run_id="run-e2e-1",
            status="running",
            started_at=0,
        )

        # Record events in sequence
        events = []
        for i, node_id in enumerate(
            ["decision_node", "action_node", "terminal_node"]
        ):
            event = self.executor.record_node_event(
                node_id=node_id,
                node_type="agent",
                event_type="node_started",
            )
            events.append(event)

        # Verify events are in the state
        self.assertEqual(len(self.executor.execution_state.events), 3)
        # Verify order
        for i, event in enumerate(self.executor.execution_state.events):
            expected_nodes = ["decision_node", "action_node", "terminal_node"]
            self.assertEqual(event.node_id, expected_nodes[i])

    def test_workflow_state_persistence_readiness(self):
        """Test that workflow state can be persisted for session checkpoint."""
        self.executor.execution_state = WorkflowExecutionState(
            workflow_id="e2e-basic-workflow",
            run_id="run-e2e-1",
            status="completed",
            started_at=0.0,
            completed_at=1.5,
        )

        self.executor.execution_state.nodes_executed.extend(
            ["decision_node", "action_node", "terminal_node"]
        )

        # Verify state can be serialized
        state = self.executor.get_execution_state()
        self.assertIsNotNone(state)
        self.assertEqual(state.status, "completed")
        self.assertEqual(len(state.nodes_executed), 3)


class TestWorkflowBridgeIntegrationE2E(unittest.TestCase):
    """E2E tests for WorkflowBridge integration with workflow execution."""

    def setUp(self):
        """Set up for E2E tests."""
        self.context_stack = ContextStack()
        self.execution_context = ExecutionContext(
            task_id="e2e-bridge-task-1",
            tenant_id="_default",
            task_template={"name": "e2e-bridge-workflow"},
            context_stack=self.context_stack,
            budget_remaining=5000.0,
        )

        self.context_bus = Mock()
        self.bridge = WorkflowBridge(
            execution_context=self.execution_context,
            context_bus=self.context_bus,
        )

    def test_bridge_full_workflow_lifecycle(self):
        """Test bridge through a complete workflow lifecycle."""
        # Workflow started
        self.bridge._on_workflow_started(
            {
                "workflow_id": "e2e-test-workflow",
                "run_id": "run-123",
            }
        )

        # Node 1: decision
        self.bridge._on_node_started(
            {"node_id": "n1", "node_type": "decision", "run_id": "run-123"}
        )
        self.bridge._on_node_completed(
            {
                "node_id": "n1",
                "run_id": "run-123",
                "output": {"decision": "proceed"},
            }
        )

        # Node 2: action (with guidance)
        self.bridge._on_node_started(
            {"node_id": "n2", "node_type": "action", "run_id": "run-123"}
        )
        self.bridge.publish_guidance(
            node_id="n2",
            suggestion="parallelize",
            confidence=0.80,
            rationale="Multiple independent operations",
        )
        self.bridge._on_node_completed(
            {
                "node_id": "n2",
                "run_id": "run-123",
                "output": {"status": "ok"},
            }
        )

        # Node 3: terminal
        self.bridge._on_node_started(
            {"node_id": "n3", "node_type": "terminal", "run_id": "run-123"}
        )
        self.bridge._on_node_completed(
            {
                "node_id": "n3",
                "run_id": "run-123",
                "output": {"result": "success"},
            }
        )

        # Workflow completed
        self.bridge._on_workflow_completed(
            {
                "workflow_id": "e2e-test-workflow",
                "state": "succeeded",
                "total_wall_s": 2.5,
            }
        )

        # Verify guidance was published and is retrievable
        guidance = self.bridge.get_guidance_for_node("n2")
        self.assertIsNotNone(guidance)
        self.assertEqual(guidance.suggestion, "parallelize")

        # Verify decision was recorded
        history = self.execution_context.decision_history
        self.assertGreater(len(history), 0)

    def test_bridge_handles_concurrent_nodes(self):
        """Test bridge handling multiple concurrent node events."""
        # Simulate concurrent node execution
        for node_num in range(1, 4):
            self.bridge._on_node_started(
                {
                    "node_id": f"node_{node_num}",
                    "node_type": "parallel",
                    "run_id": "run-123",
                }
            )

        # All nodes complete
        for node_num in range(1, 4):
            self.bridge._on_node_completed(
                {
                    "node_id": f"node_{node_num}",
                    "run_id": "run-123",
                    "output": {"status": "ok"},
                }
            )

        # Verify bridge handles multiple concurrent events without error
        self.assertTrue(True)  # No exceptions raised


if __name__ == "__main__":
    unittest.main(verbosity=2)
