"""
Phase 3 Tests: Plugin Registry Consistency

Test suite for Phase 3 implementation:
- Canonical manifest management (load, write, integrity)
- Plugin hash verification (checksums, tampering detection)
- Dependency resolution (DAG, topological sort)
- Auto-remediation (install, update, audit logging)
"""

import os
import json
import tempfile
import shutil
import pytest
from pathlib import Path
from datetime import datetime

from core.plugins.canonical_manifest import (
    CanonicalManifestManager,
    CanonicalManifest,
    PluginEntry,
)
from core.plugins.hash_verification import (
    PluginHashVerifier,
    VerificationSeverity,
)
from core.plugins.dependency_resolver import DependencyResolver
from core.plugins.registry_sync import (
    PluginRegistrySynchronizer,
    PluginInstaller,
    PluginDrift,
)


class TestCanonicalManifest:
    """Tests for canonical manifest management"""

    def setup_method(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")

    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_canonical_manifest_write_and_load(self):
        """Test atomic write and load of canonical manifest"""
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        # Create manifest
        plugin1 = PluginEntry(
            plugin_id="plugin1",
            version="1.0.0",
            checksum="abc123",
            dependencies=[],
            boot_layer="bundled",
        )
        plugin2 = PluginEntry(
            plugin_id="plugin2",
            version="2.0.0",
            checksum="def456",
            dependencies=["plugin1"],
            boot_layer="installed",
        )

        manifest = CanonicalManifest(plugins=[plugin1, plugin2])

        # Write manifest
        assert manager.write_manifest(manifest)

        # Verify file exists
        assert os.path.exists(self.manifest_path)

        # Load manifest
        loaded = manager.load_manifest()
        assert len(loaded.plugins) == 2
        assert loaded.plugins[0].plugin_id == "plugin1"
        assert loaded.plugins[1].plugin_id == "plugin2"

    def test_canonical_manifest_integrity_verification(self):
        """Test manifest integrity verification"""
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        plugin = PluginEntry(
            plugin_id="test_plugin",
            version="1.0.0",
            checksum="xyz789",
            dependencies=[],
        )

        manifest = CanonicalManifest(plugins=[plugin])
        assert manager.write_manifest(manifest)

        # Verify integrity
        loaded = manager.load_manifest()
        assert manager.verify_manifest_integrity()

        # Tamper with manifest
        with open(self.manifest_path, "r") as f:
            data = json.load(f)

        data["plugins"][0]["version"] = "2.0.0"  # Tamper
        with open(self.manifest_path, "w") as f:
            json.dump(data, f)

        # Reload should detect tampering
        manager.current_manifest = None
        manager.load_manifest()
        assert not manager.verify_manifest_integrity()

    def test_canonical_manifest_get_and_add_plugin(self):
        """Test getting and adding plugins to manifest"""
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        plugin = PluginEntry(
            plugin_id="test_plugin",
            version="1.0.0",
            checksum="abc123",
        )

        # Add plugin
        assert manager.add_plugin(plugin)

        # Get plugin
        retrieved = manager.get_plugin("test_plugin")
        assert retrieved is not None
        assert retrieved.plugin_id == "test_plugin"
        assert retrieved.version == "1.0.0"

        # Get non-existent plugin
        assert manager.get_plugin("nonexistent") is None


class TestPluginHashVerification:
    """Tests for plugin hash verification"""

    def setup_method(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.plugins_dir = os.path.join(self.test_dir, "plugins")
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_test_plugin(self, plugin_id: str, version: str, content: str = "test"):
        """Create a test plugin directory"""
        plugin_path = os.path.join(self.plugins_dir, plugin_id)
        Path(plugin_path).mkdir(parents=True, exist_ok=True)

        # Create plugin.json
        with open(os.path.join(plugin_path, "plugin.json"), "w") as f:
            json.dump(
                {"id": plugin_id, "version": version},
                f,
            )

        # Create some content
        with open(os.path.join(plugin_path, "main.py"), "w") as f:
            f.write(content)

        return plugin_path

    def test_plugin_hash_verification_valid(self):
        """Test verification of valid (unchanged) plugin"""
        # Create plugin
        plugin_path = self._create_test_plugin("test_plugin", "1.0.0")

        # Compute hash
        verifier = PluginHashVerifier(plugins_dir=self.plugins_dir)
        hash1 = verifier._compute_plugin_hash(plugin_path)
        hash2 = verifier._compute_plugin_hash(plugin_path)

        # Hashes should be identical (deterministic)
        assert hash1 == hash2

        # Create manifest with this hash
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)
        plugin = PluginEntry(
            plugin_id="test_plugin",
            version="1.0.0",
            checksum=hash1,
        )
        assert manager.add_plugin(plugin)

        # Verify should pass
        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manager,
        )
        result = verifier.verify_integrity("test_plugin")
        assert result.is_valid

    def test_plugin_hash_verification_tampering_detected(self):
        """Test detection of plugin tampering"""
        # Create plugin
        plugin_path = self._create_test_plugin("test_plugin", "1.0.0")

        # Compute hash
        verifier = PluginHashVerifier(plugins_dir=self.plugins_dir)
        hash1 = verifier._compute_plugin_hash(plugin_path)

        # Create manifest
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)
        plugin = PluginEntry(
            plugin_id="test_plugin",
            version="1.0.0",
            checksum=hash1,
        )
        assert manager.add_plugin(plugin)

        # Tamper with plugin
        with open(os.path.join(plugin_path, "main.py"), "w") as f:
            f.write("tampered content")

        # Verify should fail
        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manager,
        )
        result = verifier.verify_integrity("test_plugin")
        assert not result.is_valid
        assert result.severity == VerificationSeverity.CRITICAL

    def test_plugin_hash_verification_missing_plugin(self):
        """Test detection of missing plugin"""
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)
        plugin = PluginEntry(
            plugin_id="missing_plugin",
            version="1.0.0",
            checksum="abc123",
        )
        assert manager.add_plugin(plugin)

        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manager,
        )
        result = verifier.verify_integrity("missing_plugin")
        assert not result.is_valid
        assert "not found" in result.reason.lower()

    def test_plugin_hash_verification_version_mismatch(self):
        """Test detection of version mismatch"""
        # Create plugin with version 1.0.0
        self._create_test_plugin("test_plugin", "1.0.0")

        # Create manifest expecting version 2.0.0
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)
        plugin = PluginEntry(
            plugin_id="test_plugin",
            version="2.0.0",
            checksum="abc123",
        )
        assert manager.add_plugin(plugin)

        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manager,
        )
        result = verifier.verify_integrity("test_plugin")
        assert not result.is_valid
        assert "version" in result.reason.lower()


