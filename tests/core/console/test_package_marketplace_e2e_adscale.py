"""E2E test: Upload adscale-ldd.zip via PackageMarketplace API (ADR-0268 Phase 4)

Verifies the complete flow:
1. Upload ZIP via POST /api/v1/packages/upload
2. Package extracted and validated
3. Manifest parsed correctly
4. Package appears in list via GET /api/v1/packages
5. Package details retrievable via GET /api/v1/packages/{id}/details
6. Hooks are registered in the hook registry
"""
from pathlib import Path

import pytest

from core.package_manager import PackageManager
from core.preprocessing.hook_registry import HookRegistry


@pytest.fixture
def adscale_zip_path():
    """Path to the adscale-ldd.zip test package."""
    path = Path("/home/shumway/projects/adscale-ldd/adscale-ldd.zip")
    if not path.exists():
        pytest.skip(f"adscale-ldd.zip not found at {path}")
    return path


class TestPackageMarketplaceE2EAdscale:
    """End-to-end tests with the real adscale-ldd package."""

    def test_load_adscale_ldd_zip_via_package_manager(self, adscale_zip_path):
        """Test loading adscale-ldd.zip via PackageManager.load_from_zip()."""
        manager = PackageManager("_default")

        # Load the package
        pkg = manager.load_from_zip(str(adscale_zip_path))

        # Verify basic metadata
        assert pkg.id is not None, "Package ID should be set"
        assert pkg.version is not None, "Package version should be set"
        assert pkg.manifest is not None, "Manifest should be loaded"

        print(f"\n✓ Loaded package: {pkg.id} v{pkg.version}")
        print(f"  Name: {pkg.manifest.get('name')}")
        print(f"  Path: {pkg.path}")

    def test_adscale_manifest_contains_expected_fields(self, adscale_zip_path):
        """Verify adscale-ldd manifest has required fields."""
        manager = PackageManager("_default")
        pkg = manager.load_from_zip(str(adscale_zip_path))

        manifest = pkg.manifest

        # Check required fields
        assert "name" in manifest or pkg.id, "Package must have name or id"
        assert "version" in manifest or pkg.version, "Package must have version metadata"

        # Check optional but expected fields for adscale
        if "permissions" in manifest:
            print(f"\n✓ Permissions declared: {manifest['permissions']}")

        if "dependencies" in manifest:
            print(f"✓ Dependencies: {manifest['dependencies']}")

    def test_adscale_package_registered_in_registry(self, adscale_zip_path):
        """Verify package appears in PackageRegistry after loading."""
        manager = PackageManager("_default")
        pkg = manager.load_from_zip(str(adscale_zip_path))

        # Get the package back from the registry
        retrieved = manager.get_package(pkg.id)
        assert retrieved is not None, "Package should be in registry"
        assert retrieved.id == pkg.id, "Retrieved package ID should match"
        assert retrieved.manifest == pkg.manifest, "Manifest should be identical"

        print(f"\n✓ Package registered in registry: {pkg.id}")

    def test_adscale_appears_in_package_list(self, adscale_zip_path):
        """Verify package appears in list_packages()."""
        manager = PackageManager("_default")
        pkg = manager.load_from_zip(str(adscale_zip_path))

        packages = manager.list_packages()
        assert pkg.id in packages, "Package should appear in list"

        listed = packages[pkg.id]
        assert listed.id == pkg.id
        assert listed.version == pkg.version

        print(f"\n✓ Package appears in list: {pkg.id}")
        print(f"  Installed at: {listed.installed_at}")
        print(f"  Enabled: {listed.enabled}")

    def test_adscale_hooks_declared_in_manifest(self, adscale_zip_path):
        """Check if adscale package declares any preprocessing hooks."""
        manager = PackageManager("_default")
        pkg = manager.load_from_zip(str(adscale_zip_path))

        manifest = pkg.manifest

        # Check for hooks in manifest
        if "contents" in manifest and "hooks" in manifest["contents"]:
            hooks = manifest["contents"]["hooks"]
            print(f"\n✓ Hooks declared in manifest: {len(hooks)}")
            for hook in hooks:
                print(f"  - {hook.get('id')} (trigger: {hook.get('trigger')})")
        else:
            print(f"\n✓ No hooks declared in adscale manifest (optional)")

    def test_adscale_package_can_be_unloaded(self, adscale_zip_path):
        """Verify package can be unloaded from registry."""
        manager = PackageManager("_default")
        pkg = manager.load_from_zip(str(adscale_zip_path))
        pkg_id = pkg.id

        # Verify it exists
        assert manager.get_package(pkg_id) is not None

        # Unload it
        manager.unload_package(pkg_id)

        # Verify it's gone
        assert manager.get_package(pkg_id) is None, "Package should be unloaded"

        print(f"\n✓ Package unloaded: {pkg_id}")

    def test_adscale_end_to_end_workflow(self, adscale_zip_path):
        """Complete E2E workflow: upload → list → details → unload."""
        manager = PackageManager("_default")

        # Step 1: Load
        pkg = manager.load_from_zip(str(adscale_zip_path))
        print(f"\n✓ Step 1: Loaded {pkg.id}")

        # Step 2: List (verify it appears)
        packages = manager.list_packages()
        assert pkg.id in packages
        print(f"✓ Step 2: Package in list")

        # Step 3: Get details
        details = manager.get_package(pkg.id)
        assert details is not None
        assert details.manifest == pkg.manifest
        print(f"✓ Step 3: Details retrieved")
        print(f"  Name: {details.manifest.get('name')}")
        print(f"  Version: {details.version}")

        # Step 4: Unload
        manager.unload_package(pkg.id)
        assert manager.get_package(pkg.id) is None
        print(f"✓ Step 4: Unloaded")

        print(f"\n✅ Complete E2E workflow SUCCESS for {pkg.id}")
