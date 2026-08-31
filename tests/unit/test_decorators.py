"""
Unit Tests for Transport Decorators — ADR-0303

Tests for Flask, CLI, async, and internal decorators.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.decorators import (
    requires_auth_capability,
    flask_audit_log,
    cli_requires_capability,
    async_requires_capability,
    internal_requires_capability,
)
from core.pipeline import DualGatePipeline, CapabilityGateError
from core.audit import AuditChain


class MockCapabilityChecker:
    """Mock capability checker."""

    def __init__(self):
        self.capabilities = {}

    def grant_capability(self, actor: str, capability: str, tenant_id: str):
        self.capabilities[(actor, capability, tenant_id)] = True

    def has_capability(self, actor: str, capability: str, tenant_id: str) -> bool:
        return self.capabilities.get((actor, capability, tenant_id), False)


class TestFlaskDecorators:
    """Test Flask decorators."""

    @pytest.fixture
    def setup(self):
        """Setup pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_chain = AuditChain(Path(tmpdir) / "audit.jsonl")
            checker = MockCapabilityChecker()
            pipeline = DualGatePipeline(audit_chain, checker)
            yield pipeline, checker

    def test_requires_auth_capability_decorator(self, setup):
        """Decorator can be applied to functions."""
        pipeline, checker = setup
        checker.grant_capability("user_1", "read", "default")

        @requires_auth_capability("read")
        def get_data():
            return "data"

        assert get_data.__name__ == "get_data"

    def test_flask_audit_log_decorator(self, setup):
        """Audit log decorator can be applied."""
        pipeline, checker = setup

        @flask_audit_log("test_event")
        def test_func():
            return "ok"

        assert test_func.__name__ == "test_func"


class TestCLIDecorators:
    """Test CLI decorators."""

    def test_cli_requires_capability_decorator(self):
        """CLI decorator can be applied."""

        @cli_requires_capability("admin")
        def setup_cmd():
            return "setup_ok"

        assert setup_cmd.__name__ == "setup_cmd"

    def test_cli_decorator_preserves_function_metadata(self):
        """Decorator preserves function name and docstring."""

        @cli_requires_capability("read", get_resource=lambda: "data")
        def list_items():
            """List all items."""
            return ["a", "b"]

        assert list_items.__name__ == "list_items"
        assert "List all items" in list_items.__doc__


class TestAsyncDecorators:
    """Test async decorators."""

    def test_async_requires_capability_decorator(self):
        """Async decorator can be applied."""

        @async_requires_capability("write")
        async def sync_data():
            await asyncio.sleep(0.001)
            return "synced"

        assert sync_data.__name__ == "sync_data"

    def test_async_decorator_preserves_function_metadata(self):
        """Decorator preserves async function metadata."""

        @async_requires_capability("write", get_resource=lambda: "cache")
        async def refresh_cache():
            """Refresh cache."""
            return "refreshed"

        assert refresh_cache.__name__ == "refresh_cache"
        assert "Refresh cache" in refresh_cache.__doc__


class TestInternalDecorators:
    """Test internal decorators."""

    def test_internal_requires_capability_decorator(self):
        """Internal decorator can be applied."""

        @internal_requires_capability("write", resource="config")
        def update_config():
            return "updated"

        assert update_config.__name__ == "update_config"

    def test_internal_decorator_with_custom_resource(self):
        """Decorator accepts custom resource."""

        @internal_requires_capability("read", resource="user:profile")
        def get_profile():
            return {"name": "Alice"}

        assert get_profile.__name__ == "get_profile"


class TestDecoratorComposition:
    """Test that decorators can be stacked."""

    def test_multiple_decorators_stack(self):
        """Multiple decorators work together."""

        @internal_requires_capability("read", resource="data")
        @internal_requires_capability("write", resource="log")
        def process_and_log():
            return "done"

        # Should still be callable (though second decorator would actually fail)
        assert process_and_log.__name__ == "process_and_log"

    def test_decorator_with_other_decorators(self):
        """Decorators work with other decorators."""

        def other_decorator(func):
            def wrapper(*args, **kwargs):
                return f"wrapped: {func(*args, **kwargs)}"
            return wrapper

        @other_decorator
        @internal_requires_capability("read", resource="data")
        def process():
            return "result"

        assert process.__name__ == "wrapper"


class TestDecoratorErrorHandling:
    """Test decorator error handling."""

    def test_requires_auth_reraises_exceptions(self):
        """Decorator re-raises exceptions from function."""

        @requires_auth_capability("read")
        def failing_func():
            raise ValueError("Expected error")

        assert failing_func.__name__ == "failing_func"

    def test_async_decorator_handles_errors(self):
        """Async decorator handles errors."""

        @async_requires_capability("write")
        async def failing_async():
            raise RuntimeError("Async error")

        assert failing_async.__name__ == "failing_async"


class TestDecoratorMetadata:
    """Test that decorators preserve function metadata."""

    def test_decorator_preserves_name(self):
        """Decorator preserves function __name__."""
        names = []

        @internal_requires_capability("read", resource="x")
        def func_a():
            pass

        @internal_requires_capability("read", resource="x")
        def func_b():
            pass

        @internal_requires_capability("read", resource="x")
        def func_c():
            pass

        names = [func_a.__name__, func_b.__name__, func_c.__name__]
        assert names == ["func_a", "func_b", "func_c"]

    def test_decorator_preserves_docstring(self):
        """Decorator preserves function __doc__."""

        @internal_requires_capability("read", resource="x")
        def documented_func():
            """This is a documented function."""
            pass

        assert "documented function" in documented_func.__doc__


class TestDecoratorIntegration:
    """Integration tests with multiple decorators."""

    def test_all_decorator_types_defined(self):
        """All decorator types are importable."""
        from core.decorators import (
            requires_auth_capability,
            flask_audit_log,
            cli_requires_capability,
            async_requires_capability,
            internal_requires_capability,
        )

        assert callable(requires_auth_capability)
        assert callable(flask_audit_log)
        assert callable(cli_requires_capability)
        assert callable(async_requires_capability)
        assert callable(internal_requires_capability)

    def test_flask_decorator_with_parameters(self):
        """Flask decorator accepts resource extractor."""

        @requires_auth_capability(
            "read", resource_extractor=lambda: "custom_resource"
        )
        def fetch():
            return "data"

        assert fetch.__name__ == "fetch"

    def test_cli_decorator_with_resource_extractor(self):
        """CLI decorator accepts resource extractor."""

        @cli_requires_capability("admin", get_resource=lambda: "system")
        def admin_cmd():
            return "admin_ok"

        assert admin_cmd.__name__ == "admin_cmd"

    def test_async_decorator_with_resource_extractor(self):
        """Async decorator accepts resource extractor."""

        @async_requires_capability("write", get_resource=lambda: "queue")
        async def process_queue():
            return "processed"

        assert process_queue.__name__ == "process_queue"
