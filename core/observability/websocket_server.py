"""
WebSocket server for real-time dashboard streaming.

Streams:
- Health status updates (<100ms latency)
- Decision events (task engine choice, cost, confidence)
- Cost tracking (quota, burn rate)

Multiple clients supported with broadcast, connection pooling, and backpressure handling.

SLOs:
- Latency: <100ms per broadcast to 10+ clients
- Throughput: 1000 messages/sec
- Client scalability: 100+ concurrent connections
- Message ordering: FIFO guaranteed
"""

from typing import Set, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass, field
import asyncio
import json
import time
from collections import deque


@dataclass
class ConnectionMetrics:
    """Metrics for a single WebSocket connection."""
    client_id: str
    connected_at: float
    messages_sent: int = 0
    messages_failed: int = 0
    last_send_time: float = field(default_factory=time.time)
    latency_samples: deque = field(default_factory=lambda: deque(maxlen=100))

    def record_send(self, success: bool, latency_ms: float = 0.0):
        """Record a send attempt."""
        if success:
            self.messages_sent += 1
            self.latency_samples.append(latency_ms)
        else:
            self.messages_failed += 1
        self.last_send_time = time.time()

    def average_latency_ms(self) -> float:
        """Get average latency for this client."""
        if not self.latency_samples:
            return 0.0
        return sum(self.latency_samples) / len(self.latency_samples)


class WebSocketBroadcaster:
    """
    Broadcast events to multiple WebSocket clients with connection pooling.

    Features:
    - Connection lifecycle management
    - Message buffering with overflow handling
    - Per-client metrics and backpressure monitoring
    - Automatic cleanup of dead connections
    - Replay buffer for late joiners
    """

    def __init__(self, buffer_size: int = 1000):
        """Initialize broadcaster with configurable buffer size."""
        self.clients: Set[Any] = set()  # WebSocket connections
        self.message_buffer: asyncio.Queue = asyncio.Queue(maxsize=buffer_size)
        self.metrics: Dict[str, ConnectionMetrics] = {}  # Per-client metrics
        self.buffer_size = buffer_size
        self.total_broadcasts = 0
        self.total_failed_sends = 0

    async def register_client(self, websocket: Any, client_id: str | None = None) -> None:
        """Register a WebSocket client with optional metrics tracking."""
        self.clients.add(websocket)

        # Track metrics if client_id provided
        if client_id:
            self.metrics[client_id] = ConnectionMetrics(
                client_id=client_id,
                connected_at=time.time(),
            )

    async def unregister_client(self, websocket: Any) -> None:
        """Unregister a WebSocket client."""
        self.clients.discard(websocket)

    async def broadcast(
        self, message: Dict[str, Any], timeout_sec: float = 5.0
    ) -> Dict[str, Any]:
        """
        Broadcast message to all connected clients with backpressure handling.

        Args:
            message: Event data to broadcast
            timeout_sec: Timeout for individual sends (backpressure limit)

        Returns:
            Dict with broadcast statistics (clients_reached, clients_failed, latency_ms)
        """
        start_time = time.time()

        # Add timestamp if not present
        if "timestamp" not in message:
            message["timestamp"] = datetime.utcnow().isoformat()

        # Buffer for latecomers with overflow handling
        try:
            self.message_buffer.put_nowait(message)
        except asyncio.QueueFull:
            # Drop oldest message if buffer full (FIFO fairness)
            try:
                self.message_buffer.get_nowait()
                self.message_buffer.put_nowait(message)
            except:
                pass

        # Send to all connected clients with timeout and backpressure
        # Take a snapshot to avoid "Set changed size during iteration" error
        dead_clients = set()
        json_data = json.dumps(message)
        clients_reached = 0
        clients_failed = 0
        clients_snapshot = list(self.clients)

        for client in clients_snapshot:
            try:
                # Timeout prevents backpressure from slow clients
                await asyncio.wait_for(
                    client.send(json_data),
                    timeout=timeout_sec,
                )
                clients_reached += 1
            except asyncio.TimeoutError:
                # Client not responding (slow or stuck) — disconnect it
                dead_clients.add(client)
                clients_failed += 1
                self.total_failed_sends += 1
            except Exception:
                # Other errors (already disconnected, etc.)
                dead_clients.add(client)
                clients_failed += 1
                self.total_failed_sends += 1

        # Clean up dead clients
        for client in dead_clients:
            self.clients.discard(client)

        # Update metrics
        self.total_broadcasts += 1
        elapsed_ms = (time.time() - start_time) * 1000

        return {
            "clients_reached": clients_reached,
            "clients_failed": clients_failed,
            "total_clients": len(self.clients) + len(dead_clients),
            "latency_ms": elapsed_ms,
            "buffer_depth": self.message_buffer.qsize(),
        }

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

    def get_connection_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics."""
        if not self.metrics:
            return {
                "total_clients": len(self.clients),
                "active_connections": len(self.clients),
                "total_broadcasts": self.total_broadcasts,
                "total_failed_sends": self.total_failed_sends,
                "buffer_depth": self.message_buffer.qsize(),
                "buffer_size": self.buffer_size,
            }

        avg_latency = (
            sum(m.average_latency_ms() for m in self.metrics.values())
            / len(self.metrics)
            if self.metrics
            else 0.0
        )

        return {
            "total_clients": len(self.clients),
            "active_connections": len(self.clients),
            "tracked_clients": len(self.metrics),
            "total_broadcasts": self.total_broadcasts,
            "total_failed_sends": self.total_failed_sends,
            "avg_latency_ms": avg_latency,
            "buffer_depth": self.message_buffer.qsize(),
            "buffer_size": self.buffer_size,
            "buffer_utilization_pct": (
                self.message_buffer.qsize() / self.buffer_size * 100
            ),
        }

    async def cleanup_inactive_clients(self, inactivity_sec: float = 300.0) -> int:
        """Remove clients inactive for more than inactivity_sec."""
        now = time.time()
        inactive_ids = [
            client_id
            for client_id, metrics in self.metrics.items()
            if now - metrics.last_send_time > inactivity_sec
        ]

        count = 0
        for client_id in inactive_ids:
            del self.metrics[client_id]
            count += 1

        return count


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
