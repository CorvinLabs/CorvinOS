"""Tests for plugin subprocess isolation and lifecycle management (ADR-0426)."""

import asyncio
import json
import pytest
import signal
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from core.modularization import (
    PluginProcessState,
    PluginManifest,
    PluginProcessInfo,
    ProcessResourceLimits,
    PluginProcessManager,
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


# ─── State Lifecycle Tests (10 tests) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_state(process_manager):
    """Test that process manager starts in STOPPED state."""
    state = process_manager.get_state()
    assert state.state == PluginProcessState.STOPPED
    assert state.pid is None
    assert state.restart_count == 0


@pytest.mark.asyncio
async def test_cannot_start_twice(process_manager):
    """Test that starting an already-started plugin raises error."""
    with patch("subprocess.Popen") as mock_popen:
        mock_popen.return_value = MagicMock(pid=12345)
        process_manager._process = MagicMock()
        process_manager._state = PluginProcessState.HEALTHY

        with pytest.raises(RuntimeError, match="Cannot start plugin"):
            await process_manager.start()


@pytest.mark.asyncio
async def test_start_sets_healthy_state(process_manager):
    """Test that starting plugin transitions to HEALTHY state."""
    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        process_manager.audit_logger = AsyncMock()

        await process_manager.start()

        assert process_manager._state == PluginProcessState.HEALTHY
        assert process_manager._process is not None
        assert process_manager._process.pid == 12345


@pytest.mark.asyncio
async def test_start_logs_audit_event(process_manager):
    """Test that starting plugin logs audit events."""
    with patch("subprocess.Popen") as mock_popen:
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        audit_logger = AsyncMock()
        process_manager.audit_logger = audit_logger

        await process_manager.start()

        # Verify audit logger called for start_requested and start_completed
        calls = [call[0][0] for call in audit_logger.call_args_list]
        assert "plugin.start_requested" in calls
        assert "plugin.start_completed" in calls


@pytest.mark.asyncio
async def test_start_failure_logs_error(process_manager):
    """Test that start failure logs audit error event."""
    with patch("subprocess.Popen", side_effect=OSError("Cannot spawn process")):
        audit_logger = AsyncMock()
        process_manager.audit_logger = audit_logger

        with pytest.raises(RuntimeError, match="Failed to start plugin"):
            await process_manager.start()

        # Verify error audit event was logged
        calls = [call[0][0] for call in audit_logger.call_args_list]
        assert "plugin.start_failed" in calls


@pytest.mark.asyncio
async def test_stop_graceful_sends_sigterm(process_manager):
    """Test that graceful stop sends SIGTERM first."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]  # Not running, then exited
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.audit_logger = AsyncMock()

    await process_manager.stop(graceful=True, timeout_sec=1)

    assert process_manager._state == PluginProcessState.STOPPED
    mock_process.send_signal.assert_called_once_with(signal.SIGTERM)


@pytest.mark.asyncio
async def test_stop_forceful_sends_sigkill(process_manager):
    """Test that forceful stop sends SIGKILL immediately."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Still running
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.audit_logger = AsyncMock()

    await process_manager.stop(graceful=False, timeout_sec=1)

    assert process_manager._state == PluginProcessState.STOPPED
    mock_process.send_signal.assert_called_once_with(signal.SIGKILL)


@pytest.mark.asyncio
async def test_stop_already_stopped_is_noop(process_manager):
    """Test that stopping an already-stopped plugin is a no-op."""
    process_manager._state = PluginProcessState.STOPPED
    process_manager.audit_logger = AsyncMock()

    await process_manager.stop()

    process_manager.audit_logger.assert_not_called()


@pytest.mark.asyncio
async def test_stop_logs_audit_events(process_manager):
    """Test that stopping plugin logs audit events."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    audit_logger = AsyncMock()
    process_manager.audit_logger = audit_logger

    await process_manager.stop(graceful=True, timeout_sec=1)

    calls = [call[0][0] for call in audit_logger.call_args_list]
    assert "plugin.stop_requested" in calls
    assert "plugin.stop_completed" in calls


# ─── Restart Tests (5 tests) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_restart_stops_then_starts(process_manager):
    """Test that restart performs graceful stop then start."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]  # For stop
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    audit_logger = AsyncMock()
    process_manager.audit_logger = audit_logger

    with patch("subprocess.Popen") as mock_popen:
        mock_new_process = MagicMock()
        mock_new_process.pid = 12346
        mock_popen.return_value = mock_new_process

        await process_manager.restart(reason="test restart")

        assert process_manager._restart_count == 1
        assert process_manager._state == PluginProcessState.HEALTHY


@pytest.mark.asyncio
async def test_restart_exceeds_max_limit(process_manager):
    """Test that exceeding max restarts raises error."""
    process_manager._restart_count = 3
    process_manager.limits.max_restarts = 3
    process_manager.audit_logger = AsyncMock()

    with pytest.raises(RuntimeError, match="exceeded max restarts"):
        await process_manager.restart()


@pytest.mark.asyncio
async def test_restart_increments_counter(process_manager):
    """Test that restart increments restart counter."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.audit_logger = AsyncMock()

    with patch("subprocess.Popen") as mock_popen:
        mock_new_process = MagicMock()
        mock_new_process.pid = 12346
        mock_popen.return_value = mock_new_process

        await process_manager.restart()
        assert process_manager._restart_count == 1

        await process_manager.restart()
        assert process_manager._restart_count == 2


@pytest.mark.asyncio
async def test_restart_logs_audit_events(process_manager):
    """Test that restart logs appropriate audit events."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    audit_logger = AsyncMock()
    process_manager.audit_logger = audit_logger

    with patch("subprocess.Popen") as mock_popen:
        mock_new_process = MagicMock()
        mock_new_process.pid = 12346
        mock_popen.return_value = mock_new_process

        await process_manager.restart(reason="health check failed")

        # Should log restart_requested
        calls = [call[0][0] for call in audit_logger.call_args_list]
        assert "plugin.restart_requested" in calls


@pytest.mark.asyncio
async def test_restart_respects_cooldown(process_manager):
    """Test that restart respects cooldown period."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.side_effect = [None, 0]
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.limits.restart_cooldown_sec = 0.1  # Short for testing
    process_manager.audit_logger = AsyncMock()

    import time
    with patch("subprocess.Popen") as mock_popen:
        mock_new_process = MagicMock()
        mock_new_process.pid = 12346
        mock_popen.return_value = mock_new_process

        start_time = time.time()
        await process_manager.restart()
        elapsed = time.time() - start_time

        # Should have slept at least the cooldown period
        assert elapsed >= process_manager.limits.restart_cooldown_sec


# ─── Version Discovery Tests (5 tests) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_version_manifest_success(process_manager):
    """Test successful version manifest retrieval."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None
    mock_process.stdin = MagicMock()
    mock_process.stdout = MagicMock()

    # Prepare manifest response
    manifest_json = json.dumps({
        "plugin_id": "test-plugin",
        "version": "1.0.0",
        "api_version": "2.0",
        "origin": "community",
        "boot_layer": "installed",
    })

    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.audit_logger = AsyncMock()

    with patch.object(process_manager, "_read_subprocess_line", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = manifest_json

        manifest = await process_manager.get_version_manifest()

        assert manifest.plugin_id == "test-plugin"
        assert manifest.version == "1.0.0"


@pytest.mark.asyncio
async def test_get_version_manifest_version_mismatch(process_manager):
    """Test that version mismatch raises error (fail loud)."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None
    mock_process.stdin = MagicMock()

    # Prepare mismatched manifest response
    manifest_json = json.dumps({
        "plugin_id": "test-plugin",
        "version": "2.0.0",  # Different from expected 1.0.0
        "api_version": "2.0",
        "origin": "community",
        "boot_layer": "installed",
    })

    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.audit_logger = AsyncMock()

    with patch.object(process_manager, "_read_subprocess_line", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = manifest_json

        with pytest.raises(RuntimeError, match="version mismatch"):
            await process_manager.get_version_manifest()


@pytest.mark.asyncio
async def test_get_version_manifest_timeout(process_manager):
    """Test that version query timeout raises error."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None

    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager.audit_logger = AsyncMock()

    with patch.object(process_manager, "_read_subprocess_line", new_callable=AsyncMock) as mock_read:
        mock_read.side_effect = asyncio.TimeoutError()

        with pytest.raises(RuntimeError, match="Timeout"):
            await process_manager.get_version_manifest()


@pytest.mark.asyncio
async def test_get_version_manifest_not_healthy(process_manager):
    """Test that querying version on unhealthy plugin raises error."""
    process_manager._state = PluginProcessState.CRASHED

    with pytest.raises(RuntimeError, match="Cannot query version"):
        await process_manager.get_version_manifest()


@pytest.mark.asyncio
async def test_get_version_manifest_no_process(process_manager):
    """Test that querying version without running process raises error."""
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._process = None

    with pytest.raises(RuntimeError, match="not running"):
        await process_manager.get_version_manifest()


# ─── IPC Communication Tests (2 tests) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_ipc_message_success(process_manager):
    """Test successful IPC message send/receive."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.stdin = MagicMock()

    response_json = json.dumps({"status": "ok", "result": "test-data"})

    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY

    with patch.object(process_manager, "_read_subprocess_line", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = response_json

        result = await process_manager.send_ipc_message("test_method", {"param": "value"})

        assert result["status"] == "ok"
        assert result["result"] == "test-data"


@pytest.mark.asyncio
async def test_send_ipc_message_unhealthy_fails(process_manager):
    """Test that IPC on unhealthy plugin raises error."""
    process_manager._state = PluginProcessState.UNHEALTHY

    with pytest.raises(RuntimeError, match="Cannot send IPC"):
        await process_manager.send_ipc_message("test_method", {})


# ─── State Snapshot Tests (1 test) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_state_snapshot(process_manager):
    """Test that get_state returns complete process info snapshot."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    process_manager._process = mock_process
    process_manager._state = PluginProcessState.HEALTHY
    process_manager._restart_count = 2

    state = process_manager.get_state()

    assert state.plugin_id == "test-plugin"
    assert state.state == PluginProcessState.HEALTHY
    assert state.pid == 12345
    assert state.restart_count == 2
    assert state.timestamp is not None
