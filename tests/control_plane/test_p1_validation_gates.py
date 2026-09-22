"""
Stream P1 — Input Validation Tests (Fail-Closed Gates).

Verifies that invalid boot_layer, timeout_s, tenant_id, and dependencies are rejected.
Fail-closed: all validation errors must propagate as ValueError.

ADR-2029, defects #3, #4, #6
"""

import pytest
import asyncio
from pathlib import Path

from corvin_console.control_plane.plugin_manager import PluginManager, BootLayer
from corvin_console.control_plane.subsystem_manager import SubsystemManager


class TestBootLayerValidation:
    """Verify boot_layer enum validation is fail-closed."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create manager with temp registry."""
        registry_path = tmp_path / "plugins.json"
        return PluginManager(registry_path=registry_path)

    @pytest.mark.asyncio
    async def test_install_invalid_boot_layer_raises_error(self, manager):
        """CRITICAL: Invalid boot_layer must raise ValueError (fail-closed)."""
        with pytest.raises(ValueError) as exc_info:
            await manager.install_plugin(
                plugin_id="plugin-bad",
                name="Plugin Bad",
                version="1.0.0",
                boot_layer="invalid_layer",  # Not in enum
                tenant_id="tenant_a",
                operator_id="op_a"
            )

        # CRITICAL: Error must mention valid options
        assert "compliance" in str(exc_info.value)
        assert "core" in str(exc_info.value)
        assert "bundled" in str(exc_info.value)

    @pytest.mark.parametrize("valid_layer", [
        "compliance", "core", "bundled", "installed", "community"
    ])
    @pytest.mark.asyncio
    async def test_install_valid_boot_layers_accepted(self, manager, valid_layer):
        """All valid boot layers must be accepted."""
        result = await manager.install_plugin(
            plugin_id=f"plugin-{valid_layer}",
            name=f"Plugin {valid_layer}",
            version="1.0.0",
            boot_layer=valid_layer,
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        assert result["status"] == "success"


class TestTimeoutValidation:
    """Verify timeout_s bounds validation is fail-closed."""

    @pytest.fixture
    def manager(self):
        """Create subsystem manager."""
        return SubsystemManager()

    @pytest.mark.asyncio
    async def test_pause_negative_timeout_raises_error(self, manager):
        """CRITICAL: Negative timeout_s must raise ValueError (fail-closed)."""
        # Start a subsystem first
        await manager.start_subsystem(
            subsystem_id="subsys-1",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        with pytest.raises(ValueError) as exc_info:
            await manager.pause_subsystem(
                subsystem_id="subsys-1",
                timeout_s=-5,  # Negative
                tenant_id="tenant_a",
                operator_id="op_a"
            )

        assert "must be between" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pause_zero_timeout_raises_error(self, manager):
        """CRITICAL: Zero timeout_s must raise ValueError (fail-closed)."""
        await manager.start_subsystem(
            subsystem_id="subsys-2",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        with pytest.raises(ValueError) as exc_info:
            await manager.pause_subsystem(
                subsystem_id="subsys-2",
                timeout_s=0,  # Zero
                tenant_id="tenant_a",
                operator_id="op_a"
            )

        assert "must be between" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_pause_huge_timeout_raises_error(self, manager):
        """CRITICAL: Unbounded timeout_s must raise ValueError (fail-closed)."""
        await manager.start_subsystem(
            subsystem_id="subsys-3",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        with pytest.raises(ValueError) as exc_info:
            await manager.pause_subsystem(
                subsystem_id="subsys-3",
                timeout_s=99999,  # Way too large
                tenant_id="tenant_a",
                operator_id="op_a"
            )

        assert "must be between" in str(exc_info.value)

    @pytest.mark.parametrize("valid_timeout", [1, 30, 60, 300, 3600])
    @pytest.mark.asyncio
    async def test_pause_valid_timeouts_accepted(self, manager, valid_timeout):
        """All valid timeout_s values must be accepted."""
        subsys_id = f"subsys-valid-{valid_timeout}"

        await manager.start_subsystem(
            subsystem_id=subsys_id,
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        result = await manager.pause_subsystem(
            subsystem_id=subsys_id,
            timeout_s=valid_timeout,
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        assert result["status"] == "success"


class TestDependencyChecking:
    """Verify dependency checking prevents unsafe disables."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create manager with temp registry."""
        registry_path = tmp_path / "plugins.json"
        return PluginManager(registry_path=registry_path)

    @pytest.mark.asyncio
    async def test_cannot_disable_plugin_with_dependents(self, manager):
        """CRITICAL: Cannot disable a plugin if other plugins depend on it."""
        # Install two plugins
        await manager.install_plugin(
            plugin_id="plugin-core",
            name="Plugin Core",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        await manager.install_plugin(
            plugin_id="plugin-extension",
            name="Plugin Extension",
            version="1.0.0",
            boot_layer="installed",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Set up dependency: plugin-extension depends on plugin-core
        manager.plugins["tenant_a"]["plugin-core"]["dependents"] = ["plugin-extension"]
        manager.plugins["tenant_a"]["plugin-extension"]["dependencies"] = ["plugin-core"]

        # Enable both plugins
        await manager.enable_plugin(
            plugin_id="plugin-core",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        await manager.enable_plugin(
            plugin_id="plugin-extension",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Try to disable plugin-core (has dependent)
        result = await manager.disable_plugin(
            plugin_id="plugin-core",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # CRITICAL: Must be denied with 403 Forbidden
        assert result["status"] == "error"
        assert result.get("code") == 403
        assert "dependent" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_can_disable_plugin_with_no_dependents(self, manager):
        """Plugins with no dependents can be disabled."""
        await manager.install_plugin(
            plugin_id="plugin-safe",
            name="Plugin Safe",
            version="1.0.0",
            boot_layer="bundled",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        await manager.enable_plugin(
            plugin_id="plugin-safe",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        # Should succeed (no dependents)
        result = await manager.disable_plugin(
            plugin_id="plugin-safe",
            tenant_id="tenant_a",
            operator_id="op_a"
        )

        assert result["status"] == "success"


class TestTenantValidation:
    """Verify tenant_id validation is fail-closed."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create manager with temp registry."""
        registry_path = tmp_path / "plugins.json"
        return PluginManager(registry_path=registry_path)

    @pytest.fixture
    def subsys_manager(self):
        """Create subsystem manager."""
        return SubsystemManager()

    @pytest.mark.asyncio
    async def test_install_none_tenant_raises_error(self, manager):
        """CRITICAL: None tenant_id must raise ValueError (fail-closed)."""
        with pytest.raises(ValueError) as exc_info:
            await manager.install_plugin(
                plugin_id="plugin-bad",
                name="Plugin Bad",
                version="1.0.0",
                boot_layer="bundled",
                tenant_id=None,  # None
                operator_id="op_a"
            )

        assert "must be a non-empty string" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_install_empty_tenant_raises_error(self, manager):
        """CRITICAL: Empty tenant_id must raise ValueError (fail-closed)."""
        with pytest.raises(ValueError) as exc_info:
            await manager.install_plugin(
                plugin_id="plugin-bad",
                name="Plugin Bad",
                version="1.0.0",
                boot_layer="bundled",
                tenant_id="",  # Empty
                operator_id="op_a"
            )

        assert "must be a non-empty string" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_list_none_tenant_raises_error(self, manager):
        """CRITICAL: None tenant_id in list must raise ValueError (fail-closed)."""
        with pytest.raises(ValueError):
            await manager.list_plugins(tenant_id=None)

    @pytest.mark.asyncio
    async def test_start_subsystem_none_tenant_raises_error(self, subsys_manager):
        """CRITICAL: None tenant_id in subsystem must raise ValueError (fail-closed)."""
        with pytest.raises(ValueError) as exc_info:
            await subsys_manager.start_subsystem(
                subsystem_id="subsys-1",
                tenant_id=None,  # None
                operator_id="op_a"
            )

        assert "must be a non-empty string" in str(exc_info.value)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
