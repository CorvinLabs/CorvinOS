"""
WebSocket server for real-time dashboard streaming.

Streams:
- Health status updates (<100ms latency)
- Decision events (task engine choice, cost, confidence)
- Cost tracking (quota, burn rate)

Multiple clients supported with broadcast.
"""

from typing import Set, Dict, Any, Optional
from datetime import datetime
import asyncio
import json


class WebSocketBroadcaster:
    """
    Broadcast events to multiple WebSocket clients.

    Manages connection lifecycle and message distribution.
    """

    def __init__(self):
        """Initialize broadcaster."""
        self.clients: Set[Any] = set()  # WebSocket connections
        self.message_buffer: asyncio.Queue = asyncio.Queue(maxsize=1000)

    async def register_client(self, websocket: Any) -> None:
        """Register a WebSocket client."""
        self.clients.add(websocket)

    async def unregister_client(self, websocket: Any) -> None:
        """Unregister a WebSocket client."""
        self.clients.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """
        Broadcast message to all connected clients.

        Args:
            message: Event data to broadcast
        """
        # Add timestamp if not present
        if "timestamp" not in message:
            message["timestamp"] = datetime.utcnow().isoformat()

        # Buffer for latecomers
        try:
            self.message_buffer.put_nowait(message)
        except asyncio.QueueFull:
            # Drop oldest message if buffer full
            try:
                self.message_buffer.get_nowait()
                self.message_buffer.put_nowait(message)
            except:
                pass

        # Send to all connected clients
        dead_clients = set()
        for client in self.clients:
            try:
                await client.send(json.dumps(message))
            except Exception:
                # Client disconnected
                dead_clients.add(client)

        # Clean up dead clients
        for client in dead_clients:
            self.clients.discard(client)

    async def get_replay_buffer(self, limit: int = 100) -> list:
        """
        Get buffered messages for new clients.

        Args:
            limit: Max messages to return

        Returns:
            List of recent messages
        """
        messages = []
        temp_queue: asyncio.Queue = asyncio.Queue()

        # Copy buffer contents
        while not self.message_buffer.empty():
            try:
                msg = self.message_buffer.get_nowait()
                messages.append(msg)
                temp_queue.put_nowait(msg)
            except asyncio.QueueEmpty:
                break

        # Restore buffer
        while not temp_queue.empty():
            msg = temp_queue.get_nowait()
            try:
                self.message_buffer.put_nowait(msg)
            except asyncio.QueueFull:
                break

        return messages[-limit:]

    def client_count(self) -> int:
        """Number of connected clients."""
        return len(self.clients)


class DashboardEventStream:
    """
    Stream dashboard events from CorvinOS to WebSocket clients.

    Events:
    - health_status: Subsystem health
    - decision_event: Task routing decision
    - cost_update: Cost tracking (quota, burn)
    - alert: Operator alert (high burn rate, etc.)
    """

    def __init__(self, broadcaster: WebSocketBroadcaster):
        """Initialize event stream."""
        self.broadcaster = broadcaster

    async def emit_health_status(
        self,
        subsystem_id: str,
        status: str,
        metrics: Dict[str, float],
    ) -> None:
        """Emit health status event."""
        await self.broadcaster.broadcast({
            "type": "health_status",
            "subsystem_id": subsystem_id,
            "status": status,
            "metrics": metrics,
        })

    async def emit_decision(
        self,
        task_id: str,
        engine_choice: str,
        confidence: float,
        cost_estimate_usd: float,
        latency_estimate_ms: float,
    ) -> None:
        """Emit task decision event."""
        await self.broadcaster.broadcast({
            "type": "decision",
            "task_id": task_id,
            "engine_choice": engine_choice,
            "confidence": confidence,
            "cost_estimate_usd": cost_estimate_usd,
            "latency_estimate_ms": latency_estimate_ms,
        })

    async def emit_cost_update(
        self,
        daily_quota_usd: float,
        current_spend_usd: float,
        burn_rate_per_hour: float,
        projected_end_of_day: float,
    ) -> None:
        """Emit cost tracking update."""
        await self.broadcaster.broadcast({
            "type": "cost_update",
            "daily_quota_usd": daily_quota_usd,
            "current_spend_usd": current_spend_usd,
            "burn_rate_per_hour": burn_rate_per_hour,
            "projected_end_of_day": projected_end_of_day,
        })

    async def emit_alert(
        self,
        alert_type: str,  # "quota_warning", "high_latency", etc.
        message: str,
        severity: str = "warning",  # "info", "warning", "error"
    ) -> None:
        """Emit operator alert."""
        await self.broadcaster.broadcast({
            "type": "alert",
            "alert_type": alert_type,
            "message": message,
            "severity": severity,
        })
