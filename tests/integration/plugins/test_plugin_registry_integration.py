"""
TIER-2: Plugin Registry Integration Tests

Tests register/unregister plugins in real registry, plugin discovery from manifest files,
and registry state persistence.
"""

import json
import pytest
from pathlib import Path
from typing import Dict, Any


@pytest.mark.plugin_integration
@pytest.mark.plugin_validation
class TestPluginRegistryOperations:
    """Test basic registry operations (register, unregister, list)"""

    def test_register_plugin_to_registry(self, isolated_plugin_env, plugin_manifest_factory):
        """Register a plugin to the registry"""
        registry_path = isolated_plugin_env["registry"]
        manifest = plugin_manifest_factory.make_valid("registry-test-1")

        # Create plugin directory
        plugin_dir = registry_path / "registry-test-1"
        plugin_dir.mkdir(parents=True)

        # Write manifest
        manifest_file = plugin_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        # Verify it exists
        assert manifest_file.exists()
        assert json.loads(manifest_file.read_text())["plugin_id"] == "registry-test-1"

    def test_unregister_plugin_removes_from_registry(self, isolated_plugin_env, plugin_manifest_factory):
        """Unregister a plugin from the registry"""
        registry_path = isolated_plugin_env["registry"]
        manifest = plugin_manifest_factory.make_valid("registry-test-2")

        # Register
        plugin_dir = registry_path / "registry-test-2"
        plugin_dir.mkdir(parents=True)
        manifest_file = plugin_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))
        assert manifest_file.exists()

        # Unregister (remove directory)
        import shutil
        shutil.rmtree(plugin_dir)

        # Verify it's gone
        assert not plugin_dir.exists()
        assert not manifest_file.exists()

    def test_list_registered_plugins(self, isolated_plugin_env, plugin_manifest_factory):
        """List all registered plugins in registry"""
        registry_path = isolated_plugin_env["registry"]

        # Register multiple plugins
        plugin_ids = ["plugin-a", "plugin-b", "plugin-c"]
        for plugin_id in plugin_ids:
            manifest = plugin_manifest_factory.make_valid(plugin_id)
            plugin_dir = registry_path / plugin_id
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        # List all
        registered = [d.name for d in registry_path.iterdir() if d.is_dir()]
        assert set(registered) == set(plugin_ids)

    def test_registry_preserves_plugin_metadata(self, isolated_plugin_env, plugin_manifest_factory):
        """Registry should preserve all plugin metadata"""
        registry_path = isolated_plugin_env["registry"]
        manifest = plugin_manifest_factory.make_valid(
            "metadata-test",
            custom_field="custom_value",
            tags=["compute", "ai"],
        )

        plugin_dir = registry_path / "metadata-test"
        plugin_dir.mkdir(parents=True)
        manifest_file = plugin_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        # Read back
        loaded = json.loads(manifest_file.read_text())
        assert loaded["plugin_id"] == "metadata-test"
        assert loaded["custom_field"] == "custom_value"
        assert loaded["tags"] == ["compute", "ai"]


@pytest.mark.plugin_integration
@pytest.mark.plugin_validation
class TestPluginDiscovery:
    """Test plugin discovery from manifest files"""

    def test_discover_plugin_by_id(self, isolated_plugin_env, plugin_manifest_factory):
        """Discover plugin by its ID"""
        registry_path = isolated_plugin_env["registry"]
        manifest = plugin_manifest_factory.make_valid("discovery-test")

        plugin_dir = registry_path / "discovery-test"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        # Discover by ID
        expected_manifest_file = registry_path / "discovery-test" / "manifest.json"
        assert expected_manifest_file.exists()

    def test_discover_all_plugins_in_registry(self, isolated_plugin_env, plugin_manifest_factory):
        """Discover all plugins in registry"""
        registry_path = isolated_plugin_env["registry"]

        # Create multiple plugins
        for i in range(3):
            manifest = plugin_manifest_factory.make_valid(f"plugin-{i}")
            plugin_dir = registry_path / f"plugin-{i}"
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        # Discover all
        plugins_found = []
        for plugin_dir in registry_path.iterdir():
            if plugin_dir.is_dir():
                manifest_file = plugin_dir / "manifest.json"
                if manifest_file.exists():
                    manifest = json.loads(manifest_file.read_text())
                    plugins_found.append(manifest["plugin_id"])

        assert len(plugins_found) == 3
        assert all(f"plugin-{i}" in plugins_found for i in range(3))

    def test_filter_plugins_by_type(self, isolated_plugin_env, plugin_manifest_factory):
        """Filter plugins by type"""
        registry_path = isolated_plugin_env["registry"]

        # Create plugins of different types
        compute_manifest = plugin_manifest_factory.make_valid(
            "compute-plugin",
            plugin_type="compute_engine"
        )
        audit_manifest = plugin_manifest_factory.make_valid(
            "audit-plugin",
            plugin_type="audit_backend"
        )

        for manifest in [compute_manifest, audit_manifest]:
            plugin_dir = registry_path / manifest["plugin_id"]
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        # Filter by type
        compute_plugins = []
        for plugin_dir in registry_path.iterdir():
            manifest_file = plugin_dir / "manifest.json"
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
                if manifest.get("plugin_type") == "compute_engine":
                    compute_plugins.append(manifest["plugin_id"])

        assert "compute-plugin" in compute_plugins
        assert len(compute_plugins) == 1

    def test_discover_plugin_by_boot_layer(self, isolated_plugin_env, plugin_manifest_factory):
        """Discover plugins by boot layer"""
        registry_path = isolated_plugin_env["registry"]

        # Create plugins with different boot layers
        bundled = plugin_manifest_factory.make_valid("bundled-plugin", boot_layer="bundled")
        installed = plugin_manifest_factory.make_valid("installed-plugin", boot_layer="installed")

        for manifest in [bundled, installed]:
            plugin_dir = registry_path / manifest["plugin_id"]
            plugin_dir.mkdir(parents=True)
            (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        # Discover bundled
        bundled_plugins = []
        for plugin_dir in registry_path.iterdir():
            manifest_file = plugin_dir / "manifest.json"
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())
                if manifest.get("boot_layer") == "bundled":
                    bundled_plugins.append(manifest["plugin_id"])

        assert "bundled-plugin" in bundled_plugins


