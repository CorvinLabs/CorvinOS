"""Brain ↔ Workflow Bridge — Bidirectional event coordination (ADR-0423 Phase 2).

Enables Brain subsystem to:
1. Subscribe to workflow events (node start/end/error)
2. Update strategy + guidance based on workflow progress
3. Publish guidance suggestions back to WorkflowExecutor

This is the integration point between:
- WorkflowExecutor (Phase 2) — publishes workflow.* events to ContextBus
- Brain subsystems — subscribe to workflow events and update strategy
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Dict, List

_log = logging.getLogger("core.brain.workflow_bridge")


@dataclass
class WorkflowGuidance:
    """Guidance suggestion from Brain to WorkflowExecutor."""

    guidance_id: str
    node_id: str
    suggestion: str  # e.g., "decompose", "parallelize", "retry"
    confidence: float  # 0.0–1.0
    rationale: str
    timestamp: float


@dataclass
class WorkflowFeedback:
    """Feedback from WorkflowExecutor to Brain about outcomes."""

    run_id: str
    node_id: str
    event_type: str  # "node_completed" | "node_failed"
    output: dict[str, Any] | None = None
    error: Optional[str] = None
    retry_count: int = 0


class WorkflowBridge:
    """Bidirectional bridge between Brain and Workflow subsystems.

    Responsibilities:
    - Subscribe to workflow events from ContextBus
    - Update Brain's ExecutionContext based on workflow progress
    - Publish guidance suggestions back to workflow

    Lifecycle:
    - Instantiated by Brain subsystem
    - Registered with ContextBus at startup
    - Unregistered on shutdown
    """

    def __init__(self, execution_context: Any, context_bus: Any):
        """Initialize WorkflowBridge.

        Args:
            execution_context: ExecutionContext v2 (from Brain)
            context_bus: ContextBus instance for event pub/sub
        """
        self.execution_context = execution_context
        self.context_bus = context_bus
        self.guidance_registry: Dict[str, WorkflowGuidance] = {}
        self._subscribers: List[Callable] = []
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize bridge: register subscriptions with ContextBus.

        Must be called before any workflow events are published.

        Raises:
            RuntimeError: If context_bus not available
        """
        if self._initialized:
            return

        if self.context_bus is None:
            _log.warning(
                "WorkflowBridge: ContextBus not available; "
                "workflow events will not be routed to Brain"
            )
            return

        try:
            # Subscribe to all workflow.* events
            self.context_bus.subscribe(
                event_type="workflow.node_started",
                callback=self._on_node_started,
            )
            self.context_bus.subscribe(
                event_type="workflow.node_completed",
                callback=self._on_node_completed,
            )
            self.context_bus.subscribe(
                event_type="workflow.node_failed",
                callback=self._on_node_failed,
            )
            self.context_bus.subscribe(
                event_type="workflow.workflow_started",
                callback=self._on_workflow_started,
            )
            self.context_bus.subscribe(
                event_type="workflow.workflow_completed",
                callback=self._on_workflow_completed,
            )

            self._initialized = True
            _log.info("WorkflowBridge initialized; subscriptions registered")

        except Exception as e:
            _log.error(f"Failed to initialize WorkflowBridge: {e}")
            raise RuntimeError("WorkflowBridge initialization failed") from e

    async def shutdown(self) -> None:
        """Shutdown bridge: clean up subscriptions (placeholder for future)."""
        self._initialized = False
        self.guidance_registry.clear()
        _log.info("WorkflowBridge shut down")

    def _on_node_started(self, payload: dict[str, Any]) -> None:
        """Handle node_started event from workflow.

        Args:
            payload: Event payload from ContextBus
        """
        node_id = payload.get("node_id")
        node_type = payload.get("node_type")

        _log.debug(f"Workflow event: node_started {node_id} ({node_type})")

        # Update ExecutionContext with current node info
        try:
            self.execution_context.set_field(
                "current_node",
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "event_type": "started",
                },
            )
        except (AttributeError, Exception):
            # ExecutionContext may not have current_node field; that's OK
            pass

        # Trigger Brain's guidance suggestion routine (async, non-blocking)
        # In full Phase 2, this would call Brain's strategy update method
        self._schedule_guidance_update(node_id, node_type, "node_started")

    def _on_node_completed(self, payload: dict[str, Any]) -> None:
        """Handle node_completed event from workflow.

        Args:
            payload: Event payload from ContextBus
        """
        node_id = payload.get("node_id")
        output = payload.get("output", {})

        _log.debug(f"Workflow event: node_completed {node_id}")

        # Record feedback for Brain learning
        feedback = WorkflowFeedback(
            run_id=payload.get("run_id", ""),
            node_id=node_id,
            event_type="node_completed",
            output=output,
        )

        self._record_feedback(feedback)
        self._schedule_guidance_update(node_id, None, "node_completed")

    def _on_node_failed(self, payload: dict[str, Any]) -> None:
        """Handle node_failed event from workflow.

        Args:
            payload: Event payload from ContextBus
        """
        node_id = payload.get("node_id")
        error = payload.get("error", "unknown error")
        retry_count = payload.get("retry_count", 0)

        _log.warning(f"Workflow event: node_failed {node_id} ({error})")

        # Record failure for Brain error learning
        feedback = WorkflowFeedback(
            run_id=payload.get("run_id", ""),
            node_id=node_id,
            event_type="node_failed",
            error=error,
            retry_count=retry_count,
        )

        self._record_feedback(feedback)

        # In full Phase 2, Brain would analyze error and suggest recovery
        self._schedule_guidance_update(node_id, None, "node_failed")

    def _on_workflow_started(self, payload: dict[str, Any]) -> None:
        """Handle workflow_started event.

        Args:
            payload: Event payload from ContextBus
        """
        workflow_id = payload.get("workflow_id", "")
        run_id = payload.get("run_id", "")

        _log.info(f"Workflow execution started: {workflow_id} (run_id={run_id})")

        # Update ExecutionContext with workflow info
        try:
            self.execution_context.set_field("current_workflow", workflow_id)
            self.execution_context.set_field("current_run_id", run_id)
        except (AttributeError, Exception):
            pass

    def _on_workflow_completed(self, payload: dict[str, Any]) -> None:
        """Handle workflow_completed event.

        Args:
            payload: Event payload from ContextBus
        """
        workflow_id = payload.get("workflow_id", "")
        state = payload.get("state", "unknown")
        total_wall_s = payload.get("total_wall_s", 0)

        _log.info(
            f"Workflow execution completed: {workflow_id} "
            f"(state={state}, wall_time={total_wall_s:.1f}s)"
        )

        # Update ExecutionContext
        try:
            self.execution_context.record_decision(
                subsystem="WorkflowBridge",
                decision_type="workflow_completion",
                value=state,
                reasoning=f"Workflow {workflow_id} completed in state {state}",
                confidence=0.95,
            )
        except (AttributeError, Exception):
            pass

    def publish_guidance(
        self,
        node_id: str,
        suggestion: str,
        confidence: float,
        rationale: str,
    ) -> None:
        """Publish guidance suggestion from Brain to workflow.

        Brain calls this when it has a strategy suggestion for a node.

        Args:
            node_id: Target node ID
            suggestion: Guidance text (e.g., "decompose")
            confidence: Brain's confidence (0.0–1.0)
            rationale: Reasoning for the suggestion
        """
        from uuid import uuid4
        from time import time as time_now

        guidance = WorkflowGuidance(
            guidance_id=str(uuid4()),
            node_id=node_id,
            suggestion=suggestion,
            confidence=confidence,
            rationale=rationale,
            timestamp=time_now(),
        )

        self.guidance_registry[guidance.guidance_id] = guidance

        _log.info(
            f"Brain guidance published for {node_id}: {suggestion} "
            f"(confidence={confidence:.2f})"
        )

    def get_guidance_for_node(self, node_id: str) -> Optional[WorkflowGuidance]:
        """Retrieve guidance for a specific node.

        Called by WorkflowExecutor to check if Brain has guidance for a node.

        Args:
            node_id: Node ID to look up guidance for

        Returns:
            WorkflowGuidance if available, None otherwise
        """
        # Find most recent guidance for this node
        for guidance in reversed(list(self.guidance_registry.values())):
            if guidance.node_id == node_id:
                return guidance
        return None

    def _schedule_guidance_update(
        self,
        node_id: str,
        node_type: Optional[str],
        event_type: str,
    ) -> None:
        """Schedule Brain to update guidance (placeholder for async scheduling).

        In full Phase 2, this would trigger Brain's strategy update routine
        asynchronously.

        Args:
            node_id: Node ID
            node_type: Node type (if available)
            event_type: Workflow event type
        """
        # Placeholder: In production, would call Brain's async update method
        _log.debug(
            f"Scheduled guidance update for {node_id} on {event_type}"
        )

    def _record_feedback(self, feedback: WorkflowFeedback) -> None:
        """Record workflow feedback for Brain learning.

        Args:
            feedback: WorkflowFeedback record
        """
        # Placeholder: In production, would record to learning subsystem
        _log.debug(f"Recorded feedback: {feedback.node_id} {feedback.event_type}")

    def get_bridge_status(self) -> dict[str, Any]:
        """Get current bridge status (diagnostics)."""
        return {
            "initialized": self._initialized,
            "guidance_count": len(self.guidance_registry),
            "execution_context": (
                self.execution_context.task_id
                if self.execution_context
                else None
            ),
        }
