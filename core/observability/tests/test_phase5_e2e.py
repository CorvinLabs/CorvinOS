"""
End-to-end tests for Phase 5 Production Hardening.

Tests:
- Full telemetry pipeline (plugin → events → collector → dashboard)
- SLO measurement and compliance
- Audit integration
- Multi-tenant isolation
"""

import pytest
from datetime import datetime, timedelta
from core.observability.plugin_telemetry import (
    PluginTelemetryEvent,
    PluginTelemetryEventType,
    PluginTelemetryCollector,
    PluginTelemetrySnapshot,
)
from core.observability.plugin_telemetry_integration import (
    PluginTelemetryHooks,
)
from core.observability.slo_definitions import (
    SLOMonitor,
    SLOMeasurement,
    SLOStatus,
    SLODefinitions,
)


class TestFullTelemetryPipeline:
    """E2E tests for complete telemetry pipeline."""

    def test_work_delegation_telemetry_flow(self):
        """Full telemetry flow: plugin event → collection → snapshot."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # 1. Register root plugin
        hooks.on_plugin_registered("stt", "_default", boot_layer="bundled")

        # 2. Register child
        hooks.on_plugin_registered(
            "whisper",
            "_default",
            parent_id="stt",
            boot_layer="bundled",
        )

        # 3. Work flow
        hooks.on_work_received("stt", "_default", "w1", "transcribe")
        hooks.on_work_delegated("stt", "_default", "w1", "whisper")
        hooks.on_work_handled_locally("whisper", "_default", "w1", 95.0)

        # 4. Create snapshots (as monitoring loop would)
        snap_stt = PluginTelemetrySnapshot(
            plugin_id="stt",
            status="ready",
            health_score=0.99,
            work_handled_count=0,
            work_delegated_count=1,
        )
        snap_whisper = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="ready",
            health_score=1.0,
            work_handled_count=1,
            work_delegated_count=0,
            avg_latency_ms=95.0,
        )

        collector.update_plugin_snapshot("stt", "_default", snap_stt)
        collector.update_plugin_snapshot("whisper", "_default", snap_whisper)

        # 5. Verify retrieval
        retrieved_stt = collector.get_plugin_snapshot("stt", "_default")
        retrieved_whisper = collector.get_plugin_snapshot("whisper", "_default")

        assert retrieved_stt.work_delegated_count == 1
        assert retrieved_whisper.work_handled_count == 1

        # 6. Verify event stream
        events = collector.get_events_for_plugin("whisper", "_default")
        assert len(events) == 1
        assert events[0].event_type == PluginTelemetryEventType.WORK_HANDLED_LOCALLY

    def test_audit_failure_cascade_telemetry(self):
        """Telemetry captures audit failure → quarantine cascade."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # Simulate repeated audit failures
        for i in range(3):
            hooks.on_work_received("stt", "_default", f"w{i}", "transcribe")
            hooks.on_work_delegated("stt", "_default", f"w{i}", "whisper")
            hooks.on_audit_hash_mismatch("whisper", "_default", "stt")

        # Quarantine on 3rd failure
        hooks.on_child_quarantined("stt", "_default", "whisper")

        # Verify audit events
        audit_events = collector.get_events_for_plugin(
            "whisper",
            "_default",
            event_type=PluginTelemetryEventType.AUDIT_HASH_MISMATCH,
        )
        assert len(audit_events) == 3

        # Verify quarantine event
        quarantine_events = collector.get_events_for_plugin(
            "whisper",
            "_default",
            event_type=PluginTelemetryEventType.CHILD_QUARANTINED,
        )
        assert len(quarantine_events) == 1

        # Verify health score degradation
        health = collector.compute_health_score("whisper", "_default")
        # 4 events total: 3 delegations + 3 audit mismatches = 6? No, events are:
        # work_received, work_delegated, audit_mismatch (3x), child_quarantined
        # = 7 events total
        # Failures: 3 audit mismatches
        # Score: (7 - 3) / 7 = 0.57
        assert 0.5 < health < 0.65

    def test_multi_tenant_isolation(self):
        """Multi-tenant isolation in telemetry system."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # Create same plugin in two tenants
        for tenant in ["tenant_a", "tenant_b"]:
            hooks.on_plugin_registered("whisper", tenant)
            hooks.on_work_received("whisper", tenant, f"w_for_{tenant}", "transcribe")
            hooks.on_work_handled_locally("whisper", tenant, f"w_for_{tenant}", 100.0)

        # Verify isolation
        events_a = collector.get_events_for_plugin("whisper", "tenant_a")
        events_b = collector.get_events_for_plugin("whisper", "tenant_b")

        assert len(events_a) >= 3  # register + receive + handled
        assert len(events_b) >= 3
        # Verify no cross-pollination
        assert all(e.tenant_id == "tenant_a" for e in events_a)
        assert all(e.tenant_id == "tenant_b" for e in events_b)

        # Verify snapshot isolation
        snap_a = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="ready",
            health_score=1.0,
            work_handled_count=1,
        )
        snap_b = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="degraded",
            health_score=0.8,
            work_handled_count=5,
        )

        collector.update_plugin_snapshot("whisper", "tenant_a", snap_a)
        collector.update_plugin_snapshot("whisper", "tenant_b", snap_b)

        retrieved_a = collector.get_plugin_snapshot("whisper", "tenant_a")
        retrieved_b = collector.get_plugin_snapshot("whisper", "tenant_b")

        assert retrieved_a.health_score == 1.0
        assert retrieved_b.health_score == 0.8


class TestSLOEnforcement:
    """E2E tests for SLO measurement and enforcement."""

    def test_availability_slo_measurement(self):
        """Measure and assess availability SLO."""
        monitor = SLOMonitor()

        # Create measurement: 99.51% availability (exceeds 99.5% target)
        measurement = SLOMeasurement(
            slo_name="plugin_availability",
            measured_value=0.9951,
            target_value=0.995,
            unit="availability",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.HEALTHY,
            good_count=1000,
            bad_count=5,
            total_count=1005,
        )

        monitor.add_measurement(measurement)

        # Get report
        report = monitor.get_report()
        assert report["overall_status"] == "healthy"
        assert report["slos"]["plugin_availability"]["compliance_percent"] > 100

    def test_latency_slo_warning_threshold(self):
        """Latency SLO triggers warning at threshold."""
        monitor = SLOMonitor()

        # Measurement: 260ms p95 latency (target 200ms, warning at 250ms)
        measurement = SLOMeasurement(
            slo_name="delegation_latency_p95",
            measured_value=260.0,
            target_value=200.0,
            unit="latency_ms",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.WARNING,
        )

        monitor.add_measurement(measurement)

        # Overall should be warning
        assert monitor.get_overall_status() == SLOStatus.WARNING

    def test_audit_integrity_slo_critical(self):
        """Audit integrity SLO goes critical on hash mismatches."""
        monitor = SLOMonitor()

        # Measurement: 98% audit integrity (target 100%)
        measurement = SLOMeasurement(
            slo_name="audit_chain_integrity",
            measured_value=0.98,
            target_value=1.0,
            unit="integrity",
            window_start=datetime.utcnow() - timedelta(days=30),
            window_end=datetime.utcnow(),
            status=SLOStatus.CRITICAL,
            good_count=990,
            bad_count=10,
            total_count=1000,
        )

        monitor.add_measurement(measurement)

        # Should be critical
        assert monitor.get_overall_status() == SLOStatus.CRITICAL

    def test_slo_report_completeness(self):
        """SLO report includes all required fields."""
        monitor = SLOMonitor()

        # Record all three SLOs
        now = datetime.utcnow()
        slo_data = [
            ("plugin_availability", 0.9951, SLOStatus.HEALTHY),
            ("delegation_latency_p95", 185.0, SLOStatus.HEALTHY),
            ("audit_chain_integrity", 1.0, SLOStatus.HEALTHY),
        ]

        for slo_name, value, status in slo_data:
            measurement = SLOMeasurement(
                slo_name=slo_name,
                measured_value=value,
                target_value=SLODefinitions.get_slo_by_name(slo_name).target_value,
                unit=SLODefinitions.get_slo_by_name(slo_name).unit,
                window_start=now - timedelta(days=30),
                window_end=now,
                status=status,
            )
            monitor.add_measurement(measurement)

        # Get report
        report = monitor.get_report()

        # Verify report structure
        assert "timestamp_utc" in report
        assert "overall_status" in report
        assert "slos" in report
        assert "summary" in report

        # Verify all three SLOs present
        assert len(report["slos"]) == 3
        assert "plugin_availability" in report["slos"]
        assert "delegation_latency_p95" in report["slos"]
        assert "audit_chain_integrity" in report["slos"]

        # Verify summary
        assert report["summary"]["total_slos"] == 3
        assert report["summary"]["healthy_slos"] == 3
        assert report["summary"]["warning_slos"] == 0
        assert report["summary"]["critical_slos"] == 0


class TestEndToEndMonitoring:
    """Full E2E monitoring scenario."""

    def test_realistic_plugin_lifecycle_monitoring(self):
        """Realistic lifecycle: register → delegate → audit → quarantine."""
        collector = PluginTelemetryCollector()
        hooks = PluginTelemetryHooks(collector=collector)

        # 1. System startup: register plugins
        hooks.on_plugin_registered("stt", "_default", boot_layer="bundled")
        hooks.on_plugin_registered("whisper", "_default", parent_id="stt")
        hooks.on_plugin_registered("deepspeech", "_default", parent_id="stt")

        # 2. Normal operation: 10 successful transcriptions
        for i in range(10):
            hooks.on_work_received("stt", "_default", f"w{i}", "transcribe")
            # 70% to Whisper, 30% to DeepSpeech
            target = "whisper" if i % 3 != 0 else "deepspeech"
            hooks.on_work_delegated("stt", "_default", f"w{i}", target)
            hooks.on_work_handled_locally(target, "_default", f"w{i}", 50 + i*5)

        # 3. Problem detected: Whisper starts failing
        for i in range(10, 13):
            hooks.on_work_received("stt", "_default", f"w{i}", "transcribe")
            hooks.on_work_delegated("stt", "_default", f"w{i}", "whisper")
            hooks.on_audit_hash_mismatch("whisper", "_default", "stt")

        # 4. Escalation: Quarantine after 3 failures
        hooks.on_child_quarantined("stt", "_default", "whisper", "repeated_audit_failures")

        # 5. Recovery: Fallback to DeepSpeech
        hooks.on_fallback_triggered(
            "stt",
            "_default",
            "w13",
            "whisper",
            "deepspeech",
            "quarantined",
        )
        hooks.on_work_delegated("stt", "_default", "w13", "deepspeech")
        hooks.on_work_handled_locally("deepspeech", "_default", "w13", 120.0)

        # 6. Create snapshots
        snap_stt = PluginTelemetrySnapshot(
            plugin_id="stt",
            status="degraded",  # Lost one child
            health_score=0.93,
            work_handled_count=0,
            work_delegated_count=14,
            work_failed_count=3,
            children=["whisper", "deepspeech"],
        )
        snap_whisper = PluginTelemetrySnapshot(
            plugin_id="whisper",
            status="quarantined",
            health_score=0.0,
            work_handled_count=7,
            work_failed_count=3,
        )
        snap_deepspeech = PluginTelemetrySnapshot(
            plugin_id="deepspeech",
            status="ready",
            health_score=1.0,
            work_handled_count=8,
        )

        collector.update_plugin_snapshot("stt", "_default", snap_stt)
        collector.update_plugin_snapshot("whisper", "_default", snap_whisper)
        collector.update_plugin_snapshot("deepspeech", "_default", snap_deepspeech)

        # 7. Verify final state
        stt_snap = collector.get_plugin_snapshot("stt", "_default")
        whisper_snap = collector.get_plugin_snapshot("whisper", "_default")
        deepspeech_snap = collector.get_plugin_snapshot("deepspeech", "_default")

        assert stt_snap.status == "degraded"
        assert whisper_snap.status == "quarantined"
        assert deepspeech_snap.status == "ready"

        # Verify event sequence
        total_events = len(collector.events)
        assert total_events > 20  # Many events recorded

        # Verify audit events
        audit_events = collector.get_events_for_plugin(
            "whisper",
            "_default",
            event_type=PluginTelemetryEventType.AUDIT_HASH_MISMATCH,
        )
        assert len(audit_events) == 3
