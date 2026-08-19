"""Phase 1.2 unit tests: Meta.json Schema + Validator."""

import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta

from core.skill_management.schema import SKILL_METADATA_SCHEMA, TOOL_METADATA_SCHEMA
from core.skill_management.validator import (
    MetadataValidator,
    DependencyValidator,
    ValidationResult,
    validate_metadata_file
)


@pytest.fixture
def temp_tenant_dir(tmp_path, monkeypatch):
    """Create temp tenant structure."""
    monkeypatch.setenv("HOME", str(tmp_path))
    tenant_path = tmp_path / ".corvin" / "tenants" / "_default"
    (tenant_path / "_shared" / "skills").mkdir(parents=True)
    (tenant_path / "_local" / "skills").mkdir(parents=True)
    return tenant_path


class TestSchemaValidation:
    def test_schema_valid_minimal_metadata(self):
        """Valid minimal skill metadata passes schema."""
        metadata = {
            "id": "test-skill",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        # Should not raise
        import jsonschema
        jsonschema.validate(metadata, SKILL_METADATA_SCHEMA)

    def test_schema_invalid_version_format(self):
        """Invalid semantic version rejected."""
        metadata = {
            "id": "test",
            "version": "1.0",  # Missing patch
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        import jsonschema
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(metadata, SKILL_METADATA_SCHEMA)

    def test_schema_invalid_scope(self):
        """Invalid scope rejected."""
        metadata = {
            "id": "test",
            "version": "1.0.0",
            "scope": "invalid_scope",  # Not _platform/_shared/_local
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        import jsonschema
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(metadata, SKILL_METADATA_SCHEMA)

    def test_schema_missing_required_fields(self):
        """Missing required fields rejected."""
        metadata = {
            "id": "test",
            "version": "1.0.0"
            # Missing scope, created, last_modified
        }
        import jsonschema
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(metadata, SKILL_METADATA_SCHEMA)

    def test_schema_invalid_dependency_format(self):
        """Invalid dependency format rejected."""
        metadata = {
            "id": "test",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [
                {"id": "dep1"}  # Missing scope
            ]
        }
        import jsonschema
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(metadata, SKILL_METADATA_SCHEMA)


class TestMetadataValidator:
    def test_validator_loads_valid_metadata(self, temp_tenant_dir):
        """Validator loads and validates valid metadata."""
        skill_dir = temp_tenant_dir / "_shared" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)

        metadata = {
            "id": "test-skill",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

        validator = MetadataValidator()
        result = validator.validate_skill_metadata("test-skill", "_shared")

        assert result.valid is True
        assert len(result.errors) == 0

    def test_validator_rejects_invalid_json(self, temp_tenant_dir):
        """Validator rejects corrupted JSON."""
        skill_dir = temp_tenant_dir / "_shared" / "skills" / "bad-json"
        skill_dir.mkdir(parents=True)

        with open(skill_dir / "meta.json", "w") as f:
            f.write("{ invalid json }")

        validator = MetadataValidator()
        result = validator.validate_skill_metadata("bad-json", "_shared")

        assert result.valid is False
        assert len(result.errors) > 0
        assert "JSON" in result.errors[0].error

    def test_validator_checks_timestamps(self, temp_tenant_dir):
        """Validator checks created <= modified."""
        skill_dir = temp_tenant_dir / "_shared" / "skills" / "bad-time"
        skill_dir.mkdir(parents=True)

        now = datetime.now()
        metadata = {
            "id": "bad-time",
            "version": "1.0.0",
            "scope": "_shared",
            "created": (now + timedelta(days=1)).isoformat(),  # Created AFTER modified
            "last_modified": now.isoformat()
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

        validator = MetadataValidator()
        result = validator.validate_skill_metadata("bad-time", "_shared")

        assert result.valid is False
        assert any("timestamps" in e.field for e in result.errors)

    def test_validator_detects_circular_deps(self, temp_tenant_dir):
        """Validator detects circular dependencies."""
        # Create skill A -> B -> A
        for skill_id in ["skill-a", "skill-b"]:
            skill_dir = temp_tenant_dir / "_shared" / "skills" / skill_id
            skill_dir.mkdir(parents=True)

        # A depends on B
        meta_a = {
            "id": "skill-a",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [{"id": "skill-b", "scope": "_shared"}]
        }
        with open(temp_tenant_dir / "_shared" / "skills" / "skill-a" / "meta.json", "w") as f:
            json.dump(meta_a, f)

        # B depends on A (cycle!)
        meta_b = {
            "id": "skill-b",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [{"id": "skill-a", "scope": "_shared"}]
        }
        with open(temp_tenant_dir / "_shared" / "skills" / "skill-b" / "meta.json", "w") as f:
            json.dump(meta_b, f)

        dep_validator = DependencyValidator()
        cycles = dep_validator.validate_circular_dependencies("_shared")

        assert len(cycles) > 0, "Should detect cycle"

    def test_validator_rejects_local_deps_on_export(self, temp_tenant_dir):
        """Cannot export skill that depends on _local skills."""
        # Create skill in _shared that depends on _local
        skill_dir = temp_tenant_dir / "_shared" / "skills" / "export-test"
        skill_dir.mkdir(parents=True)

        metadata = {
            "id": "export-test",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "dependencies": [{"id": "local-dep", "scope": "_local", "min_version": "1.0.0"}]
        }
        with open(skill_dir / "meta.json", "w") as f:
            json.dump(metadata, f)

        validator = MetadataValidator()
        result = validator.validate_skill_exports("export-test", "_shared")

        assert result.valid is False
        assert any("_local" in e.error for e in result.errors)

    def test_validator_validates_all_skills(self, temp_tenant_dir):
        """Validator can validate all skills in scope."""
        # Create 3 skills
        for i in range(3):
            skill_dir = temp_tenant_dir / "_shared" / "skills" / f"skill-{i}"
            skill_dir.mkdir(parents=True)
            metadata = {
                "id": f"skill-{i}",
                "version": "1.0.0",
                "scope": "_shared",
                "created": datetime.now().isoformat(),
                "last_modified": datetime.now().isoformat()
            }
            with open(skill_dir / "meta.json", "w") as f:
                json.dump(metadata, f)

        validator = MetadataValidator()
        results = validator.validate_all_skills("_shared")

        assert len(results) == 3
        assert all(r.valid for r in results.values())

    def test_convenience_function_validate_file(self, tmp_path):
        """Test validate_metadata_file convenience function."""
        meta_file = tmp_path / "meta.json"
        metadata = {
            "id": "test",
            "version": "1.0.0",
            "scope": "_shared",
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat()
        }
        with open(meta_file, "w") as f:
            json.dump(metadata, f)

        result = validate_metadata_file(meta_file, metadata_type="skill")
        assert result.valid is True

    def test_missing_metadata_file(self, tmp_path):
        """Validator handles missing metadata file gracefully."""
        validator = MetadataValidator()
        result = validator.validate_skill_metadata("nonexistent", "_shared")

        assert result.valid is False
        assert len(result.errors) > 0
