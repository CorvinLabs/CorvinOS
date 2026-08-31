"""In-memory pub/sub for task progress broadcast (ADR-0081 M2.0, ADR-0101 M5).

Each subscriber receives its own asyncio.Queue — true fan-out so that all
open WebSocket connections for a tenant receive every event, not just one.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_SUBSCRIBER_QUEUE_SIZE = 500  # per-subscriber cap; slow consumers drop their own events


class TaskPubSub:
    """In-memory fan-out pub/sub for task progress events (per-tenant).

    Event schema:
    {
        "task_id": "uuid",
        "event": "progress|completed|failed",
        "seq": int,
        "timestamp": float,
        ...
    }
    """

    def __init__(self) -> None:
        # tenant_id -> {sub_id -> asyncio.Queue}
        self._queues: dict[str, dict[str, asyncio.Queue]] = {}

    async def subscribe(self, tenant_id: str) -> AsyncIterator[dict]:
        """Subscribe to all task events for a tenant (fan-out).

        Each caller gets its own queue, so every subscriber receives every
        published event independently (no event is consumed by one subscriber
        at the expense of another).

        Args:
            tenant_id: Tenant identifier.

        Yields:
            Task event dicts.
        """
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)

        tenant_map = self._queues.setdefault(tenant_id, {})
        tenant_map[sub_id] = q

        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=300)
                    yield event
                except asyncio.TimeoutError:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            tenant_map.pop(sub_id, None)
            if not tenant_map:
                self._queues.pop(tenant_id, None)

    async def publish(self, tenant_id: str, task_id: str, event: dict) -> None:
        """Publish event to ALL subscribers for this tenant (fan-out).

        If a subscriber's queue is full (slow consumer), only that subscriber's
        event is dropped — all other subscribers are unaffected.

        Args:
            tenant_id: Tenant identifier.
            task_id: Task identifier (added to payload).
            event: Event dict to broadcast.
        """
        tenant_map = self._queues.get(tenant_id)
        if not tenant_map:
            return

        payload = {"task_id": task_id, **event}

        for sub_id, q in list(tenant_map.items()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning(
                    "PubSub queue full for subscriber %s (tenant=%s): dropping event",
                    sub_id, tenant_id,
                )

    def subscriber_count(self, tenant_id: str) -> int:
        """Get active subscriber count for a tenant."""
        return len(self._queues.get(tenant_id, {}))


# Global singleton (replaced by per-app instance in FastAPI lifespan if needed)
_default_pubsub = TaskPubSub()


def get_pubsub() -> TaskPubSub:
    """Get global pub/sub instance."""
    return _default_pubsub
