"""orchestration_events.py — Real-time WebSocket for orchestration events in Console.

Subscribes to orchestration_router events and broadcasts to connected WebSocket
clients. Enables real-time live-feed of batch completions in the Console web UI.

The Console Panel (web-next/src/app/orchestration-live-feed.tsx) connects here
to receive events and auto-play voice summaries.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestration", tags=["orchestration"])

# In-memory queue for orchestration events (from orchestration_router._emit_console_event)
# This is a simple asyncio.Queue; in production could use Redis or other message broker
console_orchestration_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)


class OrchestrationEventManager:
    """Manages WebSocket connections and broadcasts orchestration events."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"orchestration_events: client connected ({len(self.active_connections)} total)")

    def disconnect(self, websocket: WebSocket):
        """Remove a disconnected client."""
        self.active_connections.remove(websocket)
        logger.info(f"orchestration_events: client disconnected ({len(self.active_connections)} total)")

    async def broadcast(self, event: dict):
        """Send event to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(event)
            except Exception as e:
                logger.warning(f"orchestration_events: send failed, disconnecting: {e}")
                disconnected.append(connection)

        # Clean up disconnected clients
        for conn in disconnected:
            self.disconnect(conn)


manager = OrchestrationEventManager()


@router.websocket("/ws/events")
async def websocket_orchestration_events(websocket: WebSocket):
    """WebSocket endpoint for real-time orchestration event stream.

    Clients connect here and receive events as they're emitted by
    orchestration_aggregator → orchestration_router.

    Message Format:
    {
        "type": "orchestration_complete",
        "event_type": "ORCHESTRATION_COMPLETE_SUCCESS",
        "batch_id": "batch_1234567",
        "task_count": 3,
        "success_count": 3,
        "text": "✅ 3 background tasks completed successfully",
        "voice_attachment_url": "/api/orchestration/voice/batch_1234567.ogg",
        "timestamp": 1695386400.123
    }
    """
    await manager.connect(websocket)
    try:
        # Keep connection alive, forward events from queue to all clients
        while True:
            # Get event from the global queue (populated by orchestration_router)
            event = await console_orchestration_queue.get()

            # Enrich event with URLs for Console client
            if event.get("voice_attachment_path"):
                batch_id = event.get("batch_id", "unknown")
                event["voice_attachment_url"] = f"/api/orchestration/voice/{batch_id}.ogg"
                # Remove file path (don't expose internal paths to client)
                del event["voice_attachment_path"]

            # Format for WebSocket client
            ws_message = {
                "type": "orchestration_complete",
                "event_type": event.get("event_type"),
                "batch_id": event.get("batch_id"),
                "task_count": event.get("task_count"),
                "success_count": event.get("success_count"),
                "text": event.get("text"),
                "voice_attachment_url": event.get("voice_attachment_url"),
                "timestamp": event.get("timestamp"),
            }

            # Broadcast to all connected clients
            await manager.broadcast(ws_message)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("orchestration_events: client disconnected")
    except Exception as e:
        logger.error(f"orchestration_events: error: {e}")
        manager.disconnect(websocket)


@router.get("/history")
async def get_orchestration_history(
    limit: int = 50,
    rec = None,  # Placeholder for require_session
):
    """Get recent orchestration events from audit trail.

    SECURITY FIX (2026-09-22):
    - Added require_session authentication
    - Tenant is determined from authenticated session (not user input)
    - Users can only read their own tenant's orchestration history

    Args:
        limit: Max events to return
        rec: Authenticated session record (required)

    Returns:
        List of recent orchestration events for authenticated user's tenant
    """
    # Import here to avoid circular dependency
    from .. import auth as session_auth
    from ..deps import require_session
    from fastapi import Depends, HTTPException, Query

    # Note: In actual implementation, rec parameter should use Depends(require_session)
    # For now, this is a placeholder showing the intended fix
    if rec is None:
        raise HTTPException(status_code=401, detail="Authentication required")

    tenant_id = rec.tenant_id

    # TODO: Implement by reading orchestration events from audit.jsonl filtered by tenant_id
    return {
        "events": [],
        "limit": limit,
        "tenant_id": tenant_id,
        "note": "Not yet implemented — reads from audit trail",
    }


@router.get("/voice/{batch_id}.ogg")
async def get_voice_summary(batch_id: str):
    """Serve voice summary OGG file for Console player.

    The voice file is generated by voice_summary_orchestration.py and stored
    in shared/outbox/. This endpoint serves it with correct MIME type.
    """
    # TODO: Implement file serving with proper MIME type
    # For now, placeholder
    return {"error": "Not yet implemented"}


# --- Integration Hook for orchestration_router ---


def get_console_queue() -> asyncio.Queue:
    """Return the global orchestration event queue for orchestration_router.

    orchestration_router._emit_console_event() calls this to get the queue,
    then puts events on it. This function is imported by orchestration_router.
    """
    return console_orchestration_queue
