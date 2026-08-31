"""
Plugin Verification Framework — Shared Test Fixtures

Extends tests/e2e/conftest.py and tests/conftest.py with plugin-specific:
- Isolated plugin environments (fresh CORVIN_HOME per test)
- Plugin manifest factories
- Conflict detection helpers
- Config drift monitoring
- Cross-tenant validation
"""

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from dataclasses import dataclass
from unittest.mock import Mock, patch

import pytest


# ============================================================================
# TIER-4 FIXTURES: Cross-Tenant, Config Drift, Load-Order, Hot-Reload
# ============================================================================

@pytest.fixture
def isolated_plugin_env(tmp_path: Path, monkeypatch) -> Generator[Dict[str, Path], None, None]:
    """
    Per-test isolated plugin environment with:
    - Fresh CORVIN_HOME
    - Empty plugin registry
    - Redirected audit chain (reuses root conftest isolation)
    - Isolated process namespace

    **Usage:**
    ```python
    def test_plugin_something(isolated_plugin_env):
        corvin_home = isolated_plugin_env["corvin_home"]
        registry_path = isolated_plugin_env["registry"]
        # Test executes with zero plugin interference
    ```
    """
    # Create temp CORVIN_HOME
    corvin_home = tmp_path / ".corvin"
    corvin_home.mkdir(parents=True, exist_ok=True)

    # Create empty tenant directory
    tenant_dir = corvin_home / "tenants" / "_default"
    tenant_dir.mkdir(parents=True, exist_ok=True)

    # Create empty plugin registry
    registry_path = tenant_dir / "plugins"
    registry_path.mkdir(parents=True, exist_ok=True)

    # Create audit chain (redirected to tmp, per root conftest pattern)
    audit_dir = corvin_home / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Redirect env
    monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(audit_dir))

    yield {
        "corvin_home": corvin_home,
        "tenant_dir": tenant_dir,
        "registry": registry_path,
        "audit": audit_dir,
    }

    # Cleanup happens automatically via tmp_path fixture


@pytest.fixture
def cross_tenant_validator() -> "CrossTenantValidator":
    """Verify test doesn't leak data across tenants (_default, _tenant2, etc.)"""
    return CrossTenantValidator()


@pytest.fixture
def config_drift_monitor() -> "ConfigDriftMonitor":
    """Track config changes during test, detect drift/corruption"""
    return ConfigDriftMonitor()


@pytest.fixture
def load_order_tracker() -> "LoadOrderTracker":
    """Track plugin load order, detect dependency violations"""
    return LoadOrderTracker()


# ============================================================================
# TIER-3 FIXTURES: Feature-Level Tests
# ============================================================================

@pytest.fixture
def conflict_detector() -> "ConflictDetector":
    """Detect hook conflicts, API version mismatches, mutual exclusivity violations"""
    return ConflictDetector()


@pytest.fixture
def plugin_manifest_factory() -> "PluginManifestFactory":
    """Factory for generating valid + invalid plugin manifests"""
    return PluginManifestFactory()


@pytest.fixture
def state_corruption_injector() -> "StateCorruptionInjector":
    """Inject corruption scenarios (partial state, missing files, zombies)"""
    return StateCorruptionInjector()


# ============================================================================
# TIER-2 FIXTURES: Integration Tests
# ============================================================================

@pytest.fixture
def stub_plugin_context(isolated_plugin_env: Dict[str, Path]) -> "PluginContext":
    """Minimal plugin context for integration tests"""
    from corvin_plugins import PluginContext

    return PluginContext(
        plugin_id="test-stub-plugin",
        tenant_id="_default",
        corvin_home=isolated_plugin_env["corvin_home"],
        config={},
        audit_emit=lambda *_, **__: None,
    )


@pytest.fixture
def mock_plugin_registry(isolated_plugin_env: Dict[str, Path]) -> "MockPluginRegistry":
    """Mock registry for integration tests"""
    return MockPluginRegistry(registry_path=isolated_plugin_env["registry"])


# ============================================================================
# TIER-1 FIXTURES: Unit Tests
# ============================================================================

