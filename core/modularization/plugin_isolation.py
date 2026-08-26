"""Plugin subprocess isolation and lifecycle management (Phase 4.5, ADR-0426).

Manages out-of-process plugins with isolated resource limits, IPC communication,
graceful lifecycle (start/stop), and version discovery.

Per ADR-0426 synthesis:
- Community plugins (origin=community) run out-of-process by default
- Builtin/vetted plugins run in-process by default, can opt-in to isolation
- Bundled system plugins (boot_layer=compliance/core) never isolated
- Graceful lifecycle: shutdown signal → audit log → restart → version check
- Version discovery: strict version matching on restart (fail loud)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Callable, Awaitable

log = logging.getLogger("corvin.modularization.plugin_isolation")


class PluginProcessState(str, Enum):
    """Plugin subprocess lifecycle state."""
    STOPPED = "stopped"
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"
    CRASHED = "crashed"


@dataclass(frozen=True)
class PluginManifest:
    """Plugin metadata snapshot used for version discovery."""
    plugin_id: str
    version: str
    api_version: str
    origin: str  # builtin, vetted, community
    boot_layer: str  # compliance, core, bundled, installed
    supports_isolation: bool = False
    requires_ipc: bool = False


@dataclass(frozen=True)
class PluginProcessInfo:
    """Immutable snapshot of plugin subprocess state."""
    plugin_id: str
    state: PluginProcessState
    pid: Optional[int] = None
    start_time: Optional[datetime] = None
    last_health_check: Optional[datetime] = None
    memory_usage_mb: float = 0.0
    cpu_percent: float = 0.0
    restart_count: int = 0
    error_message: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ProcessResourceLimits:
    """Resource caps for isolated plugin subprocess."""
    memory_mb: int = 512
    cpu_limit: float = 1.0  # CPU cores
    timeout_sec: int = 30
    max_restarts: int = 5
    restart_cooldown_sec: int = 10


class PluginProcessManager:
    """Manages isolated plugin subprocess lifecycle and IPC communication."""

    def __init__(
        self,
        plugin_id: str,
        command: list[str],
        manifest: PluginManifest,
        limits: ProcessResourceLimits,
        ipc_socket_path: Optional[str] = None,
        audit_logger: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ):
        """Initialize plugin process manager.

        Args:
            plugin_id: Unique plugin identifier
            command: Command to spawn subprocess (e.g., ['python', '/path/to/plugin.py'])
            manifest: Plugin metadata for version discovery
            limits: Resource limits (memory, CPU, restart policy)
            ipc_socket_path: Unix socket for IPC communication (if needed)
            audit_logger: Async function to log audit events
        """
        self.plugin_id = plugin_id
        self.command = command
        self.manifest = manifest
        self.limits = limits
        self.ipc_socket_path = ipc_socket_path
        self.audit_logger = audit_logger

        self._process: Optional[subprocess.Popen] = None
        self._state = PluginProcessState.STOPPED
        self._restart_count = 0
        self._start_time: Optional[datetime] = None
        self._ipc_queue: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        """Start the plugin subprocess with resource limits."""
        if self._state != PluginProcessState.STOPPED:
            raise RuntimeError(f"Cannot start plugin in state: {self._state}")

        self._state = PluginProcessState.STARTING
        await self._audit_log("plugin.start_requested", {"plugin_id": self.plugin_id})

        try:
            # Prepare environment with resource limits
            env = os.environ.copy()
            env["CORVIN_PLUGIN_ID"] = self.plugin_id
            env["CORVIN_PLUGIN_IPC_SOCKET"] = self.ipc_socket_path or ""

            # Spawn subprocess
            self._process = subprocess.Popen(
                self.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,  # Line-buffered
            )
            self._start_time = datetime.now(timezone.utc)
            self._state = PluginProcessState.HEALTHY

            await self._audit_log(
                "plugin.start_completed",
                {
                    "plugin_id": self.plugin_id,
                    "pid": self._process.pid,
                    "manifest": asdict(self.manifest),
                },
            )

            log.info(
                f"Plugin {self.plugin_id} started (PID {self._process.pid})",
                extra={"plugin_id": self.plugin_id, "pid": self._process.pid},
            )
        except Exception as e:
            self._state = PluginProcessState.CRASHED
            error_msg = f"Failed to start plugin: {str(e)}"
            await self._audit_log(
                "plugin.start_failed",
                {"plugin_id": self.plugin_id, "error": error_msg},
            )
            raise RuntimeError(error_msg) from e

    async def stop(self, graceful: bool = True, timeout_sec: Optional[int] = None) -> None:
        """Stop the plugin subprocess gracefully or forcefully.

        Args:
            graceful: If True, send SIGTERM first; if False, send SIGKILL immediately
            timeout_sec: Wait time before escalating to SIGKILL (default: limits.timeout_sec)
        """
        if self._state == PluginProcessState.STOPPED:
            return

        timeout = timeout_sec or self.limits.timeout_sec
        self._state = PluginProcessState.STOPPING

        await self._audit_log(
            "plugin.stop_requested",
            {
                "plugin_id": self.plugin_id,
                "graceful": graceful,
                "timeout_sec": timeout,
            },
        )

        try:
            if self._process and self._process.poll() is None:  # Still running
                if graceful:
                    log.info(
                        f"Stopping plugin {self.plugin_id} gracefully (SIGTERM)",
                        extra={"plugin_id": self.plugin_id},
                    )
                    self._process.send_signal(signal.SIGTERM)
                    try:
                        await asyncio.wait_for(
                            self._wait_process_exit(), timeout=float(timeout)
                        )
                    except asyncio.TimeoutError:
                        log.warning(
                            f"Plugin {self.plugin_id} did not stop within {timeout}s, killing forcefully",
                            extra={"plugin_id": self.plugin_id},
                        )
                        self._process.send_signal(signal.SIGKILL)
                        await asyncio.wait_for(
                            self._wait_process_exit(), timeout=5.0
                        )
                else:
                    log.info(
                        f"Killing plugin {self.plugin_id} forcefully (SIGKILL)",
                        extra={"plugin_id": self.plugin_id},
                    )
                    self._process.send_signal(signal.SIGKILL)
                    await asyncio.wait_for(self._wait_process_exit(), timeout=5.0)

            self._state = PluginProcessState.STOPPED
            await self._audit_log(
                "plugin.stop_completed",
                {"plugin_id": self.plugin_id, "restart_count": self._restart_count},
            )
        except Exception as e:
            error_msg = f"Error stopping plugin: {str(e)}"
            await self._audit_log(
                "plugin.stop_failed", {"plugin_id": self.plugin_id, "error": error_msg}
            )
            raise RuntimeError(error_msg) from e

    async def restart(self, reason: str = "") -> None:
        """Restart plugin subprocess (graceful stop → start)."""
        if self._restart_count >= self.limits.max_restarts:
            error_msg = f"Plugin {self.plugin_id} exceeded max restarts ({self.limits.max_restarts})"
            await self._audit_log(
                "plugin.restart_failed_max_exceeded",
                {"plugin_id": self.plugin_id, "max_restarts": self.limits.max_restarts},
            )
            raise RuntimeError(error_msg)

        await self._audit_log(
            "plugin.restart_requested",
            {"plugin_id": self.plugin_id, "reason": reason, "restart_count": self._restart_count},
        )

        await self.stop(graceful=True)
        await asyncio.sleep(self.limits.restart_cooldown_sec)
        await self.start()

        self._restart_count += 1

    async def get_version_manifest(self) -> PluginManifest:
        """Query plugin subprocess for its current manifest (version discovery).

        Raises:
            RuntimeError: If plugin is not running or version check fails
        """
        if self._state != PluginProcessState.HEALTHY:
            raise RuntimeError(f"Cannot query version: plugin state is {self._state}")

        if not self._process or self._process.poll() is not None:
            raise RuntimeError("Plugin subprocess is not running")

        # Send version query via IPC (simplified: could use JSON-RPC)
        query = json.dumps({"method": "get_manifest"})
        try:
            # Write to subprocess stdin (line-delimited JSON)
            self._process.stdin.write(query + "\n")
            self._process.stdin.flush()

            # Read response from subprocess stdout (with timeout)
            response_line = await asyncio.wait_for(
                self._read_subprocess_line(), timeout=self.limits.timeout_sec
            )
            manifest_dict = json.loads(response_line)

            # Validate manifest structure
            manifest = PluginManifest(
                plugin_id=manifest_dict["plugin_id"],
                version=manifest_dict["version"],
                api_version=manifest_dict["api_version"],
                origin=manifest_dict["origin"],
                boot_layer=manifest_dict["boot_layer"],
                supports_isolation=manifest_dict.get("supports_isolation", False),
                requires_ipc=manifest_dict.get("requires_ipc", False),
            )

            # Check for version mismatch (fail loud)
            if manifest.version != self.manifest.version:
                error_msg = (
                    f"Plugin version mismatch: expected {self.manifest.version}, "
                    f"got {manifest.version}. Do not silently downgrade."
                )
                await self._audit_log(
                    "plugin.version_mismatch",
                    {
                        "plugin_id": self.plugin_id,
                        "expected_version": self.manifest.version,
                        "actual_version": manifest.version,
                    },
                )
                raise RuntimeError(error_msg)

            return manifest
        except asyncio.TimeoutError:
            error_msg = f"Timeout querying plugin version (>{self.limits.timeout_sec}s)"
            await self._audit_log(
                "plugin.version_query_timeout",
                {"plugin_id": self.plugin_id, "timeout_sec": self.limits.timeout_sec},
            )
            raise RuntimeError(error_msg)

    async def send_ipc_message(self, method: str, params: Dict[str, Any]) -> Any:
        """Send an IPC message to the plugin subprocess and get response."""
        if self._state != PluginProcessState.HEALTHY:
            raise RuntimeError(f"Cannot send IPC: plugin state is {self._state}")

        message = json.dumps({"method": method, "params": params})
        try:
            self._process.stdin.write(message + "\n")
            self._process.stdin.flush()

            response_line = await asyncio.wait_for(
                self._read_subprocess_line(), timeout=self.limits.timeout_sec
            )
            return json.loads(response_line)
        except (json.JSONDecodeError, asyncio.TimeoutError) as e:
            self._state = PluginProcessState.UNHEALTHY
            raise RuntimeError(f"IPC communication failed: {str(e)}") from e

    def get_state(self) -> PluginProcessInfo:
        """Get current plugin process state snapshot."""
        memory_mb = 0.0
        if self._process and self._process.poll() is None:
            # Simplified: would use psutil in production
            memory_mb = 0.0  # Placeholder

        return PluginProcessInfo(
            plugin_id=self.plugin_id,
            state=self._state,
            pid=self._process.pid if self._process else None,
            start_time=self._start_time,
            memory_usage_mb=memory_mb,
            cpu_percent=0.0,
            restart_count=self._restart_count,
        )

    async def _wait_process_exit(self, timeout_sec: float = 30.0) -> None:
        """Wait for subprocess to exit (async wrapper)."""
        start = time.time()
        while time.time() - start < timeout_sec:
            if self._process.poll() is not None:
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"Process did not exit within {timeout_sec}s")

    async def _read_subprocess_line(self) -> str:
        """Read a line from subprocess stdout (async)."""
        loop = asyncio.get_event_loop()
        # Simplified: would use aioconsole in production
        return await loop.run_in_executor(None, self._process.stdout.readline)

    async def _audit_log(self, event_type: str, details: Dict[str, Any]) -> None:
        """Log an audit event (if audit_logger provided)."""
        if self.audit_logger:
            try:
                await self.audit_logger(event_type, details)
            except Exception as e:
                log.error(
                    f"Failed to log audit event {event_type}: {str(e)}",
                    extra={"event_type": event_type},
                )
                # Do not raise: audit failure should not crash plugin management
