"""Test suite for SlackNotifierPlugin.

Demonstrates testing patterns for CorvinOS plugins:
- Unit tests for the plugin class
- Mocking external dependencies (requests library)
- Testing lifecycle methods
- Testing error handling
- Verifying health checks
"""

import json
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from slack_notifier_plugin import SlackNotifierPlugin
from corvin.core.plugins.corvin_plugins.protocol import HealthStatus, PluginContext


class TestSlackNotifierPluginInitialization:
    """Test plugin initialization and configuration."""

    @pytest.fixture
    def plugin(self):
        """Create a fresh plugin instance."""
        return SlackNotifierPlugin()

    @pytest.fixture
    def mock_context(self):
        """Create a mock PluginContext."""
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {
            "webhook_url": "REDACTED_SLACK_WEBHOOK_URL_FOR_TESTING",
            "test_webhook_on_load": False,  # Disable for fast tests
        }
        ctx.plugin_id = "slack-notifier"
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()
        ctx.notification_registry = MagicMock()
        return ctx

    def test_attributes_defined(self, plugin):
        """Test that all required attributes are defined."""
        assert plugin.plugin_id == "slack-notifier"
        assert plugin.plugin_type == "notification_backend"
        assert plugin.version == "1.0.0"
        assert plugin.display_name == "Slack Notifier"

    def test_on_load_success(self, plugin, mock_context):
        """Test successful plugin loading."""
        plugin.on_load(mock_context)

        # Should register with the registry
        mock_context.notification_registry.register.assert_called_once_with(
            "slack-notifier", plugin
        )

        # Should emit an audit event
        mock_context.audit_emit.assert_called_once()
        call_args, call_kwargs = mock_context.audit_emit.call_args
        assert call_args[0] == "plugin.loaded"
        assert call_args[1]["plugin_id"] == "slack-notifier"

        # Plugin should be enabled
        assert plugin.enabled is True
        assert plugin.webhook_url == mock_context.config["webhook_url"]

    def test_on_load_missing_webhook_url(self, plugin, mock_context):
        """Test that on_load fails if webhook_url is missing."""
        mock_context.config = {}

        with pytest.raises(ValueError, match="webhook_url"):
            plugin.on_load(mock_context)

    def test_on_load_invalid_webhook_url(self, plugin, mock_context):
        """Test that on_load fails if webhook_url is invalid."""
        mock_context.config = {
            "webhook_url": "http://example.com/webhook",  # Should be https://hooks.slack.com/
        }

        with pytest.raises(ValueError, match="https://hooks.slack.com/"):
            plugin.on_load(mock_context)

    def test_on_load_with_webhook_test(self, plugin, mock_context):
        """Test that on_load tests the webhook if enabled."""
        mock_context.config["test_webhook_on_load"] = True

        with patch.object(plugin, "_test_webhook") as mock_test:
            plugin.on_load(mock_context)
            mock_test.assert_called_once()

    def test_on_load_webhook_test_failure_doesnt_fail_load(
        self, plugin, mock_context
    ):
        """Test that a webhook test failure doesn't prevent loading."""
        mock_context.config["test_webhook_on_load"] = True

        with patch.object(plugin, "_test_webhook", side_effect=RuntimeError("test")):
            # Should not raise; should just mark the last_error
            plugin.on_load(mock_context)

            assert plugin.last_error is not None
            assert "Webhook test failed" in plugin.last_error


class TestSlackNotifierPluginLifecycle:
    """Test plugin lifecycle methods."""

    @pytest.fixture
    def plugin(self, mock_context):
        """Create and load a plugin."""
        p = SlackNotifierPlugin()
        p.on_load(mock_context)
        return p

    @pytest.fixture
    def mock_context(self):
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {
            "webhook_url": "REDACTED_SLACK_WEBHOOK_URL_FOR_TESTING",
            "test_webhook_on_load": False,
        }
        ctx.plugin_id = "slack-notifier"
        ctx.tenant_id = "_default"
        ctx.audit_emit = MagicMock()
        ctx.notification_registry = MagicMock()
        return ctx

    def test_on_enable(self, plugin):
        """Test that on_enable sets the plugin as enabled."""
        plugin.enabled = False
        plugin.on_enable()
        assert plugin.enabled is True

    def test_on_disable(self, plugin):
        """Test that on_disable disables the plugin."""
        plugin.enabled = True
        plugin.on_disable()
        assert plugin.enabled is False

    def test_on_unload(self, plugin):
        """Test that on_unload cleans up state."""
        assert plugin.webhook_url is not None
        assert plugin.enabled is True

        plugin.on_unload()

        assert plugin.webhook_url is None
        assert plugin.enabled is False

    def test_on_unload_idempotent(self, plugin):
        """Test that on_unload can be called multiple times."""
        plugin.on_unload()
        plugin.on_unload()  # Second call should also work
        assert plugin.enabled is False


