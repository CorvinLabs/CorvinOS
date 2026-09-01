"""
Middleware Pipeline — Phase 3

Ordered execution chain with priority-based sorting.
Max 5 middlewares, no skipping, linear flow.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import asyncio
import threading


@dataclass
class Middleware:
    """Single middleware."""
    name: str
    priority: int  # Higher = earlier

    async def before_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Process event before plugins."""
        return event

    async def after_event(self, event: Dict[str, Any], result: Any) -> Any:
        """Process result after plugins."""
        return result


class MiddlewareStack:
    """Ordered middleware pipeline."""

    def __init__(self, max_middlewares: int = 5):
        self.middlewares: Dict[str, Middleware] = {}
        self.max_middlewares = max_middlewares
        self.ordered: List[Middleware] = []
        self._lock = threading.Lock()

    def register(self, middleware: Middleware) -> bool:
        """Register middleware."""
        with self._lock:
            if len(self.middlewares) >= self.max_middlewares:
                return False

            self.middlewares[middleware.name] = middleware
            self._sort()
            return True

    def _sort(self):
        """Sort by priority (higher first)."""
        self.ordered = sorted(
            self.middlewares.values(),
            key=lambda m: m.priority,
            reverse=True
        )

    async def process_before(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Process event through before_event chain."""
        with self._lock:
            ordered = list(self.ordered)  # Snapshot while locked
        for mw in ordered:
            event = await mw.before_event(event)
        return event

    async def process_after(self, event: Dict[str, Any], result: Any) -> Any:
        """Process result through after_event chain (reverse)."""
        with self._lock:
            ordered = list(self.ordered)  # Snapshot while locked
        for mw in reversed(ordered):
            result = await mw.after_event(event, result)
        return result

    async def execute_with_middleware(
        self,
        event: Dict[str, Any],
        handler,
    ) -> Any:
        """Execute with full middleware pipeline."""
        event = await self.process_before(event)
        result = await handler(event)
        result = await self.process_after(event, result)
        return result


class VersionRouter:
    """Route to plugin versions (canary support)."""

    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.versions: Dict[str, Any] = {}
        self.traffic_split: Dict[str, int] = {}  # {version: percentage}
        self.metrics = {"routing_errors": 0}
        self._lock = threading.Lock()

    def register_version(self, version: str, handler, traffic_percent: int = 0):
        """Register a plugin version."""
        with self._lock:
            self.versions[version] = handler
            self.traffic_split[version] = traffic_percent

    def route(self, event: Dict[str, Any]) -> Optional[Any]:
        """Route to correct version based on traffic split."""
        import random

        with self._lock:
            total_weight = sum(self.traffic_split.values())
            if total_weight == 0:
                return None

            # Weighted random
            r = random.randint(1, total_weight)
            cumulative = 0

            for version in sorted(self.versions.keys()):
                cumulative += self.traffic_split.get(version, 0)
                if r <= cumulative:
                    return self.versions[version]

            self.metrics["routing_errors"] += 1
            return None


class InterPluginCommunication:
    """Plugin-to-plugin call routing."""

    def __init__(self, registry):
        self.registry = registry
        self._execution_stack = threading.local()  # Thread-local storage for call depth
        self._lock = threading.Lock()

    def _get_call_depth(self, caller_id: str) -> int:
        """Get current call depth for caller in this thread."""
        if not hasattr(self._execution_stack, 'depth'):
            self._execution_stack.depth = {}
        return self._execution_stack.depth.get(caller_id, 0)

    def _set_call_depth(self, caller_id: str, depth: int):
        """Set call depth for caller in this thread."""
        if not hasattr(self._execution_stack, 'depth'):
            self._execution_stack.depth = {}
        self._execution_stack.depth[caller_id] = depth

    async def call_plugin(
        self,
        caller_id: str,
        callee_id: str,
        method: str,
        *args,
        **kwargs
    ) -> Optional[Any]:
        """Call another plugin."""
        # Check call depth
        current_depth = self._get_call_depth(caller_id)
        if current_depth >= 3:
            raise RuntimeError(f"Call depth exceeded for {caller_id}")

        if callee_id not in self.registry:
            raise RuntimeError(f"Plugin {callee_id} not found")

        callee = self.registry[callee_id]

        # Increment depth
        self._set_call_depth(caller_id, current_depth + 1)

        try:
            result = await getattr(callee, method)(*args, **kwargs)
            return result
        finally:
            # Decrement depth
            self._set_call_depth(caller_id, current_depth)


# Global middleware stack
_stack: Optional[MiddlewareStack] = None


def get_middleware_stack() -> MiddlewareStack:
    """Get global middleware stack."""
    global _stack
    if _stack is None:
        _stack = MiddlewareStack()
    return _stack
