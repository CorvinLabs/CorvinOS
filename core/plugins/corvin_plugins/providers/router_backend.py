"""Router backend provider - task routing logic.

Singleton registry for task delegation and routing decisions.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Protocol
import threading

_logger = logging.getLogger(__name__)

_lock = threading.Lock()
_active_backend: Optional['RouterBackend'] = None


@dataclass(frozen=True)
class RoutingDecision:
    """A routing decision for a task."""
    task_id: str
    tenant_id: str
    user_id: str
    destination: str  # "agent", "skill", "plugin", etc.
    confidence: float  # 0.0-1.0
    metadata: dict = None


class RouterBackend(Protocol):
    """Protocol for router backends."""

    async def decide_route(self, task_type: str, context: dict, tenant_id: str) -> RoutingDecision:
        """Make a routing decision for a task."""
        ...

    async def record_route(self, decision: RoutingDecision) -> bool:
        """Record a routing decision."""
        ...

    async def get_routing_stats(self, tenant_id: str) -> dict:
        """Get routing statistics."""
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultRouterBackend:
    """Default in-process router backend."""

    def __init__(self):
        """Initialize the router backend."""
        self._decisions: list[RoutingDecision] = []
        self._lock = threading.Lock()

    async def decide_route(self, task_type: str, context: dict, tenant_id: str) -> RoutingDecision:
        """Make a routing decision."""
        try:
            # Simple routing: default to "agent"
            decision = RoutingDecision(
                task_id=context.get("task_id", "unknown"),
                tenant_id=tenant_id,
                user_id=context.get("user_id", "unknown"),
                destination="agent",
                confidence=0.9,
                metadata={"task_type": task_type}
            )
            return decision
        except Exception as e:
            _logger.error(f"Routing decision failed: {e}")
            raise

    async def record_route(self, decision: RoutingDecision) -> bool:
        """Record a routing decision."""
        try:
            with self._lock:
                self._decisions.append(decision)
                _logger.debug(f"Route recorded: {decision.destination}")
            return True
        except Exception as e:
            _logger.error(f"Failed to record route: {e}")
            return False

    async def get_routing_stats(self, tenant_id: str) -> dict:
        """Get routing statistics."""
        try:
            with self._lock:
                matching = [d for d in self._decisions if d.tenant_id == tenant_id]
                destinations = {}
                for d in matching:
                    destinations[d.destination] = destinations.get(d.destination, 0) + 1
                return {"total_routes": len(matching), "by_destination": destinations}
        except Exception:
            return {}

    async def health_check(self) -> bool:
        """Check backend health."""
        return True


def get_active() -> RouterBackend:
    """Get the currently active router backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultRouterBackend()
        return _active_backend


def set_active(backend: RouterBackend) -> None:
    """Set the active router backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend
