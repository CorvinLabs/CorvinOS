"""Unit tests for Skill Dependency Resolver (ADR-0611)."""

import pytest

from core.plugins.corvin_plugins.capability_registry import CapabilityRegistry
from core.plugins.corvin_plugins.manifest_capabilities import (
    Capability,
    CapabilityType,
    PluginCapabilitiesManifest,
)
from core.skills.skill_dependency_resolver import SkillDependencyResolver
from core.skills.skill_manifest_dependencies import (
    CapabilityDependency,
    SkillCapabilitiesDependencies,
)


@pytest.fixture
def registry():
    """Create a fresh registry with test data."""
    reg = CapabilityRegistry()

    # Register two plugins
    cap1 = Capability(
        id="context.semantic",
        type=CapabilityType.CONTEXT_SOURCE,
        description="Semantic retrieval",
    )
    manifest1 = PluginCapabilitiesManifest(
        plugin_id="semantic-context-retriever",
        plugin_version="1.3.4",
        capabilities_version="1.0",
        capabilities=[cap1],
    )
    reg.register_manifest(manifest1)

    cap2 = Capability(
        id="context.deep",
        type=CapabilityType.CONTEXT_SOURCE,
        description="Deep retrieval",
    )
    manifest2 = PluginCapabilitiesManifest(
        plugin_id="external-context-source",
        plugin_version="2.0.0",
        capabilities_version="2.0",
        capabilities=[cap2],
    )
    reg.register_manifest(manifest2)

    yield reg
    reg.clear()


@pytest.fixture
def resolver(registry):
    """Create resolver with test registry."""
    return SkillDependencyResolver(registry)


class TestSkillDependencyResolver:
    """Test dependency resolution."""

    def test_resolve_single_required_dependency(self, resolver):
        """Resolve a required dependency."""
        dep = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="context.semantic",
            allowed_plugins=["semantic-context-retriever"],
            required=True,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="os.context_adapter",
            skill_version="1.0",
            dependencies=[dep],
        )

        resolution = resolver.resolve_all(skill)

        assert resolution.status == "ok"
        assert len(resolution.resolved_plugins) == 1
        assert "context_source" in resolution.resolved_plugins
        plugin_id, cap = resolution.resolved_plugins["context_source"]
        assert plugin_id == "semantic-context-retriever"

    def test_resolve_optional_dependency(self, resolver):
        """Resolve an optional dependency."""
        dep = CapabilityDependency(
            id="cache_provider",
            type="capability",
            capability_type="cache_provider",
            capability_id="cache.mem",
            allowed_plugins=["nonexistent-plugin"],
            required=False,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep],
        )

        resolution = resolver.resolve_all(skill)

        assert resolution.status == "degraded"  # Optional missing = degraded
        assert "context_source" not in resolution.resolved_plugins
        assert len(resolution.degraded_dependencies) == 1

    def test_resolve_required_dependency_missing(self, resolver):
        """Required dependency missing = failed."""
        dep = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="context.semantic",
            allowed_plugins=["nonexistent-plugin"],
            required=True,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep],
        )

        resolution = resolver.resolve_all(skill)

        assert resolution.status == "failed"
        assert len(resolution.errors) > 0

    def test_resolve_whitelist_enforcement(self, resolver):
        """Whitelist filters out non-allowed plugins."""
        dep = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="context.semantic",
            # Only allow external, but semantic is from semantic-context-retriever
            allowed_plugins=["external-context-source"],
            required=True,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep],
        )

        resolution = resolver.resolve_all(skill)

        assert resolution.status == "failed"
        assert len(resolution.errors) > 0

    def test_resolve_multiple_dependencies(self, resolver):
        """Resolve multiple dependencies."""
        dep1 = CapabilityDependency(
            id="context_source",
            type="capability",
            capability_type="context_source",
            capability_id="context.semantic",
            allowed_plugins=["semantic-context-retriever"],
            required=True,
        )
        dep2 = CapabilityDependency(
            id="cache_provider",
            type="capability",
            capability_type="cache_provider",
            capability_id="cache.mem",
            allowed_plugins=["nonexistent"],
            required=False,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep1, dep2],
        )

        resolution = resolver.resolve_all(skill)

        assert resolution.status == "degraded"  # One ok, one degraded
        assert len(resolution.resolved_plugins) == 1
        assert len(resolution.degraded_dependencies) == 1

    def test_resolve_unknown_capability_type(self, resolver):
        """Unknown capability type = error."""
        dep = CapabilityDependency(
            id="test",
            type="capability",
            capability_type="unknown_type",
            capability_id="test",
            allowed_plugins=["plugin"],
            required=True,
        )
        skill = SkillCapabilitiesDependencies(
            skill_id="test",
            skill_version="1.0",
            dependencies=[dep],
        )

        resolution = resolver.resolve_all(skill)

        assert resolution.status == "failed"
        assert len(resolution.errors) > 0
        assert "unknown capability_type" in resolution.errors[0]