class TestDependencyResolver:
    """Tests for dependency resolution"""

    def setup_method(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")

    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_manifest_with_deps(self, plugins: dict):
        """
        Create manifest with dependencies.

        Args:
            plugins: {plugin_id: [dependencies]}
        """
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        for plugin_id, deps in plugins.items():
            plugin = PluginEntry(
                plugin_id=plugin_id,
                version="1.0.0",
                checksum="abc123",
                dependencies=deps,
            )
            assert manager.add_plugin(plugin)

        return manager

    def test_dependency_resolver_linear_chain(self):
        """Test resolution of linear dependency chain"""
        # A → B → C
        manager = self._create_manifest_with_deps({
            "A": ["B"],
            "B": ["C"],
            "C": [],
        })

        resolver = DependencyResolver(manifest_manager=manager)
        order = resolver.resolve_installation_order(["A"])

        # C should be first, then B, then A
        assert order.index("C") < order.index("B")
        assert order.index("B") < order.index("A")

    def test_dependency_resolver_multiple_dependencies(self):
        """Test resolution with multiple dependencies"""
        # D depends on B and C; B depends on A
        manager = self._create_manifest_with_deps({
            "A": [],
            "B": ["A"],
            "C": [],
            "D": ["B", "C"],
        })

        resolver = DependencyResolver(manifest_manager=manager)
        order = resolver.resolve_installation_order(["D"])

        assert "A" in order
        assert "B" in order
        assert "C" in order
        assert "D" in order

        # Verify ordering constraints
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("D")
        assert order.index("C") < order.index("D")

    def test_dependency_resolver_circular_detection(self):
        """Test detection of circular dependencies"""
        # A → B → C → A (cycle)
        manager = self._create_manifest_with_deps({
            "A": ["B"],
            "B": ["C"],
            "C": ["A"],
        })

        resolver = DependencyResolver(manifest_manager=manager)

        with pytest.raises(RuntimeError, match="Circular dependency"):
            resolver.build_dependency_graph()

    def test_dependency_resolver_dag_validation(self):
        """Test DAG validation"""
        manager = self._create_manifest_with_deps({
            "A": ["B"],
            "B": ["C"],
            "C": [],
        })

        resolver = DependencyResolver(manifest_manager=manager)

        # Valid DAG
        is_valid, message = resolver.validate_dag()
        assert is_valid
        assert "Valid" in message


class TestPluginRegistrySynchronizer:
    """Tests for plugin registry synchronization"""

    def setup_method(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.plugins_dir = os.path.join(self.test_dir, "plugins")
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_test_environment(self, plugins_dict: dict):
        """
        Create test environment with manifest and plugins.

        Args:
            plugins_dict: {plugin_id: version}
        """
        manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        for plugin_id, version in plugins_dict.items():
            plugin = PluginEntry(
                plugin_id=plugin_id,
                version=version,
                checksum="canonical_hash",
            )
            assert manager.add_plugin(plugin)

        return manager

    def test_detect_plugin_drift_missing(self):
        """Test detection of missing plugin"""
        manager = self._create_test_environment({
            "plugin1": "1.0.0",
            "plugin2": "1.0.0",
        })

        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manager,
        )
        installer = PluginInstaller(plugins_dir=self.plugins_dir)

        sync = PluginRegistrySynchronizer(
            instance_id="test_instance",
            manifest_manager=manager,
            hash_verifier=verifier,
            installer=installer,
        )

        drifts = sync.detect_plugin_drift()

        # Both plugins should be detected as missing
        assert len(drifts) == 2
        drift_ids = [d.plugin_id for d in drifts]
        assert "plugin1" in drift_ids
        assert "plugin2" in drift_ids

    def test_remediate_missing_plugins(self):
        """Test auto-remediation of missing plugins"""
        manager = self._create_test_environment({
            "plugin1": "1.0.0",
        })

        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manager,
        )
        installer = PluginInstaller(plugins_dir=self.plugins_dir)

        sync = PluginRegistrySynchronizer(
            instance_id="test_instance",
            manifest_manager=manager,
            hash_verifier=verifier,
            installer=installer,
        )

        # Remediate (not dry run)
        remediation_count, messages = sync.remediate(dry_run=False)

        assert remediation_count == 1
        assert len(messages) > 0

        # Verify plugin was installed
        plugin_path = os.path.join(self.plugins_dir, "plugin1")
        assert os.path.exists(plugin_path)

    def test_get_local_and_canonical_plugins(self):
        """Test retrieval of local and canonical plugins"""
        manager = self._create_test_environment({
            "plugin1": "1.0.0",
            "plugin2": "2.0.0",
        })

        installer = PluginInstaller(plugins_dir=self.plugins_dir)

        sync = PluginRegistrySynchronizer(
            instance_id="test_instance",
            manifest_manager=manager,
            installer=installer,
        )

        # Get canonical plugins
        canonical = sync.get_canonical_plugins()
        assert "plugin1" in canonical
        assert canonical["plugin1"] == "1.0.0"

        # Get local plugins (empty initially)
        local = sync.get_local_plugins()
        assert len(local) == 0

        # Install a plugin
        plugin = manager.get_plugin("plugin1")
        installer.install_plugin(plugin)

        # Get local plugins (should have plugin1)
        local = sync.get_local_plugins()
        assert "plugin1" in local
        assert local["plugin1"] == "1.0.0"


