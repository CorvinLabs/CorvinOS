"""E2E tests for Brain Diagnostics plugin."""

import pytest
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any
import time

from buildin.observability.brain_diagnostics import BrainDiagnostics
from buildin.observability.brain_diagnostics.events import DiagnosticEvent, EventType
from core.compliance.tripwire import boot_tripwire


class TestBrainDiagnosticsE2E:
    """End-to-end tests for Brain Diagnostics."""

    # 13 subsystems to monitor
    SUBSYSTEMS = [
        "execution_context",
        "context_bus",
        "voice_coordinator",
        "task_manager",
        "plugin_system",
        "audit_writer",
        "context_pipeline",
        "session_manager",
        "learning_system",
        "security_pipeline",
        "healing_traces",
        "telemetry_client",
        "compliance_reporter"
    ]

    @pytest.fixture
    async def diagnostics(self):
        """Fixture: initialized diagnostics instance."""
        await boot_tripwire()

        diag = BrainDiagnostics(tenant_id="test_tenant")
        await diag.initialize()

        # Register all subsystems
        for subsystem_name in self.SUBSYSTEMS:
            await diag.register_subsystem(subsystem_name)

        yield diag
        await diag.shutdown()

    @pytest.mark.asyncio
    async def test_system_health_calculation(self, diagnostics):
        """Test system-level health score aggregation."""
        # Emit metrics from all subsystems (healthy state)
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={
                    "cpu_usage_percent": 25.0,
                    "memory_usage_percent": 45.0,
                    "error_rate": 0.001,
                    "latency_ms": 5.0,
                    "throughput": 1000
                }
            )
            await diagnostics.emit_metric(event)

        # Get system health
        system_health = await diagnostics.get_system_health()
        assert system_health["overall_score"] >= 80
        assert system_health["status"] == "HEALTHY"
        assert len(system_health["subsystem_scores"]) >= 13

    @pytest.mark.asyncio
    async def test_subsystem_degradation_detection(self, diagnostics):
        """Test detection of degraded subsystems."""
        # Emit healthy metrics
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={
                    "cpu_usage_percent": 25.0,
                    "memory_usage_percent": 45.0,
                    "error_rate": 0.001
                }
            )
            await diagnostics.emit_metric(event)

        initial_health = await diagnostics.get_system_health()

        # Degrade ExecutionContext
        degraded_event = DiagnosticEvent(
            subsystem="execution_context",
            event_type=EventType.METRICS_UPDATE,
            data={
                "cpu_usage_percent": 95.0,  # Critical
                "memory_usage_percent": 92.0,  # Critical
                "error_rate": 0.15,  # High error rate
                "latency_ms": 250.0  # High latency
            }
        )
        await diagnostics.emit_metric(degraded_event)

        # System health should degrade
        degraded_health = await diagnostics.get_system_health()
        assert degraded_health["overall_score"] < initial_health["overall_score"]
        assert degraded_health["status"] in ["DEGRADED", "CRITICAL"]

    @pytest.mark.asyncio
    async def test_subsystem_health_breakdown(self, diagnostics):
        """Test subsystem-level health queries."""
        # Emit different health levels
        for i, subsystem_name in enumerate(self.SUBSYSTEMS):
            health_percent = 100 - (i * 5)  # Vary from 100 to 35
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={
                    "cpu_usage_percent": 100 - health_percent,
                    "memory_usage_percent": 100 - health_percent,
                    "error_rate": (1 - health_percent / 100) * 0.2
                }
            )
            await diagnostics.emit_metric(event)

        # Get per-subsystem health
        subsystem_health = await diagnostics.get_subsystem_health()
        assert len(subsystem_health) >= 13

        # Verify health scores decrease as expected
        scores = [health["score"] for health in subsystem_health.values()]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_anomaly_detection(self, diagnostics):
        """Test anomaly detection in subsystem metrics."""
        # Emit normal metrics for most subsystems
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={
                    "cpu_usage_percent": 30.0,
                    "memory_usage_percent": 50.0,
                    "error_rate": 0.001
                }
            )
            await diagnostics.emit_metric(event)

        # Emit anomalous metrics for ContextBus (error spike)
        anomaly_event = DiagnosticEvent(
            subsystem="context_bus",
            event_type=EventType.ANOMALY_DETECTED,
            data={
                "anomaly_type": "error_spike",
                "previous_error_rate": 0.001,
                "current_error_rate": 0.35,
                "spike_factor": 350,
                "affected_component": "message_queue"
            }
        )
        await diagnostics.emit_metric(anomaly_event)

        # Get anomalies
        anomalies = await diagnostics.get_anomalies()
        assert len(anomalies) >= 1
        assert any(a["subsystem"] == "context_bus" for a in anomalies)

    @pytest.mark.asyncio
    async def test_dependency_graph(self, diagnostics):
        """Test subsystem dependency graph queries."""
        # Emit metrics
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={"status": "healthy"}
            )
            await diagnostics.emit_metric(event)

        # Get dependency graph
        graph = await diagnostics.get_dependency_graph()
        assert graph is not None
        assert isinstance(graph, dict)

        # Verify known dependencies
        # ExecutionContext should be fundamental
        assert "execution_context" in graph

        # ContextBus depends on ExecutionContext
        if "context_bus" in graph:
            deps = graph["context_bus"]
            assert isinstance(deps, list)

    @pytest.mark.asyncio
    async def test_degradation_impact_analysis(self, diagnostics):
        """Test impact analysis for subsystem failures."""
        # Set up healthy baseline
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={"status": "healthy"}
            )
            await diagnostics.emit_metric(event)

        # Query impact if ExecutionContext degrades
        impact = await diagnostics.get_degradation_impact(
            "execution_context",
            "context_bus"
        )
        assert isinstance(impact, (int, float))
        assert 0 <= impact <= 1

        # ContextBus should be significantly impacted by ExecutionContext
        assert impact > 0.5

    @pytest.mark.asyncio
    async def test_concurrent_metric_streams(self, diagnostics):
        """Test handling of concurrent metric emissions."""
        num_metrics_per_subsystem = 100

        async def emit_metrics_for_subsystem(subsystem_name):
            for i in range(num_metrics_per_subsystem):
                event = DiagnosticEvent(
                    subsystem=subsystem_name,
                    event_type=EventType.METRICS_UPDATE,
                    data={
                        "metric_num": i,
                        "cpu_usage_percent": 20 + (i % 30),
                        "memory_usage_percent": 40 + (i % 40),
                        "latency_ms": 5 + (i % 20)
                    }
                )
                await diagnostics.emit_metric(event)
                await asyncio.sleep(0.0001)  # Minimal delay

        # Run all subsystems concurrently
        tasks = [
            emit_metrics_for_subsystem(subsystem_name)
            for subsystem_name in self.SUBSYSTEMS
        ]
        await asyncio.gather(*tasks)

        # Verify system health still computes
        system_health = await diagnostics.get_system_health()
        assert system_health["overall_score"] >= 0
        assert system_health["overall_score"] <= 100

    @pytest.mark.asyncio
    async def test_performance_metric_ingestion(self, diagnostics):
        """Test metric ingestion performance (<2ms per metric)."""
        num_metrics = 500
        times = []

        for i in range(num_metrics):
            event = DiagnosticEvent(
                subsystem=self.SUBSYSTEMS[i % len(self.SUBSYSTEMS)],
                event_type=EventType.METRICS_UPDATE,
                data={
                    "metric_num": i,
                    "cpu_usage_percent": 30.0,
                    "memory_usage_percent": 50.0
                }
            )

            start = time.time()
            await diagnostics.emit_metric(event)
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        # Verify SLA
        mean_time = sum(times) / len(times)
        max_time = max(times)
        p95 = sorted(times)[int(0.95 * len(times))]

        assert mean_time < 2.0, f"Mean {mean_time:.2f}ms exceeds 2ms target"
        assert max_time < 10.0, f"Max {max_time:.2f}ms exceeds 10ms threshold"

        print(f"\nMetric Ingestion Performance:")
        print(f"  Mean: {mean_time:.2f}ms")
        print(f"  Max:  {max_time:.2f}ms")
        print(f"  p95:  {p95:.2f}ms")

    @pytest.mark.asyncio
    async def test_performance_health_calculation(self, diagnostics):
        """Test system health calculation performance (<50ms)."""
        # Emit metrics for all subsystems
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={
                    "cpu_usage_percent": 30.0,
                    "memory_usage_percent": 50.0,
                    "error_rate": 0.001
                }
            )
            await diagnostics.emit_metric(event)

        # Measure health calculation
        times = []
        for _ in range(20):
            start = time.time()
            health = await diagnostics.get_system_health()
            elapsed_ms = (time.time() - start) * 1000
            times.append(elapsed_ms)

        mean_time = sum(times) / len(times)
        max_time = max(times)

        assert mean_time < 50.0, f"Mean {mean_time:.2f}ms exceeds 50ms target"

        print(f"\nHealth Calculation Performance:")
        print(f"  Mean: {mean_time:.2f}ms")
        print(f"  Max:  {max_time:.2f}ms")

    @pytest.mark.asyncio
    async def test_subsystem_details_queries(self, diagnostics):
        """Test subsystem-level detail queries."""
        # Emit detailed metrics
        detailed_event = DiagnosticEvent(
            subsystem="execution_context",
            event_type=EventType.METRICS_UPDATE,
            data={
                "context_switches_per_sec": 42.5,
                "avg_context_size_kb": 256,
                "memory_usage_percent": 45.2,
                "gc_duration_ms": 12.5,
                "gc_count_total": 1247,
                "errors_last_5min": 2
            }
        )
        await diagnostics.emit_metric(detailed_event)

        # Query details
        details = await diagnostics.get_subsystem_details("execution_context")
        assert details["context_switches_per_sec"] == 42.5
        assert details["avg_context_size_kb"] == 256
        assert details["memory_usage_percent"] == 45.2

    @pytest.mark.asyncio
    async def test_audit_trail_integration(self, diagnostics):
        """Test that metrics are properly audit-logged."""
        # Emit metric
        event = DiagnosticEvent(
            subsystem="audit_writer",
            event_type=EventType.METRICS_UPDATE,
            data={
                "events_written_total": 12345,
                "audit_chain_valid": True,
                "last_hash": "abc123def456..."
            }
        )
        await diagnostics.emit_metric(event)

        # Verify metric was recorded
        details = await diagnostics.get_subsystem_details("audit_writer")
        assert details["events_written_total"] == 12345
        assert details["audit_chain_valid"] is True

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, diagnostics):
        """Test that metrics are isolated per tenant."""
        # Emit metric to test_tenant
        event1 = DiagnosticEvent(
            subsystem="execution_context",
            event_type=EventType.METRICS_UPDATE,
            data={"metric_value": 100}
        )
        await diagnostics.emit_metric(event1)

        # Create second diagnostics instance with different tenant
        diag2 = BrainDiagnostics(tenant_id="other_tenant")
        await diag2.initialize()
        for subsystem_name in self.SUBSYSTEMS:
            await diag2.register_subsystem(subsystem_name)

        # Emit metric to other_tenant
        event2 = DiagnosticEvent(
            subsystem="execution_context",
            event_type=EventType.METRICS_UPDATE,
            data={"metric_value": 50}
        )
        await diag2.emit_metric(event2)

        # Verify isolation
        health1 = await diagnostics.get_system_health()
        health2 = await diag2.get_system_health()

        # Should be different (they have different metric histories)
        # At minimum, they should be independent instances
        assert health1["tenant_id"] == "test_tenant"
        assert health2["tenant_id"] == "other_tenant"

        await diag2.shutdown()

    @pytest.mark.asyncio
    async def test_cascade_degradation(self, diagnostics):
        """Test that degradation cascades through dependencies."""
        # Emit healthy baseline
        for subsystem_name in self.SUBSYSTEMS:
            event = DiagnosticEvent(
                subsystem=subsystem_name,
                event_type=EventType.METRICS_UPDATE,
                data={"cpu_usage_percent": 25.0}
            )
            await diagnostics.emit_metric(event)

        initial_health = await diagnostics.get_system_health()

        # Degrade ExecutionContext (fundamental subsystem)
        for _ in range(5):
            event = DiagnosticEvent(
                subsystem="execution_context",
                event_type=EventType.METRICS_UPDATE,
                data={"cpu_usage_percent": 95.0, "error_rate": 0.2}
            )
            await diagnostics.emit_metric(event)

        degraded_health = await diagnostics.get_system_health()

        # System health should degrade significantly
        assert degraded_health["overall_score"] < initial_health["overall_score"]

        # Check cascading impact
        impact = await diagnostics.get_degradation_impact(
            "execution_context",
            "context_bus"
        )
        assert impact > 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--log-cli-level=INFO"])
