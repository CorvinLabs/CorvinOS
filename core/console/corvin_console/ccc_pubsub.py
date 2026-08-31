"""ccc_pubsub.py — Fan-out pub/sub for CCC entity events (ADR-0168 M3).

Each subscriber receives its own asyncio.Queue — true fan-out so all
open WebSocket connections for a tenant receive every entity event.

Event schema (ccc.entity_event):
{
    "type":         "ccc.entity_event",
    "action_id":    "uuid4",          # links to originating chat message
    "event_kind":   "created" | "updated" | "deleted" | "progress" | "error",
    "entity_type":  "workflow" | "ats_task" | ...,
    "entity_id":    "str | None",
    "tenant_id":    "str",
    "payload":      { ... }           # L34-gated: stripped to {entity_id,status} for CONFIDENTIAL+
}
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_QUEUE_SIZE = 500  # per-subscriber; slow consumers drop their own events

# Entity types whose payloads are CONFIDENTIAL — strip to {entity_id, status} only.
# Single source of truth: entity_extract.CONFIDENTIAL_ENTITY_TYPES. Imported with
# a fail-CLOSED fallback (the full set) so a broken import can never widen the
# payload that reaches subscribers (security review 2026-06-27, C5).
try:  # entity_extract lives in operator/bridges/shared (on sys.path via CCC)
    from entity_extract import CONFIDENTIAL_ENTITY_TYPES as _CONFIDENTIAL_ENTITIES
except Exception:  # noqa: BLE001 — fail closed with the complete set
    _CONFIDENTIAL_ENTITIES: frozenset[str] = frozenset({
        "erasure_request", "vault_entry", "a2a_session",
    })


def _gate_payload(entity_type: str, payload: dict) -> dict:
    """L34 gate: strip payload to {entity_id, status} for CONFIDENTIAL+ entities."""
    if entity_type in _CONFIDENTIAL_ENTITIES:
        return {
            "entity_id": payload.get("entity_id"),
            "status":    payload.get("status", "unknown"),
        }
    return payload


class CCCPubSub:
    """Fan-out pub/sub for CCC entity events (per-tenant).

    STATUS (security review 2026-06-27, C4): no WebSocket route currently calls
    ``subscribe()``, so ``publish()`` is a no-op (``if not tenant_map: return``)
    and the ``_gate_payload`` L34 control here is NOT yet on a live path. The
    only path that reaches the UI today is chat_runtime's direct ``ccc_action``
    yield on the chat WebSocket (which applies its own L34 gate). True cross-tab
    fan-out (a Tasks view in a separate browser tab) does NOT work until a CCC
    WS subscription route is wired. Do not assume this gate is active in prod.

    Usage::

        pubsub = CCCPubSub()

        # Publisher (command router):
        await pubsub.publish(tenant_id, action_id, "created", "workflow", "wf-1",
                             {"name": "my-flow", "status": "running"})

        # Subscriber (WebSocket handler):
        async for event in pubsub.subscribe(tenant_id):
            await ws.send_json(event)
    """

    def __init__(self) -> None:
        # tenant_id -> {sub_id -> asyncio.Queue}
        self._queues: dict[str, dict[str, asyncio.Queue]] = {}

    async def subscribe(self, tenant_id: str) -> AsyncIterator[dict]:
        """Subscribe to all CCC entity events for a tenant (fan-out).

        Each caller gets an independent queue — no event is consumed at the
        expense of another subscriber. The iterator exits on a 5-minute idle
        timeout (reconnect is the client's responsibility).
        """
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
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

    async def publish(
        self,
        tenant_id: str,
        action_id: str,
        event_kind: str,
        entity_type: str,
        entity_id: str | None,
        payload: dict,
    ) -> None:
        """Publish a CCC entity event to ALL subscribers for this tenant.

        Payload is L34-gated before enqueueing — CONFIDENTIAL+ entities are
        stripped to {entity_id, status}. Slow consumers drop their own events;
        other subscribers are unaffected.
        """
        tenant_map = self._queues.get(tenant_id)
        if not tenant_map:
            return

        gated = _gate_payload(entity_type, {**payload, "entity_id": entity_id})
        event = {
            "type":        "ccc.entity_event",
            "action_id":   action_id,
            "event_kind":  event_kind,
            "entity_type": entity_type,
            "entity_id":   entity_id,
            "tenant_id":   tenant_id,
            "payload":     gated,
        }

        for sub_id, q in list(tenant_map.items()):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "CCC PubSub queue full for sub %s (tenant=%s): dropping event",
                    sub_id, tenant_id,
                )

    def subscriber_count(self, tenant_id: str) -> int:
        return len(self._queues.get(tenant_id, {}))


# Global singleton (replaced per app instance in FastAPI lifespan if needed)
_default_ccc_pubsub = CCCPubSub()


def get_ccc_pubsub() -> CCCPubSub:
    return _default_ccc_pubsub
