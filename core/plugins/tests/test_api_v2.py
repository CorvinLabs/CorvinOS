"""
Tests for Plugin API v2.

Covers:
- Immutable dataclass enforcement
- Response factory methods
- Deadline checking
- Hook contract validation
- Version compatibility
"""

import pytest
from datetime import datetime, timedelta

from core.plugins.api_v2 import (
    PluginAPIVersion,
    ExecutionContext,
    PluginResponse,
    PluginBase,
    PluginException,
    PluginTimeoutException,
)


class TestPluginAPIVersion:
    """Test API version compatibility."""

    def test_version_string(self):
        """Version string is well-formed."""
        version = PluginAPIVersion.version_string()
        assert version == "2.0.0"

    def test_v2_plugin_compatible(self):
        """v2 plugin is compatible with v2 core."""
        assert PluginAPIVersion.is_compatible("2.0.0") is True
        assert PluginAPIVersion.is_compatible("2.1.0") is True
        assert PluginAPIVersion.is_compatible("2.99.99") is True

    def test_v1_plugin_not_compatible(self):
        """v1 plugin is not compatible with v2 core."""
        assert PluginAPIVersion.is_compatible("1.0.0") is False
        assert PluginAPIVersion.is_compatible("1.99.99") is False

    def test_v3_plugin_not_compatible(self):
        """v3 plugin is not compatible with v2 core."""
        assert PluginAPIVersion.is_compatible("3.0.0") is False

    def test_invalid_version_not_compatible(self):
        """Invalid version strings not compatible."""
        assert PluginAPIVersion.is_compatible("not-a-version") is False
        assert PluginAPIVersion.is_compatible("") is False


