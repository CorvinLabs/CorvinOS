"""ContextAPI — Uniform interface for Brain subsystems to interact with ExecutionContext (ADR-0358).

Provides query, update, decision recording, scope management, and event subscription
capabilities for all 13 Brain subsystems.
Includes tenant_id capture in async tasks to prevent cross-tenant ContextVar inheritance.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from .context_bus import ContextBus, get_current_tenant_id
from .execution_context import ExecutionContext
from .decision_record import DecisionRecord

logger = logging.getLogger(__name__)


class ContextAPI:
    """Uniform interface for all Brain subsystems to interact with ExecutionContext.

    Provides consistent patterns for:
    - Querying execution state
    - Updating context fields with broadcast
    - Recording subsystem decisions in audit trail
    - Managing nested scopes
    - Subscribing to context updates

    Per PERF #2 fix: tracks pending broadcast tasks to prevent resource leaks.
    """

    def __init__(self, subsystem_name: str, context_bus: ContextBus):
        """Initialize API for a specific subsystem.

        Args:
            subsystem_name: Name of the subsystem (e.g., 'LoopEngineer', 'SafetyValidator')
            context_bus: Shared ContextBus for broadcasting updates
        """
        self.name = subsystem_name
        self.bus = context_bus
        self._pending_broadcasts: set = set()  # Track fire-and-forget tasks for cleanup

    async def shutdown(self) -> None:
        """Gracefully shutdown, ensuring all pending broadcasts complete.

        Called by TaskBrain.shutdown() to ensure no broadcasts are lost
        during shutdown.
        """
        if self._pending_broadcasts:
            await asyncio.gather(*self._pending_broadcasts, return_exceptions=True)
            self._pending_broadcasts.clear()

    def _track_broadcast(self, task: asyncio.Task) -> None:
        """Track a broadcast task for cleanup."""
        self._pending_broadcasts.add(task)
        task.add_done_callback(self._pending_broadcasts.discard)

    @property
    def current_context(self) -> ExecutionContext:
        """Get current ExecutionContext.

        Returns:
            The ExecutionContext for the current task.

        Raises:
            RuntimeError: If no context has been set (task not initialized).
        """
        ctx = self.bus.get_context()
        if ctx is None:
            raise RuntimeError(
                f"ExecutionContext not initialized. {self.name} cannot operate without a task context."
            )
        return ctx

    def query_context(self, key: str) -> Any:
        """Query a context field by name.

        Args:
            key: Field name (e.g., 'budget_remaining', 'model', 'strategy_confidence')

        Returns:
            Field value, or None if not found.

        Raises:
            RuntimeError: If context not initialized.
        """
        return self.current_context.get_field(key)

    def update_context(self, **kwargs) -> Dict[str, tuple]:
        """Update one or more context fields and broadcast the change.

        Each update is recorded in the event bus, notifying all subscribers
        of the change.

        Args:
            **kwargs: Field updates (e.g., model='claude-3-sonnet', budget_remaining=150.0)

        Returns:
            Dict mapping field names to (old_value, new_value) tuples.

        Raises:
            RuntimeError: If context not initialized.
            AttributeError: If field doesn't exist on ExecutionContext.
        """
        ctx = self.current_context
        updates = {}

        # Validate and collect updates
        for key, value in kwargs.items():
            old_value = ctx.get_field(key)
            ctx.set_field(key, value)  # Raises if field doesn't exist
            updates[key] = (old_value, value)

        # Broadcast via ContextBus (tracked to prevent resource leaks)
        # IMPORTANT: Capture tenant_id at task creation time to prevent
        # asyncio.create_task() from inheriting stale ContextVar on delayed execution
        task = asyncio.create_task(
            self.bus.publish("context_updated", {
                "subsystem": self.name,
                "tenant_id": ctx.tenant_id,  # Captured at creation time (CE-005 fix)
                "updates": {k: {"old": v[0], "new": v[1]} for k, v in updates.items()},
                "context_stack": str(ctx.context_stack),
                "timestamp": DecisionRecord.now_iso(),
            })
        )
        self._track_broadcast(task)

        return updates

    def record_decision(
        self,
        decision_type: str,
        value: str,
        reasoning: str = "",
        confidence: float = 0.5,
        guidance_applied: bool = False,
    ) -> DecisionRecord:
        """Record a decision in the audit trail.

        Decisions are immutable and timestamped, creating a permanent record
        of subsystem reasoning. Used for learning and debugging.

        Args:
            decision_type: Type of decision (e.g., 'strategy_selection')
            value: The decision value (e.g., 'direct_fix')
            reasoning: Justification for the decision
            confidence: Confidence level (0.0–1.0)
            guidance_applied: True if guidance influenced this decision

        Returns:
            The created DecisionRecord.

        Raises:
            RuntimeError: If context not initialized.
        """
        record = self.current_context.record_decision(
            subsystem=self.name,
            decision_type=decision_type,
            value=value,
            reasoning=reasoning,
            confidence=confidence,
            guidance_applied=guidance_applied,
        )

        # Broadcast decision recorded (tracked to prevent resource leaks)
        # Capture tenant_id at task creation time (CE-005 fix)
        task = asyncio.create_task(
            self.bus.publish("decision_recorded", {
                "subsystem": self.name,
                "tenant_id": self.current_context.tenant_id,  # Captured at creation time
                "decision_type": decision_type,
                "value": value,
                "confidence": confidence,
                "guidance_applied": guidance_applied,
                "context_stack": str(self.current_context.context_stack),
                "timestamp": record.timestamp,
            })
        )
        self._track_broadcast(task)

        return record

    def push_scope(self, level: str, id: str, **metadata) -> None:
        """Enter a nested scope.

        Used to track hierarchical context (task → worker → file).
        Must be paired with pop_scope() to maintain a valid stack.

        Args:
            level: Scope level (e.g., 'task', 'worker', 'file')
            id: Unique identifier for this scope
            **metadata: Additional metadata for the scope

        Raises:
            RuntimeError: If context not initialized.
        """
        ctx = self.current_context
        ctx.context_stack.push(level, id, **metadata)

        # Broadcast scope change (tracked to prevent resource leaks)
        # Capture tenant_id at task creation time (CE-005 fix)
        task = asyncio.create_task(
            self.bus.publish("scope_entered", {
                "subsystem": self.name,
                "tenant_id": ctx.tenant_id,  # Captured at creation time
                "level": level,
                "id": id,
                "metadata": metadata if metadata else {},
                "context_stack": str(ctx.context_stack),
            })
        )
        self._track_broadcast(task)

    def pop_scope(self, level: Optional[str] = None) -> None:
        """Exit the current scope.

        Args:
            level: Optional scope level to verify before popping.

        Raises:
            RuntimeError: If context not initialized.
            ValueError: If level doesn't match the top of stack.
        """
        ctx = self.current_context
        popped = ctx.context_stack.pop(level)

        if popped:
            # Broadcast scope exit (tracked to prevent resource leaks)
            # Capture tenant_id at task creation time (CE-005 fix)
            task = asyncio.create_task(
                self.bus.publish("scope_exited", {
                    "subsystem": self.name,
                    "tenant_id": ctx.tenant_id,  # Captured at creation time
                    "level": popped.level,
                    "id": popped.id,
                    "context_stack": str(ctx.context_stack),
                })
            )
            self._track_broadcast(task)

    async def subscribe_context_updates(self, callback: Callable) -> None:
        """Subscribe to context update events.

        Callback will be invoked whenever any subsystem updates context fields.

        Args:
            callback: Function(payload: dict) or async function to invoke.
                     Payload contains: subsystem, updates, context_stack, timestamp
        """
        self.bus.subscribe("context_updated", callback)

    async def subscribe_decisions(self, callback: Callable) -> None:
        """Subscribe to decision recording events.

        Callback will be invoked whenever any subsystem records a decision.

        Args:
            callback: Function(payload: dict) or async function to invoke.
                     Payload contains: subsystem, decision_type, value, confidence, etc.
        """
        self.bus.subscribe("decision_recorded", callback)

    async def subscribe_scope_changes(self, callback: Callable) -> None:
        """Subscribe to scope change events.

        Callback will be invoked whenever scopes are entered or exited.

        Args:
            callback: Function(payload: dict) to invoke (for scope_entered or scope_exited events)
        """
        self.bus.subscribe("scope_entered", callback)
        self.bus.subscribe("scope_exited", callback)

    def get_context_summary(self) -> Dict[str, Any]:
        """Get a summary of the current context state.

        Returns:
            Dict with key context information (no decision history details).
        """
        ctx = self.current_context
        return {
            "task_id": ctx.task_id,
            "tenant_id": ctx.tenant_id,
            "context_stack": str(ctx.context_stack),
            "budget_remaining": ctx.budget_remaining,
            "time_remaining": ctx.time_remaining,
            "model": ctx.model,
            "strategy": ctx.strategy,
            "strategy_confidence": ctx.strategy_confidence,
            "decision_count": len(ctx.decision_history),
            "checkpoint_count": len(ctx.checkpoints),
        }

    def checkpoint(self, name: str, data: Dict[str, Any]) -> None:
        """Create a checkpoint for potential recovery.

        Args:
            name: Checkpoint identifier
            data: Checkpoint data to persist

        Raises:
            RuntimeError: If context not initialized.
        """
        ctx = self.current_context
        ctx.checkpoint(name, data)

        # Broadcast checkpoint (tracked to prevent resource leaks)
        task = asyncio.create_task(
            self.bus.publish("checkpoint_created", {
                "subsystem": self.name,
                "checkpoint_name": name,
                "context_stack": str(ctx.context_stack),
                "timestamp": DecisionRecord.now_iso(),
            })
        )
        self._track_broadcast(task)
