"""WorkflowExecutor — High-level orchestration layer for Phase 2 workflow execution (ADR-0423 Phase 2).

Wraps Phase 1's DAGRunner and integrates:
1. ExecutionContext v2 for decision tracking
2. ContextBus for workflow events (Brain subscribes)
3. Session manager checkpointing
4. Error handling & recovery with exponential backoff

This is the L3 orchestration layer that mediates between Brain subsystems and the
low-level workflow runner (DAGRunner). Does NOT reimplement DAGRunner — enhances it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List
from time import time as time_now
from uuid import uuid4

from core.context_engineering.execution_context import (
    ExecutionContext,
    ContextStack,
)
from core.context_engineering.context_bus import ContextBus, get_current_tenant_id
from core.workflows.corvin_workflows.runner import (
    DAGRunner,
    RunResult,
    ResumeContext,
)
from core.workflows.corvin_workflows.storage import WorkflowDoc

_log = logging.getLogger("core.workflows.execution_engine")


@dataclass
class WorkflowNodeEvent:
    """Event emitted when a workflow node starts/ends/fails."""

    node_id: str
    node_type: str
    event_type: str  # "node_started" | "node_completed" | "node_failed"
    timestamp: float
    output: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    retry_count: int = 0
    context_stack: str = ""  # Stringified ContextStack at event time


@dataclass
class WorkflowExecutionState:
    """Tracks the state of a workflow execution."""

    workflow_id: str
    run_id: str
    status: str  # "pending" | "running" | "completed" | "failed" | "paused"
    started_at: float
    completed_at: Optional[float] = None
    nodes_executed: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    events: List[WorkflowNodeEvent] = field(default_factory=list)


class WorkflowExecutor:
    """High-level workflow execution orchestrator.

    Responsibilities:
    - Wraps DAGRunner (Phase 1)
    - Integrates ExecutionContext v2 for decision tracking
    - Publishes workflow events to ContextBus
    - Manages error recovery (exponential backoff)
    - Coordinates session manager checkpointing
    - Receives Brain guidance via ContextBus subscriptions
    """

    # Exponential backoff config (K=3 retries)
    _RETRY_ATTEMPTS = 3
    _RETRY_BASE_DELAY = 1.0  # seconds
    _RETRY_MAX_DELAY = 4.0  # cap at 4s

    def __init__(
        self,
        dag_runner: DAGRunner,
        execution_context: ExecutionContext,
        context_bus: Optional[ContextBus] = None,
    ):
        """Initialize WorkflowExecutor.

        Args:
            dag_runner: Phase 1 DAGRunner instance (wrapped, not replaced)
            execution_context: ExecutionContext v2 for decision tracking
            context_bus: ContextBus for event pub/sub (optional for early Phase 2)
        """
        self.dag_runner = dag_runner
        self.execution_context = execution_context
        self.context_bus = context_bus
        self.execution_state: Optional[WorkflowExecutionState] = None

    async def execute(
        self,
        workflow_doc: WorkflowDoc,
        inputs: dict[str, Any] | None = None,
        resume_context: Optional[ResumeContext] = None,
    ) -> RunResult:
        """Execute a workflow with full ExecutionContext + ContextBus integration.

        Args:
            workflow_doc: The workflow definition
            inputs: Input parameters to the workflow
            resume_context: Resume from checkpoint if provided

        Returns:
            RunResult from DAGRunner (Phase 1)

        Raises:
            RuntimeError: If context mismatch or critical error
        """
        run_id = str(uuid4())
        self.execution_state = WorkflowExecutionState(
            workflow_id=workflow_doc.name,
            run_id=run_id,
            status="pending",
            started_at=time_now(),
        )

        # Validate tenant isolation (GDPR Art. 5, 32)
        tenant_id = get_current_tenant_id()
        if self.execution_context.tenant_id != tenant_id:
            raise RuntimeError(
                f"Tenant mismatch: ExecutionContext {self.execution_context.tenant_id} "
                f"vs current {tenant_id} (GDPR fail-closed)"
            )

        try:
            # Push workflow scope onto execution context stack
            self.execution_context.context_stack.push(
                level="workflow",
                id=workflow_doc.name,
                workflow_id=run_id,
            )

            # Publish workflow_started event
            await self._publish_event("workflow_started", {
                "workflow_id": workflow_doc.name,
                "run_id": run_id,
                "resume": resume_context is not None,
            })

            # Record decision: workflow execution strategy
            self.execution_context.record_decision(
                subsystem="WorkflowExecutor",
                decision_type="workflow_execution",
                value=f"execute {workflow_doc.name}",
                reasoning="Orchestrating workflow via Phase 1 DAGRunner",
                confidence=0.95,
            )

            # Execute via Phase 1 DAGRunner with retry logic
            result = await self._run_with_retry(
                inputs=inputs,
                resume=resume_context,
                run_id=run_id,
            )

            # Publish workflow_completed event
            await self._publish_event("workflow_completed", {
                "workflow_id": workflow_doc.name,
                "run_id": run_id,
                "state": result.state,
                "total_wall_s": result.total_wall_s,
            })

            self.execution_state.status = result.state
            self.execution_state.completed_at = time_now()

            return result

        except Exception as e:
            _log.exception(f"Workflow execution failed: {e}")
            self.execution_state.status = "failed"
            self.execution_state.errors.append(str(e))
            self.execution_state.completed_at = time_now()

            # Publish workflow_failed event
            await self._publish_event("workflow_failed", {
                "workflow_id": workflow_doc.name,
                "run_id": run_id,
                "error": str(e),
            })

            raise

        finally:
            # Pop workflow scope
            self.execution_context.context_stack.pop(level="workflow")

    async def _run_with_retry(
        self,
        inputs: dict[str, Any] | None = None,
        resume: Optional[ResumeContext] = None,
        run_id: Optional[str] = None,
    ) -> RunResult:
        """Execute DAGRunner with exponential backoff retry (K=3).

        Transient failures (timeouts, temporary service unavailability) trigger retry.
        Terminal failures (validation errors, malformed workflow) fail immediately.

        Args:
            inputs: Input parameters
            resume: Resume context if applicable
            run_id: Run ID for tracking

        Returns:
            RunResult from DAGRunner

        Raises:
            RuntimeError: On terminal failure or max retries exceeded
        """
        for attempt in range(self._RETRY_ATTEMPTS):
            try:
                # Call Phase 1 DAGRunner synchronously (wraps in event loop if needed)
                result = self.dag_runner.run(
                    inputs=inputs,
                    resume=resume,
                    run_id=run_id,
                )

                # Record successful execution
                self.execution_context.record_decision(
                    subsystem="WorkflowExecutor",
                    decision_type="workflow_result",
                    value=result.state,
                    reasoning=f"Workflow completed in state {result.state}",
                    confidence=0.95,
                )

                return result

            except (TimeoutError, ConnectionError, OSError) as e:
                # Transient failure — retry with exponential backoff
                if attempt < self._RETRY_ATTEMPTS - 1:
                    delay = min(
                        self._RETRY_BASE_DELAY * (2 ** attempt),
                        self._RETRY_MAX_DELAY,
                    )
                    _log.warning(
                        f"Transient failure on attempt {attempt + 1}/{self._RETRY_ATTEMPTS}: {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Max retries exceeded
                    raise RuntimeError(
                        f"Workflow execution failed after {self._RETRY_ATTEMPTS} retries: {e}"
                    ) from e

            except (ValueError, KeyError, TypeError) as e:
                # Terminal failure — don't retry
                _log.error(f"Terminal failure (invalid workflow): {e}")
                raise RuntimeError(f"Workflow validation failed: {e}") from e

    async def _publish_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish a workflow event to ContextBus (fire-and-forget).

        Brain subsystems subscribe to these events to update strategy and guidance.

        Args:
            event_type: Event type (e.g., 'workflow_started', 'workflow_completed')
            payload: Event data as dict
        """
        if self.context_bus is None:
            return  # No-op if ContextBus not available (early Phase 2)

        try:
            await self.context_bus.publish(
                event_type=f"workflow.{event_type}",
                payload={
                    **payload,
                    "tenant_id": self.execution_context.tenant_id,
                    "timestamp": time_now(),
                },
            )
        except Exception as e:
            _log.warning(f"Failed to publish event {event_type}: {e}")

    def record_node_event(
        self,
        node_id: str,
        node_type: str,
        event_type: str,
        output: dict[str, Any] | None = None,
        error: Optional[str] = None,
        retry_count: int = 0,
    ) -> WorkflowNodeEvent:
        """Record a node-level event in the execution state.

        Called by DAGRunner hooks or node executors to track node progress.

        Args:
            node_id: Node identifier
            node_type: Node type (agent, decision, action, loop, etc.)
            event_type: 'node_started' | 'node_completed' | 'node_failed'
            output: Node output if completed
            error: Error message if failed
            retry_count: Number of retries attempted

        Returns:
            WorkflowNodeEvent that was recorded
        """
        if self.execution_state is None:
            raise RuntimeError("No active workflow execution")

        event = WorkflowNodeEvent(
            node_id=node_id,
            node_type=node_type,
            event_type=event_type,
            timestamp=time_now(),
            output=output or {},
            error=error,
            retry_count=retry_count,
            context_stack=str(self.execution_context.context_stack),
        )

        self.execution_state.events.append(event)
        if event_type == "node_completed":
            self.execution_state.nodes_executed.append(node_id)
        elif event_type == "node_failed":
            self.execution_state.errors.append(f"{node_id}: {error}")

        return event

    def get_execution_state(self) -> Optional[WorkflowExecutionState]:
        """Get current execution state (for Brain guidance and diagnostics)."""
        return self.execution_state

    def get_decision_history(self) -> List[Any]:
        """Get decision history from ExecutionContext (for audit/learning)."""
        return self.execution_context.decision_history

    def save_execution_state(
        self,
        session_id: str,
        trigger_type: str = "",
    ) -> Any:
        """Save current workflow execution state to a SessionCheckpoint (k=3).

        Called on session split to capture execution state for resumption.

        Args:
            session_id: Current session ID
            trigger_type: What triggered the checkpoint (context_limit, token_burn, etc.)

        Returns:
            SessionCheckpoint object with workflow_execution_state serialized
        """
        from core.session_manager.checkpoint import SessionCheckpoint

        if self.execution_state is None:
            _log.warning("No active workflow execution to save")
            return SessionCheckpoint(
                session_id=session_id,
                task_id=self.execution_context.task_id,
                phase="unknown",
                tenant_id=self.execution_context.tenant_id,
                trigger_type=trigger_type,
            )

        # Create checkpoint with workflow execution state
        checkpoint = SessionCheckpoint(
            session_id=session_id,
            task_id=self.execution_context.task_id,
            phase="execution",  # TODO: read from execution_context if available
            tenant_id=self.execution_context.tenant_id,
            trigger_type=trigger_type,
            iterations_at_checkpoint=len(self.execution_state.events),
            workflow_execution_state=self.execution_state,
        )

        _log.info(
            f"Saved workflow execution state to checkpoint {checkpoint.checkpoint_id} "
            f"(workflow={self.execution_state.workflow_id}, nodes_executed={len(self.execution_state.nodes_executed)})"
        )

        return checkpoint

    def restore_execution_state(self, checkpoint: Any) -> None:
        """Restore workflow execution state from a SessionCheckpoint (k=3).

        Called on session resume to restore execution state for continuation.

        Args:
            checkpoint: SessionCheckpoint object with workflow_execution_state

        Raises:
            ValueError: If checkpoint does not contain workflow execution state
        """
        if not hasattr(checkpoint, "workflow_execution_state"):
            raise ValueError("Checkpoint does not have workflow_execution_state attribute")

        workflow_state_data = checkpoint.workflow_execution_state
        if workflow_state_data is None:
            _log.warning("Checkpoint has no workflow execution state to restore")
            return

        # Reconstruct WorkflowExecutionState from dict/object
        if isinstance(workflow_state_data, dict):
            # Deserialize from dict (JSON)
            self.execution_state = WorkflowExecutionState(
                workflow_id=workflow_state_data.get("workflow_id", ""),
                run_id=workflow_state_data.get("run_id", ""),
                status=workflow_state_data.get("status", "paused"),
                started_at=workflow_state_data.get("started_at", time_now()),
                completed_at=workflow_state_data.get("completed_at"),
                nodes_executed=workflow_state_data.get("nodes_executed", []),
                errors=workflow_state_data.get("errors", []),
            )
        elif isinstance(workflow_state_data, WorkflowExecutionState):
            # Already a WorkflowExecutionState object
            self.execution_state = workflow_state_data
        else:
            raise ValueError(
                f"Cannot restore workflow execution state from {type(workflow_state_data)}"
            )

        _log.info(
            f"Restored workflow execution state from checkpoint "
            f"(workflow={self.execution_state.workflow_id}, nodes_executed={len(self.execution_state.nodes_executed)})"
        )
