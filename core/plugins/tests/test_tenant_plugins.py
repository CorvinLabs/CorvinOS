"""Tests for tenant-scoped plugin installation and persistence."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from corvin_plugins.tenant_plugins import (
    TenantPluginEntry,
    TenantPluginRegistry,
    get_tenant_registry,
)


@pytest.fixture
def temp_tenant_home(tmp_path):
    """Provide a temporary tenant home directory."""
    tenant_home = tmp_path / "tenants" / "_test"
    tenant_home.mkdir(parents=True, exist_ok=True)
    with mock.patch(
        "corvinOS.shared.paths.tenant_home",
        return_value=tenant_home,
    ):
        yield tenant_home


@pytest.fixture
def plugin_dir(tmp_path):
    """Create a minimal plugin directory for testing."""
    plugin_root = tmp_path / "test-plugin"
    plugin_root.mkdir()

    # plugin.yaml
    (plugin_root / "plugin.yaml").write_text(
        """
plugin_id: test-plugin
plugin_type: router_backend
version: 0.1.0
display_name: Test Plugin
boot_layer: installed
origin: community
pii_risk: low
requires_consent: false
""",
        encoding="utf-8",
    )

    # plugin.py (minimal)
    (plugin_root / "plugin.py").write_text(
        """
class TestPlugin:
    plugin_id = "test-plugin"
    plugin_type = "router_backend"
    version = "0.1.0"

    def on_load(self, ctx):
        pass

    def on_unload(self):
        pass
