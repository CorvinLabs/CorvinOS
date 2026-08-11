"""
Unit Tests for Transport Adapters — ADR-0301 helper

Tests for Flask, CLI, async, and internal adapters.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch, Mock

import pytest

from core.pipeline import DualGatePipeline, CapabilityGateError
from core.pipeline.adapters import (
    FlaskAdapter,
    CLIAdapter,
    AsyncAdapter,
    InternalFunctionAdapter,
)
from core.audit import AuditChain


class MockCapabilityChecker:
    """Mock capability checker."""

    def __init__(self):
        self.capabilities = {}

    def grant_capability(self, actor: str, capability: str, tenant_id: str):
        self.capabilities[(actor, capability, tenant_id)] = True

    def has_capability(self, actor: str, capability: str, tenant_id: str) -> bool:
        return self.capabilities.get((actor, capability, tenant_id), False)


class TestFlaskAdapter:
    """Test Flask route adapter."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            adapter = FlaskAdapter(pipeline)
            yield pipeline, adapter, checker

    def test_route_guarded_decorator_creates_wrapper(self, setup):
        """Decorator wraps function."""
        pipeline, adapter, checker = setup
        checker.grant_capability("user_1", "read", "default")

        @adapter.route_guarded("read", "fetch_user")
        def get_user():
            return {"id": 1, "name": "Alice"}

        assert get_user.__name__ == "get_user"  # functools.wraps preserves name


class TestCLIAdapter:
    """Test CLI command adapter."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            adapter = CLIAdapter(pipeline)
            yield pipeline, adapter, checker

    def test_command_guarded_decorator(self, setup):
        """CLI command decorator works."""
        pipeline, adapter, checker = setup
        checker.grant_capability("cli_user", "admin", "default")

        @adapter.command_guarded("admin", "config_set")
        def set_config():
            return "config_updated"

        assert set_config.__name__ == "set_config"

    def test_command_guarded_execution(self, setup):
        """CLI command executes with pipeline."""
        pipeline, adapter, checker = setup
        checker.grant_capability("cli_user", "admin", "default")

        @adapter.command_guarded("admin", "config_set")
        def set_config():
            return "config_updated"

        with patch("os.getenv") as mock_getenv:
            mock_getenv.return_value = "cli_user"
            result = set_config()
            assert result == "config_updated"


class TestAsyncAdapter:
    """Test async task adapter."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            adapter = AsyncAdapter(pipeline)
            yield pipeline, adapter, checker

    @pytest.mark.asyncio
    async def test_task_guarded_decorator(self, setup):
        """Async task decorator works."""
        pipeline, adapter, checker = setup
        checker.grant_capability("system", "write", "default")

        @adapter.task_guarded("write", "background_sync")
        async def sync_data():
            await asyncio.sleep(0.01)
            return "synced"

        assert sync_data.__name__ == "sync_data"

    @pytest.mark.asyncio
    async def test_task_guarded_execution(self, setup):
        """Async task executes with pipeline."""
        pipeline, adapter, checker = setup
        checker.grant_capability("system", "write", "default")

        @adapter.task_guarded("write", "background_sync")
        async def sync_data():
            await asyncio.sleep(0.01)
            return "synced"

        result = await sync_data()
        assert result == "synced"


class TestInternalFunctionAdapter:
    """Test internal function adapter."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            adapter = InternalFunctionAdapter(pipeline)
            yield pipeline, adapter, checker

    def test_function_guarded_decorator(self, setup):
        """Internal function decorator works."""
        pipeline, adapter, checker = setup

        @adapter.function_guarded("write", "update_config", resource="config")
        def update_config():
            return "updated"

        assert update_config.__name__ == "update_config"

    def test_function_guarded_execution_fails_without_capability(self, setup):
        """Internal function execution fails without capability."""
        pipeline, adapter, checker = setup
        # Don't grant capability

        @adapter.function_guarded("write", "update_config", resource="config")
        def update_config():
            return "updated"

        with pytest.raises(CapabilityGateError):
            update_config()


class TestAdaptersAuditTrail:
    """Test that adapters produce audit trail."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            cli_adapter = CLIAdapter(pipeline)
            yield pipeline, cli_adapter, checker

    def test_cli_adapter_audits_calls(self, setup):
        """CLI adapter records audit entries."""
        pipeline, adapter, checker = setup
        checker.grant_capability("cli_user", "read", "default")

        @adapter.command_guarded("read", "list_data")
        def list_data():
            return "data"

        with patch("os.getenv") as mock_getenv:
            mock_getenv.return_value = "cli_user"
            list_data()

        # Check audit entries recorded
        assert pipeline.audit_chain.entry_count() >= 1


class TestAdaptersErrorHandling:
    """Test adapter error handling."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            cli_adapter = CLIAdapter(pipeline)
            yield pipeline, cli_adapter, checker

    def test_cli_adapter_capability_denial(self, setup):
        """CLI adapter denies on capability check failure."""
        pipeline, adapter, checker = setup
        # Do not grant capability

        @adapter.command_guarded("admin", "system_reset")
        def reset_system():
            return "reset_done"

        with patch("os.getenv") as mock_getenv:
            mock_getenv.return_value = "cli_user"
            with pytest.raises(CapabilityGateError):
                reset_system()

    def test_cli_adapter_function_error_audited(self, setup):
        """CLI adapter audits function errors."""
        pipeline, adapter, checker = setup
        checker.grant_capability("cli_user", "write", "default")

        @adapter.command_guarded("write", "save_data")
        def save_data():
            raise ValueError("Save failed")

        with patch("os.getenv") as mock_getenv:
            mock_getenv.return_value = "cli_user"
            with pytest.raises(ValueError):
                save_data()

        # Error should be audited
        assert pipeline.audit_chain.entry_count() >= 1


class TestAdaptersIntegration:
    """Integration tests with multiple adapters."""

    @pytest.fixture
    def setup(self):
        """Setup all adapters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)

            checker.grant_capability("cli_user", "read", "default")
            checker.grant_capability("system", "write", "default")

            return (
                pipeline,
                CLIAdapter(pipeline),
                AsyncAdapter(pipeline),
                checker,
            )

    @pytest.mark.asyncio
    async def test_all_adapters_in_sequence(self, setup):
        """All adapter types work together."""
        pipeline, cli, async_adapter, checker = setup

        @cli.command_guarded("read", "list")
        def list_items():
            return 2

        @async_adapter.task_guarded("write", "sync")
        async def sync():
            await asyncio.sleep(0.001)
            return 3

        with patch("os.getenv") as mock_getenv:
            mock_getenv.return_value = "cli_user"
            r1 = list_items()
            assert r1 == 2

        r2 = await sync()
        assert r2 == 3

        # All operations should be audited
        assert pipeline.audit_chain.entry_count() >= 2
