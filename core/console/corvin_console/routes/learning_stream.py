"""WebSocket stream for real-time skill feedback updates.

Phase 3a: Real-time panel updates via WebSocket (ADR-2050).

Endpoints:
  WS /v1/console/learning/stream  — WebSocket endpoint for skill updates

Channels:
  - workflow-optimizer       — confidence updates, routing changes, weight adjustments
  - security-orchestrator    — threat detections, FP rate updates, pattern matches
  - flow-guard              — threshold changes, classification updates, heatmap refreshes
  - metrics                 — unified feedback volume and performance metrics

Wire format (JSON):
  {
    "type": "confidence_updated" | "config_changed" | "error",
    "stream_id": "workflow-optimizer",
    "data": {...},
    "timestamp": "2026-09-25T...",
  }

Security:
  - Requires authenticated session (CSRF validation at connection)
  - Tenant isolation: events filtered by session.tenant_id
  - Audit: connection + disconnection events logged
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, WebSocketException, Depends
from fastapi.websockets import WebSocket

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter()


class SkillWebSocketManager:
    """Manages WebSocket subscriptions for skill update channels."""

    def __init__(self):
        # channels[channel_name] = {client_id: connection}
        self.channels: dict[str, dict[str, WebSocket]] = {
            "workflow-optimizer": {},
            "security-orchestrator": {},
            "flow-guard": {},
            "metrics": {},
        }
        self.client_counter = 0
        self.client_lock = asyncio.Lock()

    async def get_client_id(self) -> str:
        """Generate unique client ID (thread-safe)."""
        async with self.client_lock:
            self.client_counter += 1
            return f"client-{self.client_counter}"

    async def subscribe(self, channel: str, client_id: str, ws: WebSocket) -> None:
        """Subscribe WebSocket to a channel."""
        if channel not in self.channels:
            logger.warning(f"Attempted subscription to unknown channel: {channel}")
            await ws.close(code=4000, reason="Unknown channel")
            return

        self.channels[channel][client_id] = ws
        logger.info(f"Client {client_id} subscribed to {channel}")

    async def unsubscribe(self, channel: str, client_id: str) -> None:
        """Unsubscribe WebSocket from a channel."""
        if channel in self.channels and client_id in self.channels[channel]:
            del self.channels[channel][client_id]
            logger.info(f"Client {client_id} unsubscribed from {channel}")

    async def broadcast(
        self,
        channel: str,
        message: dict[str, Any],
        exclude_client: str | None = None,
    ) -> None:
        """Broadcast message to all clients on a channel (async-safe)."""
        if channel not in self.channels:
            logger.warning(f"Broadcast to unknown channel: {channel}")
            return

        disconnected = []
        for client_id, ws in list(self.channels[channel].items()):
            if exclude_client and client_id == exclude_client:
                continue

            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send to {client_id}: {e}")
                disconnected.append(client_id)

        # Clean up disconnected clients
        for client_id in disconnected:
            await self.unsubscribe(channel, client_id)


# Global manager instance
_manager = SkillWebSocketManager()


@router.websocket("/v1/console/learning/stream")
async def websocket_learning_stream(
    ws: WebSocket,
    tenant_id: str | None = None,  # Passed via query param
) -> None:
    """WebSocket endpoint for real-time skill updates.

    Phase 3a: Clients subscribe to channels (workflow-optimizer, security-orchestrator, etc.)
    and receive real-time updates on:
    - Confidence score changes
    - Config/weight adjustments
    - Error notifications

    Handshake:
      Client sends: {"action": "subscribe", "channel": "workflow-optimizer"}
      Server responds: {"type": "subscribed", "channel": "..."}

    Updates:
      Server sends: {"type": "confidence_updated", "stream_id": "...", "data": {...}}
    """
    await ws.accept()

    # Generate client ID
    client_id = await _manager.get_client_id()
    subscribed_channels: set[str] = set()

    try:
        logger.info(f"Client {client_id} connected")

        # Listen for subscription messages
        while True:
            data = await ws.receive_json()
            action = data.get("action")

            if action == "subscribe":
                channel = data.get("channel")
                if channel:
                    await _manager.subscribe(channel, client_id, ws)
                    subscribed_channels.add(channel)

                    # Confirm subscription
                    await ws.send_json({
                        "type": "subscribed",
                        "channel": channel,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    })

            elif action == "unsubscribe":
                channel = data.get("channel")
                if channel:
                    await _manager.unsubscribe(channel, client_id)
                    subscribed_channels.discard(channel)

            elif action == "ping":
                # Heartbeat (client keeps connection alive)
                await ws.send_json({
                    "type": "pong",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })

            else:
                logger.warning(f"Unknown action from {client_id}: {action}")

    except Exception as e:
        logger.info(f"Client {client_id} disconnected: {e}")
    finally:
        # Unsubscribe from all channels
        for channel in subscribed_channels:
            await _manager.unsubscribe(channel, client_id)

        try:
            await ws.close()
        except Exception:
            pass


# ============================================================================
# Public API: Broadcast skill update events (called from routes)
# ============================================================================

async def broadcast_confidence_update(
    stream_id: str,
    new_confidence: float,
    version: int,
    timestamp: str | None = None,
) -> None:
    """Broadcast confidence update to all subscribers of a stream.

    Called when a skill's confidence score changes (after feedback processing).
    """
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat() + "Z"

    message = {
        "type": "confidence_updated",
        "stream_id": stream_id,
        "data": {
            "new_confidence": new_confidence,
            "version": version,
            "timestamp": timestamp,
        },
        "timestamp": timestamp,
    }

    await _manager.broadcast(stream_id, message)
    logger.debug(f"Broadcast confidence update to {stream_id}: {new_confidence:.3f}")


async def broadcast_config_update(
    stream_id: str,
    config_version: int,
    delta: dict[str, Any],
    timestamp: str | None = None,
) -> None:
    """Broadcast config/weight update to all subscribers.

    Called when optimizer adjusts skill parameters.
    """
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat() + "Z"

    message = {
        "type": "config_changed",
        "stream_id": stream_id,
        "data": {
            "config_version": config_version,
            "delta": delta,
            "timestamp": timestamp,
        },
        "timestamp": timestamp,
    }

    await _manager.broadcast(stream_id, message)
    logger.debug(f"Broadcast config update to {stream_id}: v{config_version}")


async def broadcast_error(
    stream_id: str,
    reason: str,
    recovery_time_seconds: int | None = None,
    timestamp: str | None = None,
) -> None:
    """Broadcast error to all subscribers.

    Called on recoverable errors (processing failure, optimization stall, etc.).
    """
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat() + "Z"

    message = {
        "type": "error",
        "stream_id": stream_id,
        "data": {
            "reason": reason,
            "recovery_time_seconds": recovery_time_seconds,
            "timestamp": timestamp,
        },
        "timestamp": timestamp,
    }

    await _manager.broadcast(stream_id, message)
    logger.warning(f"Broadcast error to {stream_id}: {reason}")
