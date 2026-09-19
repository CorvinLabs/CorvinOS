"""Phase 4: Bootstrap integration tests for OTELExporter.

Tests verify:
- OTELExporter initializes on boot (before plugins load)
- Heartbeat is exported successfully or JSON fallback is written
- Audit trail integration works end-to-end
- Tenant isolation verified in boot sequence

These are integration tests verifying the complete boot-to-export flow.

ADR-0680, ADR-0681, ADR-0682
"""

import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.observability.otel_exporter.exporter import (
    GeoAttributes,
    OTELExporter,
    OTELExportError,
)


@pytest.fixture
def temp_telemetry_dir():
    """Temporary telemetry directory (simulating ~/.corvin/telemetry/)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_audit_logger():
    """Mock audit logger that captures events."""
    logger = MagicMock(spec=logging.Logger)
    logger.events = []

    def capture_info(event_name, extra=None):
        logger.events.append(("info", event_name, extra or {}))

    def capture_warning(event_name, extra=None):
        logger.events.append(("warning", event_name, extra or {}))

    logger.info.side_effect = capture_info
    logger.warning.side_effect = capture_warning

    return logger


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP INITIALIZATION TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestBootstrapInitialization:
    """Test OTELExporter initialization during bootstrap."""

    def test_exporter_initializes_on_boot(self, temp_telemetry_dir, mock_audit_logger):
        """OTELExporter should initialize successfully on boot."""
        # Simulate boot sequence
        exporter = OTELExporter(
            tenant_id="tenant_default",
            instance_id="instance-boot-12345",
            geo_granularity="country",
            json_fallback_dir=temp_telemetry_dir,
            otel_collector_url="http://localhost:4318",
            audit_logger=mock_audit_logger,
        )

        assert exporter is not None
        assert exporter.tenant_id == "tenant_default"
        assert exporter.instance_id == "instance-boot-12345"

    def test_exporter_ready_before_plugins_load(self, temp_telemetry_dir, mock_audit_logger):
        """OTELExporter should be initialized and ready before plugins load."""
        # Boot sequence: 1. Create exporter, 2. Load plugins
        exporter = OTELExporter(
            tenant_id="tenant_default",
            instance_id="instance-boot-12345",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        # Verify exporter is ready (no exceptions)
        assert exporter.json_fallback_dir.exists() is False  # Created lazily
        assert exporter.tenant_id == "tenant_default"

    def test_multi_tenant_boot_isolation(self, mock_audit_logger):
        """Multiple tenants should have isolated exporter instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir1 = Path(tmpdir) / "tenant1"
            dir2 = Path(tmpdir) / "tenant2"

            exporter1 = OTELExporter(
                tenant_id="tenant1",
                instance_id="instance-1",
                json_fallback_dir=dir1,
                audit_logger=mock_audit_logger,
            )

            exporter2 = OTELExporter(
                tenant_id="tenant2",
                instance_id="instance-2",
                json_fallback_dir=dir2,
                audit_logger=mock_audit_logger,
            )

            assert exporter1.tenant_id == "tenant1"
            assert exporter2.tenant_id == "tenant2"
            assert exporter1.json_fallback_dir != exporter2.json_fallback_dir


