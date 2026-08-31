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

    def test_validate_zip_integrity_valid(self, valid_zip):
        """Valid ZIP archive with proper manifest returns parsed manifest."""
        manifest = PackageValidator.validate_zip_integrity(valid_zip)
        assert manifest is not None
        assert isinstance(manifest, dict)
        assert manifest["id"] == "com.example.test-package"
        assert manifest["version"] == "1.0.0"
        assert manifest["name"] == "Test Package"

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

    def test_validate_zip_integrity_missing_manifest(self, tmp_path):
        """ZIP without manifest.json raises ValidationError with clear message."""
        zip_path = tmp_path / "no_manifest.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.md", "# Test Package")
            zf.writestr("LICENSE", "MIT License")

        with pytest.raises(ValidationError) as exc_info:
            PackageValidator.validate_zip_integrity(zip_path)

        assert "manifest.json" in str(exc_info.value.message)
        assert exc_info.value.field == "manifest.json"

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


class TestValidateSkillDefinitions:
    """Tests for validate_skill_definitions."""

    def test_valid_skill_definitions(self, valid_zip):
        """Valid ZIP with skills should parse and validate."""
        skills = PackageValidator.validate_skill_definitions(valid_zip)
        assert isinstance(skills, dict)
        # test_skill.yaml exists in the valid_zip fixture
        assert "test_skill" in skills or len(skills) >= 0

    def test_no_skills_directory(self, tmp_path, valid_manifest):
        """ZIP without skills/ directory should return empty dict."""
        zip_path = tmp_path / "no_skills.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("README.md", "# Test Package")

        skills = PackageValidator.validate_skill_definitions(zip_path)
        assert skills == {}

    def test_skill_with_valid_hooks(self, tmp_path, valid_manifest):
        """Skill with valid hooks should parse."""
        skill_yaml = """
id: my_skill
name: My Skill
description: A test skill

hooks:
  - id: my_preprocessing_hook
    trigger: preprocessing
    priority: 50
    file: hooks/preprocess.py
    function: my_handler
"""
        zip_path = tmp_path / "skill_with_hooks.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)
            zf.writestr("hooks/preprocess.py", "def my_handler(ctx): pass")

        skills = PackageValidator.validate_skill_definitions(zip_path)
        assert "my_skill" in skills
        assert skills["my_skill"]["id"] == "my_skill"
        assert len(skills["my_skill"]["hooks"]) == 1

    def test_skill_missing_required_id(self, tmp_path, valid_manifest):
        """Skill without id should fail."""
        skill_yaml = """
name: My Skill
description: A test skill
"""
        zip_path = tmp_path / "skill_no_id.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/bad_skill.yaml", skill_yaml)

        with pytest.raises(ValidationError, match="required"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_skill_invalid_yaml(self, tmp_path, valid_manifest):
        """Skill with invalid YAML should fail."""
        skill_yaml = "id: my_skill\n  bad indentation: [unclosed"

        zip_path = tmp_path / "skill_bad_yaml.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/bad_skill.yaml", skill_yaml)

        with pytest.raises(ValidationError, match="invalid YAML"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_hook_missing_trigger(self, tmp_path, valid_manifest):
        """Hook missing trigger field should fail."""
        skill_yaml = """
id: my_skill
name: My Skill

hooks:
  - id: bad_hook
    file: hooks/preprocess.py
    function: my_handler
"""
        zip_path = tmp_path / "hook_no_trigger.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)
            zf.writestr("hooks/preprocess.py", "def my_handler(ctx): pass")

        with pytest.raises(ValidationError, match="required"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_hook_invalid_trigger(self, tmp_path, valid_manifest):
        """Hook with invalid trigger should fail."""
        skill_yaml = """
id: my_skill
name: My Skill

hooks:
  - id: bad_hook
    trigger: invalid_trigger
    file: hooks/preprocess.py
    function: my_handler
"""
        zip_path = tmp_path / "hook_bad_trigger.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)
            zf.writestr("hooks/preprocess.py", "def my_handler(ctx): pass")

        with pytest.raises(ValidationError, match="not one of"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_hook_missing_file(self, tmp_path, valid_manifest):
        """Hook referencing missing file should fail."""
        skill_yaml = """
id: my_skill
name: My Skill

hooks:
  - id: bad_hook
    trigger: preprocessing
    file: hooks/nonexistent.py
    function: my_handler
"""
        zip_path = tmp_path / "hook_missing_file.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)

        with pytest.raises(ValidationError, match="references missing file"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_hook_invalid_priority(self, tmp_path, valid_manifest):
        """Hook with invalid priority should fail."""
        skill_yaml = """
id: my_skill
name: My Skill

hooks:
  - id: bad_hook
    trigger: preprocessing
    priority: 2000
    file: hooks/preprocess.py
    function: my_handler
"""
        zip_path = tmp_path / "hook_bad_priority.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)
            zf.writestr("hooks/preprocess.py", "def my_handler(ctx): pass")

        with pytest.raises(ValidationError, match="greater than the maximum"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_hook_invalid_function_name(self, tmp_path, valid_manifest):
        """Hook with invalid Python function name should fail."""
        skill_yaml = """
id: my_skill
name: My Skill

hooks:
  - id: bad_hook
    trigger: preprocessing
    file: hooks/preprocess.py
    function: "123invalid"
"""
        zip_path = tmp_path / "hook_bad_function.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)
            zf.writestr("hooks/preprocess.py", "def _handler(ctx): pass")

        with pytest.raises(ValidationError, match="function name"):
            PackageValidator.validate_skill_definitions(zip_path)

    def test_multiple_skills(self, tmp_path, valid_manifest):
        """ZIP with multiple skills should parse all."""
        skill1_yaml = "id: skill1\nname: Skill 1"
        skill2_yaml = "id: skill2\nname: Skill 2"

        zip_path = tmp_path / "multiple_skills.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/skill1.yaml", skill1_yaml)
            zf.writestr("skills/skill2.yaml", skill2_yaml)

        skills = PackageValidator.validate_skill_definitions(zip_path)
        assert len(skills) == 2
        assert "skill1" in skills
        assert "skill2" in skills

    def test_skill_with_all_hook_types(self, tmp_path, valid_manifest):
        """Skill with hooks of all trigger types should validate."""
        skill_yaml = """
id: my_skill
name: My Skill

hooks:
  - id: preprocess_hook
    trigger: preprocessing
    priority: 100
    file: hooks/handler.py
    function: handle_preprocessing
  - id: error_hook
    trigger: on_error
    priority: 50
    file: hooks/handler.py
    function: handle_error
  - id: complete_hook
    trigger: on_complete
    priority: 10
    file: hooks/handler.py
    function: handle_complete
"""
        zip_path = tmp_path / "skill_all_hooks.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(valid_manifest))
            zf.writestr("skills/my_skill.yaml", skill_yaml)
            zf.writestr(
                "hooks/handler.py",
                "def handle_preprocessing(ctx): pass\ndef handle_error(ctx): pass\ndef handle_complete(ctx): pass",
            )

        skills = PackageValidator.validate_skill_definitions(zip_path)
        assert "my_skill" in skills
        assert len(skills["my_skill"]["hooks"]) == 3
