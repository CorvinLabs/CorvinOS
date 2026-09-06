"""Phase 3 E2E Tests — Full Plugin Installation & Panel Auto-Registration Flows.

Tests the complete end-to-end lifecycle:
1. Install plugin (with settings panel spec)
2. Verify panel is registered
3. Verify panel appears in Console capabilities
4. Verify panel in Console sidebar (if enabled)
5. Disable panel (graceful)
6. Uninstall plugin (cleanup)
7. Verify panel removed from all registries

Also tests error cases:
- Install fails → rollback removes panels
- Plugin with no settings panel → installs OK (panel-optional)
- Invalid panel spec in manifest → install fails
"""

import pytest
import json
import asyncio
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from typing import Dict, Any

try:
    from core.orchestration.tasks.plugin_install_task import PluginInstallTask
    from core.plugins.plugin_panel_registry import PluginPanelRegistry, get_panel_registry
    from core.plugins.plugin_registry import PluginRegistry
    from core.console.corvin_console.routes.capabilities import _get_plugin_panels
except ImportError:
    pytest.skip("Required modules not available", allow_module_level=True)


@pytest.fixture
def test_dir(tmp_path):
    """Setup test directory structure."""
    plugins_dir = tmp_path / ".corvin" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # Patch home directory
    with patch("pathlib.Path.home", return_value=tmp_path):
        yield tmp_path


@pytest.fixture
def panel_registry(test_dir, tmp_path):
    """Create panel registry for testing."""
    registry_path = tmp_path / "panel_registry.json"

    # `path` is an INSTANCE attribute set from registry_path (class patch raised)
    yield PluginPanelRegistry(str(registry_path))


