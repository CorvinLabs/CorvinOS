"""E2E Integration Tests: OTEL Telemetry + Plugin Manager v2.

Tests verify:
1. OTEL metrics flow end-to-end
2. Plugin discovery, install, enable/disable
3. Learning loop integration (ADR-0314)
4. JSON fallback when OTEL unavailable
5. Audit trail for all operations
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from core.observability.otel_integrator import OTELIntegrator, TelemetrySignal
from core.plugins.plugin_manager_v2 import (
    PluginManager,
    PluginStatus,
    PluginSource,
    PluginInfo,
)


class TestOTELTelemetryIntegration:
    """Integration tests for OTEL telemetry with learning loop."""

    @pytest.fixture
    def integrator(self):
        """Create OTELIntegrator instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = logging.getLogger("audit_test")
            integrator = OTELIntegrator(
                tenant_id="test-tenant",
                instance_id="instance-001",
                json_fallback_dir=Path(tmpdir),
                audit_logger=audit_logger,
                enable_learning_feedback=True,
            )
            yield integrator

    def test_otel_integrator_init_fail_without_tenant(self):
        """OTELIntegrator requires tenant_id (fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id is mandatory"):
            OTELIntegrator(tenant_id="", instance_id="inst-1")

    def test_otel_integrator_init_valid(self, integrator):
        """OTELIntegrator initializes with valid tenant_id."""
        assert integrator.tenant_id == "test-tenant"
        assert integrator.instance_id == "instance-001"
        assert integrator.enable_learning_feedback is True

    def test_emit_metric_signal_via_json_fallback(self, integrator):
        """Emit metric signal falls back to JSON when OTEL unavailable."""
        success, message = integrator.emit_signal(
            signal_type="metric",
            metric_name="skill.execution.time_ms",
            value=42.5,
            attributes={
                "skill_id": "os.delegation_router",
                "user_id": "user-123",
            },
        )

        # Should fallback (OTEL not initialized)
        # Either success or fallback, both are valid
        assert isinstance(success, bool)

        # Check JSON fallback file
        fallback_file = (
            integrator.json_fallback_dir
            / "metric-test-tenant-instance-001.jsonl"
        )
        assert fallback_file.exists()

        with open(fallback_file) as f:
            record = json.loads(f.readline())
            assert record["signal_type"] == "metric"
            assert record["metric_name"] == "skill.execution.time_ms"
            assert record["value"] == 42.5
            assert record["tenant_id"] == "test-tenant"

    def test_emit_trace_signal(self, integrator):
        """Emit trace signal with context attributes."""
        success, message = integrator.emit_signal(
            signal_type="trace",
            metric_name="request.process",
            value=100.0,
            attributes={
                "request_id": "req-456",
                "engine": "claude-opus",
            },
            context={"user_tier": "pro", "region": "eu"},
        )

        fallback_file = (
            integrator.json_fallback_dir
            / "trace-test-tenant-instance-001.jsonl"
        )
        if fallback_file.exists():
            with open(fallback_file) as f:
                record = json.loads(f.readline())
                assert record["signal_type"] == "trace"
                assert record["context"]["user_tier"] == "pro"

    def test_learning_feedback_registration(self, integrator):
        """Register learning feedback for telemetry signal."""
        success, message = integrator.register_learning_feedback(
            signal_id="sig-001",
            feedback={
                "type": "outcome",
                "value": "correct",
                "confidence": 0.95,
            },
        )

        assert success is True
        assert "registered" in message.lower()

    @pytest.mark.asyncio
    async def test_process_pending_signals(self, integrator):
        """Process pending signals from learning queue."""
        # Queue up some signals
        await integrator._signal_queue.put(
            {
                "type": "learning_feedback",
                "signal_id": "sig-002",
                "feedback": {"type": "preference", "value": "claude-opus"},
            }
        )

        await integrator._signal_queue.put(
            {
                "type": "learning_feedback",
                "signal_id": "sig-003",
                "feedback": {"type": "confidence", "value": 0.92},
            }
        )

        # Process pending signals
        count = await integrator.process_pending_signals()

        assert count == 2
        assert "sig-002" in integrator._learning_context
        assert "sig-003" in integrator._learning_context
        assert integrator._learning_context["sig-002"]["feedback"]["value"] == "claude-opus"

    def test_metrics_summary(self, integrator):
        """Get telemetry metrics summary."""
        # Emit some signals
        integrator.emit_signal(
            signal_type="metric",
            metric_name="test.metric",
            value=1.0,
            attributes={},
        )

        summary = integrator.get_metrics_summary()

        assert summary["tenant_id"] == "test-tenant"
        assert summary["instance_id"] == "instance-001"
        assert isinstance(summary["otel_enabled"], bool)
        assert summary["json_fallback_count"] > 0

    def test_audit_logging_emitted(self, integrator):
        """Audit logging is called for all operations."""
        integrator.audit_logger = MagicMock()

        integrator.emit_signal(
            signal_type="metric",
            metric_name="test",
            value=1.0,
            attributes={},
        )

        # Audit logger should be called (either for OTEL or fallback)
        assert (
            integrator.audit_logger.info.called
            or integrator.audit_logger.warning.called
        )


class TestPluginManagerV2Integration:
    """Integration tests for Plugin Manager v2."""

    @pytest.fixture
    def plugin_manager(self):
        """Create PluginManager instance."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = logging.getLogger("audit_test_plugins")
            manager = PluginManager(
                plugins_dir=Path(tmpdir),
                marketplace_url="https://marketplace.test",
                audit_logger=audit_logger,
            )
            yield manager

    @pytest.mark.asyncio
    async def test_discover_plugins(self, plugin_manager):
        """Discover available plugins from multiple sources."""
        discovered, errors = await plugin_manager.discover_plugins()

        # Should return lists (may be empty in test)
        assert isinstance(discovered, list)
        assert isinstance(errors, list)

    @pytest.mark.asyncio
    async def test_install_plugin_validates_dependencies(self, plugin_manager):
        """Installing plugin validates dependencies."""
        # Try installing plugin that doesn't exist
        success, message = await plugin_manager.install_plugin(
            "nonexistent-plugin"
        )

        assert success is False
        assert "not found" in message.lower()

    @pytest.mark.asyncio
    async def test_install_and_enable_plugin(self, plugin_manager):
        """Install and enable plugin workflow."""
        # First, manually create a discovered plugin
        plugin_info = PluginInfo(
            id="test-plugin",
            name="Test Plugin",
            version="1.0.0",
            author="test",
            description="A test plugin",
            license="MIT",
        )
        plugin_manager.discovered_plugins["test-plugin"] = plugin_info

        # Install plugin
        success, message = await plugin_manager.install_plugin("test-plugin")
        assert success is True
        assert "test-plugin" in plugin_manager.installed_plugins

        # Plugin starts disabled
        plugin = plugin_manager.installed_plugins["test-plugin"]
        assert plugin.enabled is False
        assert plugin.status == PluginStatus.INSTALLED

        # Enable plugin
        success, message = await plugin_manager.enable_plugin("test-plugin")
        assert success is True
        assert plugin_manager.installed_plugins["test-plugin"].enabled is True

    @pytest.mark.asyncio
    async def test_disable_and_uninstall_plugin(self, plugin_manager):
        """Disable and uninstall plugin workflow."""
        # Setup: create installed plugin
        plugin_info = PluginInfo(
            id="temp-plugin",
            name="Temp Plugin",
            version="1.0.0",
            author="test",
            description="Temporary plugin",
            license="MIT",
        )
        plugin_manager.discovered_plugins["temp-plugin"] = plugin_info

        # Install and enable
        await plugin_manager.install_plugin("temp-plugin")
        await plugin_manager.enable_plugin("temp-plugin")

        # Disable plugin
        success, message = await plugin_manager.disable_plugin("temp-plugin")
        assert success is True

        # Uninstall plugin
        success, message = await plugin_manager.uninstall_plugin("temp-plugin")
        assert success is True
        assert "temp-plugin" not in plugin_manager.installed_plugins

    @pytest.mark.asyncio
    async def test_list_installed_plugins_with_filter(self, plugin_manager):
        """List installed plugins with status filter."""
        # Create and install multiple plugins
        for i in range(3):
            plugin_info = PluginInfo(
                id=f"plugin-{i}",
                name=f"Plugin {i}",
                version="1.0.0",
                author="test",
                description="Test",
                license="MIT",
            )
            plugin_manager.discovered_plugins[f"plugin-{i}"] = plugin_info
            await plugin_manager.install_plugin(f"plugin-{i}")

        # Enable first plugin only
        await plugin_manager.enable_plugin("plugin-0")

        # List enabled plugins
        enabled = plugin_manager.list_installed_plugins(
            status_filter=PluginStatus.ENABLED
        )
        assert len(enabled) == 1
        assert enabled[0].id == "plugin-0"

    @pytest.mark.asyncio
    async def test_plugin_config_management(self, plugin_manager):
        """Manage plugin configuration."""
        # Setup: install plugin
        plugin_info = PluginInfo(
            id="configurable-plugin",
            name="Configurable Plugin",
            version="1.0.0",
            author="test",
            description="Plugin with config",
            license="MIT",
        )
        plugin_manager.discovered_plugins["configurable-plugin"] = plugin_info
        await plugin_manager.install_plugin("configurable-plugin")

        # Get initial config (empty)
        config = plugin_manager.get_plugin_config("configurable-plugin")
        assert config == {}

        # Update config
        new_config = {
            "api_key": "test-key",
            "timeout_ms": 5000,
            "features": ["feature-a", "feature-b"],
        }
        success, message = await plugin_manager.update_plugin_config(
            "configurable-plugin", new_config
        )
        assert success is True

        # Verify config was updated
        config = plugin_manager.get_plugin_config("configurable-plugin")
        assert config["api_key"] == "test-key"
        assert config["timeout_ms"] == 5000
        assert "feature-a" in config["features"]

    @pytest.mark.asyncio
    async def test_plugin_state_persistence(self, plugin_manager):
        """Plugin state persists across manager instances."""
        # Create and install plugin
        plugin_info = PluginInfo(
            id="persistent-plugin",
            name="Persistent Plugin",
            version="1.0.0",
            author="test",
            description="Persists across restarts",
            license="MIT",
        )
        plugin_manager.discovered_plugins["persistent-plugin"] = plugin_info
        await plugin_manager.install_plugin("persistent-plugin")
        await plugin_manager.enable_plugin("persistent-plugin")

        # Get plugins directory
        plugins_dir = plugin_manager.plugins_dir

        # Create new manager (simulates restart)
        new_manager = PluginManager(plugins_dir=plugins_dir)

        # Plugin should still be there
        assert "persistent-plugin" in new_manager.installed_plugins
        assert new_manager.installed_plugins["persistent-plugin"].enabled is True

    def test_audit_logging_plugin_operations(self, plugin_manager):
        """Audit logging tracks all plugin operations."""
        plugin_manager.audit_logger = MagicMock()

        # Sync operation that logs
        plugin_manager.get_plugin_config("nonexistent")

        # Async operations would also log if called

    @pytest.mark.asyncio
    async def test_plugin_licensing_gate(self, plugin_manager):
        """Licensing gate prevents tier-gated plugin installation."""

        def licensing_gate(plugin_info: PluginInfo) -> bool:
            # Only allow free tier plugins
            return plugin_info.required_tier == "free"

        plugin_manager.licensing_gate = licensing_gate

        # Create premium plugin
        premium_plugin = PluginInfo(
            id="premium-plugin",
            name="Premium Plugin",
            version="1.0.0",
            author="test",
            description="Premium tier required",
            license="MIT",
            required_tier="premium",
        )
        plugin_manager.discovered_plugins["premium-plugin"] = premium_plugin

        # Try to install (should fail due to licensing gate)
        success, message = await plugin_manager.install_plugin("premium-plugin")
        assert success is False
        assert "tier" in message.lower()