class TestExecutionContext:
    """Test ExecutionContext dataclass."""

    def test_context_creation(self):
        """ExecutionContext can be created."""
        now = datetime.utcnow()
        deadline = now + timedelta(seconds=60)
        ctx = ExecutionContext(
            operation_id="op-123",
            plugin_id="test-plugin",
            operator_id="operator-1",
            tenant_id="default",
            version="2.0.0",
            started_at=now,
            deadline=deadline,
            audit_hash="hash-123",
        )
        assert ctx.plugin_id == "test-plugin"
        assert ctx.operation_id == "op-123"

    def test_context_is_frozen(self):
        """ExecutionContext is immutable."""
        now = datetime.utcnow()
        ctx = ExecutionContext(
            operation_id="op-123",
            plugin_id="test",
            operator_id="op-1",
            tenant_id="default",
            version="2.0.0",
            started_at=now,
            deadline=now + timedelta(seconds=60),
            audit_hash="hash",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            ctx.operation_id = "new-id"

    def test_time_remaining_calculation(self):
        """Time remaining is calculated correctly."""
        now = datetime.utcnow()
        future = now + timedelta(seconds=60)
        ctx = ExecutionContext(
            operation_id="op-1",
            plugin_id="test",
            operator_id="op",
            tenant_id="default",
            version="2.0.0",
            started_at=now,
            deadline=future,
            audit_hash="hash",
        )
        remaining = ctx.time_remaining_seconds()
        assert 59 <= remaining <= 61  # Allow 1 second variance

    def test_deadline_exceeded_check(self):
        """Deadline check works correctly."""
        now = datetime.utcnow()
        past = now - timedelta(seconds=10)
        ctx = ExecutionContext(
            operation_id="op-1",
            plugin_id="test",
            operator_id="op",
            tenant_id="default",
            version="2.0.0",
            started_at=past,
            deadline=past,  # Already passed
            audit_hash="hash",
        )
        assert ctx.is_deadline_exceeded() is True

    def test_deadline_not_exceeded(self):
        """Deadline check for future deadline."""
        now = datetime.utcnow()
        future = now + timedelta(seconds=100)
        ctx = ExecutionContext(
            operation_id="op-1",
            plugin_id="test",
            operator_id="op",
            tenant_id="default",
            version="2.0.0",
            started_at=now,
            deadline=future,
            audit_hash="hash",
        )
        assert ctx.is_deadline_exceeded() is False


class TestPluginResponse:
    """Test PluginResponse dataclass."""

    def test_success_response(self):
        """Success response."""
        resp = PluginResponse.success({"result": "ok"})
        assert resp.status == "success"
        assert resp.data == {"result": "ok"}
        assert resp.error is None

    def test_error_response(self):
        """Error response."""
        resp = PluginResponse.failure("Something went wrong", code="ERROR_001")
        assert resp.status == "error"
        assert resp.error == "Something went wrong"
        assert resp.error_code == "ERROR_001"
        assert resp.data is None

    def test_retry_response(self):
        """Retry response."""
        resp = PluginResponse.retry("Transient network error")
        assert resp.status == "retry"
        assert resp.error == "Transient network error"

    def test_response_is_frozen(self):
        """PluginResponse is immutable."""
        resp = PluginResponse.success({"key": "value"})
        with pytest.raises(Exception):  # FrozenInstanceError
            resp.status = "error"

    def test_success_invariant_must_have_data(self):
        """Success response must have data."""
        with pytest.raises(AssertionError):
            PluginResponse(status="success", error="has error")

    def test_error_invariant_must_have_message(self):
        """Error response must have error message."""
        with pytest.raises(AssertionError):
            PluginResponse(status="error", data={"result": "ok"})

    def test_response_to_dict(self):
        """Response serializes to dictionary."""
        resp = PluginResponse.success(
            {"result": "value"},
            metadata={"time_ms": 100},
            audit_hash="hash-xyz",
        )
        d = resp.to_dict()
        assert d["status"] == "success"
        assert d["data"] == {"result": "value"}
        assert d["metadata"]["time_ms"] == 100
        assert d["audit_hash"] == "hash-xyz"


class TestPluginBase:
    """Test PluginBase class."""

    def test_plugin_base_is_abstract(self):
        """PluginBase is abstract and can't be instantiated."""
        with pytest.raises(TypeError):
            PluginBase()  # Missing init() implementation

    def test_plugin_implementation_minimal(self):
        """Minimal plugin implementation."""
        class MinimalPlugin(PluginBase):
            async def init(self, context):
                pass

        plugin = MinimalPlugin()
        assert plugin is not None
        assert plugin.__version__ == "2.0.0"

    def test_plugin_default_hooks(self):
        """Default hook implementations."""
        class TestPlugin(PluginBase):
            async def init(self, context):
                pass

        import asyncio
        plugin = TestPlugin()
        now = datetime.utcnow()
        ctx = ExecutionContext(
            operation_id="op-1",
            plugin_id="test",
            operator_id="op",
            tenant_id="default",
            version="2.0.0",
            started_at=now,
            deadline=now + timedelta(seconds=60),
            audit_hash="hash",
        )

        # Test default hooks return success
        result = asyncio.run(plugin.on_task_start(ctx, "task-1", "auth", {}))
        assert result.status == "success"

        result = asyncio.run(plugin.on_task_complete(ctx, "task-1", {}, 100.0))
        assert result.status == "success"

        result = asyncio.run(plugin.on_error(ctx, "err-1", "RuntimeError", "msg", None))
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_plugin_init_required(self):
        """Plugin init() is abstract and must be implemented."""
        class BadPlugin(PluginBase):
            pass  # Missing init()

        # ABC enforcement fires at INSTANTIATION, not at class definition —
        # the old form (the class statement inside `raises`) never raised and
        # only passed while the whole class was already failing for A7.
        with pytest.raises(TypeError):
            BadPlugin()


class TestPluginExceptions:
    """Test plugin exception hierarchy."""

    def test_plugin_exception_base(self):
        """PluginException is base exception."""
        exc = PluginException("test")
        assert isinstance(exc, Exception)

    def test_timeout_exception(self):
        """PluginTimeoutException for deadline exceeded."""
        exc = PluginTimeoutException("deadline")
        assert isinstance(exc, PluginException)

    def test_exception_inheritance(self):
        """Exception hierarchy is correct."""
        timeout_exc = PluginTimeoutException("timeout")
        assert isinstance(timeout_exc, PluginException)
        assert isinstance(timeout_exc, Exception)


class TestPluginValidateDeadline:
    """Test deadline validation in plugins."""

    @pytest.mark.asyncio
    async def test_validate_deadline_expired(self):
        """Validate deadline raises if exceeded."""
        class TestPlugin(PluginBase):
            async def init(self, context):
                self.context = context

        plugin = TestPlugin()
        now = datetime.utcnow()
        past = now - timedelta(seconds=10)

        ctx = ExecutionContext(
            operation_id="op-1",
            plugin_id="test",
            operator_id="op",
            tenant_id="default",
            version="2.0.0",
            started_at=past,
            deadline=past,
            audit_hash="hash",
        )

        await plugin.init(ctx)

        with pytest.raises(PluginTimeoutException):
            plugin.validate_deadline()

    @pytest.mark.asyncio
    async def test_validate_deadline_valid(self):
        """Validate deadline doesn't raise if valid."""
        class TestPlugin(PluginBase):
            async def init(self, context):
                self.context = context

        plugin = TestPlugin()
        now = datetime.utcnow()
        future = now + timedelta(seconds=60)

        ctx = ExecutionContext(
            operation_id="op-1",
            plugin_id="test",
            operator_id="op",
            tenant_id="default",
            version="2.0.0",
            started_at=now,
            deadline=future,
            audit_hash="hash",
        )

        await plugin.init(ctx)

        # Should not raise
        plugin.validate_deadline()


class TestPluginMetadata:
    """Test plugin metadata reporting."""

    @pytest.mark.asyncio
    async def test_plugin_metadata_method(self):
        """Plugin can report metadata."""
        class CustomPlugin(PluginBase):
            __plugin_id__ = "my-plugin"
            __version__ = "1.5.2"

            async def init(self, context):
                pass

            async def get_plugin_metadata(self):
                return {
                    "plugin_id": self.__plugin_id__,
                    "version": self.__version__,
                    "name": "My Custom Plugin",
                    "supported_task_types": ["auth"],
                }

        plugin = CustomPlugin()
        metadata = await plugin.get_plugin_metadata()
        assert metadata["plugin_id"] == "my-plugin"
        assert metadata["version"] == "1.5.2"
        assert "name" in metadata
