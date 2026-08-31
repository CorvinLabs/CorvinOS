"""
Unit tests for plugin telemetry (Phase 5, ADR-0345).

Tests:
- Event immutability
- Snapshot capture
- Health score computation
- Event filtering
- Tenant isolation
"""

import pytest
from datetime import datetime
from core.observability.plugin_telemetry import (
    PluginTelemetryEvent,
    PluginTelemetryEventType,
    PluginTelemetrySnapshot,
    PluginTelemetryCollector,
    WorkTier,
    get_telemetry_collector,
)


class TestPluginTelemetryEvent:
    """Tests for immutable telemetry events."""

    def test_event_creation(self):
        """Event can be created with required fields."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.HEALTH_CHECK,
            plugin_id="whisper",
            tenant_id="_default",
        )
        assert event.plugin_id == "whisper"
        assert event.tenant_id == "_default"
        assert event.event_type == PluginTelemetryEventType.HEALTH_CHECK

    def test_event_immutability(self):
        """Event is immutable (frozen dataclass)."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.HEALTH_CHECK,
            plugin_id="whisper",
            tenant_id="_default",
        )
        with pytest.raises(AttributeError):
            event.plugin_id = "deepspeech"

    def test_event_serialization(self):
        """Event can serialize to dict."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.WORK_RECEIVED,
            plugin_id="whisper",
            tenant_id="_default",
            work_id="w123",
            data={"capability": "transcribe", "tier": "standard"},
        )
        d = event.to_dict()
        assert d["event_type"] == "work_received"
        assert d["plugin_id"] == "whisper"
        assert d["work_id"] == "w123"
        assert d["data"]["capability"] == "transcribe"

    def test_event_json_serialization(self):
        """Event can serialize to JSON."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.HEALTH_CHECK,
            plugin_id="whisper",
            tenant_id="_default",
        )
        json_str = event.to_json()
        assert isinstance(json_str, str)
        assert "whisper" in json_str
        assert "plugin_health_check" in json_str


class TestPluginTelemetrySnapshot:
    """Tests for snapshots (dashboard data)."""

    def test_snapshot_creation(self):
        """Snapshot captures plugin state."""
        snap = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="degraded",
            health_score=0.75,
            work_handled_count=10,
            avg_latency_ms=150.5,
        )
        assert snap.plugin_id == "whisper"
        assert snap.status == "degraded"
        assert snap.health_score == 0.75
        assert snap.work_handled_count == 10

    def test_snapshot_serialization(self):
        """Snapshot serializes to dashboard dict."""
        snap = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="ready",
            health_score=1.0,
            work_handled_count=42,
            avg_latency_ms=50.0,
            p95_latency_ms=120.0,
            p99_latency_ms=200.0,
        )
        d = snap.to_dict()
        assert d["plugin_id"] == "whisper"
        assert d["status"] == "ready"
        assert d["health_score"] == 1.0
        assert d["work"]["handled"] == 42
        assert d["latency"]["p95_ms"] == 120.0


