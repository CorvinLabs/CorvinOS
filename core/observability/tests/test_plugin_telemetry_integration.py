"""
Tests for plugin telemetry integration hooks (Phase 5).

Tests that hooks correctly emit telemetry events for plugin lifecycle.
"""

import pytest
from core.observability.plugin_telemetry_integration import (
    PluginTelemetryHooks,
    PluginTelemetryCollector,
    PluginTelemetryEventType,
    get_telemetry_hooks,
    set_telemetry_hooks,
)


class TestPluginTelemetryHooks:
    """Tests for telemetry hooks."""

    @pytest.fixture
    def collector(self):
        """Fresh collector for each test."""
        return PluginTelemetryCollector()

    @pytest.fixture
    def hooks(self, collector):
        """Create hooks with test collector."""
        return PluginTelemetryHooks(collector=collector)

    def test_on_plugin_registered(self, hooks, collector):
        """Hook emits event when plugin registers."""
        hooks.on_plugin_registered(
            plugin_id="whisper",
            tenant_id="_default",
            boot_layer="bundled",
            capabilities=["transcribe"],
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.HEALTH_CHECK
        assert event.plugin_id == "whisper"
        assert event.data["boot_layer"] == "bundled"

    def test_on_work_received(self, hooks, collector):
        """Hook emits event when plugin receives work."""
        hooks.on_work_received(
            plugin_id="whisper",
            tenant_id="_default",
            work_id="w123",
            required_capability="transcribe",
            priority_tier="high",
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.WORK_RECEIVED
        assert event.work_id == "w123"
        assert event.data["priority_tier"] == "high"

    def test_on_work_handled_locally(self, hooks, collector):
        """Hook emits event when plugin handles work locally."""
        hooks.on_work_handled_locally(
            plugin_id="whisper",
            tenant_id="_default",
            work_id="w123",
            latency_ms=75.5,
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.WORK_HANDLED_LOCALLY
        assert event.data["latency_ms"] == 75.5

    def test_on_work_delegated(self, hooks, collector):
        """Hook emits event when work is delegated."""
        hooks.on_work_delegated(
            plugin_id="stt",
            tenant_id="_default",
            work_id="w123",
            target_child="whisper",
            priority_tier="standard",
            budget_cost=20,
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.WORK_DELEGATED
        assert event.data["target_child"] == "whisper"
        assert event.data["budget_cost"] == 20

    def test_on_work_failed(self, hooks, collector):
        """Hook emits event when work fails."""
        hooks.on_work_failed(
            plugin_id="whisper",
            tenant_id="_default",
            work_id="w123",
            error="timeout",
            latency_ms=30000.0,
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.WORK_FAILED
        assert event.data["error"] == "timeout"

    def test_on_budget_exhausted(self, hooks, collector):
        """Hook emits event when budget is exhausted."""
        hooks.on_budget_exhausted(
            plugin_id="stt",
            tenant_id="_default",
            tier="standard",
            used=100,
            limit=100,
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.BUDGET_EXHAUSTED
        assert event.data["exhausted_percent"] == 100.0

    def test_on_audit_hash_mismatch(self, hooks, collector):
        """Hook emits event when audit hash mismatches."""
        hooks.on_audit_hash_mismatch(
            plugin_id="whisper",
            tenant_id="_default",
            parent_id="stt",
            expected_hash="abc123",
            actual_hash="def456",
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.AUDIT_HASH_MISMATCH
        assert event.parent_id == "stt"
        assert event.data["expected_hash"] == "abc123"

    def test_on_child_quarantined(self, hooks, collector):
        """Hook emits event when child is quarantined."""
        hooks.on_child_quarantined(
            plugin_id="stt",
            tenant_id="_default",
            child_id="whisper",
            reason="repeated_audit_failures",
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.CHILD_QUARANTINED
        assert event.parent_id == "stt"
        assert event.data["reason"] == "repeated_audit_failures"

    def test_on_fallback_triggered(self, hooks, collector):
        """Hook emits event when fallback is triggered."""
        hooks.on_fallback_triggered(
            plugin_id="stt",
            tenant_id="_default",
            work_id="w123",
            failed_child="whisper",
            fallback_child="deepspeech",
            reason="timeout",
        )
        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.event_type == PluginTelemetryEventType.FALLBACK_TRIGGERED
        assert event.data["failed_child"] == "whisper"
        assert event.data["fallback_child"] == "deepspeech"


class TestIntegrationWorkflow:
    """Test multi-step workflows."""

    def test_full_delegation_workflow(self):
        """Test complete delegation workflow with telemetry."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # 1. Register root plugin
        hooks.on_plugin_registered("stt", "_default", boot_layer="bundled")

        # 2. Register child plugin
        hooks.on_plugin_registered(
            "whisper",
            "_default",
            parent_id="stt",
            boot_layer="bundled",
        )

        # 3. Work arrives at root
        hooks.on_work_received(
            "stt",
            "_default",
            "w123",
            "transcribe",
        )

        # 4. Root delegates to child
        hooks.on_work_delegated(
            "stt",
            "_default",
            "w123",
            "whisper",
        )

        # 5. Child handles locally
        hooks.on_work_handled_locally(
            "whisper",
            "_default",
            "w123",
            latency_ms=100.0,
        )

        # Verify event sequence
        assert len(collector.events) == 5
        assert collector.events[0].event_type == PluginTelemetryEventType.HEALTH_CHECK
        assert collector.events[1].event_type == PluginTelemetryEventType.HEALTH_CHECK
        assert collector.events[2].event_type == PluginTelemetryEventType.WORK_RECEIVED
        assert collector.events[3].event_type == PluginTelemetryEventType.WORK_DELEGATED
        assert collector.events[4].event_type == PluginTelemetryEventType.WORK_HANDLED_LOCALLY

    def test_fallback_workflow(self):
        """Test fallback triggering workflow."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # 1. Work arrives and fails
        hooks.on_work_received("stt", "_default", "w123", "transcribe")
        hooks.on_work_delegated("stt", "_default", "w123", "whisper")
        hooks.on_work_failed("whisper", "_default", "w123", "timeout")

        # 2. Fallback triggered
        hooks.on_fallback_triggered(
            "stt",
            "_default",
            "w123",
            "whisper",
            "deepspeech",
            "timeout",
        )

        # 3. Retry with fallback succeeds
        hooks.on_work_delegated("stt", "_default", "w123", "deepspeech")
        hooks.on_work_handled_locally("deepspeech", "_default", "w123", 80.0)

        # Verify sequence
        assert len(collector.events) == 7
        failed_event = [
            e for e in collector.events
            if e.event_type == PluginTelemetryEventType.WORK_FAILED
        ][0]
        assert failed_event.data["error"] == "timeout"

    def test_audit_failure_cascade(self):
        """Test audit failure leading to quarantine."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # 1. Work fails with audit mismatch
        for i in range(3):
            hooks.on_work_received(f"stt", "_default", f"w{i}", "transcribe")
            hooks.on_work_delegated("stt", "_default", f"w{i}", "whisper")
            hooks.on_audit_hash_mismatch("whisper", "_default", "stt")

        # 2. After 3 failures, quarantine
        hooks.on_child_quarantined("stt", "_default", "whisper", "repeated_audit_failures")

        # Verify audit failures and quarantine
        audit_events = [
            e for e in collector.events
            if e.event_type == PluginTelemetryEventType.AUDIT_HASH_MISMATCH
        ]
        assert len(audit_events) == 3

        quarantine_events = [
            e for e in collector.events
            if e.event_type == PluginTelemetryEventType.CHILD_QUARANTINED
        ]
        assert len(quarantine_events) == 1


class TestGlobalHooks:
    """Tests for global hooks instance."""

    def test_get_telemetry_hooks_returns_singleton(self):
        """Global hooks should be singleton."""
        hooks1 = get_telemetry_hooks()
        hooks2 = get_telemetry_hooks()
        assert hooks1 is hooks2

    def test_set_telemetry_hooks(self):
        """Can set global hooks for testing."""
        new_hooks = PluginTelemetryHooks()
        set_telemetry_hooks(new_hooks)

        retrieved = get_telemetry_hooks()
        assert retrieved is new_hooks
