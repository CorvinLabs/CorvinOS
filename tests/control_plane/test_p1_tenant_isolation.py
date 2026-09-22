"""
Stream P1 — Tenant Isolation Tests (Cross-Tenant Boundary Validation).

Verifies that tenant A cannot access, modify, or observe tenant B's plugins/subsystems.
Fail-closed: no cross-tenant leakage under any circumstance.

ADR-2029, defects #1, #8, #9
"""

import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any

from corvin_console.control_plane.plugin_manager import PluginManager
from corvin_console.control_plane.subsystem_manager import SubsystemManager


class TestPluginManagerTenantIsolation:
    """Verify plugin manager enforces tenant isolation."""

    @pytest.fixture
    def temp_registry(self, tmp_path):
        """Create temporary plugin registry."""
        registry_path = tmp_path / "plugins.json"
        return registry_path

    @pytest.fixture
    def manager(self, temp_registry):
        """Create manager with temp registry."""
        return PluginManager(registry_path=temp_registry)

    @pytest.mark.asyncio
    async def test_tenant_a_install_cannot_be_seen_by_tenant_b(self, manager):
        """CRITICAL: Tenant A's plugins must not appear in Tenant B's list."""
        # Tenant A installs a plugin
        result_a = await manager.install_plugin(
            plugin_id="plugin-x",
            name="Plugin X",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_a",
            operator_id="operator_a"
        )
        assert result_a["status"] == "success"

        # Tenant B lists plugins
        plugins_b = await manager.list_plugins(tenant_id="tenant_b")

        # CRITICAL: Tenant B must see nothing (empty list)
        assert len(plugins_b) == 0, "Tenant B saw Tenant A's plugin (CROSS-TENANT LEAKAGE)"

        # Tenant A lists plugins
        plugins_a = await manager.list_plugins(tenant_id="tenant_a")

        # Tenant A must see their own plugin
        assert len(plugins_a) == 1
        assert plugins_a[0].plugin_id == "plugin-x"

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_enable_tenant_b_plugin(self, manager):
        """CRITICAL: Tenant A cannot enable Tenant B's plugins."""
        # Tenant B installs a plugin
        await manager.install_plugin(
            plugin_id="plugin-y",
            name="Plugin Y",
            version="1.0.0",
            boot_layer="installed",
            tenant_id="tenant_b",
            operator_id="operator_b"
        )

        # Tenant A tries to enable Tenant B's plugin
        result = await manager.enable_plugin(
            plugin_id="plugin-y",
            tenant_id="tenant_a",
            operator_id="operator_a"
        )

        # CRITICAL: Must fail (plugin not found for tenant A)
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_audit_log_filtered_by_tenant(self, manager):
        """CRITICAL: Audit logs must not leak across tenants."""
        # Tenant A: install plugin
        await manager.install_plugin(
            plugin_id="plugin-a1",
            name="Plugin A1",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Tenant B: install plugin
        await manager.install_plugin(
            plugin_id="plugin-b1",
            name="Plugin B1",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_b",
            operator_id="op_b"
        )

        # Get audit logs for Tenant A
        logs_a = manager.get_audit_log(tenant_id="tenant_a")

        # CRITICAL: Tenant A must only see their own events
        assert len(logs_a) == 1
        assert logs_a[0]["plugin_id"] == "plugin-a1"
        assert logs_a[0]["tenant_id"] == "tenant_a"

        # Get audit logs for Tenant B
        logs_b = manager.get_audit_log(tenant_id="tenant_b")

        # CRITICAL: Tenant B must only see their own events
        assert len(logs_b) == 1
        assert logs_b[0]["plugin_id"] == "plugin-b1"
        assert logs_b[0]["tenant_id"] == "tenant_b"

    @pytest.mark.asyncio
    async def test_tenant_isolation_survives_restart(self, temp_registry, manager):
        """CRITICAL: Tenant isolation persists after manager restart."""
        # Tenant A installs
        await manager.install_plugin(
            plugin_id="plugin-persist-a",
            name="Plugin Persist A",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Tenant B installs
        await manager.install_plugin(
            plugin_id="plugin-persist-b",
            name="Plugin Persist B",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_b",
            operator_id="op_b"
        )

        # Simulate restart: create new manager, reload from disk
        manager2 = PluginManager(registry_path=temp_registry)

        # CRITICAL: Tenant isolation must survive reload
        plugins_a = await manager2.list_plugins(tenant_id="tenant_a")
        plugins_b = await manager2.list_plugins(tenant_id="tenant_b")

        assert len(plugins_a) == 1
        assert plugins_a[0].plugin_id == "plugin-persist-a"

        assert len(plugins_b) == 1
        assert plugins_b[0].plugin_id == "plugin-persist-b"


class TestSubsystemManagerTenantIsolation:
    """Verify subsystem manager enforces tenant isolation."""

    @pytest.fixture
    def manager(self):
        """Create subsystem manager."""
        return SubsystemManager()

    @pytest.mark.asyncio
    async def test_tenant_a_subsystem_hidden_from_tenant_b(self, manager):
        """CRITICAL: Tenant A's subsystems must not appear in Tenant B's list."""
        # Tenant A starts a subsystem
        await manager.start_subsystem(
            subsystem_id="subsys-a",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Tenant B lists subsystems
        subsystems_b = await manager.list_subsystems(tenant_id="tenant_b")

        # CRITICAL: Tenant B must see nothing (empty list)
        assert len(subsystems_b) == 0, "Tenant B saw Tenant A's subsystem (CROSS-TENANT LEAKAGE)"

        # Tenant A lists subsystems
        subsystems_a = await manager.list_subsystems(tenant_id="tenant_a")

        # Tenant A must see their own subsystem
        assert len(subsystems_a) == 1
        assert subsystems_a[0]["subsystem_id"] == "subsys-a"

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_pause_tenant_b_subsystem(self, manager):
        """CRITICAL: Tenant A cannot control Tenant B's subsystems."""
        # Tenant B starts a subsystem
        await manager.start_subsystem(
            subsystem_id="subsys-b",
            tenant_id="tenant_b",
            operator_id="op_b"
        )

        # Tenant A tries to pause Tenant B's subsystem
        result = await manager.pause_subsystem(
            subsystem_id="subsys-b",
            timeout_s=30,
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # CRITICAL: Must fail (subsystem not found for tenant A)
        assert result["status"] == "error"
        assert "not found" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_subsystem_audit_log_tenant_filtered(self, manager):
        """CRITICAL: Subsystem audit logs must not leak across tenants."""
        # Tenant A: start subsystem
        await manager.start_subsystem(
            subsystem_id="subsys-a1",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Tenant B: start subsystem
        await manager.start_subsystem(
            subsystem_id="subsys-b1",
            tenant_id="tenant_b",
            operator_id="op_b"
        )

        # Get audit logs for Tenant A
        logs_a = manager.get_audit_log(tenant_id="tenant_a")

        # CRITICAL: Tenant A must only see their own events
        assert len(logs_a) == 1
        assert logs_a[0]["subsystem_id"] == "subsys-a1"
        assert logs_a[0]["tenant_id"] == "tenant_a"

        # Get audit logs for Tenant B
        logs_b = manager.get_audit_log(tenant_id="tenant_b")

        # CRITICAL: Tenant B must only see their own events
        assert len(logs_b) == 1
        assert logs_b[0]["subsystem_id"] == "subsys-b1"
        assert logs_b[0]["tenant_id"] == "tenant_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
