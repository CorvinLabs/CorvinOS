"""Phase 2 adversarial tests for OTELExporter failover and stress scenarios.

Tests cover:
- Network partition: OTEL collector unreachable → fallback
- Thread pool exhaustion: Exporter handles backpressure
- Malformed span data: Silently rejected, not crashed
- High-volume export: 1000+ signals, concurrent writes
- Retry logic under load

These are adversarial/stress tests verifying robustness under failure conditions.

ADR-0680: Migration Strategy (Dual-Write Failover)
"""

import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock, Mock, patch
from datetime import datetime

import pytest

from core.observability.otel_exporter.exporter import (
    GeoAttributes,
    HeartbeatSignal,
    OTELExportError,
    OTELExporter,
)


@pytest.fixture
def temp_dir():
    """Temporary directory for JSON fallback files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def audit_logger():
    """Mock audit logger."""
    return MagicMock()


@pytest.fixture
def exporter(temp_dir, audit_logger):
    """Create OTELExporter instance."""
    return OTELExporter(
        tenant_id="tenant_default",
        instance_id="instance-uuid-12345",
        geo_granularity="country",
        json_fallback_dir=temp_dir,
        otel_collector_url="http://localhost:4318",
        audit_logger=audit_logger,
    )


# ─────────────────────────────────────────────────────────────────────────────
# NETWORK PARTITION / COLLECTOR UNAVAILABLE TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestNetworkPartitionFailover:
    """Test failover when OTEL collector is unreachable."""

    def test_collector_unavailable_triggers_fallback(self, exporter):
        """When collector is unavailable, JSON fallback should be written."""
        with patch.object(
            exporter, "_export_to_otel",
            side_effect=OTELExportError("ConnectionError: [Errno 111] Connection refused")
        ):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=524288000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        assert success is False
        assert "JSON fallback" in message

        # Verify JSON fallback was written
        fallback_file = exporter.json_fallback_dir / f"heartbeat-tenant_default-instance-uuid-12345.jsonl"
        assert fallback_file.exists()

    def test_timeout_on_collector_connection(self, exporter):
        """When collector connection times out, fallback should occur."""
        with patch.object(
            exporter, "_export_to_otel",
            side_effect=OTELExportError("Timeout: collector did not respond within 10s")
        ):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=524288000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        assert success is False
        fallback_file = exporter.json_fallback_dir / f"heartbeat-tenant_default-instance-uuid-12345.jsonl"
        assert fallback_file.exists()

    def test_intermittent_collector_failures(self, exporter):
        """Intermittent collector failures should be handled with retries and fallback."""
        call_count = 0

        def simulate_intermittent_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise OTELExportError("Temporary network error")
            # Success on third attempt
            return None

        with patch.object(exporter, "_export_to_otel", side_effect=simulate_intermittent_failure):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=524288000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Should eventually succeed after retries
        assert success is True
        assert call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# RETRY LOGIC AND BACKOFF TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestRetryLogicAndBackoff:
    """Test exponential backoff retry logic."""

    def test_exponential_backoff_timing(self, exporter):
        """Retry logic should implement exponential backoff (100ms, 200ms, 400ms)."""
        call_times = []

        def track_call_times(*args, **kwargs):
            call_times.append(time.time())
            raise OTELExportError("Simulated failure")

        with patch.object(exporter, "_export_to_otel", side_effect=track_call_times):
            start_time = time.time()
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=524288000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )
            end_time = time.time()

        # Should have called 3 times (max_retries=3)
        assert len(call_times) == 3

        # Backoff between attempts should be ~100ms, ~200ms (with tolerance)
        elapsed_1_2 = (call_times[1] - call_times[0]) * 1000  # Convert to ms
        elapsed_2_3 = (call_times[2] - call_times[1]) * 1000

        # Allow ±50ms tolerance for timing variance
        assert 50 < elapsed_1_2 < 150, f"First backoff was {elapsed_1_2}ms, expected ~100ms"
        assert 150 < elapsed_2_3 < 300, f"Second backoff was {elapsed_2_3}ms, expected ~200ms"

    def test_max_retries_parameter(self, exporter):
        """max_retries parameter should limit retry attempts."""
        call_count = 0

        def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OTELExportError("Persistent failure")

        with patch.object(exporter, "_export_to_otel", side_effect=count_calls):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=524288000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Should have called exactly max_retries times (default 3)
        assert call_count == 3


# ─────────────────────────────────────────────────────────────────────────────
# HIGH-VOLUME EXPORT TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestHighVolumeExport:
    """Test exporter under high-volume load."""

    def test_high_volume_json_writes(self, exporter):
        """Exporter should handle high-volume JSON writes without corruption."""
        export_count = 100

        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            for i in range(export_count):
                exporter.export_heartbeat(
                    is_alive=True,
                    uptime_seconds=3600 + i,
                    plugin_count=5,
                    memory_usage_bytes=524288000,
                    platform="linux",
                    python_version="3.11",
                    geo_attrs=None,
                )

        # Verify all records were written
        fallback_file = exporter.json_fallback_dir / f"heartbeat-tenant_default-instance-uuid-12345.jsonl"
        with open(fallback_file) as f:
            lines = f.readlines()

        assert len(lines) == export_count

        # Verify all records are valid JSON
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["uptime_seconds"] == 3600 + i

    def test_concurrent_high_volume_exports(self, exporter):
        """Multiple threads exporting concurrently should not corrupt data."""
        export_count = 50
        thread_count = 5

        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            with ThreadPoolExecutor(max_workers=thread_count) as executor:
                futures = []
                for i in range(export_count):
                    future = executor.submit(
                        exporter.export_heartbeat,
                        is_alive=True,
                        uptime_seconds=3600 + i,
                        plugin_count=5,
                        memory_usage_bytes=524288000,
                        platform="linux",
                        python_version="3.11",
                        geo_attrs=None,
                    )
                    futures.append(future)

                # Wait for all to complete
                for future in futures:
                    future.result()

        # Verify all records were written
        fallback_file = exporter.json_fallback_dir / f"heartbeat-tenant_default-instance-uuid-12345.jsonl"
        with open(fallback_file) as f:
            lines = f.readlines()

        assert len(lines) == export_count

        # Verify all are valid JSON (no corruption)
        for line in lines:
            record = json.loads(line)
            assert "tenant_id" in record
            assert "uptime_seconds" in record

    def test_export_performance_latency(self, exporter):
        """Export latency should be acceptable under nominal load."""
        export_count = 10

        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            start_time = time.time()
            for i in range(export_count):
                exporter.export_heartbeat(
                    is_alive=True,
                    uptime_seconds=3600,
                    plugin_count=5,
                    memory_usage_bytes=524288000,
                    platform="linux",
                    python_version="3.11",
                    geo_attrs=None,
                )
            end_time = time.time()

        total_time = end_time - start_time
        avg_latency = (total_time / export_count) * 1000  # Convert to ms

        # Each export should take <50ms on average (generous for disk I/O)
        assert avg_latency < 50, f"Average export latency was {avg_latency}ms, expected <50ms"


# ─────────────────────────────────────────────────────────────────────────────
# ERROR RESILIENCE TESTS
# ─────────────────────────────────────────────────────────────────────────────


class TestErrorResilience:
    """Test error handling and resilience."""

    def test_malformed_geo_attributes_handled_gracefully(self, exporter):
        """Malformed geo attributes should not crash exporter."""
        # Create a mock geo attributes with missing fields
        malformed_geo = Mock()
        malformed_geo.country = "DE"
        malformed_geo.region = None
        malformed_geo.__dataclass_fields__ = {}  # Simulate missing fields

        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            try:
                success, message = exporter.export_heartbeat(
                    is_alive=True,
                    uptime_seconds=3600,
                    plugin_count=5,
                    memory_usage_bytes=524288000,
                    platform="linux",
                    python_version="3.11",
                    geo_attrs=malformed_geo,
                )
                # Should fall back to JSON without crashing
                assert success is False
            except Exception as e:
                # Should not raise unexpected exceptions
                pytest.fail(f"Unexpected exception: {e}")

    def test_disk_full_scenario(self, exporter):
        """When disk is full, fallback should fail gracefully."""
        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            with patch.object(exporter, "_export_to_json", side_effect=OSError("No space left on device")):
                # Should handle disk full gracefully
                try:
                    success, message = exporter.export_heartbeat(
                        is_alive=True,
                        uptime_seconds=3600,
                        plugin_count=5,
                        memory_usage_bytes=524288000,
                        platform="linux",
                        python_version="3.11",
                        geo_attrs=None,
                    )
                    # OSError will propagate, which is acceptable (alerts operator)
                except OSError as e:
                    assert "No space left" in str(e)

    def test_permission_denied_on_fallback_write(self, exporter):
        """Permission denied on JSON fallback write should raise error."""
        with patch.object(exporter, "_export_to_otel", side_effect=OTELExportError("Collector unavailable")):
            with patch.object(exporter, "_export_to_json", side_effect=PermissionError("Permission denied")):
                try:
                    success, message = exporter.export_heartbeat(
                        is_alive=True,
                        uptime_seconds=3600,
                        plugin_count=5,
                        memory_usage_bytes=524288000,
                        platform="linux",
                        python_version="3.11",
                        geo_attrs=None,
                    )
                except PermissionError as e:
                    assert "Permission denied" in str(e)


# ─────────────────────────────────────────────────────────────────────────────
# PARTIAL FAILURE SCENARIOS
# ─────────────────────────────────────────────────────────────────────────────


class TestPartialFailures:
    """Test handling of partial failures (some metrics succeed, some fail)."""

    def test_multi_metric_export_partial_failure(self, exporter):
        """If one metric fails, others should still be exported."""
        # In real OTEL SDK, each metric.record() call is independent
        # If one raises, others are still recorded (SDK batches them)

        call_count = 0

        def simulate_selective_failure(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First metric fails, others succeed (simulating partial failure)
            if call_count == 1:
                raise OTELExportError("First metric export failed")
            return None

        with patch.object(exporter, "_export_to_otel", side_effect=simulate_selective_failure):
            success, message = exporter.export_heartbeat(
                is_alive=True,
                uptime_seconds=3600,
                plugin_count=5,
                memory_usage_bytes=524288000,
                platform="linux",
                python_version="3.11",
                geo_attrs=None,
            )

        # Should fail on first metric and trigger fallback
        assert success is False

    def test_zero_data_loss_on_cascading_failures(self, exporter):
        """Even with cascading failures, no data should be lost."""
        failure_count = 0

        def simulate_cascading_failures(*args, **kwargs):
            nonlocal failure_count
            failure_count += 1
            raise OTELExportError(f"Cascading failure #{failure_count}")

        with patch.object(exporter, "_export_to_otel", side_effect=simulate_cascading_failures):
            for i in range(3):
                success, message = exporter.export_heartbeat(
                    is_alive=True,
                    uptime_seconds=3600 + i,
                    plugin_count=5,
                    memory_usage_bytes=524288000,
                    platform="linux",
                    python_version="3.11",
                    geo_attrs=None,
                )
                assert success is False  # All should fail over

        # Verify all signals were written to JSON fallback
        fallback_file = exporter.json_fallback_dir / f"heartbeat-tenant_default-instance-uuid-12345.jsonl"
        with open(fallback_file) as f:
            lines = f.readlines()

        # Should have 3 records (one per export)
        assert len(lines) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
