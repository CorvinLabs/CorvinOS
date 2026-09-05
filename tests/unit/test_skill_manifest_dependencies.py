"""Unit tests for Skill Manifest Dependencies (ADR-0611)."""

import pytest

from core.skills.skill_manifest_dependencies import (
    CapabilityDependency,
    DependencyFallbackMode,
    SkillCapabilitiesDependencies,
    dependencies_from_skill_config,
)


class TestCapabilityDependency:
    """Test individual dependency definitions."""

    def test_valid_dependency(self):
        """Create a valid dependency."""
        dep = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="context.semantic_retrieval",
            min_capabilities_version="1.0",
            allowed_plugins=["semantic-context-retriever", "external-context-source"],
            required=True,
        )
        assert dep.id == "context_source"
        assert dep.required is True

    def test_dependency_invalid_id(self):
        """Reject invalid dependency id."""
        with pytest.raises(ValueError, match="Invalid dependency id"):
            CapabilityDependency(
                id="",
                type="capability",
                capability_type="context_source",
                capability_id="test",
                allowed_plugins=["plugin"],
            )

    def test_dependency_empty_whitelist(self):
        """Reject empty allowed_plugins."""
        with pytest.raises(ValueError, match="allowed_plugins cannot be empty"):
            CapabilityDependency(
                id="test",
                type="capability",
                capability_type="context_source",
                capability_id="test",
                allowed_plugins=[],
            )

    def test_dependency_fallback_missing_capability_id(self):
        """Require fallback_capability_id for retry_with_degraded."""
        with pytest.raises(ValueError, match="fallback_capability_id required"):
            CapabilityDependency(
                id="test",
                type="capability",
                capability_type="context_source",
                capability_id="test",
                allowed_plugins=["plugin"],
                fallback_mode=DependencyFallbackMode.RETRY_WITH_DEGRADED,
                fallback_capability_id=None,
            )


class TestSkillManifest:
    """Test SkillCapabilitiesDependencies validation."""

    def test_valid_manifest(self):
        """Create a valid manifest."""
        dep = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="context.semantic_retrieval",
            allowed_plugins=["semantic-context-retriever"],
            required=True,
        )
        manifest = SkillCapabilitiesDependencies(
            skill_id="os.context_adapter",
            skill_version="1.0",
            dependencies=[dep],
        )
        errors = manifest.validate()
        assert len(errors) == 0

    def test_manifest_duplicate_dependency_ids(self):
        """Reject duplicate dependency ids."""
        dep1 = CapabilityDependency(
            id="same",
            type="capability",
            capability_type="context_source",
            capability_id="test1",
            allowed_plugins=["plugin1"],
        )
        dep2 = CapabilityDependency(
            id="same",
            type="capability",
            capability_type="context_source",
            capability_id="test2",
            allowed_plugins=["plugin2"],
        )
        manifest = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep1, dep2],
        )
        errors = manifest.validate()
        assert len(errors) > 0
        assert "Duplicate dependency id" in errors[0]

    def test_manifest_required_dependencies(self):
        """Get required dependencies."""
        req_dep = CapabilityDependency(
            id="required",
            type="capability",
            capability_type="context_source",
            capability_id="test",
            allowed_plugins=["plugin"],
            required=True,
        )
        opt_dep = CapabilityDependency(
            id="optional",
            type="capability",
            capability_type="cache_provider",
            capability_id="test",
            allowed_plugins=["plugin"],
            required=False,
        )
        manifest = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[req_dep, opt_dep],
        )
        required = manifest.required_dependencies()
        assert len(required) == 1
        assert required[0].id == "required"

    def test_manifest_optional_dependencies(self):
        """Get optional dependencies."""
        req_dep = CapabilityDependency(
            id="required",
            type="capability",
            capability_type="context_source",
            capability_id="test",
            allowed_plugins=["plugin"],
            required=True,
        )
        opt_dep = CapabilityDependency(
            id="optional",
            type="capability",
            capability_type="cache_provider",
            capability_id="test",
            allowed_plugins=["plugin"],
            required=False,
        )
        manifest = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[req_dep, opt_dep],
        )
        optional = manifest.optional_dependencies()
        assert len(optional) == 1
        assert optional[0].id == "optional"

    def test_manifest_get_dependency(self):
        """Get dependency by id."""
        dep = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="test",
            allowed_plugins=["plugin"],
        )
        manifest = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep],
        )
        found = manifest.get_dependency("context_source")
        assert found == dep

        not_found = manifest.get_dependency("nonexistent")
        assert not_found is None


class TestManifestFactory:
    """Test factory from config dict."""

    def test_from_skill_config(self):
        """Create manifest from skill config."""
        config = {
            "dependencies": [
                {
                    "id": "context_source",
                    "type": "capability",
                    "capability_type": "context_source",
                    "capability_id": "context.semantic_retrieval",
                    "min_capabilities_version": "1.0",
                    "allowed_plugins": ["semantic-context-retriever"],
                    "required": True,
                }
            ]
        }
        manifest = dependencies_from_skill_config("os.context_adapter", "1.0", config)
        assert manifest.skill_id == "os.context_adapter"
        assert len(manifest.dependencies) == 1
        assert manifest.dependencies[0].id == "context_source"

    def test_from_empty_config(self):
        """Handle config with no dependencies."""
        manifest = dependencies_from_skill_config("test", "1.0", {})
        assert len(manifest.dependencies) == 0
