"""Test template for audit backend plugins.

Use this as a starting point for testing your plugin. It demonstrates:
- Fixtures for creating a plugin instance
- Mocking the PluginContext
- Testing lifecycle methods
- Testing health checks
- Testing the capability interface (fanout, verify_chain, etc.)
"""

from unittest.mock import MagicMock

import pytest

from plugin import ExampleAuditBackend
from corvin.core.plugins.corvin_plugins.protocol import HealthStatus, PluginContext


class TestExampleAuditBackendLifecycle:
    """Test the plugin lifecycle methods."""

    @pytest.fixture
    def plugin(self):
        """Create a fresh plugin instance."""
        return ExampleAuditBackend()

    @pytest.fixture
    def mock_context(self):
        """Create a mock PluginContext."""
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"destination": "localhost"}
        ctx.plugin_id = "example-audit-backend"
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()
        ctx.audit_registry = MagicMock()
        return ctx

    def test_attributes_defined(self, plugin):
        """Test that all required attributes are defined."""
        assert plugin.plugin_id == "example-audit-backend"
        assert plugin.plugin_type == "audit_backend"
        assert plugin.version == "0.1.0"
        assert plugin.display_name == "Example Audit Backend"

    def test_on_load_success(self, plugin, mock_context):
        """Test successful plugin loading."""
        plugin.on_load(mock_context)

        # Should register with the registry
        mock_context.audit_registry.register.assert_called_once_with(
            "example-audit-backend", plugin
        )

        # Should emit an audit event
        mock_context.audit_emit.assert_called_once()
        event_type, details = mock_context.audit_emit.call_args[0]
        assert event_type == "plugin.loaded"
        assert details["plugin_id"] == "example-audit-backend"

        # Should be connected
        assert plugin.is_connected is True

    def test_on_load_missing_config(self, plugin, mock_context):
        """Test that on_load fails if config is missing."""
        mock_context.config = {}

        with pytest.raises(ValueError, match="destination"):
            plugin.on_load(mock_context)

    def test_on_unload(self, plugin, mock_context):
        """Test graceful unload."""
        plugin.on_load(mock_context)
        assert plugin.is_connected is True

        plugin.on_unload()
        assert plugin.is_connected is False

    def test_on_unload_idempotent(self, plugin):
        """Test that on_unload can be called multiple times."""
        plugin.on_unload()
        plugin.on_unload()  # Should not raise


class TestExampleAuditBackendHealthCheck:
    """Test the health_check() method."""

    @pytest.fixture
    def plugin(self, mock_context):
        p = ExampleAuditBackend()
        p.on_load(mock_context)
        return p

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"destination": "localhost"}
        ctx.audit_emit = MagicMock()
        ctx.audit_registry = MagicMock()
        return ctx

    def test_health_check_when_connected(self, plugin):
        """Test health_check returns ok=True when connected."""
        status = plugin.health_check()

        assert status.ok is True
        assert isinstance(status, HealthStatus)

    def test_health_check_when_not_connected(self, plugin):
        """Test health_check returns ok=False when not connected."""
        plugin.is_connected = False

        status = plugin.health_check()

        assert status.ok is False
        assert "not connected" in status.message.lower()


class TestExampleAuditBackendFanout:
    """Test the fanout() method (AuditBackend capability)."""

    @pytest.fixture
    def plugin(self, mock_context):
        p = ExampleAuditBackend()
        p.on_load(mock_context)
        return p

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"destination": "localhost"}
        ctx.audit_emit = MagicMock()
        ctx.audit_registry = MagicMock()
        return ctx

    def test_fanout_stores_event(self, plugin):
        """Test that fanout stores the event."""
        event = {"user_id": "123", "action": "login"}

        plugin.fanout("user.login", event, severity="INFO")

        assert len(plugin.events) == 1
        assert plugin.events[0]["event_type"] == "user.login"
        assert plugin.events[0]["details"] == event

    def test_fanout_with_different_severities(self, plugin):
        """Test fanout with different severity levels."""
        severities = ["DEBUG", "INFO", "WARNING", "ERROR"]

        for severity in severities:
            plugin.fanout("test.event", {}, severity=severity)

        assert len(plugin.events) == len(severities)
        for i, severity in enumerate(severities):
            assert plugin.events[i]["severity"] == severity

    def test_fanout_never_raises(self, plugin):
        """Test that fanout never raises, even with bad input."""
        # Should not raise for any input
        plugin.fanout(None, None)
        plugin.fanout("", {})
        plugin.fanout("test", object())  # Non-dict details


class TestExampleAuditBackendVerifyChain:
    """Test the verify_chain() method."""

    @pytest.fixture
    def plugin(self, mock_context):
        p = ExampleAuditBackend()
        p.on_load(mock_context)
        return p

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"destination": "localhost"}
        ctx.audit_emit = MagicMock()
        ctx.audit_registry = MagicMock()
        return ctx

    def test_verify_chain_when_connected(self, plugin):
        """Test verify_chain returns ok=True when connected."""
        status = plugin.verify_chain()

        assert status.ok is True
        assert isinstance(status, HealthStatus)

    def test_verify_chain_when_not_connected(self, plugin):
        """Test verify_chain returns ok=False when not connected."""
        plugin.is_connected = False

        status = plugin.verify_chain()

        assert status.ok is False


class TestExampleAuditBackendMetrics:
    """Test the get_metrics() method."""

    def test_get_metrics(self):
        """Test that metrics are exported correctly."""
        plugin = ExampleAuditBackend()
        plugin.is_connected = True
        plugin.events = [{"event_type": "test.event"}]

        metrics = plugin.get_metrics()

        assert "events_stored" in metrics
        assert "is_connected" in metrics
        assert metrics["events_stored"] == 1
        assert metrics["is_connected"] == 1