class TestSlackNotifierPluginHealthCheck:
    """Test the health_check() method."""

    @pytest.fixture
    def plugin(self):
        p = SlackNotifierPlugin()
        p.webhook_url = "REDACTED_SLACK_WEBHOOK_URL_FOR_TESTING"
        p.enabled = True
        return p

    def test_health_check_when_connected(self, plugin):
        """Test health_check returns ok=True when configured and enabled."""
        status = plugin.health_check()

        assert status.ok is True
        assert "Connected" in status.message or "operational" in status.message

    def test_health_check_when_webhook_not_configured(self, plugin):
        """Test health_check returns ok=False when webhook is not configured."""
        plugin.webhook_url = None

        status = plugin.health_check()

        assert status.ok is False
        assert "configured" in status.message.lower()

    def test_health_check_when_disabled(self, plugin):
        """Test health_check returns ok=False when plugin is disabled."""
        plugin.enabled = False

        status = plugin.health_check()

        assert status.ok is False
        assert "disabled" in status.message.lower()

    def test_health_check_with_high_error_rate(self, plugin):
        """Test health_check returns ok=False when error rate is high."""
        # Simulate 20 requests sent, 5 failed (25% error rate)
        plugin.requests_sent = 20
        plugin.requests_failed = 5

        status = plugin.health_check()

        assert status.ok is False
        assert "error rate" in status.message.lower()

    def test_health_check_with_acceptable_error_rate(self, plugin):
        """Test health_check returns ok=True when error rate is acceptable."""
        # Simulate 100 requests sent, 5 failed (5% error rate)
        plugin.requests_sent = 100
        plugin.requests_failed = 5

        status = plugin.health_check()

        assert status.ok is True

    def test_health_check_no_requests_yet(self, plugin):
        """Test health_check returns ok=True when no requests have been sent."""
        assert plugin.requests_sent == 0
        assert plugin.requests_failed == 0

        status = plugin.health_check()

        assert status.ok is True
        assert "no requests" in status.message.lower()


class TestSlackNotifierPluginNotify:
    """Test the notify() method (NotificationBackend capability)."""

    @pytest.fixture
    def plugin(self):
        p = SlackNotifierPlugin()
        p.webhook_url = "REDACTED_SLACK_WEBHOOK_URL_FOR_TESTING"
        p.enabled = True
        return p

    def test_notify_sends_to_slack(self, plugin):
        """Test that notify() sends a message to Slack."""
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200

            plugin.notify(
                "user.login",
                {"user_id": "123", "ip": "192.168.1.1"},
                severity="info",
            )

            # Should have posted to the webhook
            mock_post.assert_called_once()
            call_args, call_kwargs = mock_post.call_args
            assert call_args[0] == plugin.webhook_url
            assert call_kwargs["json"]["text"] == ":info: user.login"

            # Should increment requests_sent
            assert plugin.requests_sent == 1
            assert plugin.requests_failed == 0

    def test_notify_increments_error_count_on_failure(self, plugin):
        """Test that notify() increments error count on HTTP failure."""
        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 500

            plugin.notify("plugin.error", {"error": "test"})

            # Should increment error count
            assert plugin.requests_sent == 0
            assert plugin.requests_failed == 1
            assert "500" in plugin.last_error

    def test_notify_handles_timeout(self, plugin):
        """Test that notify() handles request timeouts gracefully."""
        with patch("requests.post", side_effect=requests.Timeout):
            plugin.notify("plugin.error", {"error": "test"})

            # Should not raise; should increment error count
            assert plugin.requests_failed == 1
            assert plugin.last_error == "Timeout"

    def test_notify_handles_connection_error(self, plugin):
        """Test that notify() handles connection errors gracefully."""
        with patch("requests.post", side_effect=requests.ConnectionError):
            plugin.notify("plugin.error", {"error": "test"})

            # Should not raise; should increment error count
            assert plugin.requests_failed == 1
            assert "ConnectionError" in plugin.last_error

    def test_notify_when_not_enabled(self, plugin):
        """Test that notify() doesn't send when plugin is disabled."""
        plugin.enabled = False

        with patch("requests.post") as mock_post:
            plugin.notify("user.login", {})

            mock_post.assert_not_called()
            assert plugin.requests_failed == 1

    def test_notify_when_webhook_not_configured(self, plugin):
        """Test that notify() doesn't send when webhook is not configured."""
        plugin.webhook_url = None

        with patch("requests.post") as mock_post:
            plugin.notify("user.login", {})

            mock_post.assert_not_called()
            assert plugin.requests_failed == 1


