"""Shared learning-event emission helper for the video_producer workers (ADR-0314/0695).

Every worker (`slide_renderer`, `voice_synthesizer`, `screenshot_capturer`,
`video_assembler`, `youtube_uploader`) previously constructed `EventEmitter()`
with no arguments and called `await emitter.emit("some_string", data_dict)`.
Neither matches the real API: `EventEmitter.__init__` requires an `EventStore`
(ADR-0314 audit-first tightening), and `EventEmitter.emit()` is synchronous
and takes one `LearningEvent`, not an `(event_type_str, dict)` pair. This
module is the one place that constructs both correctly, so the 5 workers stay
thin and consistent instead of repeating the same tenant-scoping + event
construction boilerplate five times.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore
from core.learning.learning_events import EventType, LearningEvent
from core.paths import tenant_home

logger = logging.getLogger(__name__)


def build_event_emitter(tenant_id: str) -> EventEmitter:
    """One tenant-scoped EventStore + EventEmitter per worker instance."""
    store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
    return EventEmitter(store)


def emit_worker_event(
    emitter: EventEmitter,
    *,
    event_type: EventType,
    skill_id: str,
    tenant_id: str,
    signal: dict,
) -> None:
    """Fire-and-forget a LearningEvent. Never blocks or raises into the caller's
    workflow -- a learning-event failure must not fail a render/upload step."""
    try:
        event = LearningEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            skill_id=skill_id,
            tenant_id=tenant_id,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            signal=signal,
        )
        emitter.emit(event)
    except Exception as e:  # noqa: BLE001 -- fire-and-forget by design
        logger.warning(f"Failed to emit {event_type.value} event (skill_id={skill_id}): {e}")
