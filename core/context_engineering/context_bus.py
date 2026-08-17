"""ContextBus — FIFO event pub/sub for context updates (ADR-0358).

Enables atomic, ordered propagation of context changes across all Brain subsystems.
Uses asyncio.Queue for FIFO ordering and ContextVar for task isolation.
"""

import asyncio
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional


_EXECUTION_CONTEXT: ContextVar[Optional["ExecutionContext"]] = ContextVar(
    "_execution_context",
    default=None,
)


class ContextBus:
    """FIFO event pub/sub for context updates.

    Events are processed sequentially in FIFO order, ensuring consistent
    ordering across all subscribers. Backed by asyncio.Queue.
    """

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

    def subscriber_count(self, event_type: str) -> int:
        """Get count of subscribers for event type."""
        return len(self._subscribers.get(event_type, []))

    def event_queue_size(self) -> int:
        """Get current size of event queue (pending events)."""
        if self.event_queue is None:
            return 0
        return self.event_queue.qsize()
