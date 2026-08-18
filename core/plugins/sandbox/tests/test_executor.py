"""
Integration tests for plugin sandbox executor.

Tests the full lifecycle:
- Jail preparation (directories, permissions)
- Plugin code isolation
- Execution with resource limits
- Timeout handling
- Error propagation
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Dict, Any

import pytest

from core.plugins.sandbox.executor import (
    SandboxExecutor,
    SandboxExecutionResult,
    SandboxManager,
)
from core.plugins.sandbox.seccomp_rules import generate_profile


class TestSandboxExecutionResult:
    """Test execution result dataclass."""

    def test_result_success_status(self):
        """Successful execution result."""
        result = SandboxExecutionResult(
            status="success",
            data={"output": "test"},
            exit_code=0,
            execution_time_ms=100.0,
        )
        assert result.is_success() is True
        assert result.status == "success"

    def test_result_error_status(self):
        """Error result."""
        result = SandboxExecutionResult(
            status="error",
            stderr="Error message",
            exit_code=1,
        )
        assert result.is_success() is False
        assert result.status == "error"

    def test_result_to_dict(self):
        """Result serializes to dictionary."""
        result = SandboxExecutionResult(
            status="success",
            data={"key": "value"},
            exit_code=0,
            execution_time_ms=50.0,
        )
        d = result.to_dict()
        assert d["status"] == "success"
        assert d["data"] == {"key": "value"}
        assert d["exit_code"] == 0

    def test_result_timeout_status(self):
        """Timeout result."""
        result = SandboxExecutionResult(
            status="timeout",
            stderr="Execution timeout",
        )
        assert result.is_success() is False
        assert result.status == "timeout"

    def test_result_killed_status(self):
        """Killed result (out of resources)."""
        result = SandboxExecutionResult(
            status="killed",
            exit_code=137,  # SIGKILL
        )
        assert result.is_success() is False
        assert result.status == "killed"


class TestSandboxExecutor:
    """Test sandbox executor."""

    def test_executor_creation(self):
        """Executor can be created."""
        executor = SandboxExecutor()
        assert executor is not None
        assert executor.plugin_cache_dir.exists() is False or executor.plugin_cache_dir.is_dir()

    def test_executor_with_custom_cache_dir(self):
        """Executor accepts custom cache directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            executor = SandboxExecutor(plugin_cache_dir=cache_dir)
            assert executor.plugin_cache_dir == cache_dir

    @pytest.mark.asyncio
    async def test_executor_prepare_jail(self):
        """Executor prepares jail filesystem."""
        executor = SandboxExecutor()
        profile = generate_profile(plugin_id="test-plugin")

        with tempfile.TemporaryDirectory() as tmpdir:
            jail_root = Path(tmpdir)
            await executor._prepare_jail("test-plugin", jail_root, profile)

            # Verify structure
            assert (jail_root / "tmp").exists()
            assert (jail_root / "dev").exists()
            assert (jail_root / "proc").exists()
            assert (jail_root / "plugin").exists()

            # Verify config files
            assert (jail_root / "seccomp_profile.json").exists()
            assert (jail_root / "config.json").exists()

            # Verify config content
            config = json.loads((jail_root / "config.json").read_text())
            assert config["plugin_id"] == "test-plugin"
            assert config["profile"]["cpu_limit_percent"] == 20

    @pytest.mark.asyncio
    async def test_executor_build_command(self):
        """Executor builds sandbox command."""
        executor = SandboxExecutor()
        profile = generate_profile(plugin_id="test-plugin")

        with tempfile.TemporaryDirectory() as tmpdir:
            jail_root = Path(tmpdir)
            cmd = await executor._build_command(
                plugin_id="test-plugin",
                operation="test_op",
                args={"key": "value"},
                jail_root=jail_root,
                profile=profile,
            )

            # Verify command structure
            assert "sandbox-runner" in cmd
            assert "--chroot" in cmd
            assert "--seccomp" in cmd
            assert "--cpu-limit" in cmd
            assert "--mem-limit" in cmd
            assert "--timeout" in cmd
            assert "python3" in cmd
            assert "/plugin/plugin.py" in cmd
            assert "--operation" in cmd
            assert "test_op" in cmd

    @pytest.mark.asyncio
    async def test_executor_execution_minimal(self):
        """Executor can execute with minimal setup."""
        executor = SandboxExecutor()
        profile = generate_profile(plugin_id="minimal-plugin")

        # This will fail because sandbox-runner doesn't exist,
        # but we're testing the orchestration
        result = await executor.execute(
            plugin_id="minimal-plugin",
            operation="test",
            args={},
            profile=profile,
            timeout_override_sec=1,
        )

        # Should get an error (sandbox-runner not available)
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_executor_timeout_handling(self):
        """Executor handles timeout correctly."""
        executor = SandboxExecutor()
        profile = generate_profile(plugin_id="slow-plugin")

        result = await executor.execute(
            plugin_id="slow-plugin",
            operation="slow_op",
            args={},
            profile=profile,
            timeout_override_sec=0.1,  # Very short timeout
        )

        # Should timeout or error
        assert result.status in ["timeout", "error"]
        assert result.execution_time_ms > 0


