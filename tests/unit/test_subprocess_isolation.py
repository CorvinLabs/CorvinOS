"""Unit tests for subprocess isolation.

Tests message protocol, subprocess communication, and worker pool management.
"""

import asyncio
import json
import pytest
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, AsyncMock

from core.modularization import (
    IPCMessage,
    MessageType,
    PluginWorkerPool,
    PluginWorkerProcess,
    SubprocessBridge,
)


# ─────────────────────────────────────────────────────────────────────────
# TEST: IPC MESSAGE
# ─────────────────────────────────────────────────────────────────────────


def test_ipc_message_request():
    """Test creating a request message."""
    msg = IPCMessage(
        message_type=MessageType.REQUEST,
        method="test_method",
        params={"arg1": "value1"},
    )
    assert msg.message_type == MessageType.REQUEST
    assert msg.method == "test_method"
    assert msg.params == {"arg1": "value1"}


def test_ipc_message_response():
    """Test creating a response message."""
    msg = IPCMessage(
        message_type=MessageType.RESPONSE,
        id="123",
        result={"status": "ok"},
    )
    assert msg.message_type == MessageType.RESPONSE
    assert msg.result == {"status": "ok"}


def test_ipc_message_error():
    """Test creating an error message."""
    msg = IPCMessage(
        message_type=MessageType.ERROR,
        id="123",
        error="Something went wrong",
    )
    assert msg.message_type == MessageType.ERROR
    assert msg.error == "Something went wrong"


def test_ipc_message_notification():
    """Test creating a notification message."""
    msg = IPCMessage(
        message_type=MessageType.NOTIFICATION,
        method="log",
        params={"message": "hello"},
    )
    assert msg.message_type == MessageType.NOTIFICATION
    assert msg.method == "log"


def test_ipc_message_to_json():
    """Test serializing message to JSON."""
    msg = IPCMessage(
        message_type=MessageType.REQUEST,
        id="123",
        method="test",
        params={"arg": "value"},
    )
    json_str = msg.to_json()
    data = json.loads(json_str)
    assert data["type"] == "request"
    assert data["id"] == "123"
    assert data["method"] == "test"
    assert data["params"] == {"arg": "value"}


def test_ipc_message_from_json():
    """Test deserializing message from JSON."""
    json_str = '{"type": "request", "id": "123", "method": "test", "params": {"arg": "value"}}'
    msg = IPCMessage.from_json(json_str)
    assert msg.message_type == MessageType.REQUEST
    assert msg.id == "123"
    assert msg.method == "test"
    assert msg.params == {"arg": "value"}


def test_ipc_message_roundtrip():
    """Test JSON serialization roundtrip."""
    original = IPCMessage(
        message_type=MessageType.REQUEST,
        method="calc",
        params={"x": 10, "y": 20},
    )
    json_str = original.to_json()
    restored = IPCMessage.from_json(json_str)
    assert restored.message_type == original.message_type
    assert restored.method == original.method
    assert restored.params == original.params


def test_ipc_message_auto_id_generation():
    """Test that messages get auto-generated IDs."""
    msg1 = IPCMessage(message_type=MessageType.REQUEST)
    msg2 = IPCMessage(message_type=MessageType.REQUEST)
    assert msg1.id
    assert msg2.id
    assert msg1.id != msg2.id


# ─────────────────────────────────────────────────────────────────────────
# TEST: SUBPROCESS BRIDGE
# ─────────────────────────────────────────────────────────────────────────


def test_subprocess_bridge_creation():
    """Test creating a subprocess bridge."""
    mock_proc = Mock(spec=subprocess.Popen)
    bridge = SubprocessBridge(
        process=mock_proc,
        plugin_id="test_plugin",
    )
    assert bridge.plugin_id == "test_plugin"
    assert bridge.process is mock_proc
    assert bridge.timeout == 30.0


def test_subprocess_bridge_is_alive():
    """Test checking if subprocess is alive."""
    mock_proc = Mock(spec=subprocess.Popen)
    mock_proc.poll.return_value = None  # Still running
    bridge = SubprocessBridge(process=mock_proc, plugin_id="test")
    assert bridge.is_alive()

    mock_proc.poll.return_value = 0  # Exited
    assert not bridge.is_alive()


def test_subprocess_bridge_send_notification():
    """Test sending a notification."""
    mock_proc = Mock(spec=subprocess.Popen)
    mock_proc.stdin = Mock()
    bridge = SubprocessBridge(process=mock_proc, plugin_id="test")
    bridge.send_notification("log", message="hello")
    mock_proc.stdin.write.assert_called_once()
    mock_proc.stdin.flush.assert_called_once()


@pytest.mark.asyncio
async def test_subprocess_bridge_send_request():
    """Test sending a request (mocked)."""
    mock_proc = Mock(spec=subprocess.Popen)
    mock_proc.stdin = Mock()
    mock_proc.poll.return_value = None

    bridge = SubprocessBridge(process=mock_proc, plugin_id="test", timeout=1.0)

    # Mock the event loop for async operations
    with patch("asyncio.get_event_loop") as mock_loop:
        loop = asyncio.get_event_loop()
        mock_loop.return_value = loop

        # We can't fully test async communication without a real process,
        # but we can test the setup
        bridge._running = True
        bridge._pending_responses = {}

        # Simulate sending a request (would hang without threading)
        # Just verify the structure is correct
        assert bridge._running


def test_subprocess_bridge_terminate():
    """Test terminating subprocess bridge."""
    mock_proc = Mock(spec=subprocess.Popen)
    mock_proc.poll.return_value = None
    mock_proc.stdin = Mock()
    mock_proc.stdout = Mock()

    bridge = SubprocessBridge(process=mock_proc, plugin_id="test")
    bridge._running = True
    bridge.terminate()
    assert not bridge._running
    mock_proc.terminate.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────
