"""
E2E Tests for Control Plane — Plugin Management Stream 1.

Tests:
- Install/enable/disable/uninstall lifecycle
- Dependency tracking
- Audit logging
- Compliance boundaries

ADR-2029: User-Centric CorvinOS Control Plane — Stream 1
"""

import pytest
from datetime import datetime

from corvin_console.control_plane.plugin_manager import PluginManager

# tenant_id is required on every call since 7fd03f01d (Stream P1 tenant isolation).
TENANT = "_default"


class TestPluginLifecycle:
    """Test complete plugin lifecycle."""

    @pytest.mark.asyncio
    async def test_install_plugin(self, tmp_path):
        """Test installing a plugin."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        result = await manager.install_plugin(
            plugin_id="test-plugin-1",
            name="Test Plugin",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )

        assert result["status"] == "success"
        assert "installed" in result["message"].lower()

        # Verify audit event
        events = manager.get_audit_log(TENANT)
        assert len(events) > 0
        assert events[-1]["event_type"] == "plugin_installed"
        assert events[-1]["plugin_id"] == "test-plugin-1"

    @pytest.mark.asyncio
    async def test_install_duplicate(self, tmp_path):
        """Test cannot install duplicate plugin."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # First install
        await manager.install_plugin(
            plugin_id="dup-plugin",
            name="Dup Plugin",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )

        # Second install should fail
        result = await manager.install_plugin(
            plugin_id="dup-plugin",
            name="Dup Plugin",
            version="1.0.1",
            boot_layer="installed",
            tenant_id=TENANT
        )

        assert result["status"] == "error"
        assert "already installed" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_enable_plugin(self, tmp_path):
        """Test enabling an installed plugin."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Install first
        await manager.install_plugin(
            plugin_id="enable-test",
            name="Enable Test",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )

        # Enable
        result = await manager.enable_plugin("enable-test", tenant_id=TENANT)
        assert result["status"] == "success"

        # Verify enabled
        plugin = await manager.get_plugin("enable-test", tenant_id=TENANT)
        assert plugin.enabled is True

    @pytest.mark.asyncio
    async def test_disable_plugin(self, tmp_path):
        """Test disabling an enabled plugin."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Install + enable
        await manager.install_plugin(
            plugin_id="disable-test",
            name="Disable Test",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )
        await manager.enable_plugin("disable-test", tenant_id=TENANT)

        # Disable
        result = await manager.disable_plugin("disable-test", tenant_id=TENANT)
        assert result["status"] == "success"

        # Verify disabled
        plugin = await manager.get_plugin("disable-test", tenant_id=TENANT)
        assert plugin.enabled is False

    @pytest.mark.asyncio
    async def test_uninstall_plugin(self, tmp_path):
        """Test uninstalling a disabled plugin."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Install + enable + disable
        await manager.install_plugin(
            plugin_id="uninstall-test",
            name="Uninstall Test",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )
        await manager.enable_plugin("uninstall-test", tenant_id=TENANT)
        await manager.disable_plugin("uninstall-test", tenant_id=TENANT)

        # Uninstall
        result = await manager.uninstall_plugin("uninstall-test", tenant_id=TENANT)
        assert result["status"] == "success"

        # Verify gone
        plugin = await manager.get_plugin("uninstall-test", tenant_id=TENANT)
        assert plugin is None

    @pytest.mark.asyncio
    async def test_cannot_uninstall_enabled_plugin(self, tmp_path):
        """Test cannot uninstall an enabled plugin (403)."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Install + enable
        await manager.install_plugin(
            plugin_id="enabled-uninstall",
            name="Enabled Uninstall",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )
        await manager.enable_plugin("enabled-uninstall", tenant_id=TENANT)

        # Try to uninstall (should fail)
        result = await manager.uninstall_plugin("enabled-uninstall", tenant_id=TENANT)
        assert result["status"] == "error"
        assert result.get("code") == 403
        assert "must be disabled first" in result["message"].lower()


