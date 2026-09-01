"""E2E tests for Brain Layer Monitor plugin."""

import pytest
import asyncio
import time
from datetime import datetime, timezone

from buildin.observability.brain_layer_monitor import BrainLayerMonitor
from buildin.observability.brain_layer_monitor.events import LayerMetricEvent
from core.compliance.tripwire import boot_tripwire


class TestBrainLayerMonitorE2E:
    """End-to-end tests for Brain Layer Monitor."""

    LAYERS = ["L1", "L4", "L5", "L6", "L7", "L10", "L16", "L18", "L22", "L24",
              "L28", "L29", "L30", "L32", "L33", "L34", "L35", "L36", "L37", "L38"]

    @pytest.fixture
    async def monitor(self):
        """Fixture: initialized monitor instance."""
        await boot_tripwire()
        monitor = BrainLayerMonitor(tenant_id="test_tenant")
        await monitor.initialize()
        yield monitor
        await monitor.shutdown()

    @pytest.mark.asyncio
    async def test_full_layer_metrics_emission(self, monitor):
        """Test emitting metrics for all layers."""
        for layer_id in self.LAYERS:
            event = LayerMetricEvent(
                layer_id=layer_id,
                layer_name=f"layer_{layer_id.lower()}",
                data={
                    "requests_total": 100000 + int(layer_id[1:]) * 10000,
                    "latency_p50_ms": 1.0,
                    "latency_p95_ms": 5.0,
                    "latency_p99_ms": 15.0,
                    "error_rate": 0.0001,
                    "compliance_check_passed": True
                }
            )
            await monitor.emit_layer_metric(event)

        # Verify all layers tracked
        layer_perf = await monitor.get_layer_performance("L10")
        assert layer_perf["layer_id"] == "L10"
        assert layer_perf["latency_p99_ms"] == 15.0

    @pytest.mark.asyncio
    async def test_layer_group_aggregation(self, monitor):
        """Test aggregation by layer group."""
        # Emit metrics for core layers
        core_layers = ["L1", "L4", "L5", "L6", "L7", "L10"]
        for layer_id in core_layers:
            event = LayerMetricEvent(
                layer_id=layer_id,
                layer_name=f"layer_{layer_id}",
                data={
                    "requests_total": 50000,
                    "latency_p95_ms": 3.0,
                    "error_rate": 0.0001,
                    "compliance_check_passed": True
                }
            )
            await monitor.emit_layer_metric(event)

        # Query group summary
        group_summary = await monitor.get_layer_group_summary("core")
        assert group_summary["group_name"] == "core"
        assert len(group_summary["layers"]) >= 6

    @pytest.mark.asyncio
    async def test_compliance_layer_monitoring(self, monitor):
        """Test monitoring of compliance layers."""
        compliance_layers = ["L16", "L34", "L35", "L36", "L37", "L38", "L44"]

        for layer_id in compliance_layers:
            event = LayerMetricEvent(
                layer_id=layer_id,
                layer_name=f"compliance_layer_{layer_id}",
                data={
                    "compliance_check_passed": True,
                    "checkpoint_validated": True,
                    "audit_event_logged": True
                }
            )
            await monitor.emit_layer_metric(event)

        # Verify compliance status
        compliance_report = await monitor.get_compliance_report()
        assert compliance_report["all_compliance_layers_passed"] is True

    @pytest.mark.asyncio
    async def test_performance_metric_ingestion(self, monitor):
        """Test metric ingestion performance (<1ms per metric)."""
        times = []

        for i in range(500):
            layer_id = self.LAYERS[i % len(self.LAYERS)]
            event = LayerMetricEvent(
                layer_id=layer_id,
                layer_name=f"layer_{layer_id}",
                data={
                    "requests_total": 10000,
                    "latency_p95_ms": 2.0 + (i % 10),
                    "error_rate": 0.0001
                }
            )

            start = time.time()
            await monitor.emit_layer_metric(event)
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        mean_time = sum(times) / len(times)
        assert mean_time < 1.0, f"Mean {mean_time:.2f}ms exceeds 1ms target"

    @pytest.mark.asyncio
    async def test_performance_layer_query(self, monitor):
        """Test per-layer query performance (<10ms)."""
        # Emit metrics first
        for layer_id in self.LAYERS:
            event = LayerMetricEvent(
                layer_id=layer_id,
                layer_name=f"layer_{layer_id}",
                data={"latency_p95_ms": 2.0}
            )
            await monitor.emit_layer_metric(event)

        # Measure query time
        times = []
        for _ in range(20):
            start = time.time()
            perf = await monitor.get_layer_performance("L10")
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        mean_time = sum(times) / len(times)
        assert mean_time < 10.0, f"Mean {mean_time:.2f}ms exceeds 10ms target"

    @pytest.mark.asyncio
    async def test_concurrent_layer_streams(self, monitor):
        """Test concurrent metric emissions from multiple layers."""
        async def emit_for_layer(layer_id):
            for i in range(50):
                event = LayerMetricEvent(
                    layer_id=layer_id,
                    layer_name=f"layer_{layer_id}",
                    data={
                        "metric_num": i,
                        "latency_p95_ms": 2.0 + (i % 5),
                        "error_rate": 0.0001
                    }
                )
                await monitor.emit_layer_metric(event)

        tasks = [emit_for_layer(layer_id) for layer_id in self.LAYERS]
        await asyncio.gather(*tasks)

        # Verify all layers tracked
        for layer_id in self.LAYERS:
            perf = await monitor.get_layer_performance(layer_id)
            assert perf is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