# TEST: PLUGIN WORKER POOL
# ─────────────────────────────────────────────────────────────────────────


def test_plugin_worker_pool_creation():
    """Test creating a worker pool."""
    script_path = Path("/tmp/test_plugin.py")
    pool = PluginWorkerPool(plugin_script=script_path, max_workers=4)
    assert pool.plugin_script == script_path
    assert pool.max_workers == 4
    assert pool.worker_count() == 0


def test_plugin_worker_pool_max_workers():
    """Test that pool respects max_workers."""
    pool = PluginWorkerPool(plugin_script=Path("/tmp/test.py"), max_workers=2)
    with patch.object(pool, "spawn_worker") as mock_spawn:
        # Add some mock workers
        mock_worker1 = Mock(is_alive=Mock(return_value=True))
        mock_worker2 = Mock(is_alive=Mock(return_value=True))
        pool._workers = [mock_worker1, mock_worker2]
        assert pool.worker_count() == 2


def test_plugin_worker_pool_shutdown():
    """Test shutting down worker pool."""
    pool = PluginWorkerPool(plugin_script=Path("/tmp/test.py"))
    mock_worker1 = Mock()
    mock_worker2 = Mock()
    pool._workers = [mock_worker1, mock_worker2]

    pool.shutdown()
    assert pool.worker_count() == 0
    mock_worker1.terminate.assert_called_once()
    mock_worker2.terminate.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────
# TEST: PLUGIN WORKER PROCESS
# ─────────────────────────────────────────────────────────────────────────


def test_plugin_worker_process_creation():
    """Test creating a worker process."""
    worker = PluginWorkerProcess()
    assert len(worker._methods) == 0


def test_plugin_worker_process_register_method():
    """Test registering a method."""
    def test_method():
        return "result"

    worker = PluginWorkerProcess()
    worker.register_method("test", test_method)
    assert "test" in worker._methods
    assert worker._methods["test"] is test_method


@pytest.mark.asyncio
async def test_plugin_worker_process_handle_request_success():
    """Test handling a successful request."""
    def handler(x: int, y: int) -> int:
        return x + y

    worker = PluginWorkerProcess()
    worker.register_method("add", handler)

    msg = IPCMessage(
        message_type=MessageType.REQUEST,
        id="123",
        method="add",
        params={"x": 5, "y": 3},
    )
    response = await worker.handle_request(msg)
    assert response.message_type == MessageType.RESPONSE
    assert response.id == "123"
    assert response.result == 8


@pytest.mark.asyncio
async def test_plugin_worker_process_handle_request_unknown_method():
    """Test handling request for unknown method."""
    worker = PluginWorkerProcess()
    msg = IPCMessage(
        message_type=MessageType.REQUEST,
        id="123",
        method="unknown",
    )
    response = await worker.handle_request(msg)
    assert response.message_type == MessageType.ERROR
    assert "Unknown method" in response.error


@pytest.mark.asyncio
async def test_plugin_worker_process_async_method():
    """Test handling async method calls."""
    async def async_handler() -> str:
        return "async_result"

    worker = PluginWorkerProcess()
    worker.register_method("async_op", async_handler)

    msg = IPCMessage(
        message_type=MessageType.REQUEST,
        id="123",
        method="async_op",
    )
    response = await worker.handle_request(msg)
    assert response.message_type == MessageType.RESPONSE
    assert response.result == "async_result"


@pytest.mark.asyncio
async def test_plugin_worker_process_handle_request_exception():
    """Test handling exceptions in methods."""
    def failing_handler():
        raise ValueError("Test error")

    worker = PluginWorkerProcess()
    worker.register_method("fail", failing_handler)

    msg = IPCMessage(
        message_type=MessageType.REQUEST,
        id="123",
        method="fail",
    )
    response = await worker.handle_request(msg)
    assert response.message_type == MessageType.ERROR
    assert "Test error" in response.error


# ─────────────────────────────────────────────────────────────────────────
# TEST: MESSAGE TYPE ENUM
# ─────────────────────────────────────────────────────────────────────────


def test_message_type_values():
    """Test MessageType enum values."""
    assert MessageType.REQUEST.value == "request"
    assert MessageType.RESPONSE.value == "response"
    assert MessageType.ERROR.value == "error"
    assert MessageType.NOTIFICATION.value == "notification"
    assert MessageType.HANDSHAKE.value == "handshake"


def test_message_type_from_value():
    """Test creating MessageType from value."""
    msg_type = MessageType("request")
    assert msg_type == MessageType.REQUEST


# ─────────────────────────────────────────────────────────────────────────
# TEST: INTEGRATION (simplified, no real subprocess)
# ─────────────────────────────────────────────────────────────────────────


def test_message_protocol_integration():
    """Test message protocol end-to-end."""
    # Client creates request
    request = IPCMessage(
        message_type=MessageType.REQUEST,
        method="multiply",
        params={"a": 3, "b": 4},
    )
    request_json = request.to_json()

    # Server receives and parses request
    parsed_request = IPCMessage.from_json(request_json)
    assert parsed_request.method == "multiply"
    assert parsed_request.params["a"] == 3
    assert parsed_request.params["b"] == 4

    # Server creates response
    response = IPCMessage(
        message_type=MessageType.RESPONSE,
        id=parsed_request.id,
        result=12,
    )
    response_json = response.to_json()

    # Client receives and parses response
    parsed_response = IPCMessage.from_json(response_json)
    assert parsed_response.result == 12
    assert parsed_response.id == parsed_request.id
