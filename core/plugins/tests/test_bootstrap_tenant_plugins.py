"""Tests for Phase 1d: Boot-time plugin loading from TenantPluginRegistry.

These tests verify that tenant-installed plugins are loaded from
tenant/plugins/installed/ during bootstrap.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
_FORGE = _REPO / "operator" / "forge"
_SHARED = _REPO / "operator" / "bridges" / "shared"

for _p in (str(_PKG), str(_FORGE), str(_SHARED), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins import bootstrap
from corvin_plugins.tenant_plugins import TenantPluginRegistry, TenantPluginEntry


class TestBootstrapTenantPlugins(unittest.TestCase):
    """Test bootstrap_tenant() loading from TenantPluginRegistry."""

    def setUp(self):
        """Create temporary tenant directory."""
        self.temp_dir = tempfile.mkdtemp(prefix="test_bootstrap_plugins_")
        self.tenant_id = "_test"
        self.corvin_home = Path(self.temp_dir)
        # Set CORVIN_HOME so TenantPluginRegistry uses our temp directory
        self.orig_corvin_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(self.corvin_home)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        # Restore original CORVIN_HOME
        if self.orig_corvin_home is not None:
            os.environ["CORVIN_HOME"] = self.orig_corvin_home
        else:
            os.environ.pop("CORVIN_HOME", None)
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)

    def _create_test_plugin(self, plugin_id: str, enabled: bool = True) -> Path:
        """Create a minimal test plugin directory."""
        # Create tenant directory structure
        tenant_dir = self.corvin_home / "tenants" / self.tenant_id
        plugin_dir = tenant_dir / "plugins" / "installed" / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # Create plugin.py with a Plugin class that implements required interface
        plugin_code = f'''
"""Test plugin {plugin_id}."""

from corvin_plugins.protocol import HealthStatus

class Plugin:
    """A test plugin that does nothing."""
    plugin_id = "{plugin_id}"
    plugin_type = "compute_engine"
    version = "1.0.0"
    display_name = "{plugin_id}"

    def __init__(self):
        self.initialized = True

    def on_load(self, context):
        pass

    def on_unload(self):
        pass

    def health_check(self):
        return HealthStatus(ok=True)
'''
        (plugin_dir / "plugin.py").write_text(plugin_code)

        # Create manifest.json
        (plugin_dir / "manifest.json").write_text('{"id": "%s", "version": "1.0.0"}' % plugin_id)

        return plugin_dir

    def _create_test_plugin_with_setup(self, plugin_id: str) -> Path:
        """Create a test plugin with a setup() hook."""
        plugin_dir = self.corvin_home / "tenants" / self.tenant_id
        plugin_dir = plugin_dir / "plugins" / "installed" / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)

        # Create plugin.py with setup() function
        plugin_code = f'''
"""Test plugin {plugin_id} with setup hook."""

def setup(context):
    """Plugin setup hook."""
    pass
'''
        (plugin_dir / "plugin.py").write_text(plugin_code)

        return plugin_dir

    def test_bootstrap_skips_when_disabled(self):
        """Bootstrap skips when lifecycle_enabled=False."""
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=False,
        )
        self.assertEqual(result, [])

    def test_bootstrap_loads_enabled_plugin(self):
        """Bootstrap loads enabled plugins."""
        # Create plugin and register it
        self._create_test_plugin("test-plugin")
        registry = TenantPluginRegistry(tenant_id=self.tenant_id)
        registry.load_registry()
        registry.plugins = [
            TenantPluginEntry(
                plugin_id="test-plugin",
                version="1.0.0",
                display_name="Test Plugin",
                enabled=True,
            )
        ]
        registry.save_registry()

        # Bootstrap should load it
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # Plugin should be loaded
        self.assertIn("test-plugin", result)

    def test_bootstrap_skips_disabled_plugin(self):
        """Bootstrap skips disabled plugins."""
        # Create plugin and register it as disabled
        self._create_test_plugin("disabled-plugin", enabled=False)
        registry = TenantPluginRegistry(tenant_id=self.tenant_id)
        registry.load_registry()
        registry.plugins = [
            TenantPluginEntry(
                plugin_id="disabled-plugin",
                version="1.0.0",
                display_name="Disabled Plugin",
                enabled=False,
            )
        ]
        registry.save_registry()

        # Bootstrap should skip it
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # Plugin should NOT be loaded
        self.assertNotIn("disabled-plugin", result)

    def test_bootstrap_continues_on_plugin_load_error(self):
        """Bootstrap continues with other plugins if one fails."""
        # Create good plugin
        self._create_test_plugin("good-plugin")
        registry = TenantPluginRegistry(tenant_id=self.tenant_id)
        registry.load_registry()
        registry.plugins = [
            TenantPluginEntry(
                plugin_id="good-plugin",
                version="1.0.0",
                display_name="Good Plugin",
                enabled=True,
            ),
            TenantPluginEntry(
                plugin_id="bad-plugin",
                version="1.0.0",
                display_name="Bad Plugin",
                enabled=True,
            ),
        ]
        registry.save_registry()

        # Create bad plugin (will cause import error)
        bad_dir = (
            self.corvin_home / "tenants" / self.tenant_id
            / "plugins" / "installed" / "bad-plugin"
        )
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "plugin.py").write_text("import nonexistent_module_xyz")

        # Bootstrap should load good, skip bad
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # Good plugin loaded, bad plugin skipped
        self.assertIn("good-plugin", result)
        self.assertNotIn("bad-plugin", result)

    def test_bootstrap_loads_plugin_with_setup_hook(self):
        """Bootstrap loads plugins with setup() hook."""
        # Create plugin with setup hook
        self._create_test_plugin_with_setup("setup-plugin")
        registry = TenantPluginRegistry(tenant_id=self.tenant_id)
        registry.load_registry()
        registry.plugins = [
            TenantPluginEntry(
                plugin_id="setup-plugin",
                version="1.0.0",
                display_name="Setup Plugin",
                enabled=True,
            )
        ]
        registry.save_registry()

        # Bootstrap should load it
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # Plugin should be loaded via setup hook
        self.assertIn("setup-plugin", result)

    def test_bootstrap_handles_missing_plugin_py(self):
        """Bootstrap handles missing plugin.py gracefully."""
        # Create plugin directory without plugin.py
        plugin_dir = (
            self.corvin_home / "tenants" / self.tenant_id
            / "plugins" / "installed" / "no-py-plugin"
        )
        plugin_dir.mkdir(parents=True, exist_ok=True)

        registry = TenantPluginRegistry(tenant_id=self.tenant_id)
        registry.load_registry()
        registry.plugins = [
            TenantPluginEntry(
                plugin_id="no-py-plugin",
                version="1.0.0",
                display_name="No Python Plugin",
                enabled=True,
            )
        ]
        registry.save_registry()

        # Bootstrap should skip it
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # Plugin should NOT be loaded
        self.assertNotIn("no-py-plugin", result)

    def test_bootstrap_handles_corrupted_registry(self):
        """Bootstrap handles corrupted registry gracefully."""
        # Create a corrupted registry file
        tenant_dir = self.corvin_home / "tenants" / self.tenant_id
        plugins_dir = tenant_dir / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "registry.yaml").write_text("invalid: [yaml: content: {")

        # Bootstrap should degrade (not raise)
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # Should return empty list (no plugins loaded due to corrupt registry)
        self.assertEqual(result, [])

    def test_bootstrap_loads_multiple_plugins_in_order(self):
        """Bootstrap loads all enabled plugins."""
        # Create multiple plugins
        self._create_test_plugin("plugin-a")
        self._create_test_plugin("plugin-b")
        self._create_test_plugin("plugin-c")

        registry = TenantPluginRegistry(tenant_id=self.tenant_id)
        registry.load_registry()
        registry.plugins = [
            TenantPluginEntry(
                plugin_id="plugin-a",
                version="1.0.0",
                display_name="Plugin A",
                enabled=True,
            ),
            TenantPluginEntry(
                plugin_id="plugin-b",
                version="1.0.0",
                display_name="Plugin B",
                enabled=True,
            ),
            TenantPluginEntry(
                plugin_id="plugin-c",
                version="1.0.0",
                display_name="Plugin C",
                enabled=True,
            ),
        ]
        registry.save_registry()

        # Bootstrap should load all
        result = bootstrap.bootstrap_tenant(
            tenant_id=self.tenant_id,
            corvin_home=self.corvin_home,
            lifecycle_enabled=True,
        )

        # All plugins should be loaded
        self.assertEqual(len(result), 3)
        self.assertIn("plugin-a", result)
        self.assertIn("plugin-b", result)
        self.assertIn("plugin-c", result)


if __name__ == "__main__":
    unittest.main()
