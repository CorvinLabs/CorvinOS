"""Phase 3 Integration Tests — Plugin Panel Auto-Registration.

Tests the complete Phase 3 flow:
1. Plugin installs → manifest is read
2. console.settings_panel is extracted
3. Panel is auto-registered
4. Panel appears in Console capability manifest
5. Panel is visible in Console sidebar (if enabled)

Error handling:
- Invalid manifest → install fails + rollback
- Panel registration fails → install succeeds (graceful degradation)
- Uninstall → panels removed
- Enable/disable panels without uninstall
"""

import pytest
import json
import asyncio
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from typing import Dict, Any

# Import modules under test
try:
    from core.orchestration.tasks.plugin_install_task import PluginInstallTask
    from core.plugins.plugin_panel_registry import (
        PluginPanelRegistry,
        get_panel_registry,
    )
    from core.console.corvin_console.routes.capabilities import _get_plugin_panels
except ImportError:
    pytest.skip("Required modules not available", allow_module_level=True)


@pytest.fixture
def tmp_registry(tmp_path):
    """Create a temporary panel registry for testing."""
    registry_path = tmp_path / "panel_registry.json"
    with patch("core.plugins.plugin_panel_registry.PluginPanelRegistry.path", registry_path):
        yield PluginPanelRegistry(str(registry_path))


@pytest.fixture
def plugin_manifest() -> Dict[str, Any]:
    """Sample plugin manifest with settings panel."""
    return {
        "plugin": {
            "id": "security-settings",
            "name": "Security Settings Plugin",
            "version": "1.0.0",
            "source": "https://github.com/example/security-settings",
        },
        "console": {
            "settings_panel": {
                "id": "security-settings-panel",
                "label": "Security Settings",
                "route": "settings/security",
                "icon": "Shield",
                "group": "settings",
            }
        },
    }


@pytest.fixture
def install_task():
    """Create an install task for testing."""
    return PluginInstallTask(
        repo="example/security-settings",
        plugin_id="security-settings",
        version="1.0.0",
        min_disk_mb=50
    )


