"""Unit Tests for SkillManifest Validation (ADR-0853 Layer 1)"""

import pytest
from core.skill_forge.manifest import (
    SkillManifest,
    SkillManifestError,
    InvalidSkillIdError,
    InvalidSchemaError,
    CircularDependencyError,
)


class TestSkillIdValidation:
    """Validate skill_id format constraints."""

    def test_valid_skill_id_simple(self):
        """Valid: lowercase namespace.name"""
        manifest = SkillManifest(
            skill_id="os.delegation_router",
            version="1.0.0",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert manifest.skill_id == "os.delegation_router"

    def test_valid_skill_id_complex(self):
        """Valid: namespace.category.name"""
        manifest = SkillManifest(
            skill_id="marketplace.plugin.search",
            version="1.0.0",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert manifest.skill_id == "marketplace.plugin.search"

    def test_invalid_skill_id_uppercase(self):
        """Invalid: uppercase not allowed"""
        with pytest.raises(InvalidSkillIdError):
            SkillManifest(
                skill_id="OS.delegation_router",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )

    def test_invalid_skill_id_special_chars(self):
        """Invalid: special characters not allowed"""
        with pytest.raises(InvalidSkillIdError):
            SkillManifest(
                skill_id="os-delegation_router",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )

    def test_invalid_skill_id_no_namespace(self):
        """Invalid: must have at least namespace.name"""
        with pytest.raises(InvalidSkillIdError):
            SkillManifest(
                skill_id="skill_name",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )


class TestSemanticVersioning:
    """Validate semantic versioning (major.minor.patch)."""

    def test_valid_semver(self):
        """Valid: major.minor.patch"""
        manifest = SkillManifest(
            skill_id="os.test",
            version="1.2.3",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        assert manifest.version == "1.2.3"

    def test_invalid_semver_two_parts(self):
        """Invalid: only major.minor (missing patch)"""
        with pytest.raises(SkillManifestError):
            SkillManifest(
                skill_id="os.test",
                version="1.2",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )

    def test_invalid_semver_prerelease(self):
        """Invalid: prerelease suffix not allowed"""
        with pytest.raises(SkillManifestError):
            SkillManifest(
                skill_id="os.test",
                version="1.2.3-alpha",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )


class TestSchemaValidation:
    """Validate input_schema and output_schema are valid JSON schema."""

    def test_valid_schemas(self):
        """Valid: both schemas valid"""
        manifest = SkillManifest(
            skill_id="os.test",
            version="1.0.0",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
        )
        assert manifest.input_schema["type"] == "object"

    def test_invalid_input_schema(self):
        """Invalid: input_schema violates JSON schema"""
        with pytest.raises(InvalidSchemaError):
            SkillManifest(
                skill_id="os.test",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "invalid_type"},  # invalid type
                output_schema={"type": "object"},
            )

    def test_invalid_output_schema(self):
        """Invalid: output_schema violates JSON schema"""
        with pytest.raises(InvalidSchemaError):
            SkillManifest(
                skill_id="os.test",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"required": ["missing_type_field"]},  # missing type
            )


class TestDependencyValidation:
    """Validate dependency constraints."""

    def test_valid_dependencies(self):
        """Valid: dependencies listed but not checked (checked by registry)"""
        manifest = SkillManifest(
            skill_id="os.test",
            version="1.0.0",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            depends_on=(("os.base", ">=1.0.0"),),
        )
        assert len(manifest.depends_on) == 1

    def test_invalid_self_dependency(self):
        """Invalid: skill cannot depend on itself"""
        with pytest.raises(CircularDependencyError):
            SkillManifest(
                skill_id="os.test",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                depends_on=(("os.test", "1.0.0"),),
            )


class TestLearnableParamValidation:
    """Validate learnable_params schema."""

    def test_valid_learnable_params(self):
        """Valid: well-formed learnable params"""
        manifest = SkillManifest(
            skill_id="os.test",
            version="1.0.0",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            learnable_params={
                "threshold": {"type": "float", "min": 0.0, "max": 1.0},
                "max_retries": {"type": "int", "min": 0, "max": 10},
                "strategy": {"type": "str"},
            },
        )
        assert "threshold" in manifest.learnable_params
        assert manifest.learnable_params["threshold"]["type"] == "float"

    def test_invalid_learnable_param_type(self):
        """Invalid: unsupported type"""
        with pytest.raises(SkillManifestError):
            SkillManifest(
                skill_id="os.test",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                learnable_params={"param": {"type": "list"}},  # unsupported
            )

    def test_invalid_learnable_param_bounds(self):
        """Invalid: min > max"""
        with pytest.raises(SkillManifestError):
            SkillManifest(
                skill_id="os.test",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                learnable_params={
                    "threshold": {"type": "float", "min": 1.0, "max": 0.0}  # invalid bounds
                },
            )

    def test_missing_learnable_param_type(self):
        """Invalid: learnable param missing 'type' field"""
        with pytest.raises(SkillManifestError):
            SkillManifest(
                skill_id="os.test",
                version="1.0.0",
                author="test",
                description="Test skill",
                entry_point="test.skill",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                learnable_params={"param": {"min": 0.0, "max": 1.0}},  # missing type
            )


class TestManifestSerialization:
    """Test to_dict, to_json, from_dict, from_json."""

    def test_to_dict_and_back(self):
        """Round-trip: manifest → dict → manifest"""
        original = SkillManifest(
            skill_id="os.test",
            version="1.0.0",
            author="test_author",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            learnable_params={"threshold": {"type": "float", "min": 0.0, "max": 1.0}},
            audit_events=("skill_executed", "config_optimized"),
            required_checks=("audit_trail",),
        )

        # to_dict and back
        data_dict = original.to_dict()
        restored = SkillManifest.from_dict(data_dict)

        assert restored.skill_id == original.skill_id
        assert restored.version == original.version
        assert restored.author == original.author
        assert restored.learnable_params == original.learnable_params

    def test_to_json_and_back(self):
        """Round-trip: manifest → JSON → manifest"""
        original = SkillManifest(
            skill_id="marketplace.search",
            version="2.1.0",
            author="marketplace_team",
            description="Plugin search skill",
            entry_point="marketplace.skills.search",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"results": {"type": "array"}}},
        )

        # to_json and back
        json_str = original.to_json()
        restored = SkillManifest.from_json(json_str)

        assert restored.skill_id == original.skill_id
        assert restored.version == original.version
        assert restored.entry_point == original.entry_point


class TestManifestImmutability:
    """Verify manifest is frozen (immutable)."""

    def test_manifest_frozen(self):
        """Manifest is frozen after creation."""
        manifest = SkillManifest(
            skill_id="os.test",
            version="1.0.0",
            author="test",
            description="Test skill",
            entry_point="test.skill",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )

        # Attempting to modify should raise FrozenInstanceError
        with pytest.raises(Exception):  # FrozenInstanceError from dataclass(frozen=True)
            manifest.version = "2.0.0"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
