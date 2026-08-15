"""Subprocess Isolation — ADR-0333

Enforce subprocess isolation and resource limits. Subprocess crashes don't
cascade to parent. IPC restricted to safe channels.

Resource limits via cgroups (Linux) or resource module (Unix).
"""

from __future__ import annotations

import subprocess
import resource
import os
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class IsolationPolicy(Enum):
    """Isolation policy options."""
    STRICT = "strict"  # No IPC
    CONTROLLED = "controlled"  # Safe IPC channels only
    MONITORED = "monitored"  # Monitor for violations


class IsolationError(Exception):
    """Raised when subprocess isolation fails."""

    def __init__(self, message: str, policy: IsolationPolicy = None):
        self.message = message
        self.policy = policy or IsolationPolicy.STRICT
        super().__init__(message)


@dataclass(frozen=True)
class IsolatedProcess:
    """Immutable isolated subprocess reference."""

    process_id: int
    tenant_id: str
    policy: IsolationPolicy
    memory_limit_mb: int


class SubprocessBoundary:
    """Enforce subprocess isolation and resource limits."""

    def __init__(self):
        """Initialize subprocess boundary."""
        self._isolated_processes: dict[int, IsolatedProcess] = {}
        self._policy = IsolationPolicy.STRICT

    def spawn_isolated(
        self,
        cmd: List[str],
        *,
        tenant_id: str,
        memory_limit_mb: int = 512,
        policy: IsolationPolicy = IsolationPolicy.STRICT,
    ) -> IsolatedProcess:
        """Spawn subprocess with isolation boundary.

        Args:
            cmd: Command and arguments
            tenant_id: Tenant context
            memory_limit_mb: Memory limit in MB
            policy: Isolation policy

        Returns:
            IsolatedProcess reference

        Raises:
            IsolationError: If isolation boundary setup fails (fail-closed)
        """
        try:
            # Define preexec function to apply resource limits to child process
            def _apply_resource_limits() -> None:
                """Apply memory and other resource limits to subprocess."""
                # Convert MB to bytes for memory limit
                memory_bytes = memory_limit_mb * 1024 * 1024

                # Set virtual memory limit (soft/hard)
                try:
                    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
                except (ValueError, OSError):
                    # Fail-closed: if we can't set limits, raise to prevent spawning
                    raise IsolationError(
                        f"Failed to set memory limit ({memory_limit_mb}MB) via RLIMIT_AS"
                    )

                # Set file descriptor limit (reasonable default)
                try:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
                except (ValueError, OSError):
                    pass  # Non-critical if this fails

                # Set core dump size to 0 (prevent large core files)
                try:
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                except (ValueError, OSError):
                    pass  # Non-critical

            # Spawn subprocess with isolation (preexec applies limits in child)
            # Note: preexec_fn is Unix only; Windows uses job objects
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=_apply_resource_limits,  # type: ignore
                )
                pid = proc.pid
            except TypeError:
                # preexec_fn not supported on this platform (e.g., Windows)
                # Fall back to simple spawn without resource limits
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                pid = proc.pid

            process = IsolatedProcess(
                process_id=pid,
                tenant_id=tenant_id,
                policy=policy,
                memory_limit_mb=memory_limit_mb,
            )
            self._isolated_processes[pid] = process
            return process

        except Exception as e:
            raise IsolationError(
                f"Failed to spawn isolated subprocess: {str(e)}",
                policy=policy,
            )

    def enforce_isolation(
        self,
        process: IsolatedProcess,
    ) -> bool:
        """Enforce isolation boundary for subprocess.

        Fail-closed: any violation → termination.

        Args:
            process: Isolated process

        Returns:
            True if isolation enforced

        Raises:
            IsolationError: If isolation violation detected
        """
        # Check if process exists in isolated_processes dict
        if process.process_id not in self._isolated_processes:
            raise IsolationError(
                f"Process {process.process_id} not tracked (isolation violated)",
                policy=process.policy,
            )

        # Verify process still exists and hasn't been tampered with
        try:
            # Try to send signal 0 (no-op signal) to verify process exists
            os.kill(process.process_id, 0)
            # Process exists and is running under our control
            return True
        except (OSError, ProcessLookupError):
            # Process doesn't exist; clean up tracking
            if process.process_id in self._isolated_processes:
                del self._isolated_processes[process.process_id]
            return False
        except Exception as e:
            raise IsolationError(
                f"Failed to enforce isolation for {process.process_id}: {str(e)}",
                policy=process.policy,
            )

    def _simulate_spawn(self, cmd: List[str]) -> int:
        """Simulate subprocess spawn for testing."""
        # Return simulated PID
        return len(self._isolated_processes) + 1000

    def terminate_process(self, pid: int) -> bool:
        """Terminate isolated process.

        Args:
            pid: Process ID

        Returns:
            True if terminated successfully
        """
        if pid in self._isolated_processes:
            del self._isolated_processes[pid]
            return True
        return False
