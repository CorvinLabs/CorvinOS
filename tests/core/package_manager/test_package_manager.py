"""Tests for PackageManager (ADR-0268 Phase 1)."""
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from core.package_manager import PackageManager, PackageValidator
from core.package_manager.validators import ValidationError


@pytest.fixture
def test_manifest():
    """Create a test manifest."""
    return {
        "id": "com.example.test-pkg",
        "version": "1.0.0",
        "name": "Test Package",
        "display_name": "Test Package",
        "permissions": ["storage:read"],
        "dependencies": [],
        "capabilities": ["skill_loading"],
        "contents": {
            "skills": [{"id": "test_skill", "file": "skills/test_skill.yaml"}],
            "hooks": [],
        },
    }


@pytest.fixture
def test_zip(test_manifest, tmp_path):
    """Create a valid test ZIP with manifest."""
    zip_path = tmp_path / "test-pkg.zip"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(test_manifest))
        zf.writestr("README.md", "# Test Package")
        zf.writestr("skills/test_skill.yaml", "id: test_skill\nname: Test Skill")

    return zip_path


@pytest.fixture
def pm(monkeypatch, tmp_path):
    """Create PackageManager with temp CORVIN_HOME."""
    corvin_home = tmp_path / ".corvin"
    monkeypatch.setenv("HOME", str(tmp_path))
    return PackageManager(tenant_id="_default")


class TestPackageManagerLoadFromZip:
    """Tests for PackageManager.load_from_zip."""

    def test_load_valid_zip(self, pm, test_zip):
        """Loading valid ZIP should succeed."""
        pkg = pm.load_from_zip(test_zip)
        assert pkg.id == "com.example.test-pkg"
        assert pkg.version == "1.0.0"
        assert pkg.manifest["name"] == "Test Package"

    def test_package_extracted_to_correct_path(self, pm, test_zip):
        """Package should be extracted to ~/.corvin/tenants/{tenant}/packages/{pkg_id}/"""
        pkg = pm.load_from_zip(test_zip)
        pkg_path = Path(pkg.path)
        assert pkg_path.exists()
        assert (pkg_path / "manifest.json").exists()
        assert (pkg_path / "skills" / "test_skill.yaml").exists()

    def test_package_added_to_registry(self, pm, test_zip):
        """Loaded package should be in registry."""
        pkg = pm.load_from_zip(test_zip)
        assert pm.registry.has_package(pkg.id)
        retrieved = pm.registry.get_package(pkg.id)
        assert retrieved.version == "1.0.0"

    def test_load_invalid_zip(self, pm, tmp_path):
        """Loading invalid ZIP should raise."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("not a zip")

        with pytest.raises(ValidationError):
            pm.load_from_zip(bad_zip)

    def test_load_zip_missing_manifest(self, pm, tmp_path):
        """ZIP without manifest.json should raise."""
        zip_path = tmp_path / "no_manifest.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.md", "No manifest")

        with pytest.raises(ValidationError, match="manifest.json not found"):
            pm.load_from_zip(zip_path)

    def test_load_zip_with_unmet_dependencies(self, test_manifest, tmp_path, pm):
        """ZIP with unmet dependencies should raise."""
        test_manifest["dependencies"] = [
            {"id": "com.missing.dep", "version": ">=1.0.0"},
        ]

        zip_path = tmp_path / "with_deps.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(test_manifest))

        with pytest.raises(ValidationError, match="Unmet dependencies"):
            pm.load_from_zip(zip_path)


class TestPackageManagerList:
    """Tests for PackageManager.list_packages."""

    def test_list_empty(self, pm):
        """Empty package list initially."""
        packages = pm.list_packages()
        assert len(packages) == 0

    def test_list_after_install(self, pm, test_zip):
        """Should list installed packages."""
        pm.load_from_zip(test_zip)
        packages = pm.list_packages()
        assert len(packages) == 1
        assert "com.example.test-pkg" in packages


class TestPackageManagerUnload:
    """Tests for PackageManager.unload_package."""

    def test_unload_package(self, pm, test_zip):
        """Unloading package should remove it."""
        pkg = pm.load_from_zip(test_zip)
        assert pm.registry.has_package(pkg.id)

        pm.unload_package(pkg.id)
        assert not pm.registry.has_package(pkg.id)
        assert not Path(pkg.path).exists()

    def test_unload_nonexistent_package(self, pm):
        """Unloading non-existent package should raise."""
        with pytest.raises(ValueError, match="Package not found"):
            pm.unload_package("nonexistent")


class TestPackageManagerGetPackage:
    """Tests for PackageManager.get_package."""

    def test_get_installed_package(self, pm, test_zip):
        """Get should return installed package metadata."""
        original = pm.load_from_zip(test_zip)
        retrieved = pm.get_package(original.id)
        assert retrieved is not None
        assert retrieved.version == original.version

    def test_get_nonexistent_package(self, pm):
        """Get non-existent package should return None."""
        assert pm.get_package("nonexistent") is None
