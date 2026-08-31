"""Tests for plugin health monitoring and auto-restart orchestration (ADR-0426)."""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from core.modularization import (
    PluginManifest,
    ProcessResourceLimits,
    PluginProcessManager,
    PluginProcessState,
    HealthCheckState,
    HealthProbe,
    HealthCheckConfig,
    HealthCheckRegistry,
    PluginHealthMonitor,
)


@pytest.fixture
def sample_manifest() -> PluginManifest:
    """Create a sample plugin manifest for testing."""
    return PluginManifest(
        plugin_id="test-plugin",
        version="1.0.0",
        api_version="2.0",
        origin="community",
        boot_layer="installed",
        supports_isolation=True,
        requires_ipc=True,
    )


@pytest.fixture
def resource_limits() -> ProcessResourceLimits:
    """Create resource limits for testing."""
    return ProcessResourceLimits(
        memory_mb=512,
        cpu_limit=1.0,
        timeout_sec=5,
        max_restarts=3,
        restart_cooldown_sec=1,
    )


@pytest.fixture
def process_manager(sample_manifest, resource_limits) -> PluginProcessManager:
    """Create a plugin process manager for testing."""
    return PluginProcessManager(
        plugin_id="test-plugin",
        command=["python", "-m", "test_plugin"],
        manifest=sample_manifest,
        limits=resource_limits,
        ipc_socket_path="/tmp/test_plugin.sock",
    )


@pytest.fixture
def health_config() -> HealthCheckConfig:
    """Create health check configuration for testing."""
    return HealthCheckConfig(
        enabled=True,
        interval_sec=1,  # Short for testing
        timeout_sec=2,
        consecutive_failures_threshold=2,
        degraded_threshold_ms=100,
    )


@pytest.fixture
def health_monitor(process_manager, health_config) -> PluginHealthMonitor:
    """Create a health monitor for testing."""
    return PluginHealthMonitor(
        process_manager=process_manager,
        config=health_config,
    )


# ─── Health Probe Tests (8 tests) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_healthy_response(health_monitor, process_manager):
    """Test that successful probe returns HEALTHY state."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_manifest = PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )
        mock_get.return_value = mock_manifest

        probe = await health_monitor.probe()

        assert probe.state == HealthCheckState.HEALTHY
        assert probe.error_message is None
        assert probe.response_time_ms >= 0


@pytest.mark.asyncio
async def test_probe_degraded_on_slow_response(health_monitor, process_manager):
    """Test that slow response (>5s default) returns DEGRADED state."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    async def slow_manifest():
        await asyncio.sleep(0.150)  # 150ms sleep
        return PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = slow_manifest

        probe = await health_monitor.probe()

        assert probe.state == HealthCheckState.DEGRADED
        assert probe.response_time_ms > 100


@pytest.mark.asyncio
async def test_probe_unhealthy_on_timeout(health_monitor, process_manager):
    """Test that timeout returns UNHEALTHY state."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = asyncio.TimeoutError()

        probe = await health_monitor.probe()

        assert probe.state == HealthCheckState.UNHEALTHY
        assert "timeout" in probe.error_message.lower()


@pytest.mark.asyncio
async def test_probe_unhealthy_on_exception(health_monitor, process_manager):
    """Test that exception returns UNHEALTHY state."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = RuntimeError("Connection refused")

        probe = await health_monitor.probe()

        assert probe.state == HealthCheckState.UNHEALTHY
        assert "Connection refused" in probe.error_message


@pytest.mark.asyncio
async def test_probe_logs_audit_event_on_timeout(health_monitor, process_manager):
    """Test that probe timeout logs audit event."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()
    audit_logger = AsyncMock()
    health_monitor.audit_logger = audit_logger

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = asyncio.TimeoutError()

        await health_monitor.probe()

        audit_logger.assert_called()
        call_args = audit_logger.call_args
        assert call_args[0][0] == "health_probe.timeout"


@pytest.mark.asyncio
async def test_probe_logs_audit_event_on_failure(health_monitor, process_manager):
    """Test that probe failure logs audit event."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()
    audit_logger = AsyncMock()
    health_monitor.audit_logger = audit_logger

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = RuntimeError("Test error")

        await health_monitor.probe()

        audit_logger.assert_called()
        call_args = audit_logger.call_args
        assert call_args[0][0] == "health_probe.failed"


