"""Subprocess isolation for plugins and workers.

Phase 4.5 Modularization: Light-weight subprocess management for external plugins.
Processes spawned on-demand, communication via JSON-RPC over stdout/stdin.
"""

import asyncio
import json
import logging
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# MESSAGE PROTOCOL (JSON-RPC 2.0 compatible)
# ─────────────────────────────────────────────────────────────────────────


class MessageType(Enum):
    """IPC message types."""
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"
    NOTIFICATION = "notification"
    HANDSHAKE = "handshake"


@dataclass(frozen=True)
class IPCMessage:
    """Typed IPC message for subprocess communication."""
    message_type: MessageType
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    method: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_json(self) -> str:
        """Serialize to JSON."""
        data = {
            "type": self.message_type.value,
            "id": self.id,
        }
        if self.method:
            data["method"] = self.method
        if self.params:
            data["params"] = self.params
        if self.result is not None:
            data["result"] = self.result
        if self.error:
            data["error"] = self.error
        return json.dumps(data)

    @classmethod
    def from_json(cls, json_str: str) -> "IPCMessage":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls(
            message_type=MessageType(data["type"]),
            id=data.get("id", str(uuid.uuid4())),
            method=data.get("method"),
            params=data.get("params", {}),
            result=data.get("result"),
            error=data.get("error"),
        )


