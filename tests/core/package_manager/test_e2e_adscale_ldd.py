"""E2E test: Load adscale-ldd.zip skill package (ADR-0268 Phase 1)."""
from pathlib import Path

import pytest

from core.package_manager import PackageManager


@pytest.fixture
def adscale_ldd_zip():
    """Path to real adscale-ldd.zip test fixture."""
    zip_path = Path("/home/shumway/projects/adscale-ldd/adscale-ldd.zip")
    if not zip_path.exists():
        pytest.skip(f"adscale-ldd.zip not found at {zip_path}")
    return zip_path


@pytest.fixture
def pm(monkeypatch, tmp_path):
    """Create PackageManager with temp CORVIN_HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return PackageManager(tenant_id="_default")


class TestAdscaleLddE2E:
    """End-to-end test: load, verify, and unload adscale-ldd package."""

    def test_load_adscale_ldd_zip(self, pm, adscale_ldd_zip):
        """Load adscale-ldd.zip should succeed."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        assert pkg.id == "adscale-ldd"
        assert pkg.version.startswith("0.")
        assert pkg.manifest["name"]

    def test_manifest_structure(self, pm, adscale_ldd_zip):
        """adscale-ldd manifest should have expected structure."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        manifest = pkg.manifest

        # Required fields
        assert "id" in manifest
        assert "version" in manifest
        assert "name" in manifest

        # Skill 2.0 format
        assert "capabilities" in manifest
        assert "exports" in manifest
        assert "permissions" in manifest
        assert "configuration" in manifest

    def test_manifest_entry_point(self, pm, adscale_ldd_zip):
        """adscale-ldd should have SKILL.md as entry point."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        pkg_path = Path(pkg.path)
        assert (pkg_path / "SKILL.md").exists()

    def test_package_structure_extracted(self, pm, adscale_ldd_zip):
        """adscale-ldd should have expected directory structure."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        pkg_path = Path(pkg.path)

        # Root files
        assert (pkg_path / "manifest.json").exists()
        assert (pkg_path / "README.md").exists()
        assert (pkg_path / "SKILL.md").exists()

        # Source code
        assert (pkg_path / "src").is_dir()
        assert (pkg_path / "src" / "ldd_core.py").exists()

        # Hooks
        assert (pkg_path / "hooks").is_dir()
        assert (pkg_path / "hooks" / "pre_execute.py").exists()

        # Config
        assert (pkg_path / "config").is_dir()
        assert (pkg_path / "config" / "loop-config.yaml").exists()

        # Tests
        assert (pkg_path / "tests").is_dir()
        assert (pkg_path / "tests" / "test_phase1_complete.py").exists()

    def test_permissions_extracted(self, pm, adscale_ldd_zip):
        """adscale-ldd permissions should be present."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        permissions = pkg.manifest.get("permissions", [])
        assert isinstance(permissions, list)

    def test_package_in_registry(self, pm, adscale_ldd_zip):
        """Loaded package should appear in registry."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        assert pm.registry.has_package(pkg.id)

        registered = pm.registry.get_package(pkg.id)
        assert registered is not None
        assert registered.version == pkg.version
        assert registered.installed_at is not None

    def test_list_packages_includes_adscale(self, pm, adscale_ldd_zip):
        """list_packages should include loaded adscale-ldd."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        packages = pm.list_packages()
        assert pkg.id in packages

    def test_get_package_returns_metadata(self, pm, adscale_ldd_zip):
        """get_package should return full metadata."""
        original = pm.load_from_zip(adscale_ldd_zip)
        retrieved = pm.get_package(original.id)
        assert retrieved is not None
        assert retrieved.id == original.id
        assert retrieved.version == original.version
        assert retrieved.manifest == original.manifest

    def test_unload_adscale_ldd(self, pm, adscale_ldd_zip):
        """Unload adscale-ldd should clean up."""
        pkg = pm.load_from_zip(adscale_ldd_zip)
        pkg_path = Path(pkg.path)
        assert pkg_path.exists()

        pm.unload_package(pkg.id)
        assert not pkg_path.exists()
        assert not pm.registry.has_package(pkg.id)

    def test_registry_persistence(self, pm, adscale_ldd_zip, tmp_path):
        """Registry should persist across PackageManager instances."""
        # Load in first manager instance
        pkg = pm.load_from_zip(adscale_ldd_zip)
        pkg_id = pkg.id

        # Create new manager instance
        import os

        os.environ["HOME"] = str(tmp_path)
        pm2 = PackageManager(tenant_id="_default")

        # Should still see the package
        assert pm2.registry.has_package(pkg_id)
        retrieved = pm2.get_package(pkg_id)
        assert retrieved is not None
        assert retrieved.version == pkg.version