class TestSlackNotifierPluginFormatting:
    """Test the message formatting logic."""

    @pytest.fixture
    def plugin(self):
        return SlackNotifierPlugin()

    def test_format_slack_message_structure(self, plugin):
        """Test that formatted messages have the expected structure."""
        message = plugin._format_slack_message(
            event="user.login",
            payload={"user_id": "123", "ip": "192.168.1.1"},
            severity="info",
            tenant_id="_default",
        )

        # Should be a valid Slack message
        assert "text" in message
        assert "attachments" in message
        assert len(message["attachments"]) > 0

        # First attachment should have color and fields
        attachment = message["attachments"][0]
        assert "color" in attachment
        assert "fields" in attachment

    def test_format_slack_message_color_by_severity(self, plugin):
        """Test that message color varies by severity."""
        severities = {
            "debug": "#808080",
            "info": "#439FE0",
            "warning": "#FF9800",
            "error": "#F44336",
            "critical": "#9C27B0",
        }

        for severity, expected_color in severities.items():
            message = plugin._format_slack_message(
                "test", {}, severity, "_default"
            )
            assert message["attachments"][0]["color"] == expected_color

    def test_format_slack_message_truncates_large_payload(self, plugin):
        """Test that large payloads are truncated to avoid huge messages."""
        large_payload = {"data": "x" * 10000}

        message = plugin._format_slack_message("test", large_payload, "info", "_default")

        # Payload should be truncated
        attachment_text = message["attachments"][0]["text"]
        assert len(attachment_text) < 600  # Should have "..." truncation indicator


class TestSlackNotifierPluginMetrics:
    """Test the metrics export."""

    def test_get_metrics(self):
        """Test that get_metrics returns expected metrics."""
        plugin = SlackNotifierPlugin()
        plugin.requests_sent = 100
        plugin.requests_failed = 5

        metrics = plugin.get_metrics()

        assert metrics["requests_sent"] == 100
        assert metrics["requests_failed"] == 5
        assert metrics["total_requests"] == 105
        assert metrics["error_rate_percent"] == pytest.approx(5.0, rel=0.1)

    def test_get_metrics_zero_requests(self):
        """Test metrics when no requests have been sent."""
        plugin = SlackNotifierPlugin()

        metrics = plugin.get_metrics()

        assert metrics["requests_sent"] == 0
        assert metrics["requests_failed"] == 0
        assert metrics["total_requests"] == 0
        assert metrics["error_rate_percent"] == 0


class TestSlackNotifierPluginWebhookTest:
    """Test the webhook validation."""

    def test_test_webhook_success(self):
        """Test successful webhook validation."""
        plugin = SlackNotifierPlugin()
        plugin.webhook_url = "REDACTED_SLACK_WEBHOOK_URL_FOR_TESTING"

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 200

            # Should not raise
            plugin._test_webhook()

            # Should have posted to the webhook
            mock_post.assert_called_once()

    def test_test_webhook_failure(self):
        """Test webhook validation failure."""
        plugin = SlackNotifierPlugin()
        plugin.webhook_url = "https://hooks.slack.com/services/invalid"

        with patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 404

            with pytest.raises(RuntimeError, match="404"):
                plugin._test_webhook()

    def test_test_webhook_connection_error(self):
        """Test webhook validation with connection error."""
        plugin = SlackNotifierPlugin()
        plugin.webhook_url = "https://hooks.slack.com/services/unreachable"

        with patch(
            "requests.post", side_effect=requests.ConnectionError
        ):
            with pytest.raises(requests.ConnectionError):
                plugin._test_webhook()