# ─────────────────────────────────────────────────────────────────────────
# SUBPROCESS BRIDGE (communicates with worker process)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class SubprocessBridge:
    """Manages communication with a plugin subprocess."""

    process: subprocess.Popen
    plugin_id: str
    timeout: float = 30.0
    _pending_responses: Dict[str, asyncio.Future] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader_thread: Optional[threading.Thread] = None
    _running: bool = False

    def start(self):
        """Start reading from subprocess."""
        if self._running:
            return
        self._running = True
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name=f"SubprocessBridge-{self.plugin_id}",
        )
        self._reader_thread.start()
        logger.info(f"SubprocessBridge started for plugin {self.plugin_id}")

    def _read_loop(self):
        """Read messages from subprocess (runs in thread)."""
        try:
            while self._running and self.process.poll() is None:
                try:
                    line = self.process.stdout.readline()
                    if not line:
                        break
                    msg_json = line.decode("utf-8").strip()
                    if not msg_json:
                        continue
                    msg = IPCMessage.from_json(msg_json)
                    self._handle_message(msg)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode message: {e}")
                except Exception as e:
                    logger.error(f"Error reading from subprocess: {e}")
                    break
        finally:
            self._running = False

    def _handle_message(self, msg: IPCMessage):
        """Process incoming message."""
        with self._lock:
            if msg.id in self._pending_responses:
                future = self._pending_responses.pop(msg.id)
                if msg.message_type == MessageType.ERROR:
                    future.set_exception(RuntimeError(msg.error or "Unknown error"))
                else:
                    future.set_result(msg.result)

    async def send_request(self, method: str, **params) -> Any:
        """Send a request and wait for response."""
        if not self._running:
            raise RuntimeError(f"Bridge for {self.plugin_id} is not running")

        msg_id = str(uuid.uuid4())
        msg = IPCMessage(
            message_type=MessageType.REQUEST,
            id=msg_id,
            method=method,
            params=params,
        )

        # Create future for response
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        with self._lock:
            self._pending_responses[msg_id] = future

        # Send message
        try:
            self.process.stdin.write((msg.to_json() + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except Exception as e:
            with self._lock:
                self._pending_responses.pop(msg_id, None)
            raise RuntimeError(f"Failed to send to subprocess: {e}")

        # Wait for response with timeout
        try:
            result = await asyncio.wait_for(future, timeout=self.timeout)
            return result
        except asyncio.TimeoutError:
            with self._lock:
                self._pending_responses.pop(msg_id, None)
            raise RuntimeError(f"Request timeout after {self.timeout}s")

    def send_notification(self, method: str, **params):
        """Send a one-way notification."""
        msg = IPCMessage(
            message_type=MessageType.NOTIFICATION,
            method=method,
            params=params,
        )
        try:
            self.process.stdin.write((msg.to_json() + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def terminate(self):
        """Stop the bridge and terminate subprocess."""
        self._running = False
        try:
            self.process.terminate()
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        except Exception as e:
            logger.error(f"Error terminating subprocess: {e}")
        if self._reader_thread:
            self._reader_thread.join(timeout=2)

    def is_alive(self) -> bool:
        """Check if subprocess is still running."""
        return self.process.poll() is None


# ─────────────────────────────────────────────────────────────────────────
# PLUGIN WORKER POOL (manages plugin subprocess lifecycle)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class PluginWorkerPool:
    """Manages a pool of plugin worker processes."""

    plugin_script: Path  # Path to plugin entry point
    max_workers: int = 4
    timeout: float = 30.0
    _workers: List[SubprocessBridge] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def spawn_worker(self, plugin_id: str) -> SubprocessBridge:
        """Spawn a new worker process for a plugin."""
        with self._lock:
            # Check if we have a free worker
            for worker in self._workers:
                if not worker.is_alive():
                    self._workers.remove(worker)

            if len(self._workers) >= self.max_workers:
                # Reuse least-busy worker
                worker = min(self._workers, key=lambda w: len(w._pending_responses))
                return worker

            # Spawn new worker
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(self.plugin_script)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False,  # Binary mode
                )
                bridge = SubprocessBridge(
                    process=proc,
                    plugin_id=plugin_id,
                    timeout=self.timeout,
                )
                bridge.start()
                self._workers.append(bridge)
                logger.info(
                    f"Spawned worker for plugin {plugin_id} "
                    f"(total: {len(self._workers)})"
                )
                return bridge
            except Exception as e:
                logger.error(f"Failed to spawn worker: {e}")
                raise

    async def call_plugin(self, plugin_id: str, method: str, **kwargs) -> Any:
        """Call a method on a plugin (spawns worker if needed)."""
        worker = self.spawn_worker(plugin_id)
        return await worker.send_request(method, **kwargs)

    def shutdown(self):
        """Shut down all workers."""
        with self._lock:
            for worker in self._workers:
                try:
                    worker.terminate()
                except Exception as e:
                    logger.error(f"Error shutting down worker: {e}")
            self._workers.clear()

    def worker_count(self) -> int:
        """Return current worker count."""
        with self._lock:
            return len(self._workers)


# ─────────────────────────────────────────────────────────────────────────
# SPAWN PLUGIN (entry point for plugin subprocess)
# ─────────────────────────────────────────────────────────────────────────


class PluginWorkerProcess:
    """Base class for plugin worker processes."""

    def __init__(self):
        self._methods: Dict[str, Callable] = {}

    def register_method(self, name: str, func: Callable):
        """Register a method handler."""
        self._methods[name] = func

    async def handle_request(self, msg: IPCMessage) -> IPCMessage:
        """Handle incoming request."""
        if msg.method not in self._methods:
            return IPCMessage(
                message_type=MessageType.ERROR,
                id=msg.id,
                error=f"Unknown method: {msg.method}",
            )

        try:
            handler = self._methods[msg.method]
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**msg.params)
            else:
                result = handler(**msg.params)
            return IPCMessage(
                message_type=MessageType.RESPONSE,
                id=msg.id,
                result=result,
            )
        except Exception as e:
            logger.error(f"Error handling request {msg.method}: {e}")
            return IPCMessage(
                message_type=MessageType.ERROR,
                id=msg.id,
                error=str(e),
            )

    async def run(self):
        """Main event loop for plugin worker."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                msg_json = line.strip()
                if not msg_json:
                    continue

                msg = IPCMessage.from_json(msg_json)
                response = await self.handle_request(msg)
                print(response.to_json())
                sys.stdout.flush()
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                break

        loop.close()