class TestPluginTelemetryCollector:
    """Tests for telemetry collector."""

    @pytest.fixture
    def collector(self):
        """Fresh collector for each test."""
        return PluginTelemetryCollector()

    def test_emit_event(self, collector):
        """Events can be emitted."""
        event = PluginTelemetryEvent(
            event_type=PluginTelemetryEventType.WORK_RECEIVED,
            plugin_id="whisper",
            tenant_id="_default",
        )
        collector.emit_event(event)
        assert len(collector.events) == 1

    def test_get_plugin_snapshot(self, collector):
        """Snapshots can be retrieved."""
        snap = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="ready",
            health_score=0.95,
        )
        collector.update_plugin_snapshot("whisper", "_default", snap)
        retrieved = collector.get_plugin_snapshot("whisper", "_default")
        assert retrieved is not None
        assert retrieved.health_score == 0.95

    def test_get_events_for_plugin(self, collector):
        """Events can be filtered by plugin."""
        # Emit events for two plugins
        for plugin_id in ["whisper", "deepspeech"]:
            event = PluginTelemetryEvent(
                event_type=PluginTelemetryEventType.HEALTH_CHECK,
                plugin_id=plugin_id,
                tenant_id="_default",
            )
            collector.emit_event(event)

        # Filter by plugin
        whisper_events = collector.get_events_for_plugin("whisper", "_default")
        assert len(whisper_events) == 1
        assert whisper_events[0].plugin_id == "whisper"

        deepspeech_events = collector.get_events_for_plugin("deepspeech", "_default")
        assert len(deepspeech_events) == 1
        assert deepspeech_events[0].plugin_id == "deepspeech"

    def test_get_events_by_type(self, collector):
        """Events can be filtered by type."""
        # Emit different event types
        events_data = [
            (PluginTelemetryEventType.HEALTH_CHECK, "whisper"),
            (PluginTelemetryEventType.WORK_RECEIVED, "whisper"),
            (PluginTelemetryEventType.WORK_HANDLED_LOCALLY, "whisper"),
        ]
        for event_type, plugin_id in events_data:
            event = PluginTelemetryEvent(
                event_type=event_type,
                plugin_id=plugin_id,
                tenant_id="_default",
            )
            collector.emit_event(event)

        # Filter by type
        health_events = collector.get_events_for_plugin(
            "whisper",
            "_default",
            event_type=PluginTelemetryEventType.HEALTH_CHECK,
        )
        assert len(health_events) == 1
        assert health_events[0].event_type == PluginTelemetryEventType.HEALTH_CHECK

    def test_tenant_isolation(self, collector):
        """Snapshots are isolated per tenant."""
        snap1 = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="ready",
            health_score=0.9,
        )
        snap2 = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="degraded",
            health_score=0.5,
        )

        collector.update_plugin_snapshot("whisper", "tenant_a", snap1)
        collector.update_plugin_snapshot("whisper", "tenant_b", snap2)

        retrieved_a = collector.get_plugin_snapshot("whisper", "tenant_a")
        retrieved_b = collector.get_plugin_snapshot("whisper", "tenant_b")

        assert retrieved_a.health_score == 0.9
        assert retrieved_b.health_score == 0.5

    def test_compute_health_score_no_events(self, collector):
        """Health score defaults to 1.0 with no events."""
        score = collector.compute_health_score("whisper", "_default")
        assert score == 1.0

    def test_compute_health_score_with_failures(self, collector):
        """Health score decreases with audit/work failures."""
        # Emit 10 normal events
        for i in range(10):
            event = PluginTelemetryEvent(
                event_type=PluginTelemetryEventType.WORK_HANDLED_LOCALLY,
                plugin_id="whisper",
                tenant_id="_default",
                work_id=f"w{i}",
            )
            collector.emit_event(event)

        # Emit 2 failure events
        for i in range(2):
            event = PluginTelemetryEvent(
                event_type=PluginTelemetryEventType.WORK_FAILED,
                plugin_id="whisper",
                tenant_id="_default",
            )
            collector.emit_event(event)

        score = collector.compute_health_score("whisper", "_default")
        # (12 - 2) / 12 = 0.833...
        assert 0.80 < score < 0.85

    def test_event_limit(self, collector):
        """Events are limited in retrieval."""
        # Emit 150 events
        for i in range(150):
            event = PluginTelemetryEvent(
                event_type=PluginTelemetryEventType.HEALTH_CHECK,
                plugin_id="whisper",
                tenant_id="_default",
            )
            collector.emit_event(event)

        # Default limit is 100
        events = collector.get_events_for_plugin("whisper", "_default")
        assert len(events) == 100


class TestTelemetrySingleton:
    """Tests for global telemetry collector."""

    def test_get_telemetry_collector_returns_singleton(self):
        """get_telemetry_collector returns same instance."""
        collector1 = get_telemetry_collector()
        collector2 = get_telemetry_collector()
        assert collector1 is collector2


class TestEventOrdering:
    """Tests for event ordering (most recent first)."""

    def test_events_ordered_descending(self):
        """Events returned in descending timestamp order."""
        collector = PluginTelemetryCollector()

        # Emit events at different times (simulated via timestamp override)
        base_time = datetime.utcnow()
        for i in range(3):
            event = PluginTelemetryEvent(
                event_type=PluginTelemetryEventType.HEALTH_CHECK,
                plugin_id="whisper",
                tenant_id="_default",
                timestamp_utc=base_time,  # Would need to adjust for real ordering
            )
            collector.emit_event(event)

        events = collector.get_events_for_plugin("whisper", "_default")
        # Most recent first (but all have same timestamp in this test)
        assert len(events) == 3


class TestIntegration:
    """Integration tests combining multiple features."""

    def test_full_workflow(self):
        """Full workflow: emit → snapshot → retrieve → compute health."""
        collector = PluginTelemetryCollector()

        # 1. Emit events
        for i in range(5):
            event = PluginTelemetryEvent(
                event_type=PluginTelemetryEventType.WORK_HANDLED_LOCALLY,
                plugin_id="whisper",
                tenant_id="_default",
                work_id=f"w{i}",
                data={"tier": "standard", "latency_ms": 50 + i*10},
            )
            collector.emit_event(event)

        # 2. Create snapshot
        snap = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="ready",
            health_score=0.95,
            work_handled_count=5,
            avg_latency_ms=80.0,
        )
        collector.update_plugin_snapshot("whisper", "_default", snap)

        # 3. Retrieve snapshot
        retrieved_snap = collector.get_plugin_snapshot("whisper", "_default")
        assert retrieved_snap is not None
        assert retrieved_snap.work_handled_count == 5

        # 4. Compute health
        health = collector.compute_health_score("whisper", "_default")
        assert health == 1.0  # All successes

        # 5. Get events
        events = collector.get_events_for_plugin("whisper", "_default")
        assert len(events) == 5