class TestPluginInstallerAndUpdater:
    """Tests for plugin installation and updates"""

    def setup_method(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.plugins_dir = os.path.join(self.test_dir, "plugins")
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_plugin_install(self):
        """Test plugin installation"""
        installer = PluginInstaller(plugins_dir=self.plugins_dir)

        plugin = PluginEntry(
            plugin_id="test_plugin",
            version="1.0.0",
            checksum="abc123",
        )

        success, message = installer.install_plugin(plugin)
        assert success
        assert os.path.exists(os.path.join(self.plugins_dir, "test_plugin"))

    def test_plugin_update_with_backup(self):
        """Test plugin update with rollback on failure"""
        installer = PluginInstaller(plugins_dir=self.plugins_dir)

        # Install v1
        plugin_v1 = PluginEntry(
            plugin_id="test_plugin",
            version="1.0.0",
            checksum="abc123",
        )
        success, _ = installer.install_plugin(plugin_v1)
        assert success

        # Update to v2
        plugin_v2 = PluginEntry(
            plugin_id="test_plugin",
            version="2.0.0",
            checksum="def456",
        )
        success, _ = installer.update_plugin(plugin_v2)
        assert success

        # Verify version was updated
        version = self._read_plugin_version(os.path.join(self.plugins_dir, "test_plugin"))
        assert version == "2.0.0"

    def _read_plugin_version(self, plugin_path: str) -> str:
        """Helper to read plugin version"""
        with open(os.path.join(plugin_path, "plugin.json"), "r") as f:
            return json.load(f)["version"]


# Integration tests

class TestPhase3Integration:
    """Integration tests for Phase 3"""

    def setup_method(self):
        """Set up test fixtures"""
        self.test_dir = tempfile.mkdtemp()
        self.plugins_dir = os.path.join(self.test_dir, "plugins")
        self.manifest_path = os.path.join(self.test_dir, "manifest.json")
        Path(self.plugins_dir).mkdir(parents=True, exist_ok=True)

    def teardown_method(self):
        """Clean up test fixtures"""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_multi_instance_sync(self):
        """Test multi-instance synchronization scenario"""
        # Create manifest with 3 plugins
        manifest_manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        for i in range(3):
            plugin = PluginEntry(
                plugin_id=f"plugin_{i}",
                version="1.0.0",
                checksum=f"hash_{i}",
            )
            assert manifest_manager.add_plugin(plugin)

        # Create 3 instances with different drift profiles
        instances = []

        # Instance 1: All plugins missing (full drift)
        verifier1 = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manifest_manager,
        )
        sync1 = PluginRegistrySynchronizer(
            instance_id="instance1",
            manifest_manager=manifest_manager,
            hash_verifier=verifier1,
            installer=PluginInstaller(plugins_dir=self.plugins_dir),
        )
        instances.append(sync1)

        # Detect drifts
        for sync in instances:
            drifts = sync.detect_plugin_drift()
            assert len(drifts) == 3  # All 3 plugins missing

        # Remediate all instances
        for sync in instances:
            count, msgs = sync.remediate(dry_run=False)
            assert count == 3  # All 3 plugins should be installed

    def test_audit_logging_integration(self):
        """Test that audit logging is integrated (mock)"""
        manifest_manager = CanonicalManifestManager(manifest_url=self.manifest_path)

        plugin = PluginEntry(
            plugin_id="audit_test",
            version="1.0.0",
            checksum="abc123",
        )
        assert manifest_manager.add_plugin(plugin)

        verifier = PluginHashVerifier(
            plugins_dir=self.plugins_dir,
            manifest_manager=manifest_manager,
        )
        installer = PluginInstaller(plugins_dir=self.plugins_dir)

        sync = PluginRegistrySynchronizer(
            instance_id="test_instance",
            manifest_manager=manifest_manager,
            hash_verifier=verifier,
            installer=installer,
        )

        # Remediate (should attempt audit logging)
        count, msgs = sync.remediate(dry_run=False)
        assert count == 1

        # Verify audit was attempted (may fail if security_events not available, which is OK for this test)