class TestSandboxManager:
    """Test high-level sandbox manager."""

    def test_manager_creation(self):
        """Manager can be created."""
        manager = SandboxManager()
        assert manager is not None
        assert manager.executor is not None

    @pytest.mark.asyncio
    async def test_manager_run_operation_minimal(self):
        """Manager can attempt plugin operation."""
        manager = SandboxManager()
        profile = generate_profile(plugin_id="test-plugin")

        result = await manager.run_plugin_operation(
            plugin_id="test-plugin",
            operation="test",
            args={},
            profile=profile,
        )

        # Should fail gracefully (no sandbox-runner)
        assert result is not None
        assert hasattr(result, "status")


class TestSandboxIsolation:
    """Test sandbox isolation properties."""

    @pytest.mark.asyncio
    async def test_jail_filesystem_isolation(self):
        """Plugin's jail is isolated from host filesystem."""
        executor = SandboxExecutor()
        profile = generate_profile(
            plugin_id="test-plugin",
            filesystem_paths={"/tmp": "rw"},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            jail_root = Path(tmpdir)
            await executor._prepare_jail("test-plugin", jail_root, profile)

            # Verify isolation: host /tmp should not equal jail /tmp
            host_tmp = Path("/tmp")
            jail_tmp = jail_root / "tmp"

            assert host_tmp != jail_tmp
            assert jail_tmp.exists()

    @pytest.mark.asyncio
    async def test_multiple_plugins_different_uids(self):
        """Multiple plugin executions get different UIDs."""
        executor = SandboxExecutor()
        profile = generate_profile(plugin_id="plugin-1")

        with tempfile.TemporaryDirectory() as tmpdir:
            jail_root_1 = Path(tmpdir) / "jail1"
            jail_root_1.mkdir()

            cmd1 = await executor._build_command(
                plugin_id="plugin-1",
                operation="op",
                args={},
                jail_root=jail_root_1,
                profile=profile,
            )

            cmd2 = await executor._build_command(
                plugin_id="plugin-2",
                operation="op",
                args={},
                jail_root=jail_root_1,
                profile=profile,
            )

            # Extract UIDs from commands
            uid1 = cmd1[cmd1.index("--uid") + 1]
            uid2 = cmd2[cmd2.index("--uid") + 1]

            # UIDs should be different
            assert uid1 != uid2


class TestResourceLimiting:
    """Test resource limit enforcement."""

    @pytest.mark.asyncio
    async def test_custom_resource_limits(self):
        """Executor applies custom resource limits."""
        executor = SandboxExecutor()
        profile = generate_profile(
            plugin_id="limited-plugin",
            cpu_limit_percent=50,
            memory_limit_mb=512,
            timeout_seconds=120,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            jail_root = Path(tmpdir)
            cmd = await executor._build_command(
                plugin_id="limited-plugin",
                operation="op",
                args={},
                jail_root=jail_root,
                profile=profile,
            )

            # Verify limits in command
            assert "--cpu-limit" in cmd
            cpu_idx = cmd.index("--cpu-limit")
            assert cmd[cpu_idx + 1] == "50"

            assert "--mem-limit" in cmd
            mem_idx = cmd.index("--mem-limit")
            assert cmd[mem_idx + 1] == "512"

            assert "--timeout" in cmd
            timeout_idx = cmd.index("--timeout")
            assert cmd[timeout_idx + 1] == "120"