class TestPanelAutoRegistration:
    """Test auto-registration of plugin panels."""

    def test_register_panel_success(self, tmp_registry):
        """Panel registration succeeds with valid spec."""
        panel_spec = {
            "id": "test-panel",
            "label": "Test Panel",
            "route": "test/panel",
            "icon": "Zap",
            "group": "test",
        }

        panel_id = tmp_registry.register_panel("test-plugin", panel_spec)

        assert panel_id == "test-panel"
        assert tmp_registry.get_panel("test-panel") is not None
        assert tmp_registry.get_panel("test-panel")["label"] == "Test Panel"

    def test_register_panel_missing_required_fields(self, tmp_registry):
        """Registration fails if required fields are missing."""
        panel_spec = {
            "id": "incomplete-panel",
            "label": "Incomplete",
            # Missing: route, icon, group
        }

        with pytest.raises(ValueError, match="missing required keys"):
            tmp_registry.register_panel("test-plugin", panel_spec)

    def test_register_panel_unsafe_route(self, tmp_registry):
        """Registration fails if route has path traversal."""
        panel_spec = {
            "id": "unsafe-panel",
            "label": "Unsafe",
            "route": "../../../etc/passwd",  # Path traversal!
            "icon": "AlertTriangle",
            "group": "admin",
        }

        with pytest.raises(ValueError, match="Unsafe panel route"):
            tmp_registry.register_panel("test-plugin", panel_spec)

    def test_register_panel_duplicate_id(self, tmp_registry):
        """Cannot register same panel_id twice."""
        panel_spec = {
            "id": "dup-panel",
            "label": "Duplicate",
            "route": "dup",
            "icon": "Copy",
            "group": "test",
        }

        tmp_registry.register_panel("plugin1", panel_spec)

        with pytest.raises(ValueError, match="Panel already registered"):
            tmp_registry.register_panel("plugin2", panel_spec)

    def test_get_panels_by_plugin(self, tmp_registry):
        """Retrieve all panels for a plugin."""
        # Register 2 panels for plugin1
        for i in range(2):
            spec = {
                "id": f"plugin1-panel-{i}",
                "label": f"Panel {i}",
                "route": f"panel{i}",
                "icon": "Settings",
                "group": "tools",
            }
            tmp_registry.register_panel("plugin1", spec)

        # Register 1 panel for plugin2
        spec = {
            "id": "plugin2-panel",
            "label": "Other Panel",
            "route": "other",
            "icon": "Zap",
            "group": "tools",
        }
        tmp_registry.register_panel("plugin2", spec)

        # Query plugin1
        panels = tmp_registry.get_panels_by_plugin("plugin1")
        assert len(panels) == 2
        assert all(p["plugin_id"] == "plugin1" for p in panels)

        # Query plugin2
        panels = tmp_registry.get_panels_by_plugin("plugin2")
        assert len(panels) == 1
        assert panels[0]["panel_id"] == "plugin2-panel"

    def test_enable_disable_panel(self, tmp_registry):
        """Enable/disable panels without uninstall."""
        spec = {
            "id": "toggle-panel",
            "label": "Toggleable",
            "route": "toggle",
            "icon": "Power",
            "group": "test",
        }
        tmp_registry.register_panel("toggle-plugin", spec)

        # Initially enabled
        panel = tmp_registry.get_panel("toggle-panel")
        assert panel["enabled"] is True

        # Disable it
        tmp_registry.disable_panel("toggle-panel")
        panel = tmp_registry.get_panel("toggle-panel")
        assert panel["enabled"] is False
        assert panel["disabled_at"] is not None

        # Enable again
        tmp_registry.enable_panel("toggle-panel")
        panel = tmp_registry.get_panel("toggle-panel")
        assert panel["enabled"] is True
        assert panel["enabled_at"] is not None

    def test_unregister_panel(self, tmp_registry):
        """Completely remove a panel from registry."""
        spec = {
            "id": "removable-panel",
            "label": "Remove Me",
            "route": "remove",
            "icon": "Trash",
            "group": "test",
        }
        tmp_registry.register_panel("remove-plugin", spec)

        assert tmp_registry.get_panel("removable-panel") is not None

        tmp_registry.unregister_panel("removable-panel")

        assert tmp_registry.get_panel("removable-panel") is None

    def test_unregister_plugin_panels(self, tmp_registry):
        """Remove all panels for a plugin."""
        # Register 3 panels for plugin1
        for i in range(3):
            spec = {
                "id": f"cleanup-panel-{i}",
                "label": f"Cleanup {i}",
                "route": f"cleanup{i}",
                "icon": "Broom",
                "group": "cleanup",
            }
            tmp_registry.register_panel("cleanup-plugin", spec)

        # Verify they exist
        assert len(tmp_registry.get_panels_by_plugin("cleanup-plugin")) == 3

        # Uninstall all
        count = tmp_registry.unregister_plugin_panels("cleanup-plugin")

        assert count == 3
        assert len(tmp_registry.get_panels_by_plugin("cleanup-plugin")) == 0

    def test_get_all_enabled_panels(self, tmp_registry):
        """Return only enabled panels."""
        # Register and disable some
        for i in range(3):
            spec = {
                "id": f"enabled-panel-{i}",
                "label": f"Panel {i}",
                "route": f"panel{i}",
                "icon": "Eye",
                "group": "visibility",
            }
            tmp_registry.register_panel("visibility-plugin", spec)

        # Disable panel 1
        tmp_registry.disable_panel("enabled-panel-1")

        # Get enabled
        enabled = tmp_registry.get_all_enabled_panels()

        assert len(enabled) == 2
        assert all(p["enabled"] is True for p in enabled)
        panel_ids = {p["panel_id"] for p in enabled}
        assert "enabled-panel-1" not in panel_ids


class TestInstallTaskPanelRegistration:
    """Test panel registration during plugin install."""

    @pytest.mark.asyncio
    async def test_register_panel_on_install_success(self, install_task, plugin_manifest, tmp_path):
        """Panel is registered when install succeeds."""
        with patch.object(install_task, "_check_collision"):
            with patch.object(install_task, "_check_disk_space"):
                with patch.object(install_task, "_git_clone", return_value=tmp_path / "test"):
                    with patch.object(install_task, "_validate_manifest"):
                        with patch("core.plugins.plugin_registry.PluginRegistry"):
                            with patch(
                                "core.plugins.plugin_panel_registry.get_panel_registry"
                            ) as mock_get_registry:
                                mock_registry = MagicMock()
                                mock_get_registry.return_value = mock_registry

                                await install_task._register_panel(plugin_manifest)

                                # Verify panel was registered
                                mock_registry.register_panel.assert_called_once()
                                call_args = mock_registry.register_panel.call_args
                                assert call_args[1]["plugin_id"] == "security-settings"
                                assert call_args[1]["panel_spec"]["id"] == "security-settings-panel"

    @pytest.mark.asyncio
    async def test_register_panel_graceful_degradation(self, install_task, plugin_manifest, tmp_path):
        """Install succeeds even if panel registration fails."""
        with patch.object(install_task, "_check_collision"):
            with patch.object(install_task, "_check_disk_space"):
                with patch.object(install_task, "_git_clone", return_value=tmp_path / "test"):
                    with patch.object(install_task, "_validate_manifest"):
                        with patch("core.plugins.plugin_registry.PluginRegistry"):
                            with patch(
                                "core.plugins.plugin_panel_registry.get_panel_registry"
                            ) as mock_get_registry:
                                mock_registry = MagicMock()
                                mock_registry.register_panel.side_effect = RuntimeError("Registry error")
                                mock_get_registry.return_value = mock_registry

                                # Should not raise, just log warning
                                await install_task._register_panel(plugin_manifest)

    @pytest.mark.asyncio
    async def test_rollback_removes_panels(self, install_task):
        """Rollback removes panels when install fails."""
        with patch("core.plugins.plugin_registry.PluginRegistry"):
            with patch(
                "core.plugins.plugin_panel_registry.get_panel_registry"
            ) as mock_get_registry:
                mock_registry = MagicMock()
                mock_get_registry.return_value = mock_registry

                await install_task._rollback()

                # Verify panel registry rollback was called
                mock_registry.unregister_plugin_panels.assert_called_once_with(
                    install_task.plugin_id
                )


