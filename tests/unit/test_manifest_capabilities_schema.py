"""Unit tests for Capabilities Manifest Schema (ADR-0610)."""

import pytest

from core.plugins.corvin_plugins.manifest_capabilities import (
    Capability,
    CapabilityType,
    FailureMode,
    PluginCapabilitiesManifest,
    manifest_from_plugin_config,
)


class TestCapabilitySchema:
    """Test individual Capability validation."""

    def test_valid_capability(self):
        """Create a valid capability."""
        cap = Capability(
            id="context.semantic_retrieval",
            type=CapabilityType.CONTEXT_SOURCE,
            description="Retrieve semantic context",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
            returns={"type": "array"},
            slo_latency_ms=500,
            audit_event="plugin.context_retrieved",
        )
        assert cap.id == "context.semantic_retrieval"
        assert cap.type == CapabilityType.CONTEXT_SOURCE
        assert cap.slo_latency_ms == 500

    def test_capability_invalid_id(self):
        """Reject invalid capability id."""
        with pytest.raises(ValueError, match="Invalid capability id"):
            Capability(
                id="",
                type=CapabilityType.CONTEXT_SOURCE,
                description="Test",
            )

    def test_capability_invalid_slo_latency(self):
        """Reject negative SLO latency."""
        with pytest.raises(ValueError, match="SLO latency must be"):
            Capability(
                id="test.cap",
                type=CapabilityType.CONTEXT_SOURCE,
                description="Test",
                slo_latency_ms=-1,
            )

    def test_capability_invalid_error_rate(self):
        """Reject invalid SLO error rate."""
        with pytest.raises(ValueError, match="SLO error rate must be"):
            Capability(
                id="test.cap",
                type=CapabilityType.CONTEXT_SOURCE,
                description="Test",
                slo_error_rate=1.5,
            )

    def test_capability_json_schema_validation(self):
        """Validate JSON Schema in parameters/returns."""
        cap = Capability(
            id="test.cap",
            type=CapabilityType.CONTEXT_SOURCE,
            description="Test",
            parameters={"type": "object", "properties": {"q": {"type": "string"}}},
            returns={"type": "array"},
        )
        errors = cap.validate_json_schema()
        assert len(errors) == 0

    def test_capability_invalid_json_schema_parameters(self):
        """Reject invalid JSON Schema in parameters."""
        cap = Capability(
            id="test.cap",
            type=CapabilityType.CONTEXT_SOURCE,
            description="Test",
            parameters="not a dict",  # Should be dict
        )
        errors = cap.validate_json_schema()
        assert len(errors) > 0
        assert "parameters must be dict" in errors[0]

    def test_all_capability_types_exist(self):
        """All capability types are defined."""
        assert CapabilityType.CONTEXT_SOURCE.value == "context_source"
        assert CapabilityType.CACHE_PROVIDER.value == "cache_provider"
        assert CapabilityType.COMPUTE_ENGINE.value == "compute_engine"


class TestManifestSchema:
    """Test PluginCapabilitiesManifest validation."""

    def test_valid_manifest(self):
        """Create a valid manifest."""
        cap = Capability(
            id="context.semantic",
            type=CapabilityType.CONTEXT_SOURCE,
            description="Semantic context",
            audit_event="plugin.context",
        )
        manifest = PluginCapabilitiesManifest(
            plugin_id="semantic-context",
            plugin_version="1.3.4",
            capabilities_version="1.0",
            capabilities=[cap],
        )
        errors = manifest.validate()
        assert len(errors) == 0

    def test_manifest_invalid_version(self):
        """Reject invalid capabilities_version."""
        manifest = PluginCapabilitiesManifest(
            plugin_id="test",
            plugin_version="1.0",
            capabilities_version="invalid",
        )
        errors = manifest.validate()
        assert len(errors) > 0
        assert "Invalid capabilities_version" in errors[0]

    def test_manifest_duplicate_capability_ids(self):
        """Reject duplicate capability ids."""
        cap1 = Capability(id="same", type=CapabilityType.CONTEXT_SOURCE, description="1")
        cap2 = Capability(id="same", type=CapabilityType.CONTEXT_SOURCE, description="2")
        manifest = PluginCapabilitiesManifest(
            plugin_id="test",
            plugin_version="1.0",
            capabilities=[cap1, cap2],
        )
        errors = manifest.validate()
        assert len(errors) > 0
        assert "Duplicate capability id" in errors[0]

    def test_manifest_fallback_cycle_detection(self):
        """Detect cycles in fallback_capability_id."""
        cap1 = Capability(
            id="cap1",
            type=CapabilityType.CONTEXT_SOURCE,
            description="1",
            fallback_capability_id="cap2",
        )
        cap2 = Capability(
            id="cap2",
            type=CapabilityType.CONTEXT_SOURCE,
            description="2",
            fallback_capability_id="cap1",
        )
        manifest = PluginCapabilitiesManifest(
            plugin_id="test",
            plugin_version="1.0",
            capabilities=[cap1, cap2],
        )
        errors = manifest.validate()
        assert len(errors) > 0
        assert "Fallback cycle" in errors[0]

    def test_manifest_capabilities_by_type(self):
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
        context_caps = manifest.capabilities_by_type(CapabilityType.CONTEXT_SOURCE)
        assert len(context_caps) == 1
        assert context_caps[0].id == "context.sem"

    def test_manifest_get_capability(self):
        """Get capability by id."""
        cap = Capability(id="test.cap", type=CapabilityType.CONTEXT_SOURCE, description="Test")
        manifest = PluginCapabilitiesManifest(
            plugin_id="test",
            plugin_version="1.0",
            capabilities=[cap],
        )
        found = manifest.get_capability("test.cap")
        assert found == cap

    def test_manifest_get_capability_not_found(self):
        """Return None for missing capability."""
        manifest = PluginCapabilitiesManifest(
            plugin_id="test",
            plugin_version="1.0",
        )
        found = manifest.get_capability("nonexistent")
        assert found is None


class TestManifestFactory:
    """Test manifest_from_plugin_config factory."""

    def test_manifest_from_config(self):
        """Create manifest from plugin config dict."""
        config = {
            "capabilities_version": "1.0",
            "capabilities": [
                {
                    "id": "context.semantic",
                    "type": "context_source",
                    "description": "Semantic retrieval",
                    "slo_latency_ms": 500,
                    "audit_event": "plugin.context",
                }
            ],
        }
        manifest = manifest_from_plugin_config("test-plugin", "1.2.3", config)
        assert manifest.plugin_id == "test-plugin"
        assert manifest.plugin_version == "1.2.3"
        assert manifest.capabilities_version == "1.0"
        assert len(manifest.capabilities) == 1
        assert manifest.capabilities[0].id == "context.semantic"

    def test_manifest_from_empty_config(self):
        """Handle config with no capabilities."""
        manifest = manifest_from_plugin_config("test", "1.0", {})
        assert manifest.plugin_id == "test"
        assert len(manifest.capabilities) == 0


class TestCapabilityRegistry:
    """Test CapabilityRegistry (import in Phase 1.2 tests)."""
    # Tests for registry in separate test_capability_registry.py
