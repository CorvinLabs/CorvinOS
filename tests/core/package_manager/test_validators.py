"""Tests for package validators (ADR-0268)."""
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from core.package_manager.validators import (
    PackageValidator,
    ValidationError,
    MANIFEST_SCHEMA,
)


@pytest.fixture
def valid_manifest():
    """Valid manifest matching adscale-ldd schema."""
    return {
        "id": "com.example.test-package",
        "version": "1.0.0",
        "name": "Test Package",
        "display_name": "Test Package Display",
        "corvinOS": {"min_version": "0.10.110"},
        "permissions": ["audit:write", "storage:read"],
        "dependencies": [
            {"id": "com.corvinlabs.core", "version": ">=1.0.0"},
        ],
        "capabilities": ["skill_loading", "hook_execution"],
        "configuration": {
            "required": ["api_key"],
            "optional": ["debug_mode"],
        },
    }


@pytest.fixture
def valid_zip(valid_manifest, tmp_path):
    """Create a valid test ZIP with manifest."""
    zip_path = tmp_path / "test-package.zip"

    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(valid_manifest))
        zf.writestr("README.md", "# Test Package")
        zf.writestr("skills/test_skill.yaml", "id: test_skill\nname: Test Skill")

    return zip_path


class TestValidateZipIntegrity:
    """Tests for validate_zip_integrity."""

    def test_valid_zip(self, valid_zip):
        """Valid ZIP with manifest should parse."""
        manifest = PackageValidator.validate_zip_integrity(valid_zip)
        assert manifest["id"] == "com.example.test-package"
        assert manifest["version"] == "1.0.0"

    def test_missing_zip_file(self):
        """Missing ZIP file should raise."""
        with pytest.raises(ValidationError, match="ZIP file not found"):
            PackageValidator.validate_zip_integrity("/nonexistent/file.zip")

    def test_corrupted_zip(self, tmp_path):
        """Corrupted ZIP should raise."""
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_text("not a zip file")

        with pytest.raises(ValidationError, match="ZIP archive corrupted"):
            PackageValidator.validate_zip_integrity(bad_zip)

    def test_missing_manifest(self, tmp_path):
        """ZIP without manifest.json should raise."""
        zip_path = tmp_path / "no_manifest.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.md", "No manifest here")

        with pytest.raises(ValidationError, match="manifest.json not found"):
            PackageValidator.validate_zip_integrity(zip_path)

    def test_invalid_manifest_json(self, tmp_path):
        """ZIP with invalid JSON manifest should raise."""
        zip_path = tmp_path / "bad_json.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", "{ invalid json }")

        with pytest.raises(ValidationError, match="invalid JSON"):
            PackageValidator.validate_zip_integrity(zip_path)


class TestValidateManifestSchema:
    """Tests for validate_manifest_schema."""

    def test_valid_manifest(self, valid_manifest):
        """Valid manifest should pass."""
        PackageValidator.validate_manifest_schema(valid_manifest)

    def test_missing_required_name(self, valid_manifest):
        """Manifest without 'name' should fail."""
        del valid_manifest["name"]
        with pytest.raises(ValidationError, match="name"):
            PackageValidator.validate_manifest_schema(valid_manifest)

    def test_manifest_without_id_ok(self, valid_manifest):
        """Manifest without 'id' should be OK (Skill 2.0 format)."""
        del valid_manifest["id"]
        # Should not raise - id is optional
        PackageValidator.validate_manifest_schema(valid_manifest)

    def test_manifest_without_version_ok(self, valid_manifest):
        """Manifest without 'version' should be OK (Skill 2.0 format)."""
        del valid_manifest["version"]
        # Should not raise - version is optional
        PackageValidator.validate_manifest_schema(valid_manifest)

    def test_invalid_version_type(self, valid_manifest):
        """Non-string version should fail."""
        valid_manifest["version"] = 123
        with pytest.raises(ValidationError, match="must be string"):
            PackageValidator.validate_manifest_schema(valid_manifest)


class TestValidateDependencies:
    """Tests for validate_dependencies."""

    def test_all_dependencies_present(self, valid_manifest):
        """All dependencies met should pass."""
        installed = {
            "com.corvinlabs.core": "1.5.0",
        }
        PackageValidator.validate_dependencies(valid_manifest, installed)

    def test_missing_dependency(self, valid_manifest):
        """Missing dependency should raise."""
        installed = {}
        with pytest.raises(ValidationError, match="Unmet dependencies"):
            PackageValidator.validate_dependencies(valid_manifest, installed)

    def test_version_too_low(self, valid_manifest):
        """Installed version too low should raise."""
        installed = {
            "com.corvinlabs.core": "0.9.0",
        }
        with pytest.raises(ValidationError, match="Unmet dependencies"):
            PackageValidator.validate_dependencies(valid_manifest, installed)

    def test_no_dependencies(self, valid_manifest):
        """Manifest without dependencies should pass."""
        valid_manifest["dependencies"] = []
        PackageValidator.validate_dependencies(valid_manifest, {})

    def test_version_constraint_equality(self, valid_manifest):
        """Version equality constraint."""
        valid_manifest["dependencies"] = [
            {"id": "test.lib", "version": "=2.0.0"},
        ]
        installed = {"test.lib": "2.0.0"}
        PackageValidator.validate_dependencies(valid_manifest, installed)

    def test_version_constraint_greater_than(self, valid_manifest):
        """Version > constraint."""
        valid_manifest["dependencies"] = [
            {"id": "test.lib", "version": ">2.0.0"},
        ]
        installed = {"test.lib": "2.1.0"}
        PackageValidator.validate_dependencies(valid_manifest, installed)


class TestValidatePermissions:
    """Tests for validate_permissions."""

    def test_extract_permissions(self, valid_manifest):
        """Should extract permissions list."""
        perms = PackageValidator.validate_permissions(valid_manifest)
        assert "audit:write" in perms
        assert "storage:read" in perms

    def test_empty_permissions(self, valid_manifest):
        """Manifest with no permissions should return empty list."""
        valid_manifest["permissions"] = []
        perms = PackageValidator.validate_permissions(valid_manifest)
        assert perms == []

    def test_missing_permissions(self, valid_manifest):
        """Manifest without permissions field should return empty list."""
        del valid_manifest["permissions"]
        perms = PackageValidator.validate_permissions(valid_manifest)
        assert perms == []