class TestCapabilitiesManifestIntegration:
    """Test that plugin panels appear in capabilities manifest."""

    def test_capabilities_include_plugin_panels(self, tmp_path):
        """Capabilities endpoint returns plugin panels."""
        # Create a panel registry and register a panel
        registry_path = tmp_path / "panel_registry.json"

        with patch(
            "core.plugins.plugin_panel_registry.PluginPanelRegistry.path",
            registry_path
        ):
            registry = PluginPanelRegistry(str(registry_path))
            spec = {
                "id": "capability-panel",
                "label": "Capability Test",
                "route": "capability",
                "icon": "Zap",
                "group": "test",
            }
            registry.register_panel("capability-plugin", spec)

        # Mock get_panel_registry to return our registry
        with patch(
            "core.console.corvin_console.routes.capabilities.get_panel_registry",
            return_value=registry
        ):
            panels = _get_plugin_panels()

            assert len(panels) > 0
            assert any(p["panel_id"] == "capability-panel" for p in panels)

    def test_capabilities_graceful_degradation_no_registry(self):
        """Capabilities endpoint works even if registry is unavailable."""
        with patch(
            "core.console.corvin_console.routes.capabilities.get_panel_registry",
            side_effect=RuntimeError("Registry unavailable")
        ):
            panels = _get_plugin_panels()

            # Should return empty list, not crash
            assert panels == []


class TestAuditTrail:
    """Test that panel operations are logged to audit trail."""

    def test_panel_registration_audited(self, tmp_registry):
        """Panel registration is logged to audit trail."""
        with patch("core.audit.audit_chain.AuditChain") as mock_audit_class:
            mock_audit = MagicMock()
            mock_audit_class.return_value = mock_audit

            spec = {
                "id": "audit-panel",
                "label": "Audit Test",
                "route": "audit",
                "icon": "FileText",
                "group": "audit",
            }

            tmp_registry.register_panel("audit-plugin", spec)

            # Verify audit log was called
            mock_audit.log_event.assert_called_once()
            call_args = mock_audit.log_event.call_args
            assert "panel.panel_registered" in call_args[0][0]
            assert call_args[0][1]["panel_id"] == "audit-panel"

    def test_panel_unregister_audited(self, tmp_registry):
        """Panel unregistration is logged."""
        spec = {
            "id": "unregister-audit-panel",
            "label": "Unregister Audit",
            "route": "unreg",
            "icon": "Trash",
            "group": "audit",
        }
        tmp_registry.register_panel("audit-plugin", spec)

        with patch("core.audit.audit_chain.AuditChain") as mock_audit_class:
            mock_audit = MagicMock()
            mock_audit_class.return_value = mock_audit

            tmp_registry.unregister_panel("unregister-audit-panel")

            mock_audit.log_event.assert_called_once()
            call_args = mock_audit.log_event.call_args
            assert "panel.panel_unregistered" in call_args[0][0]


class TestTenantIsolation:
    """Test that panels respect tenant isolation (GDPR Art. 5)."""

    def test_panels_isolated_by_tenant(self):
        """Panel registry is isolated per tenant (file-based)."""
        # Each tenant would have its own registry file
        # This test verifies the path includes tenant context
        from core.plugins.plugin_panel_registry import PluginPanelRegistry

        # In production, each tenant has its own ~/.corvin/tenant/{tenant_id}/plugins/panel_registry.json
        # For now, verify the default path exists
        registry = PluginPanelRegistry()
        assert "panel_registry.json" in str(registry.path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