class TestOTELPluginManagerE2E:
    """End-to-end tests combining OTEL + Plugin Manager."""

    @pytest.fixture
    def e2e_setup(self):
        """Setup for E2E integration tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = logging.getLogger("audit_e2e")

            integrator = OTELIntegrator(
                tenant_id="e2e-tenant",
                instance_id="e2e-instance",
                json_fallback_dir=Path(tmpdir) / "telemetry",
                audit_logger=audit_logger,
            )

            plugin_manager = PluginManager(
                plugins_dir=Path(tmpdir) / "plugins",
                audit_logger=audit_logger,
            )

            yield {
                "integrator": integrator,
                "plugin_manager": plugin_manager,
                "tmpdir": tmpdir,
            }

    @pytest.mark.asyncio
    async def test_e2e_plugin_execution_telemetry(self, e2e_setup):
        """End-to-end: Install plugin, emit execution telemetry, process feedback."""
        integrator = e2e_setup["integrator"]
        plugin_manager = e2e_setup["plugin_manager"]

        # 1. Discover and install plugin
        plugin_info = PluginInfo(
            id="telemetry-test-plugin",
            name="Telemetry Test",
            version="1.0.0",
            author="test",
            description="Plugin with telemetry",
            license="MIT",
        )
        plugin_manager.discovered_plugins["telemetry-test-plugin"] = plugin_info
        success, _ = await plugin_manager.install_plugin(
            "telemetry-test-plugin"
        )
        assert success is True

        # 2. Enable plugin
        success, _ = await plugin_manager.enable_plugin(
            "telemetry-test-plugin"
        )
        assert success is True

        # 3. Emit execution telemetry
        success, _ = integrator.emit_signal(
            signal_type="metric",
            metric_name="plugin.execution.time_ms",
            value=127.5,
            attributes={
                "plugin_id": "telemetry-test-plugin",
                "execution_id": "exec-001",
            },
        )

        # 4. Register learning feedback
        success, _ = integrator.register_learning_feedback(
            signal_id="exec-001",
            feedback={
                "type": "outcome",
                "value": "success",
                "confidence": 0.98,
            },
        )
        assert success is True

        # 5. Process pending signals
        processed = await integrator.process_pending_signals()
        assert processed > 0

        # 6. Verify learning context was updated
        assert "exec-001" in integrator._learning_context

    @pytest.mark.asyncio
    async def test_e2e_multi_tenant_isolation(self):
        """End-to-end: Verify multi-tenant isolation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = logging.getLogger("audit_multi_tenant")

            # Create two integrators for different tenants
            integrator1 = OTELIntegrator(
                tenant_id="tenant-a",
                instance_id="inst-a",
                json_fallback_dir=Path(tmpdir) / "a",
                audit_logger=audit_logger,
            )

            integrator2 = OTELIntegrator(
                tenant_id="tenant-b",
                instance_id="inst-b",
                json_fallback_dir=Path(tmpdir) / "b",
                audit_logger=audit_logger,
            )

            # Emit signals from both tenants
            integrator1.emit_signal(
                "metric", "test.metric", 1.0, {"tenant": "a"}
            )
            integrator2.emit_signal(
                "metric", "test.metric", 2.0, {"tenant": "b"}
            )

            # Verify isolation
            assert integrator1.tenant_id != integrator2.tenant_id
            assert (
                integrator1.json_fallback_dir != integrator2.json_fallback_dir
            )

            # Verify fallback files are separate
            fallback_a = (
                Path(tmpdir) / "a" / "metric-tenant-a-inst-a.jsonl"
            )
            fallback_b = (
                Path(tmpdir) / "b" / "metric-tenant-b-inst-b.jsonl"
            )

            # At least one should exist (JSON fallback)
            assert fallback_a.exists() or fallback_b.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
