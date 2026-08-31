"""SubsystemHub: Event bus and request router for Brain subsystems.

ADR-0347: Brain Subsystem Hub Architecture
ADR-0348: Event Bus Pattern
ADR-0358: Context Engineering v2 integration
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SubsystemHub:
    """Central coordinator for Brain subsystems.

    Provides:
    - Event pub/sub (one-way broadcasts)
    - Request/response routing (two-way queries)
    - Subsystem lifecycle management
    - API registry for loose coupling (ADR-0361)
    - ContextBus integration for context-aware subsystems (ADR-0358)
    """

    def __init__(self, max_event_queue_size: int = 10000, context_bus: Optional[Any] = None):
        """Initialize SubsystemHub.

        Args:
            max_event_queue_size: Max size of event queue before dropping events
            context_bus: Optional ContextBus instance for subsystem context updates.
                        If None, one will be imported and created lazily.
        """
        self.subsystems: Dict[str, Any] = {}
        self.subscribers: Dict[str, List[Callable]] = {}
        self.event_queue: asyncio.Queue = asyncio.Queue(maxsize=max_event_queue_size)
        self._running = False
        self._apis: Dict[str, Any] = {}  # API registry (ADR-0361)
        self._context_bus = context_bus  # ContextBus for context updates (ADR-0358)

    @property
    def context_bus(self) -> Any:
        """Get or lazily initialize ContextBus.

        Subsystems call this during startup() to access context updates.
        Lazy initialization avoids circular imports.
        """
        if self._context_bus is None:
            from core.context_engineering.context_bus import ContextBus
            self._context_bus = ContextBus()
            # Register singleton instance so session_reset.py can access it
            ContextBus.set_instance(self._context_bus)
            self._context_bus.hub = self
        return self._context_bus

    def register_subsystem(self, subsystem: "Subsystem") -> None:  # noqa: F821
        """Register a subsystem and call its startup hook."""
        name = subsystem.name
        if name in self.subsystems:
            raise ValueError(f"Subsystem {name} already registered")

        self.subsystems[name] = subsystem
        logger.info(f"Registering subsystem: {name} v{subsystem.version}")
        subsystem.startup(self)

    def unregister_subsystem(self, name: str) -> None:
        """Unregister a subsystem and call its shutdown hook."""
        if name not in self.subsystems:
            return

        subsystem = self.subsystems[name]
        try:
            subsystem.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down {name}: {e}")

        del self.subsystems[name]
        logger.info(f"Unregistered subsystem: {name}")

    def subscribe(self, event_name: str, handler: Callable) -> None:
        """Subscribe to an event."""
        if event_name not in self.subscribers:
            self.subscribers[event_name] = []
        self.subscribers[event_name].append(handler)
        logger.debug(f"Subscribed to {event_name}")

    def publish_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Publish event (fail-closed on queue full, per ADR-0347).

        Per ADR-0347 L198 "Must NOT do: silently drop events", this method
        fails-closed: raises exception on queue full so caller knows event
        wasn't delivered. Caller can then decide to retry, buffer, or escalate.

        Raises:
            RuntimeError: If event queue is full.
        """
        try:
            self.event_queue.put_nowait((event_name, event_data))
        except asyncio.QueueFull:
            error_msg = f"Event queue full, cannot queue {event_name} event (max_size={self.event_queue.maxsize})"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from None

    async def request_from_subsystem(
        self, subsystem_name: str, request_type: str, **kwargs
    ) -> Any:
        """Query another subsystem and wait for response."""
        if subsystem_name not in self.subsystems:
            raise ValueError(f"Subsystem {subsystem_name} not found")

        subsystem = self.subsystems[subsystem_name]
        return await subsystem.handle_request(request_type, **kwargs)

    async def process_events(self, timeout_s: float = 60.0) -> None:
        """Process one queued event (FIFO order, per ADR-0358 BLOCKER #3).

        ADR-0358 BLOCKER #3 specifies: "Choose Option A (FIFO): Sequential event
        processing; higher latency but predictable" instead of concurrent processing.

        This method processes events in strict FIFO order. All handlers for an event
        are invoked sequentially before the next event is processed. This ensures:
        - No race conditions on shared state
        - Subsystems see consistent ordering of state changes
        - Cost updates and budget reads are atomic w.r.t. event ordering

        Per ADR-0358 L272-274: "Typical ~10ms per event for 100 subscribers is acceptable."
        """
        try:
            event_name, event_data = await asyncio.wait_for(
                self.event_queue.get(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            return

        if event_name not in self.subscribers:
            return

        handlers = self.subscribers[event_name]

        # Process handlers SEQUENTIALLY (not concurrent) to maintain FIFO ordering
        # ADR-0358 L268-274: sequential processing prevents CostController and
        # LoopEngineer from reading stale state concurrently
        for i, handler in enumerate(handlers):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event_name, event_data)
                else:
                    handler(event_name, event_data)
            except Exception as e:
                logger.error(f"Error in {event_name} handler {i}: {e}")

    async def run_forever(self, poll_interval_s: float = 5.0) -> None:
        """Main orchestration loop."""
        self._running = True
        logger.info("Hub running forever")

        try:
            while self._running:
                await self.process_events()
                await asyncio.sleep(poll_interval_s)
        except Exception as e:
            logger.error(f"Hub crashed: {e}")
            raise
        finally:
            self._running = False

    def stop(self) -> None:
        """Stop the hub."""
        self._running = False

    # ========================================================================
    # API Registry (ADR-0361): Loose coupling for subsystems
    # ========================================================================

    def register_api(self, api_name: str, api_impl: Any) -> None:
        """Register an API for loose coupling.

        Called during subsystem startup to expose its API to other subsystems.

        Args:
            api_name: API identifier (e.g., "forged_tool", "forged_skill")
            api_impl: API implementation object (must implement the interface)

        Raises:
            ValueError: If API already registered
        """
        if api_name in self._apis:
            raise ValueError(f"API already registered: {api_name}")
        self._apis[api_name] = api_impl
        logger.debug(f"Registered API: {api_name}")

    def get_api(self, api_name: str) -> Any:
        """Get an API by name.

        Args:
            api_name: API identifier (e.g., "forged_tool", "forged_skill")

        Returns:
            API implementation object

        Raises:
            KeyError: If API not found
        """
        if api_name not in self._apis:
            raise KeyError(f"API not found: {api_name}")
        return self._apis[api_name]

    def has_api(self, api_name: str) -> bool:
        """Check if API is available.

        Args:
            api_name: API identifier

        Returns:
            True if API is registered, False otherwise
        """
        return api_name in self._apis
