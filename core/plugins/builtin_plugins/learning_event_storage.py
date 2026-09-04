"""Learning Event Storage Plugin — Persistent storage for learning events.

Category: data_processing | Type: storage_backend
Stores and retrieves learning events with queue backpressure management.
"""

import asyncio
import queue
import threading
from typing import Optional, Any


class LearningEventStorage:
    """Plugin: stores learning events with backpressure."""

    def __init__(self, queue_size: int = 1000):
        """Initialize storage."""
        self._events: list[dict[str, Any]] = []
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._initialized = False

    async def initialize(self, ctx) -> bool:
        """Initialize the plugin."""
        self._initialized = True
        return True

    async def execute(self, op: str, **kwargs) -> dict:
        """Execute a storage operation.

        Operations:
        - store_event: Store a learning event
        - retrieve_events: Get stored events
        - queue_stats: Check queue status
        """
        if not self._initialized:
            return {"success": False, "error": "not initialized"}

        op_lower = op.lower()

        if op_lower == "store_event":
            event = kwargs.get("event", {})

            try:
                # Try to queue with backpressure handling
                try:
                    self._queue.put_nowait(event)
                    # Also store in memory
                    with self._lock:
                        self._events.append(event)
                    return {"success": True, "queued": True}
                except queue.Full:
                    return {"success": False, "error": "queue full (backpressure)", "dropped": True}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "retrieve_events":
            event_type = kwargs.get("event_type")
            tenant_id = kwargs.get("tenant_id", "_default")

            try:
                with self._lock:
                    # Filter by tenant_id first (GDPR Art. 5, 6, 32)
                    tenant_events = [e for e in self._events if e.get("tenant_id") == tenant_id]
                    if event_type:
                        filtered = [e for e in tenant_events if e.get("type") == event_type]
                    else:
                        filtered = tenant_events
                return {"success": True, "events": filtered, "count": len(filtered)}
            except Exception as e:
                return {"success": False, "error": str(e)}

        elif op_lower == "queue_stats":
            try:
                with self._queue_lock:
                    queue_size = self._queue.qsize()
                    queue_maxsize = self._queue.maxsize
                with self._lock:
                    event_count = len(self._events)
                return {
                    "success": True,
                    "queue_size": queue_size,
                    "queue_maxsize": queue_maxsize,
                    "stored_events": event_count
                }
            except Exception as e:
                return {"success": False, "error": str(e)}

        return {"success": False, "error": f"unknown operation: {op}"}

    def get_all_events(self, tenant_id: str) -> list[dict]:
        """Get all events for a specific tenant (with lock).

        GDPR Art. 5/6/32: Filter by tenant_id, never cross-tenant leakage.
        """
        with self._lock:
            events = []
            for event in self._events:
                if event.get("tenant_id") == tenant_id:
                    events.append(event)
            return events

    def query_by_type(self, event_type: str, tenant_id: str) -> list[dict]:
        """Query events by type for a specific tenant (with tenant isolation)."""
        with self._lock:
            return [e for e in self._events
                    if e.get("tenant_id") == tenant_id and e.get("type") == event_type]

    def query_by_skill(self, skill_id: str, tenant_id: str) -> list[dict]:
        """Query events by skill for a specific tenant (with tenant isolation)."""
        with self._lock:
            return [e for e in self._events
                    if e.get("tenant_id") == tenant_id and e.get("skill_id") == skill_id]

    async def health_check(self) -> bool:
        """Check plugin health."""
        return self._initialized

    async def shutdown(self) -> None:
        """Shutdown the plugin."""
        with self._lock:
            self._events.clear()
        self._initialized = False
