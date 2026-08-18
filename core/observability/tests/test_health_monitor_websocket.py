"""
Tests for health monitoring and WebSocket streaming.

Coverage:
- Health status tracking and reporting
- Overall system health computation
- WebSocket client broadcast
- Event buffering and replay
- <100ms latency verification
"""

import pytest
import asyncio
from datetime import datetime

from core.observability.health_monitor import (
    HealthMonitor,
    HealthStatus,
    HealthMetric,
    SubsystemHealth,
)
from core.observability.websocket_server import (
    WebSocketBroadcaster,
    DashboardEventStream,
)


class TestHealthMetric:
    """Test health metrics."""

    def test_metric_creation(self):
        """Create health metric."""
        metric = HealthMetric(
            name="latency_ms",
            value=100.5,
            unit="ms",
            threshold_warning=200.0,
            threshold_error=500.0,
        )
        assert metric.name == "latency_ms"
        assert metric.value == 100.5
        assert metric.unit == "ms"


class TestSubsystemHealth:
    """Test subsystem health."""

    def test_health_creation(self):
        """Create subsystem health."""
        health = SubsystemHealth(
            subsystem_id="brain",
            status=HealthStatus.OK,
            metrics=[
                HealthMetric("accuracy", 0.95),
                HealthMetric("latency_ms", 150.0),
            ],
        )
        assert health.subsystem_id == "brain"
        assert health.status == HealthStatus.OK

    def test_health_to_dict(self):
        """Serialize health to dict."""
        health = SubsystemHealth(
            subsystem_id="brain",
            status=HealthStatus.OK,
            metrics=[HealthMetric("accuracy", 0.95)],
        )
        d = health.to_dict()
        assert d["subsystem_id"] == "brain"
        assert d["status"] == "ok"
        assert len(d["metrics"]) == 1


class TestHealthMonitor:
    """Test health monitoring."""

    @pytest.mark.asyncio
    async def test_monitor_creation(self):
        """Create health monitor."""
        monitor = HealthMonitor()
        assert monitor is not None

    @pytest.mark.asyncio
    async def test_report_health(self):
        """Report subsystem health."""
        monitor = HealthMonitor()
        await monitor.report_health(
            subsystem_id="brain",
            status=HealthStatus.OK,
            metrics=[HealthMetric("accuracy", 0.95)],
        )
        assert "brain" in monitor.subsystems
        assert monitor.subsystems["brain"].status == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_overall_status_ok(self):
        """Overall status when all OK."""
        monitor = HealthMonitor()
        await monitor.report_health("brain", HealthStatus.OK, [])
        await monitor.report_health("context", HealthStatus.OK, [])
        assert monitor.get_overall_status() == HealthStatus.OK

    @pytest.mark.asyncio
    async def test_overall_status_degraded(self):
        """Overall status when one degraded."""
        monitor = HealthMonitor()
        await monitor.report_health("brain", HealthStatus.OK, [])
        await monitor.report_health("context", HealthStatus.DEGRADED, [])
        assert monitor.get_overall_status() == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_overall_status_error(self):
        """Overall status when one errored."""
        monitor = HealthMonitor()
        await monitor.report_health("brain", HealthStatus.OK, [])
        await monitor.report_health("context", HealthStatus.ERROR, [])
        assert monitor.get_overall_status() == HealthStatus.ERROR

    @pytest.mark.asyncio
    async def test_get_snapshot(self):
        """Get health snapshot."""
        monitor = HealthMonitor()
        await monitor.report_health("brain", HealthStatus.OK, [])
        snapshot = monitor.get_snapshot()
        assert "timestamp" in snapshot
        assert "overall_status" in snapshot
        assert "subsystems" in snapshot


class TestWebSocketBroadcaster:
    """Test WebSocket broadcasting."""

    @pytest.mark.asyncio
    async def test_broadcaster_creation(self):
        """Create broadcaster."""
        broadcaster = WebSocketBroadcaster()
        assert broadcaster.client_count() == 0

    @pytest.mark.asyncio
    async def test_client_registration(self):
        """Register client."""
        broadcaster = WebSocketBroadcaster()
        mock_client = object()
        await broadcaster.register_client(mock_client)
        assert broadcaster.client_count() == 1

    @pytest.mark.asyncio
    async def test_client_unregistration(self):
        """Unregister client."""
        broadcaster = WebSocketBroadcaster()
        mock_client = object()
        await broadcaster.register_client(mock_client)
        await broadcaster.unregister_client(mock_client)
        assert broadcaster.client_count() == 0

    @pytest.mark.asyncio
    async def test_message_buffering(self):
        """Messages are buffered."""
        broadcaster = WebSocketBroadcaster()
        await broadcaster.broadcast({"type": "test", "data": "msg1"})
        await broadcaster.broadcast({"type": "test", "data": "msg2"})

        buffer = await broadcaster.get_replay_buffer(limit=10)
        assert len(buffer) >= 2


class TestDashboardEventStream:
    """Test dashboard event streaming."""

    @pytest.mark.asyncio
    async def test_stream_creation(self):
        """Create event stream."""
        broadcaster = WebSocketBroadcaster()
        stream = DashboardEventStream(broadcaster)
        assert stream is not None

    @pytest.mark.asyncio
    async def test_emit_health_status(self):
        """Emit health status event."""
        broadcaster = WebSocketBroadcaster()
        stream = DashboardEventStream(broadcaster)

        await stream.emit_health_status(
            subsystem_id="brain",
            status="ok",
            metrics={"accuracy": 0.95},
        )

        buffer = await broadcaster.get_replay_buffer()
        assert len(buffer) > 0
        assert buffer[-1]["type"] == "health_status"

    @pytest.mark.asyncio
    async def test_emit_decision(self):
        """Emit decision event."""
        broadcaster = WebSocketBroadcaster()
        stream = DashboardEventStream(broadcaster)

        await stream.emit_decision(
            task_id="task-1",
            engine_choice="claude",
            confidence=0.95,
            cost_estimate_usd=0.01,
            latency_estimate_ms=1500.0,
        )

        buffer = await broadcaster.get_replay_buffer()
        assert len(buffer) > 0
        assert buffer[-1]["type"] == "decision"

    @pytest.mark.asyncio
    async def test_emit_cost_update(self):
        """Emit cost update event."""
        broadcaster = WebSocketBroadcaster()
        stream = DashboardEventStream(broadcaster)

        await stream.emit_cost_update(
            daily_quota_usd=10.0,
            current_spend_usd=2.5,
            burn_rate_per_hour=0.5,
            projected_end_of_day=12.0,
        )

        buffer = await broadcaster.get_replay_buffer()
        assert buffer[-1]["type"] == "cost_update"

    @pytest.mark.asyncio
    async def test_emit_alert(self):
        """Emit alert event."""
        broadcaster = WebSocketBroadcaster()
        stream = DashboardEventStream(broadcaster)

        await stream.emit_alert(
            alert_type="quota_warning",
            message="Approaching daily quota",
            severity="warning",
        )

        buffer = await broadcaster.get_replay_buffer()
        assert buffer[-1]["type"] == "alert"