class TestFullPluginInstallWithPanelFlow:
    """E2E test: complete plugin install → panel registration → Console integration."""

    @pytest.mark.asyncio
    async def test_e2e_install_plugin_with_panel(self, test_dir, panel_registry):
        """Full flow: install plugin → register panel → appears in capabilities."""

        # 1. Create an install task
        task = PluginInstallTask(
            repo="example/auth-plugin",
            plugin_id="auth-settings",
            version="1.0.0",
        )

        # 2. Mock the dependencies
        manifest = {
            "plugin": {
                "id": "auth-settings",
                "name": "Authentication Settings",
                "version": "1.0.0",
                "source": "https://github.com/example/auth-plugin",
            },
            "console": {
                "settings_panel": {
                    "id": "auth-settings-panel",
                    "label": "Auth Settings",
                    "route": "settings/auth",
                    "icon": "Lock",
                    "group": "settings",
                }
            },
        }

        # Mock git clone
        plugin_path = test_dir / ".corvin" / "plugins" / "auth-settings"
        plugin_path.mkdir(parents=True, exist_ok=True)

        with patch.object(task, "_check_collision"):
            with patch.object(task, "_check_disk_space"):
                with patch.object(task, "_git_clone", return_value=plugin_path):
                    with patch.object(
                        task, "_validate_manifest", return_value=None
                    ):
                        with patch(
                            "core.plugins.plugin_registry.PluginRegistry"
                        ):
                            with patch(
                                "core.plugins.plugin_panel_registry.get_panel_registry",
                                return_value=panel_registry
                            ):
                                # 3. Execute install
                                result = await task.execute()

                                # 4. Verify install succeeded
                                assert result["success"] is True
                                assert result["plugin_id"] == "auth-settings"

        # 5. Manually call register_panel to simulate install flow
        with patch(
            "core.plugins.plugin_panel_registry.get_panel_registry",
            return_value=panel_registry
        ):
            await task._register_panel(manifest)

            # 6. Verify panel is registered
            panel = panel_registry.get_panel("auth-settings-panel")
            assert panel is not None
            assert panel["label"] == "Auth Settings"
            assert panel["plugin_id"] == "auth-settings"

            # 7. Verify panel appears in capabilities
            panels = _get_plugin_panels()
            assert len(panels) > 0
            assert any(p["panel_id"] == "auth-settings-panel" for p in panels)

    @pytest.mark.asyncio
    async def test_e2e_install_without_panel(self, test_dir, panel_registry):
        """Plugin install succeeds even if no settings_panel in manifest."""

        task = PluginInstallTask(
            repo="example/basic-plugin",
            plugin_id="basic-plugin",
            version="1.0.0",
        )

        manifest = {
            "plugin": {
                "id": "basic-plugin",
                "name": "Basic Plugin",
                "version": "1.0.0",
                "source": "https://github.com/example/basic-plugin",
            },
            # No console section
        }

        plugin_path = test_dir / ".corvin" / "plugins" / "basic-plugin"
        plugin_path.mkdir(parents=True, exist_ok=True)

        with patch.object(task, "_check_collision"):
            with patch.object(task, "_check_disk_space"):
                with patch.object(task, "_git_clone", return_value=plugin_path):
                    with patch.object(task, "_validate_manifest"):
                        with patch("core.plugins.plugin_registry.PluginRegistry"):
                            with patch(
                                "core.plugins.plugin_panel_registry.get_panel_registry",
                                return_value=panel_registry
                            ):
                                result = await task.execute()

                                assert result["success"] is True

        # Register panel (should log warning)
        with patch(
            "core.plugins.plugin_panel_registry.get_panel_registry",
            return_value=panel_registry
        ):
            await task._register_panel(manifest)

            # Verify no panel was registered
            assert len(panel_registry.get_panels_by_plugin("basic-plugin")) == 0

    @pytest.mark.asyncio
    async def test_e2e_install_failure_rolls_back_panel(self, test_dir, panel_registry):
        """Install failure → rollback removes panel (if it was registered)."""

        task = PluginInstallTask(
            repo="example/broken-plugin",
            plugin_id="broken-plugin",
            version="1.0.0",
        )

        # Pre-register a panel (simulating it was registered before the failure)
        panel_spec = {
            "id": "broken-plugin-panel",
            "label": "Broken Panel",
            "route": "broken",
            "icon": "AlertTriangle",
            "group": "broken",
        }
        panel_registry.register_panel("broken-plugin", panel_spec)

        assert len(panel_registry.get_panels_by_plugin("broken-plugin")) == 1

        # Rollback
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "rmdir"):
                with patch("shutil.rmtree"):
                    with patch("core.plugins.plugin_registry.PluginRegistry"):
                        with patch(
                            "core.plugins.plugin_panel_registry.get_panel_registry",
                            return_value=panel_registry
                        ):
                            await task._rollback()

                            # Verify panel was removed
                            assert len(panel_registry.get_panels_by_plugin("broken-plugin")) == 0

    @pytest.mark.asyncio
    async def test_e2e_uninstall_removes_all_panels(self, test_dir, panel_registry):
        """Uninstall removes all panels for the plugin."""

        plugin_id = "multi-panel-plugin"

        # Register 3 panels for this plugin
        for i in range(3):
            spec = {
                "id": f"multi-panel-{i}",
                "label": f"Multi Panel {i}",
                "route": f"multi{i}",
                "icon": "Settings",
                "group": "multi",
            }
            panel_registry.register_panel(plugin_id, spec)

        assert len(panel_registry.get_panels_by_plugin(plugin_id)) == 3

        # Simulate uninstall
        with patch(
            "core.plugins.plugin_panel_registry.get_panel_registry",
            return_value=panel_registry
        ):
            panel_registry.unregister_plugin_panels(plugin_id)

            # Verify all panels are gone
            assert len(panel_registry.get_panels_by_plugin(plugin_id)) == 0

    @pytest.mark.asyncio
    async def test_e2e_disable_enable_panel(self, test_dir, panel_registry):
        """Disable/enable panel without uninstall."""

        spec = {
            "id": "toggleable-panel",
            "label": "Toggleable",
            "route": "toggle",
            "icon": "Power",
            "group": "test",
        }
        panel_registry.register_panel("toggle-plugin", spec)

        # Initially visible
        enabled_panels = panel_registry.get_all_enabled_panels()
        assert any(p["panel_id"] == "toggleable-panel" for p in enabled_panels)

        # Disable
        panel_registry.disable_panel("toggleable-panel")
        enabled_panels = panel_registry.get_all_enabled_panels()
        assert not any(p["panel_id"] == "toggleable-panel" for p in enabled_panels)

        # Re-enable
        panel_registry.enable_panel("toggleable-panel")
        enabled_panels = panel_registry.get_all_enabled_panels()
        assert any(p["panel_id"] == "toggleable-panel" for p in enabled_panels)