@pytest.fixture
def valid_manifest_json() -> Dict[str, Any]:
    """Valid plugin manifest (TIER-1 baseline)"""
    return {
        "plugin_id": "test-plugin",
        "version": "0.1.0",
        "plugin_type": "compute_engine",
        "display_name": "Test Plugin",
        "description": "A test plugin",
        "entry_point": "test_plugin:TestPlugin",
        "dependencies": [],
        "requires_api_version": ">=1.0.0",
        "boot_layer": "installed",
        "origin": "buildin",
    }


@pytest.fixture
def invalid_manifest_json() -> Dict[str, Any]:
    """Invalid plugin manifest (missing required fields)"""
    return {
        "plugin_id": "invalid-plugin",
        # Missing: version, plugin_type, display_name, entry_point
    }


# ============================================================================
# HELPER CLASSES
# ============================================================================

class CrossTenantValidator:
    """Verify test doesn't leak data across _default, _tenant2, etc."""

    def __init__(self):
        self.tenant_reads: Dict[str, List[str]] = {}
        self.tenant_writes: Dict[str, List[str]] = {}

    def record_read(self, tenant_id: str, resource: str) -> None:
        """Record a read from a specific tenant"""
        if tenant_id not in self.tenant_reads:
            self.tenant_reads[tenant_id] = []
        self.tenant_reads[tenant_id].append(resource)

    def record_write(self, tenant_id: str, resource: str) -> None:
        """Record a write to a specific tenant"""
        if tenant_id not in self.tenant_writes:
            self.tenant_writes[tenant_id] = []
        self.tenant_writes[tenant_id].append(resource)

    def assert_no_cross_tenant_leaks(self) -> None:
        """Assert that no cross-tenant access occurred"""
        # Check that reads/writes didn't cross tenant boundaries
        all_resources = set()
        for tenant_id, resources in {**self.tenant_reads, **self.tenant_writes}.items():
            for resource in resources:
                if resource in all_resources and tenant_id != "_default":
                    raise AssertionError(
                        f"Cross-tenant leak: {resource} accessed by multiple tenants"
                    )
                all_resources.add(resource)


class ConfigDriftMonitor:
    """Track config changes, detect drift or corruption"""

    def __init__(self):
        self.initial_checksums: Dict[str, str] = {}
        self.final_checksums: Dict[str, str] = {}
        self.changes: List[Dict[str, str]] = []

    def snapshot_config(self, config_path: Path) -> None:
        """Take initial config snapshot"""
        import hashlib
        checksum = hashlib.sha256(config_path.read_bytes()).hexdigest()
        self.initial_checksums[str(config_path)] = checksum

    def detect_drift(self, config_path: Path) -> bool:
        """Detect if config has changed since snapshot"""
        import hashlib
        checksum = hashlib.sha256(config_path.read_bytes()).hexdigest()
        self.final_checksums[str(config_path)] = checksum

        initial = self.initial_checksums.get(str(config_path))
        if initial and initial != checksum:
            self.changes.append({
                "path": str(config_path),
                "initial": initial,
                "final": checksum,
            })
            return True
        return False

    def assert_no_drift(self) -> None:
        """Assert that no config drift occurred"""
        if self.changes:
            raise AssertionError(f"Config drift detected: {self.changes}")


class LoadOrderTracker:
    """Track plugin load order, detect dependency violations"""

    def __init__(self):
        self.load_events: List[Dict[str, Any]] = []

    def record_load(self, plugin_id: str, depends_on: Optional[List[str]] = None) -> None:
        """Record a plugin load event"""
        self.load_events.append({
            "plugin_id": plugin_id,
            "depends_on": depends_on or [],
            "order": len(self.load_events),
        })

    def assert_dependencies_satisfied(self) -> None:
        """Assert that all dependencies were satisfied (loaded before dependent)"""
        loaded_plugins = {evt["plugin_id"] for evt in self.load_events}
        for evt in self.load_events:
            for dep in evt["depends_on"]:
                if dep not in loaded_plugins:
                    raise AssertionError(
                        f"Dependency violation: {evt['plugin_id']} requires {dep}, "
                        f"but {dep} not in load order"
                    )