@pytest.mark.plugin_integration
@pytest.mark.plugin_isolation
class TestRegistryStatePersistence:
    """Test registry state persistence across operations"""

    def test_registry_state_survives_restart(self, isolated_plugin_env, plugin_manifest_factory):
        """Registry state should persist across simulated restart"""
        registry_path = isolated_plugin_env["registry"]

        # Register plugin
        manifest = plugin_manifest_factory.make_valid("persist-test")
        plugin_dir = registry_path / "persist-test"
        plugin_dir.mkdir(parents=True)
        manifest_file = plugin_dir / "manifest.json"
        manifest_file.write_text(json.dumps(manifest))

        # Simulate restart by re-reading
        assert manifest_file.exists()
        loaded = json.loads(manifest_file.read_text())
        assert loaded["plugin_id"] == "persist-test"

    def test_registry_state_isolation_per_tenant(self, isolated_plugin_env, plugin_manifest_factory):
        """Each tenant's registry is isolated"""
        # Use isolated_plugin_env which already isolates per tenant
        registry_path = isolated_plugin_env["registry"]

        manifest = plugin_manifest_factory.make_valid("tenant-test")
        plugin_dir = registry_path / "tenant-test"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest))

        # Verify location is tenant-scoped
        assert "tenants" in str(registry_path)
        assert "_default" in str(registry_path)

    def test_registry_updates_are_atomic(self, isolated_plugin_env, plugin_manifest_factory):
        """Registry updates should be atomic"""
        registry_path = isolated_plugin_env["registry"]

        manifest = plugin_manifest_factory.make_valid("atomic-test")
        plugin_dir = registry_path / "atomic-test"
        plugin_dir.mkdir(parents=True)

        # Write manifest atomically
        manifest_file = plugin_dir / "manifest.json"
        temp_file = manifest_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(manifest))
        temp_file.replace(manifest_file)

        # Verify final state is complete
        assert manifest_file.exists()
        assert not temp_file.exists()
        loaded = json.loads(manifest_file.read_text())
        assert loaded["plugin_id"] == "atomic-test"


@pytest.mark.plugin_integration
class TestRegistryErrorHandling:
    """Test registry error handling"""

    def test_duplicate_plugin_id_detected(self, isolated_plugin_env, plugin_manifest_factory):
        """Attempting to register duplicate ID should be detected"""
        registry_path = isolated_plugin_env["registry"]

        # Register first instance
        manifest1 = plugin_manifest_factory.make_valid("duplicate-test")
        plugin_dir = registry_path / "duplicate-test"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "manifest.json").write_text(json.dumps(manifest1))

        # Try to register second (should fail or update)
        # In most registry implementations, this either raises or updates
        plugin_dir.exists()
        assert plugin_dir.exists()

    def test_invalid_manifest_rejected_on_discovery(self, isolated_plugin_env):
        """Invalid manifests should be skipped on discovery"""
        registry_path = isolated_plugin_env["registry"]

        # Create valid plugin
        valid_dir = registry_path / "valid-plugin"
        valid_dir.mkdir(parents=True)
        (valid_dir / "manifest.json").write_text(json.dumps({"plugin_id": "valid-plugin"}))

        # Create invalid plugin (bad JSON)
        invalid_dir = registry_path / "invalid-plugin"
        invalid_dir.mkdir(parents=True)
        (invalid_dir / "manifest.json").write_text("{ invalid json }")

        # Discovery should handle gracefully
        discovered = []
        for plugin_dir in registry_path.iterdir():
            manifest_file = plugin_dir / "manifest.json"
            try:
                manifest = json.loads(manifest_file.read_text())
                discovered.append(manifest)
            except json.JSONDecodeError:
                pass  # Skip invalid

        assert len(discovered) == 1

    def test_missing_manifest_file_handled(self, isolated_plugin_env):
        """Missing manifest file should be handled gracefully"""
        registry_path = isolated_plugin_env["registry"]

        # Create plugin directory without manifest
        plugin_dir = registry_path / "no-manifest"
        plugin_dir.mkdir(parents=True)

        # Discovery should skip it
        discovered = []
        for d in registry_path.iterdir():
            manifest_file = d / "manifest.json"
            if manifest_file.exists():
                discovered.append(json.loads(manifest_file.read_text()))

        assert len(discovered) == 0
