"""Tests for health check engine (ADR-0327)."""

import asyncio
import pytest

from core.modules.health_check import (
    HealthState,
    ModuleHealthReport,
    SystemHealthReport,
    HealthCheckEngine,
)


class TestHealthCheckEngine:
    """Tests for HealthCheckEngine."""

    def test_register_probe_adds_probe_function(self):
        """register_probe adds probe function."""
        engine = HealthCheckEngine()

        def healthy_probe():
            return True

        engine.register_probe("module1", healthy_probe)
        assert "module1" in engine._probes

    def test_register_probe_validates_module_id(self):
        """register_probe validates module_id."""
        engine = HealthCheckEngine()

        def probe_fn():
            return True

        with pytest.raises(ValueError, match="Invalid module_id"):
            engine.register_probe("", probe_fn)

    def test_register_probe_validates_callable(self):
        """register_probe validates probe_fn is callable."""
        engine = HealthCheckEngine()

        with pytest.raises(ValueError, match="must be callable"):
            engine.register_probe("module1", "not callable")

    @pytest.mark.asyncio
    async def test_probe_module_health_returns_healthy_status(self):
        """probe_module_health returns HEALTHY for passing probe."""
        engine = HealthCheckEngine()

        def healthy_probe():
            return True

        engine.register_probe("module1", healthy_probe)
        report = await engine.probe_module_health("module1")

        assert report.state == HealthState.HEALTHY
        assert report.module_id == "module1"
        assert report.recoverable is True

    @pytest.mark.asyncio
    async def test_probe_module_health_returns_degraded_status(self):
        """probe_module_health returns DEGRADED if probe returns False."""
        engine = HealthCheckEngine()

        def degraded_probe():
            return False

        engine.register_probe("module1", degraded_probe)
        report = await engine.probe_module_health("module1")

        assert report.state == HealthState.DEGRADED

    @pytest.mark.asyncio
    async def test_probe_module_health_timeout_handling(self):
        """probe_module_health handles timeout."""
        engine = HealthCheckEngine(probe_timeout_seconds=0.1)

        async def slow_probe():
            await asyncio.sleep(1.0)
            return True

        engine.register_probe("slow_module", slow_probe)
        report = await engine.probe_module_health("slow_module")

        assert report.state == HealthState.UNHEALTHY
        assert "timed out" in report.message

    @pytest.mark.asyncio
    async def test_probe_module_health_exception_handling(self):
        """probe_module_health handles exceptions."""
        engine = HealthCheckEngine()

        def error_probe():
            raise RuntimeError("Probe failed")

        engine.register_probe("error_module", error_probe)
        report = await engine.probe_module_health("error_module")

        assert report.state == HealthState.UNHEALTHY
        assert "RuntimeError" in report.message

    @pytest.mark.asyncio
    async def test_probe_module_health_unregistered_raises_error(self):
        """probe_module_health raises for unregistered module."""
        engine = HealthCheckEngine()

        with pytest.raises(ValueError, match="not registered"):
            await engine.probe_module_health("unregistered")

    @pytest.mark.asyncio
    async def test_aggregate_health_state_computes_system_level(self):
        """aggregate_health_state computes overall system health."""
        engine = HealthCheckEngine()

        def healthy_probe():
            return True

        def degraded_probe():
            return False

        engine.register_probe("healthy_module", healthy_probe)
        engine.register_probe("degraded_module", degraded_probe)

        report = await engine.aggregate_health_state()

        assert report.overall_state == HealthState.DEGRADED
        assert report.healthy_count == 1
        assert report.degraded_count == 1

    @pytest.mark.asyncio
    async def test_aggregate_health_state_unhealthy_takes_precedence(self):
        """Unhealthy modules make system unhealthy."""
        engine = HealthCheckEngine()

        def healthy_probe():
            return True

        def error_probe():
            raise RuntimeError("Error")

        engine.register_probe("healthy", healthy_probe)
        engine.register_probe("error", error_probe)

        report = await engine.aggregate_health_state()

        assert report.overall_state == HealthState.UNHEALTHY

    @pytest.mark.asyncio
    async def test_aggregate_health_state_all_healthy(self):
        """All healthy modules result in healthy system."""
        engine = HealthCheckEngine()

        for i in range(3):
            engine.register_probe(f"module{i}", lambda: True)

        report = await engine.aggregate_health_state()

        assert report.overall_state == HealthState.HEALTHY
        assert report.healthy_count == 3

    @pytest.mark.asyncio
    async def test_aggregate_health_state_no_probes_raises_error(self):
        """aggregate_health_state raises if no probes registered."""
        engine = HealthCheckEngine()

        with pytest.raises(ValueError, match="No health probes"):
            await engine.aggregate_health_state()

    def test_get_unhealthy_modules_returns_degraded_list(self):
        """get_unhealthy_modules returns list of degraded modules."""
        engine = HealthCheckEngine()

        # Manually add reports (for testing)
        engine._last_reports["degraded1"] = ModuleHealthReport(
            module_id="degraded1",
            state=HealthState.DEGRADED,
            last_probe_time=asyncio.get_event_loop().time(),
            probe_duration_ms=10.0,
            message="degraded",
        )
        engine._last_reports["healthy"] = ModuleHealthReport(
            module_id="healthy",
            state=HealthState.HEALTHY,
            last_probe_time=asyncio.get_event_loop().time(),
            probe_duration_ms=5.0,
            message="healthy",
        )

        unhealthy = engine.get_unhealthy_modules()
        assert "degraded1" in unhealthy
        assert "healthy" not in unhealthy

    def test_reset_for_testing_clears_state(self):
        """reset_for_testing clears engine state."""
        engine = HealthCheckEngine()
        engine.register_probe("module1", lambda: True)
        engine._last_reports["module1"] = ModuleHealthReport(
            module_id="module1",
            state=HealthState.HEALTHY,
            last_probe_time=asyncio.get_event_loop().time(),
            probe_duration_ms=5.0,
            message="healthy",
        )

        engine.reset_for_testing()

        assert len(engine._probes) == 0
        assert len(engine._last_reports) == 0

    def test_module_health_report_to_audit_event(self):
        """ModuleHealthReport converts to audit event."""
        from datetime import datetime

        report = ModuleHealthReport(
            module_id="test_module",
            state=HealthState.HEALTHY,
            last_probe_time=datetime(2026, 8, 14, 12, 0, 0),
            probe_duration_ms=10.0,
            message="All systems operational",
        )
        event = report.to_audit_event()

        assert event["event_type"] == "health.module_status"
        assert event["module_id"] == "test_module"
        assert event["state"] == "healthy"

    def test_system_health_report_to_audit_event(self):
        """SystemHealthReport converts to audit event."""
        from datetime import datetime

        report = SystemHealthReport(
            overall_state=HealthState.HEALTHY,
            timestamp=datetime(2026, 8, 14, 12, 0, 0),
            module_reports=[],
            healthy_count=3,
            degraded_count=0,
            unhealthy_count=0,
        )
        event = report.to_audit_event()

        assert event["event_type"] == "health.system_status"
        assert event["overall_state"] == "healthy"
        assert event["healthy"] == 3

    @pytest.mark.asyncio
    async def test_probe_handles_async_probe_function(self):
        """probe_module_health handles async probe functions."""
        engine = HealthCheckEngine()

        async def async_probe():
            await asyncio.sleep(0.01)
            return True

        engine.register_probe("async_module", async_probe)
        report = await engine.probe_module_health("async_module")

        assert report.state == HealthState.HEALTHY

    @pytest.mark.asyncio
    async def test_concurrent_probes_isolation(self):
        """Multiple concurrent probes run in isolation."""
        engine = HealthCheckEngine()

        call_order = []

        def probe1():
            call_order.append(1)
            return True

        def probe2():
            call_order.append(2)
            return True

        def probe3():
            call_order.append(3)
            return True

        engine.register_probe("module1", probe1)
        engine.register_probe("module2", probe2)
        engine.register_probe("module3", probe3)

        report = await engine.aggregate_health_state()

        assert len(call_order) == 3
        assert report.healthy_count == 3