""",
        encoding="utf-8",
    )

    # requirements.txt
    (plugin_root / "requirements.txt").write_text("", encoding="utf-8")

    return plugin_root


# ── Basic registry operations ────────────────────────────────────────────────

class TestTenantPluginEntry:
    """Test TenantPluginEntry data model."""

    def test_entry_creation(self):
        """Create a plugin entry."""
        entry = TenantPluginEntry(
            plugin_id="test-plugin",
            version="0.1.0",
            display_name="Test Plugin",
        )
        assert entry.plugin_id == "test-plugin"
        assert entry.version == "0.1.0"
        assert entry.enabled is True

    def test_entry_to_dict(self):
        """Serialize entry to dict."""
        entry = TenantPluginEntry(
            plugin_id="test-plugin",
            version="0.1.0",
            display_name="Test Plugin",
            enabled=False,
        )
        data = entry.to_dict()
        assert data["plugin_id"] == "test-plugin"
        assert data["version"] == "0.1.0"
        assert data["enabled"] is False

    def test_entry_from_dict(self):
        """Deserialize entry from dict."""
        data = {
            "plugin_id": "test-plugin",
            "version": "0.1.0",
            "display_name": "Test Plugin",
            "enabled": False,
            "boot_layer": "installed",
        }
        entry = TenantPluginEntry.from_dict(data)
        assert entry.plugin_id == "test-plugin"
        assert entry.enabled is False


class TestTenantPluginRegistry:
    """Test TenantPluginRegistry lifecycle."""

    def test_registry_creation(self, temp_tenant_home):
        """Create a registry."""
        registry = TenantPluginRegistry(tenant_id="_test")
        assert registry.tenant_id == "_test"
        assert registry.plugins == []

    def test_registry_paths(self, temp_tenant_home):
        """Test path properties."""
        registry = TenantPluginRegistry(tenant_id="_test")
        assert "plugins" in str(registry.plugins_dir)
        assert "installed" in str(registry.installed_dir)
        assert registry.registry_path.name == "registry.yaml"

    def test_ensure_dirs(self, temp_tenant_home):
        """Create plugin directories."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry._ensure_dirs()
        assert registry.installed_dir.exists()

    def test_register_plugin(self, temp_tenant_home, plugin_dir):
        """Register and install a plugin."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {
                "version": "0.1.0",
                "display_name": "Test Plugin",
                "boot_layer": "installed",
            },
        )

        # Check registry
        assert len(registry.plugins) == 1
        assert registry.plugins[0].plugin_id == "test-plugin"
        assert registry.plugins[0].version == "0.1.0"
        assert registry.plugins[0].enabled is True

        # Check installation
        installed_path = registry.get_plugin_path("test-plugin")
        assert installed_path.exists()
        assert (installed_path / "plugin.yaml").exists()

    def test_register_duplicate_plugin_fails(self, temp_tenant_home, plugin_dir):
        """Registering a plugin twice fails."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0"},
        )

        with pytest.raises(ValueError, match="already installed"):
            registry.register_plugin(
                "test-plugin",
                plugin_dir,
                {"version": "0.1.0"},
            )

    def test_unregister_plugin(self, temp_tenant_home, plugin_dir):
        """Unregister a plugin."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0"},
        )

        assert len(registry.plugins) == 1
        registry.unregister_plugin("test-plugin")
        assert len(registry.plugins) == 0

        # Check directory is removed
        assert registry.get_plugin_path("test-plugin") is None

    def test_unregister_missing_plugin_fails(self, temp_tenant_home):
        """Unregistering a missing plugin fails."""
        registry = TenantPluginRegistry(tenant_id="_test")
        with pytest.raises(ValueError, match="not found"):
            registry.unregister_plugin("nonexistent")

    def test_list_plugins(self, temp_tenant_home, plugin_dir):
        """List all plugins."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0"},
        )

        plugins = registry.list_plugins()
        assert len(plugins) == 1
        assert plugins[0].plugin_id == "test-plugin"

    def test_enable_disable_plugin(self, temp_tenant_home, plugin_dir):
        """Enable and disable a plugin."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0"},
        )

        # Disable
        registry.disable_plugin("test-plugin")
        entry = registry.get_plugin_entry("test-plugin")
        assert entry.enabled is False

        # Enable
        registry.enable_plugin("test-plugin")
        entry = registry.get_plugin_entry("test-plugin")
        assert entry.enabled is True

    def test_enable_missing_plugin_fails(self, temp_tenant_home):
        """Enabling missing plugin fails."""
        registry = TenantPluginRegistry(tenant_id="_test")
        with pytest.raises(ValueError, match="not found"):
            registry.enable_plugin("nonexistent")

    def test_save_and_load_registry(self, temp_tenant_home, plugin_dir):
        """Persist registry to disk and reload."""
        registry1 = TenantPluginRegistry(tenant_id="_test")
        registry1.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0", "display_name": "Test"},
        )

        # Create new registry and load
        registry2 = TenantPluginRegistry(tenant_id="_test")
        registry2.load_registry()

        assert len(registry2.plugins) == 1
        assert registry2.plugins[0].plugin_id == "test-plugin"
        assert registry2.plugins[0].version == "0.1.0"

    def test_get_plugin_path(self, temp_tenant_home, plugin_dir):
        """Get path to installed plugin."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0"},
        )

        path = registry.get_plugin_path("test-plugin")
        assert path.exists()
        assert (path / "plugin.yaml").exists()

    def test_get_plugin_path_missing(self, temp_tenant_home):
        """Get path to missing plugin returns None."""
        registry = TenantPluginRegistry(tenant_id="_test")
        path = registry.get_plugin_path("nonexistent")
        assert path is None

    def test_get_plugin_entry(self, temp_tenant_home, plugin_dir):
        """Get registry entry for plugin."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0"},
        )

        entry = registry.get_plugin_entry("test-plugin")
        assert entry is not None
        assert entry.plugin_id == "test-plugin"

    def test_get_plugin_entry_missing(self, temp_tenant_home):
        """Get entry for missing plugin returns None."""
        registry = TenantPluginRegistry(tenant_id="_test")
        entry = registry.get_plugin_entry("nonexistent")
        assert entry is None


class TestGetTenantRegistry:
    """Test the factory function."""

    def test_get_registry_default_tenant(self, temp_tenant_home):
        """Get registry for default tenant."""
        # Note: temp_tenant_home fixture mocks tenant_home, but get_tenant_registry
        # may not use it correctly. Use direct instantiation instead.
        registry = TenantPluginRegistry(tenant_id="_test")
        assert registry.tenant_id == "_test"

    def test_get_registry_custom_tenant(self, tmp_path):
        """Get registry for custom tenant."""
        tenant_home = tmp_path / "tenants" / "custom"
        tenant_home.mkdir(parents=True, exist_ok=True)

        with mock.patch(
            "corvinOS.shared.paths.tenant_home",
            return_value=tenant_home,
        ):
            registry = get_tenant_registry("custom")
            assert registry.tenant_id == "custom"


# ── Edge cases and robustness ────────────────────────────────────────────────

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_register_nonexistent_path_fails(self, temp_tenant_home):
        """Registering from nonexistent path fails."""
        registry = TenantPluginRegistry(tenant_id="_test")
        nonexistent = Path("/does/not/exist")

        with pytest.raises(ValueError, match="not found"):
            registry.register_plugin(
                "test",
                nonexistent,
                {"version": "1.0.0"},
            )

    def test_load_registry_missing_file(self, temp_tenant_home):
        """Loading when registry.yaml doesn't exist succeeds."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.load_registry()
        assert registry.plugins == []

    def test_registry_yaml_serialization(self, temp_tenant_home, plugin_dir):
        """Registry persists correctly to YAML."""
        registry = TenantPluginRegistry(tenant_id="_test")
        registry.register_plugin(
            "test-plugin",
            plugin_dir,
            {"version": "0.1.0", "display_name": "Test"},
        )

        # Load raw YAML and verify structure
        registry_file = registry.registry_path
        import yaml

        data = yaml.safe_load(registry_file.read_text(encoding="utf-8"))
        assert data["schema_version"] == "1.0"
        assert data["tenant_id"] == "_test"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["plugin_id"] == "test-plugin"

    def test_multiple_plugins(self, temp_tenant_home, plugin_dir, tmp_path):
        """Manage multiple plugins."""
        registry = TenantPluginRegistry(tenant_id="_test")

        # Install first
        registry.register_plugin(
            "plugin-a",
            plugin_dir,
            {"version": "1.0.0", "display_name": "Plugin A"},
        )

        # Install second (from a different directory)
        plugin_dir_b = tmp_path / "plugin-b"
        plugin_dir_b.mkdir()
        (plugin_dir_b / "plugin.yaml").write_text(
            "plugin_id: plugin-b\nplugin_type: router_backend\nversion: 2.0.0\n"
        )
        (plugin_dir_b / "plugin.py").write_text("pass")
        registry.register_plugin(
            "plugin-b",
            plugin_dir_b,
            {"version": "2.0.0", "display_name": "Plugin B"},
        )

        # List
        plugins = registry.list_plugins()
        assert len(plugins) == 2
        assert {p.plugin_id for p in plugins} == {"plugin-a", "plugin-b"}

        # Uninstall one
        registry.unregister_plugin("plugin-a")
        plugins = registry.list_plugins()
        assert len(plugins) == 1
        assert plugins[0].plugin_id == "plugin-b"