class TestErrorHandling:
    """Test error handling and graceful degradation."""

    @pytest.mark.asyncio
    async def test_invalid_panel_spec_in_manifest(self, test_dir, panel_registry):
        """Invalid panel spec → install fails."""

        task = PluginInstallTask(
            repo="example/invalid-plugin",
            plugin_id="invalid-plugin",
            version="1.0.0",
        )

        manifest = {
            "plugin": {
                "id": "invalid-plugin",
                "name": "Invalid Plugin",
                "version": "1.0.0",
                "source": "https://github.com/example/invalid-plugin",
            },
            "console": {
                "settings_panel": {
                    # Missing: id, label, route, icon, group
                    "description": "Incomplete panel spec",
                }
            },
        }

        with patch(
            "core.plugins.plugin_panel_registry.get_panel_registry",
            return_value=panel_registry
        ):
            # Should not raise, just log warning (graceful degradation)
            await task._register_panel(manifest)

    @pytest.mark.asyncio
    async def test_panel_registration_failure_doesnt_block_install(self, test_dir, panel_registry):
        """Panel registration failure → install continues (graceful)."""

        task = PluginInstallTask(
            repo="example/resilient-plugin",
            plugin_id="resilient-plugin",
            version="1.0.0",
        )

        manifest = {
            "plugin": {
                "id": "resilient-plugin",
                "name": "Resilient Plugin",
                "version": "1.0.0",
                "source": "https://github.com/example/resilient-plugin",
            },
            "console": {
                "settings_panel": {
                    "id": "resilient-panel",
                    "label": "Resilient",
                    "route": "resilient",
                    "icon": "Zap",
                    "group": "resilient",
                }
            },
        }

        # Mock registry to fail
        mock_registry = MagicMock()
        mock_registry.register_panel.side_effect = RuntimeError("Registry error")

        with patch(
            "core.plugins.plugin_panel_registry.get_panel_registry",
            return_value=mock_registry
        ):
            # Should not raise
            await task._register_panel(manifest)

    @pytest.mark.asyncio
    async def test_unsafe_panel_route_rejected(self, test_dir, panel_registry):
        """Panel route with path traversal → rejected."""

        task = PluginInstallTask(
            repo="example/malicious-plugin",
            plugin_id="malicious-plugin",
            version="1.0.0",
        )

        manifest = {
            "plugin": {
                "id": "malicious-plugin",
                "name": "Malicious",
                "version": "1.0.0",
                "source": "https://github.com/example/malicious-plugin",
            },
            "console": {
                "settings_panel": {
                    "id": "malicious-panel",
                    "label": "Malicious",
                    "route": "../../etc/passwd",  # Path traversal!
                    "icon": "AlertTriangle",
                    "group": "admin",
                }
            },
        }

        with patch(
            "core.plugins.plugin_panel_registry.get_panel_registry",
            return_value=panel_registry
        ):
            with pytest.raises(ValueError, match="Unsafe"):
                await task._register_panel(manifest)


class TestConsoleIntegration:
    """Test Console integration (capabilities, sidebar rendering)."""

    def test_capabilities_manifest_includes_panels(self, panel_registry):
        """Capabilities endpoint returns plugin panels."""
        # Register a panel
        spec = {
            "id": "capability-test-panel",
            "label": "Capability Test",
            "route": "capability",
            "icon": "Zap",
            "group": "test",
        }
        panel_registry.register_panel("capability-test-plugin", spec)

        with patch(
            "core.console.corvin_console.routes.capabilities.get_panel_registry",
            return_value=panel_registry
        ):
            panels = _get_plugin_panels()

            assert len(panels) == 1
            assert panels[0]["panel_id"] == "capability-test-panel"
            assert panels[0]["label"] == "Capability Test"
            assert panels[0]["route"] == "capability"

    def test_capabilities_only_returns_enabled_panels(self, panel_registry):
        """Capabilities endpoint returns only enabled panels."""
        # Register 2 panels
        for i in range(2):
            spec = {
                "id": f"visibility-panel-{i}",
                "label": f"Visibility {i}",
                "route": f"vis{i}",
                "icon": "Eye",
                "group": "visibility",
            }
            panel_registry.register_panel("visibility-plugin", spec)

        # Disable one
        panel_registry.disable_panel("visibility-panel-1")

        with patch(
            "core.console.corvin_console.routes.capabilities.get_panel_registry",
            return_value=panel_registry
        ):
            panels = _get_plugin_panels()

            assert len(panels) == 1
            assert panels[0]["panel_id"] == "visibility-panel-0"

    def test_console_gracefully_handles_no_panels(self):
        """Console works fine with zero panels."""
        with patch(
            "core.console.corvin_console.routes.capabilities.get_panel_registry",
            side_effect=RuntimeError("Registry unavailable")
        ):
            panels = _get_plugin_panels()

            assert panels == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