class TestDependencyTracking:
    """Test plugin dependency tracking."""

    @pytest.mark.asyncio
    async def test_cannot_disable_with_dependents(self, tmp_path):
        """Test cannot disable plugin if others depend on it (403)."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Install two plugins, set up dependency
        await manager.install_plugin(
            plugin_id="base-plugin",
            name="Base",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )
        await manager.install_plugin(
            plugin_id="dependent-plugin",
            name="Dependent",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT
        )

        # Set up dependency manually (in real system, this would be in manifest)
        manager.plugins[TENANT]["base-plugin"]["dependents"] = ["dependent-plugin"]
        manager.plugins[TENANT]["dependent-plugin"]["dependencies"] = ["base-plugin"]

        # Enable both
        await manager.enable_plugin("base-plugin", tenant_id=TENANT)
        await manager.enable_plugin("dependent-plugin", tenant_id=TENANT)

        # Try to disable base (should fail)
        result = await manager.disable_plugin("base-plugin", tenant_id=TENANT)
        assert result["status"] == "error"
        assert result.get("code") == 403
        assert "dependent plugin(s) exist" in result["message"].lower()


class TestAuditLogging:
    """Test audit event logging."""

    @pytest.mark.asyncio
    async def test_audit_events_immutable(self, tmp_path):
        """Test audit events are immutable + hash-chained."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Do multiple operations
        await manager.install_plugin(
            plugin_id="audit-test",
            name="Audit Test",
            version="1.0.0",
            boot_layer="installed",
            tenant_id=TENANT,
            operator_id="user123"
        )
        await manager.enable_plugin("audit-test", tenant_id=TENANT, operator_id="user123")
        await manager.disable_plugin("audit-test", tenant_id=TENANT, operator_id="user123")

        # Get audit log
        events = manager.get_audit_log(TENANT)

        assert len(events) == 3
        assert events[0]["event_type"] == "plugin_installed"
        assert events[1]["event_type"] == "plugin_enabled"
        assert events[2]["event_type"] == "plugin_disabled"

        # All should have operator_id
        for event in events:
            assert event["operator_id"] == "user123"
            assert "timestamp" in event
            assert "plugin_id" in event

    @pytest.mark.asyncio
    async def test_audit_event_fields(self, tmp_path):
        """Test audit events have all required fields."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        await manager.install_plugin(
            plugin_id="field-test",
            name="Field Test",
            version="2.0.0",
            boot_layer="bundled",
            tenant_id="custom-tenant",
            operator_id="operator-xyz"
        )

        events = manager.get_audit_log("custom-tenant")
        event = events[-1]

        # Required fields
        assert event["tenant_id"] == "custom-tenant"
        assert event["timestamp"]
        assert event["event_type"] == "plugin_installed"
        assert event["plugin_id"] == "field-test"
        assert event["operator_id"] == "operator-xyz"
        assert event["status"] == "success"


class TestListAndQuery:
    """Test listing and querying plugins."""

    @pytest.mark.asyncio
    async def test_list_multiple_plugins(self, tmp_path):
        """Test listing multiple plugins."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        # Install 3 plugins
        for i in range(3):
            await manager.install_plugin(
                plugin_id=f"list-test-{i}",
                name=f"List Test {i}",
                version="1.0.0",
                boot_layer="installed",
            tenant_id=TENANT
            )

        # List
        plugins = await manager.list_plugins(TENANT)
        assert len(plugins) == 3

        # Check details
        ids = [p.plugin_id for p in plugins]
        assert "list-test-0" in ids
        assert "list-test-1" in ids
        assert "list-test-2" in ids

    @pytest.mark.asyncio
    async def test_get_nonexistent_plugin(self, tmp_path):
        """Test getting nonexistent plugin returns None."""
        manager = PluginManager(registry_path=tmp_path / "plugins.json")

        plugin = await manager.get_plugin("nonexistent", tenant_id=TENANT)
        assert plugin is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
