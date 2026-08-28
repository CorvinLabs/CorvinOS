"""ContextBus — FIFO event pub/sub for context updates (ADR-0358).

Enables atomic, ordered propagation of context changes across all Brain subsystems.
Uses asyncio.Queue for FIFO ordering and ContextVar for task isolation.
Includes per-tenant validation to prevent cross-tenant ContextVar leaks.
"""

import asyncio
import logging
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger(__name__)

_EXECUTION_CONTEXT: ContextVar[Optional["ExecutionContext"]] = ContextVar(
    "_execution_context",
    default=None,
)

_CURRENT_TENANT_ID: ContextVar[str] = ContextVar(
    "_current_tenant_id",
    default="_default",
)


def get_current_tenant_id() -> str:
    """Get current tenant ID from ContextVar (or default)."""
    return _CURRENT_TENANT_ID.get()


def set_current_tenant_id(tenant_id: str) -> None:
    """Set current tenant ID in ContextVar (with validation)."""
    if not isinstance(tenant_id, str) or len(tenant_id.strip()) == 0:
        raise ValueError(f"Invalid tenant_id: {tenant_id}")
    _CURRENT_TENANT_ID.set(tenant_id)


def get_execution_context() -> Optional["ExecutionContext"]:
    """Get ExecutionContext from ContextVar with tenant validation (fail-closed).

    Validates that the stored ExecutionContext's tenant_id matches the current
    tenant. If mismatch detected (cross-tenant leak), returns None and logs alert.
    """
    ctx = _EXECUTION_CONTEXT.get()
    if ctx is None:
        return None

    current_tenant = get_current_tenant_id()
    if hasattr(ctx, 'tenant_id') and ctx.tenant_id != current_tenant:
        # ALERT: Cross-tenant leak detected
        logger.critical(
            f"ContextVar leak detected: ExecutionContext tenant_id={ctx.tenant_id} "
            f"does not match current tenant_id={current_tenant}. Returning None (fail-closed)."
        )
        return None

    return ctx


def set_execution_context(ctx: Optional["ExecutionContext"]) -> None:
    """Set ExecutionContext in ContextVar with tenant validation (fail-closed)."""
    if ctx is not None:
        if not hasattr(ctx, 'tenant_id'):
            raise ValueError("ExecutionContext must have tenant_id attribute")
        current_tenant = get_current_tenant_id()
        if ctx.tenant_id != current_tenant:
            raise ValueError(
                f"Cannot set ExecutionContext: tenant_id mismatch (got {ctx.tenant_id}, "
                f"expected {current_tenant})"
            )
    _EXECUTION_CONTEXT.set(ctx)


class ContextBus:
    """FIFO event pub/sub for context updates.

    Events are processed sequentially in FIFO order, ensuring consistent
    ordering across all subscribers. Backed by asyncio.Queue.
    """

    _instance: Optional["ContextBus"] = None

    def __init__(self):
        self.event_queue: Optional[asyncio.Queue] = None
        self.worker_task: Optional[asyncio.Task] = None
        self._subscribers: Dict[str, List[Callable]] = {}
        self._is_running = False

    async def start(self) -> None:
        """Start FIFO event processor.

        Must be called before publishing any events.
        """
        if self._is_running:
            return
        self.event_queue = asyncio.Queue()
        self.worker_task = asyncio.create_task(self._process_events())
        self._is_running = True

    async def stop(self) -> None:
        """Stop event processor gracefully."""
        if not self._is_running:
            return
        self._is_running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass

    async def _process_events(self) -> None:
        """Process events in FIFO order (sequential).

        Runs continuously, pulling events from queue and invoking subscribers
        in order. Ensures no event is lost.
        """
        while self._is_running:
            try:
                event_type, payload = await self.event_queue.get()
            except asyncio.CancelledError:
                break
            except Exception:
                continue

            # Invoke all subscribers for this event type
            for callback in self._subscribers.get(event_type, []):
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(payload)
                    else:
                        callback(payload)
                except Exception:
                    # Swallow exceptions from callbacks; don't block other subscribers
                    pass

            self.event_queue.task_done()

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """Subscribe to event type.

        Multiple callbacks can subscribe to the same event type.
        Callbacks are invoked in subscription order.

        Args:
            event_type: Event type to subscribe to (e.g., 'context_updated')
            callback: Function or coroutine to invoke. Signature: callback(payload: dict)
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    async def publish(self, event_type: str, payload: dict) -> None:
        """Publish event (queued, processed in FIFO order).

        Events are added to queue in order and processed sequentially.
        This method returns immediately (fire-and-forget).

        Args:
            event_type: Event type identifier.
            payload: Event data as dict.

        Raises:
            RuntimeError: If event bus is not started.
        """
        if not self._is_running:
            raise RuntimeError("ContextBus not started; call await start() first")
        if self.event_queue is None:
            raise RuntimeError("Event queue is None")
        await self.event_queue.put((event_type, payload))

    @staticmethod
    def get_context() -> Optional["ExecutionContext"]:
        """Get current ExecutionContext from ContextVar.

        Returns None if no context has been set.
        """
        return _EXECUTION_CONTEXT.get()

    @staticmethod
    def set_context(ctx: "ExecutionContext") -> None:
        """Set current ExecutionContext in ContextVar.

        Args:
            ctx: ExecutionContext to store.
        """
        _EXECUTION_CONTEXT.set(ctx)

    @classmethod
    def get_instance(cls) -> Optional["ContextBus"]:
        """Get the global singleton ContextBus instance.

        Returns None if the singleton has not been set.
        Used by session_reset and other modules to access the bus without
        importing the hub.
        """
        return cls._instance

    @classmethod
    def set_instance(cls, instance: Optional["ContextBus"]) -> None:
        """Set the global singleton ContextBus instance.

        Called by SubsystemHub during initialization to register the bus.
        """
        cls._instance = instance

    def subscriber_count(self, event_type: str) -> int:
        """Get count of subscribers for event type."""
        return len(self._subscribers.get(event_type, []))

    def event_queue_size(self) -> int:
        """Get current size of event queue (pending events)."""
        if self.event_queue is None:
            return 0
        return self.event_queue.qsize()