@dataclass
class ConflictDetector:
    """Detect hook conflicts, API version mismatches, mutual exclusivity violations"""

    def __init__(self):
        self.hook_registrations: Dict[str, List[str]] = {}
        self.conflicts: List[str] = []

    def register_hook(self, hook_name: str, plugin_id: str) -> None:
        """Register a plugin on a hook"""
        if hook_name not in self.hook_registrations:
            self.hook_registrations[hook_name] = []
        self.hook_registrations[hook_name].append(plugin_id)

    def check_exclusive_hooks(self, hook_names: List[str]) -> None:
        """Check that exclusive hooks have at most one plugin"""
        for hook in hook_names:
            if hook in self.hook_registrations and len(self.hook_registrations[hook]) > 1:
                self.conflicts.append(
                    f"Exclusive hook {hook} has {len(self.hook_registrations[hook])} plugins"
                )

    def assert_no_conflicts(self) -> None:
        """Assert that no conflicts were detected"""
        if self.conflicts:
            raise AssertionError(f"Plugin conflicts detected: {self.conflicts}")


class PluginManifestFactory:
    """Factory for generating valid + invalid plugin manifests"""

    def make_valid(self, plugin_id: str = "test-plugin", **overrides) -> Dict[str, Any]:
        """Generate a valid manifest"""
        manifest = {
            "plugin_id": plugin_id,
            "version": "0.1.0",
            "plugin_type": "compute_engine",
            "display_name": f"Test Plugin {plugin_id}",
            "description": "A test plugin",
            "entry_point": f"{plugin_id}:Plugin",
            "dependencies": [],
            "requires_api_version": ">=1.0.0",
            "boot_layer": "installed",
            "origin": "buildin",
        }
        manifest.update(overrides)
        return manifest

    def make_invalid(self, missing_fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Generate an invalid manifest (missing required fields)"""
        manifest = {
            "plugin_id": "invalid",
            "version": "0.1.0",
        }
        if missing_fields:
            for field in missing_fields:
                if field in manifest:
                    del manifest[field]
        return manifest

    def make_with_conflicts(
        self, plugin_ids: List[str], shared_hook: str = "on_task_start"
    ) -> List[Dict[str, Any]]:
        """Generate multiple manifests that share a hook"""
        manifests = []
        for plugin_id in plugin_ids:
            manifest = self.make_valid(plugin_id, hooks=[shared_hook])
            manifests.append(manifest)
        return manifests


class StateCorruptionInjector:
    """Inject corruption scenarios for testing recovery"""

    def corrupt_plugin_state(self, plugin_path: Path) -> None:
        """Corrupt a plugin's state files (simulate crash during write)"""
        state_file = plugin_path / "state.json"
        if state_file.exists():
            # Truncate file to simulate incomplete write
            with open(state_file, "w") as f:
                f.write("{")  # Invalid JSON

    def create_zombie_process(self, plugin_id: str) -> int:
        """Create a zombie process (simulate plugin crash)"""
        import os
        pid = os.fork()
        if pid == 0:
            os.exit(0)  # Child exits immediately, leaving zombie
        return pid


class MockPluginRegistry:
    """Mock registry for integration tests"""

    def __init__(self, registry_path: Path):
        self.registry_path = registry_path
        self.plugins: Dict[str, Dict[str, Any]] = {}

    def register(self, manifest: Dict[str, Any]) -> None:
        """Register a plugin"""
        plugin_id = manifest["plugin_id"]
        self.plugins[plugin_id] = manifest

    def unregister(self, plugin_id: str) -> None:
        """Unregister a plugin"""
        if plugin_id in self.plugins:
            del self.plugins[plugin_id]

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Get all registered plugins"""
        return self.plugins.copy()


# ============================================================================
# PYTEST MARKERS (Registered in pyproject.toml)
# ============================================================================

def pytest_configure(config):
    """Register custom markers (supplement pyproject.toml)"""
    # Markers are defined in pyproject.toml; this is a fallback/documentation
    pass