@pytest.mark.asyncio
async def test_probe_records_in_history(health_monitor, process_manager):
    """Test that probe results are recorded in history."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_manifest = PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )
        mock_get.return_value = mock_manifest

        await health_monitor.probe()
        await health_monitor.probe()

        assert len(health_monitor._registry.probe_history) == 2


@pytest.mark.asyncio
async def test_probe_keeps_last_100_history(health_monitor, process_manager):
    """Test that probe history is capped at 100 entries."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_manifest = PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )
        mock_get.return_value = mock_manifest

        # Add 150 probes
        for _ in range(150):
            await health_monitor.probe()

        # Should only keep last 100
        assert len(health_monitor._registry.probe_history) <= 100


# ─── State Machine Tests (6 tests) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_machine_healthy_clears_failures(health_monitor, process_manager):
    """Test that HEALTHY probe resets consecutive_failures counter."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()
    health_monitor._registry.consecutive_failures = 2

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_manifest = PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )
        mock_get.return_value = mock_manifest

        await health_monitor.probe()

        assert health_monitor._registry.consecutive_failures == 0
        assert health_monitor._registry.current_state == HealthCheckState.HEALTHY


@pytest.mark.asyncio
async def test_state_machine_degraded_clears_failures(health_monitor, process_manager):
    """Test that DEGRADED probe resets consecutive_failures counter."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()
    health_monitor._registry.consecutive_failures = 2

    async def slow_manifest():
        await asyncio.sleep(0.200)
        return PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = slow_manifest

        await health_monitor.probe()

        assert health_monitor._registry.consecutive_failures == 0
        assert health_monitor._registry.current_state == HealthCheckState.DEGRADED


@pytest.mark.asyncio
async def test_state_machine_unhealthy_accumulates_failures(health_monitor, process_manager):
    """Test that consecutive UNHEALTHY probes accumulate failures."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = RuntimeError("Test error")

        await health_monitor.probe()
        assert health_monitor._registry.consecutive_failures == 1
        assert health_monitor._registry.current_state == HealthCheckState.UNKNOWN

        await health_monitor.probe()
        assert health_monitor._registry.consecutive_failures == 2
        assert health_monitor._registry.current_state == HealthCheckState.UNHEALTHY


@pytest.mark.asyncio
async def test_is_restart_needed_on_threshold(health_monitor):
    """Test that restart is needed when threshold is exceeded."""
    health_monitor._registry.consecutive_failures = 2
    health_monitor._registry.current_state = HealthCheckState.UNHEALTHY

    assert health_monitor._registry.is_restart_needed() is True


@pytest.mark.asyncio
async def test_is_restart_not_needed_below_threshold(health_monitor):
    """Test that restart is not needed below threshold."""
    health_monitor._registry.consecutive_failures = 1
    health_monitor._registry.current_state = HealthCheckState.UNKNOWN

    assert health_monitor._registry.is_restart_needed() is False


@pytest.mark.asyncio
async def test_is_restart_not_needed_when_healthy(health_monitor):
    """Test that restart is not needed when healthy."""
    health_monitor._registry.consecutive_failures = 0
    health_monitor._registry.current_state = HealthCheckState.HEALTHY

    assert health_monitor._registry.is_restart_needed() is False


# ─── Monitor Lifecycle Tests (4 tests) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_monitor_start_creates_task(health_monitor):
    """Test that starting monitor creates background task."""
    await health_monitor.start()

    assert health_monitor._running is True
    assert health_monitor._monitor_task is not None

    await health_monitor.stop()


@pytest.mark.asyncio
async def test_monitor_start_when_disabled(health_monitor):
    """Test that starting monitor when disabled is logged."""
    health_monitor.config.enabled = False

    await health_monitor.start()

    assert health_monitor._running is False
    assert health_monitor._monitor_task is None


@pytest.mark.asyncio
async def test_monitor_stop_cancels_task(health_monitor):
    """Test that stopping monitor cancels background task."""
    await health_monitor.start()
    await asyncio.sleep(0.1)

    await health_monitor.stop()

    assert health_monitor._running is False
    assert health_monitor._monitor_task.done() or health_monitor._monitor_task.cancelled()


@pytest.mark.asyncio
async def test_monitor_stop_when_not_running(health_monitor):
    """Test that stopping a non-running monitor is a no-op."""
    health_monitor._running = False

    await health_monitor.stop()

    assert health_monitor._running is False


# ─── Restart Orchestration Tests (4 tests) ───────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrate_restart_success(health_monitor, process_manager):
    """Test successful restart orchestration."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()
    process_manager._process.poll.side_effect = [None, 0]
    process_manager.audit_logger = AsyncMock()
    health_monitor.audit_logger = AsyncMock()
    health_monitor._registry.consecutive_failures = 2

    with patch.object(process_manager, "stop", new_callable=AsyncMock):
        with patch.object(process_manager, "start", new_callable=AsyncMock):
            await health_monitor._orchestrate_restart(reason="test restart")

    assert health_monitor._registry.restart_count == 1
    assert health_monitor._registry.consecutive_failures == 0
    assert health_monitor._registry.current_state == HealthCheckState.UNKNOWN


