"""Unit tests for Capability Registry (ADR-0610)."""

import pytest

from core.plugins.corvin_plugins.capability_registry import CapabilityRegistry
from core.plugins.corvin_plugins.manifest_capabilities import (
    Capability,
    CapabilityType,
    PluginCapabilitiesManifest,
)


@pytest.fixture
def registry():
    """Create a fresh registry for each test."""
    reg = CapabilityRegistry()
    yield reg
    reg.clear()


class TestCapabilityRegistry:
    """Test CapabilityRegistry functionality."""

    def test_register_manifest(self, registry):
        """Register a plugin manifest."""
        cap = Capability(
            id="context.semantic",
            type=CapabilityType.CONTEXT_SOURCE,
            description="Semantic context",
        )
        manifest = PluginCapabilitiesManifest(
            plugin_id="semantic-context",
            plugin_version="1.3.4",
            capabilities=[cap],
        )
        registry.register_manifest(manifest)

        # Verify registration
        assert len(registry.all_plugins()) == 1
        stored = registry.get_plugin_manifest("semantic-context")
        assert stored.plugin_id == "semantic-context"
        assert len(stored.capabilities) == 1

    def test_capabilities_by_type(self, registry):
        """Query capabilities by type."""
        cap1 = Capability(
            id="context.sem",
            type=CapabilityType.CONTEXT_SOURCE,
            description="1",
        )
        cap2 = Capability(
            id="cache.mem",
            type=CapabilityType.CACHE_PROVIDER,
            description="2",
        )
        manifest = PluginCapabilitiesManifest(
            plugin_id="test",
            plugin_version="1.0",
            capabilities=[cap1, cap2],
        )
        registry.register_manifest(manifest)

        # Query by type
        context_caps = registry.capabilities_by_type(CapabilityType.CONTEXT_SOURCE)
        assert len(context_caps) == 1
        assert context_caps[0] == ("test", cap1)

    def test_find_implementations_version_filter(self, registry):
        """Filter implementations by minimum capabilities version."""
        cap = Capability(id="cap", type=CapabilityType.CONTEXT_SOURCE, description="Test")

        manifest1 = PluginCapabilitiesManifest(
            plugin_id="v1.0",
            plugin_version="1.0",
            capabilities_version="1.0",
            capabilities=[cap],
        )
        manifest2 = PluginCapabilitiesManifest(
            plugin_id="v2.0",
            plugin_version="2.0",
            capabilities_version="2.0",
            capabilities=[cap],
        )
        registry.register_manifest(manifest1)
        registry.register_manifest(manifest2)

        # Require version >= 2.0
        impls = registry.find_implementations(CapabilityType.CONTEXT_SOURCE, min_version="2.0")
        assert len(impls) == 1
        assert impls[0][0] == "v2.0"

    def test_version_comparison(self, registry):
        """Test semver version comparison."""
        assert registry._compare_versions("2.0", "1.0") > 0
        assert registry._compare_versions("1.0", "2.0") < 0
        assert registry._compare_versions("1.0", "1.0") == 0