# ─────────────────────────────────────────────────────────────────────────────
# END-TO-END HEARTBEAT EXPORT TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestHeartbeatExportE2E:
    """End-to-end tests for heartbeat export."""

    def test_heartbeat_exported_on_boot(self, temp_telemetry_dir, mock_audit_logger):
        """Heartbeat should be exported on bootstrap startup."""
        exporter = OTELExporter(
            tenant_id="tenant_default",
            instance_id="instance-boot-12345",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        # Simulate boot heartbeat export
        with patch.object(exporter, "_export_to_otel"):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=5,  # Just booted
                plugin_count=0,  # No plugins loaded yet
                memory_usage_bytes=100_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        assert success is True
        assert "OTEL export succeeded" in message

    def test_heartbeat_exported_with_plugins_loaded(self, temp_telemetry_dir, mock_audit_logger):
        """Heartbeat should include plugin count after plugins load."""
        exporter = OTELExporter(
            tenant_id="tenant_default",
            instance_id="instance-boot-12345",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        # Simulate heartbeat after plugins loaded
        with patch.object(exporter, "_export_to_otel"):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,  # Plugins loaded
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        assert success is True

    def test_heartbeat_exported_with_geo_attributes(self, temp_telemetry_dir, mock_audit_logger):
        """Heartbeat should include geo attributes when available."""
        exporter = OTELExporter(
            tenant_id="tenant_default",
            instance_id="instance-boot-12345",
            geo_granularity="region",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        geo = GeoAttributes(country="DE", region="BW", granularity="region")

        with patch.object(exporter, "_export_to_otel"):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=geo,
            )

        assert success is True


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT TRAIL INTEGRATION TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditTrailIntegration:
    """Test audit trail captures all telemetry events."""

    def test_audit_trail_on_successful_export(self, temp_telemetry_dir, mock_audit_logger):
        """Audit trail should record successful OTEL export."""
        exporter = OTELExporter(
            tenant_id="tenant_prod",
            instance_id="instance-audit-test",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        with patch.object(exporter, "_export_to_otel"):
            exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Verify audit event was logged
        assert len(mock_audit_logger.events) > 0
        event_type, event_name, event_extra = mock_audit_logger.events[0]
        assert event_type == "info"
        assert event_name == "telemetry_dual_write"
        assert event_extra["tenant_id"] == "tenant_prod"

    def test_audit_trail_on_fallback(self, temp_telemetry_dir, mock_audit_logger):
        """Audit trail should record fallback activation."""
        exporter = OTELExporter(
            tenant_id="tenant_prod",
            instance_id="instance-audit-test",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Verify warning event was logged
        assert len(mock_audit_logger.events) > 0
        event_type, event_name, event_extra = mock_audit_logger.events[0]
        assert event_type == "warning"
        assert event_name == "telemetry_fallback_activated"
        assert event_extra["tenant_id"] == "tenant_prod"

    def test_audit_trail_immutability(self, temp_telemetry_dir, mock_audit_logger):
        """Audit events should be immutable and appended (not overwritten)."""
        exporter = OTELExporter(
            tenant_id="tenant_prod",
            instance_id="instance-audit-test",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        # Export twice
        with patch.object(exporter, "_export_to_otel"):
            exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

            exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=7200,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Both events should be in audit trail (append-only)
        assert len(mock_audit_logger.events) == 2


# ─────────────────────────────────────────────────────────────────────────────
# JSON FALLBACK VERIFICATION TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestJSONFallbackVerification:
    """Verify JSON fallback is written correctly on OTEL failure."""

    def test_json_fallback_written_on_collector_down(self, temp_telemetry_dir, mock_audit_logger):
        """JSON fallback should be written when collector is down."""
        exporter = OTELExporter(
            tenant_id="tenant_prod",
            instance_id="instance-fallback-test",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Connection refused")):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Verify fallback was activated
        assert success is False
        assert "JSON fallback" in message

        # Verify JSON file was created
        fallback_file = temp_telemetry_dir / "heartbeat-tenant_prod-instance-fallback-test.jsonl"
        assert fallback_file.exists()

        # Verify JSON content is valid
        with open(fallback_file) as f:
            record = json.loads(f.readline())

        assert record["tenant_id"] == "tenant_prod"
        assert record["instance_id"] == "instance-fallback-test"
        assert record["is_alive"] is True
        assert record["uptime_seconds"] == 3600
        assert record["plugin_count"] == 5

    def test_json_fallback_complete_after_retries(self, temp_telemetry_dir, mock_audit_logger):
        """JSON fallback should be written only after retries exhausted."""
        exporter = OTELExporter(
            tenant_id="tenant_prod",
            instance_id="instance-retry-test",
            json_fallback_dir=temp_telemetry_dir,
            audit_logger=mock_audit_logger,
        )

        call_count = 0

        def simulate_persistent_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OTELExportError(f"Attempt {call_count} failed")

        with patch.object(exporter, "_export_to_otel", side_effect=simulate_persistent_failure):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=200_000_000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Should have retried 3 times
        assert call_count == 3

        # JSON fallback should be written
        fallback_file = temp_telemetry_dir / "heartbeat-tenant_prod-instance-retry-test.jsonl"
        assert fallback_file.exists()


# ─────────────────────────────────────────────────────────────────────────────
# TENANT ISOLATION VERIFICATION TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestTenantIsolationE2E:
    """Verify tenant isolation in end-to-end flow."""

    def test_tenant_isolation_in_audit_trail(self, temp_telemetry_dir, mock_audit_logger):
        """Audit trail should always include tenant_id."""
        tenant_ids = ["tenant_1", "tenant_2", "tenant_3"]

        for tenant_id in tenant_ids:
            exporter = OTELExporter(
                tenant_id=tenant_id,
                instance_id=f"instance-{tenant_id}",
                json_fallback_dir=temp_telemetry_dir / tenant_id,
                audit_logger=mock_audit_logger,
            )

            with patch.object(exporter, "_export_to_otel"):
                exporter.export_heartbeat(
                    is_alive=True,
                    uptime_seconds=3600,
                    plugin_count=5,
                    memory_usage_bytes=200_000_000,
                    platform="linux",
                    python_version="3.11",
                    geo_attrs=None,
                )

        # Verify each tenant's audit events are isolated
        for event_type, event_name, event_extra in mock_audit_logger.events:
            assert "tenant_id" in event_extra

    def test_tenant_isolation_in_json_fallback(self, mock_audit_logger):
        """JSON fallback files should be isolated per tenant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            tenant_ids = ["tenant_1", "tenant_2"]

            for tenant_id in tenant_ids:
                exporter = OTELExporter(
                    tenant_id=tenant_id,
                    instance_id=f"instance-{tenant_id}",
                    json_fallback_dir=base_dir / tenant_id,
                    audit_logger=mock_audit_logger,
                )

                with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector down")):
                    exporter.export_heartbeat(
                        is_alive=True,
                        uptime_seconds=3600,
                        plugin_count=5,
                        memory_usage_bytes=200_000_000,
                        platform="linux",
                        python_version="3.11",
                        geo_attrs=None,
                    )

            # Verify files are isolated
            file_1 = base_dir / "tenant_1" / "heartbeat-tenant_1-instance-tenant_1.jsonl"
            file_2 = base_dir / "tenant_2" / "heartbeat-tenant_2-instance-tenant_2.jsonl"

            assert file_1.exists()
            assert file_2.exists()

            # Verify no cross-tenant data
            with open(file_1) as f:
                record_1 = json.loads(f.readline())
            with open(file_2) as f:
                record_2 = json.loads(f.readline())

            assert record_1["tenant_id"] == "tenant_1"
            assert record_2["tenant_id"] == "tenant_2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