@pytest.mark.asyncio
async def test_orchestrate_restart_logs_initiated(health_monitor, process_manager):
    """Test that restart logs initiation event."""
    audit_logger = AsyncMock()
    health_monitor.audit_logger = audit_logger

    with patch.object(process_manager, "restart", new_callable=AsyncMock):
        await health_monitor._orchestrate_restart(reason="health failed")

    calls = [call[0][0] for call in audit_logger.call_args_list]
    assert "plugin_restart.initiated" in calls


@pytest.mark.asyncio
async def test_orchestrate_restart_logs_completed(health_monitor, process_manager):
    """Test that restart logs completion event."""
    audit_logger = AsyncMock()
    health_monitor.audit_logger = audit_logger

    with patch.object(process_manager, "restart", new_callable=AsyncMock):
        await health_monitor._orchestrate_restart()

    calls = [call[0][0] for call in audit_logger.call_args_list]
    assert "plugin_restart.completed" in calls


@pytest.mark.asyncio
async def test_orchestrate_restart_logs_failure(health_monitor, process_manager):
    """Test that restart failure logs error event."""
    audit_logger = AsyncMock()
    health_monitor.audit_logger = audit_logger

    with patch.object(process_manager, "restart", new_callable=AsyncMock) as mock_restart:
        mock_restart.side_effect = RuntimeError("Restart failed")

        await health_monitor._orchestrate_restart()

    calls = [call[0][0] for call in audit_logger.call_args_list]
    assert "plugin_restart.failed" in calls


# ─── Health State Retrieval Tests (1 test) ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_health_state_snapshot(health_monitor, process_manager):
    """Test that get_health_state returns complete registry snapshot."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = MagicMock()

    with patch.object(process_manager, "get_version_manifest", new_callable=AsyncMock) as mock_get:
        mock_manifest = PluginManifest(
            plugin_id="test-plugin",
            version="1.0.0",
            api_version="2.0",
            origin="community",
            boot_layer="installed",
        )
        mock_get.return_value = mock_manifest

        await health_monitor.probe()

    state = await health_monitor.get_health_state()

    assert state.plugin_id == "test-plugin"
    assert state.current_state == HealthCheckState.HEALTHY
    assert state.last_probe is not None
    assert len(state.probe_history) == 1
