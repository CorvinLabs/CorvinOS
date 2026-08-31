"""Unit tests for ADR-0345 k=2: Hierarchical Registry Integration (25 tests)."""

import pytest

from corvin_plugins.hierarchical_registry import (
    HierarchicalRegistry,
    PluginNotFound,
    BootLayerMismatch,
    VersionConflictError,
    VersionConstraint,
)
from corvin_plugins.node import PluginCycleDetected


class TestHierarchicalRegistryBasics:
    """Test basic registration and retrieval."""

    def test_register_root_plugin(self):
        """Register a root plugin (no parent)."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="stt_plugin",
            boot_layer="bundled",
            origin="builtin",
            capabilities=["transcribe_audio"],
        )
        assert node.id == "stt_plugin"
        assert node.parent_id is None
        assert len(registry.nodes) == 1

    def test_register_child_plugin(self):
        """Register a child plugin under parent."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="stt_plugin", boot_layer="bundled", origin="builtin"
        )
        child = registry.register_plugin(
            plugin_id="whisper_plugin",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt_plugin",
        )
        assert child.parent_id == "stt_plugin"
        assert "whisper_plugin" in registry.get_plugin("stt_plugin").sub_plugins

    def test_get_plugin_by_id(self):
        """Retrieve plugin by ID."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        node = registry.get_plugin("p1")
        assert node.id == "p1"

    def test_get_plugin_not_found(self):
        """Raise PluginNotFound for missing plugin."""
        registry = HierarchicalRegistry()
        with pytest.raises(PluginNotFound):
            registry.get_plugin("missing")

    def test_list_plugins_all(self):
        """List all plugins."""
        registry = HierarchicalRegistry()
        registry.register_plugin(plugin_id="p1", boot_layer="bundled", origin="builtin")
        registry.register_plugin(
            plugin_id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"
        )
        plugins = registry.list_plugins()
        assert len(plugins) == 2

    def test_list_plugins_by_boot_layer(self):
        """Filter plugins by boot_layer."""
        registry = HierarchicalRegistry()
        registry.register_plugin(plugin_id="p1", boot_layer="bundled", origin="builtin")
        registry.register_plugin(plugin_id="p2", boot_layer="core", origin="builtin")
        bundled = registry.list_plugins(boot_layer="bundled")
        assert len(bundled) == 1
        assert bundled[0].id == "p1"


class TestBootLayerInheritance:
    """Test boot-layer inheritance enforcement."""

    def test_child_inherits_parent_boot_layer(self):
        """Child must have same boot_layer as parent."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )
        assert registry.get_plugin("p2").boot_layer == "bundled"

    def test_boot_layer_mismatch_rejected(self):
        """Reject child with different boot_layer."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        with pytest.raises(BootLayerMismatch):
            registry.register_plugin(
                plugin_id="p2",
                boot_layer="core",
                origin="builtin",
                parent_id="p1",
            )

    def test_grandchild_inherits_through_chain(self):
        """Grandchild inherits same boot_layer through parent chain."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )
        registry.register_plugin(
            plugin_id="p3",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p2",
        )
        assert registry.get_plugin("p3").boot_layer == "bundled"


class TestCycleDetection:
    """Test cycle detection during registration."""

    def test_cannot_register_missing_parent(self):
        """Cannot register child with non-existent parent."""
        registry = HierarchicalRegistry()
        with pytest.raises(PluginNotFound):
            registry.register_plugin(
                plugin_id="child",
                boot_layer="bundled",
                origin="builtin",
                parent_id="nonexistent_parent",
            )

    def test_cycle_detection_at_validator_level(self):
        """DAGValidator catches cycles (tested in k=1)."""
        # Cycle detection is tested thoroughly in k=1 DAG validator tests
        # This test just ensures HierarchicalRegistry uses validator correctly
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )

        # Validator is initialized after registration
        assert registry.validator is not None
        has_cycle, _ = registry.validator.detect_cycle()
        assert has_cycle is False


class TestVersionConstraints:
    """Test version constraint propagation."""

    def test_version_constraint_satisfied(self):
        """Version satisfies constraint."""
        constraint = VersionConstraint(min_version="1.0.0", max_version="2.0.0")
        assert constraint.satisfies("1.5.0") is True
        assert constraint.satisfies("0.9.0") is False
        assert constraint.satisfies("2.1.0") is False

    def test_propagate_version_constraint(self):
        """Propagate version constraint down tree."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )

        constraint = VersionConstraint(
            min_version="1.0.0", max_version="2.0.0"
        )
        registry.propagate_version_constraint("p1", constraint)

        # p2 should have inherited the constraint
        p2_constraint = registry.version_constraints["p2"]
        assert p2_constraint.min_version == "1.0.0"
        assert p2_constraint.max_version == "2.0.0"

    def test_propagate_to_grandchildren(self):
        """Constraint propagates through multiple levels."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )
        registry.register_plugin(
            plugin_id="p3",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p2",
        )

        constraint = VersionConstraint(
            min_version="2.0.0", max_version="3.0.0"
        )
        registry.propagate_version_constraint("p1", constraint)

        p3_constraint = registry.version_constraints["p3"]
        assert p3_constraint.min_version == "2.0.0"
        assert p3_constraint.max_version == "3.0.0"


class TestHierarchyQueries:
    """Test hierarchy query operations."""

    def test_get_ancestors(self):
        """Query ancestors of plugin."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )
        registry.register_plugin(
            plugin_id="p3",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p2",
        )

        ancestors = registry.get_ancestors("p3")
        assert ancestors == {"p1", "p2"}

    def test_get_descendants(self):
        """Query descendants of plugin."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )
        registry.register_plugin(
            plugin_id="p3",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )
        registry.register_plugin(
            plugin_id="p4",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p2",
        )

        descendants = registry.get_descendants("p1")
        assert descendants == {"p2", "p3", "p4"}


class TestFallbackChains:
    """Test fallback chain setup and validation."""

    def test_set_fallback_chain(self):
        """Set fallback chain for plugin."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="stt_plugin", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="whisper",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt_plugin",
        )
        registry.register_plugin(
            plugin_id="deepspeech",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt_plugin",
        )

        registry.set_fallback_chain("whisper", ["deepspeech"])
        chain = registry.get_fallback_chain("whisper")
        assert chain == ["deepspeech"]

    def test_fallback_chain_not_found(self):
        """Reject fallback ID not in registry."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        with pytest.raises(PluginNotFound):
            registry.set_fallback_chain("p1", ["missing"])


class TestPluginTree:
    """Test plugin tree serialization."""

    def test_get_plugin_tree(self):
        """Serialize plugin tree."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="stt_plugin",
            boot_layer="bundled",
            origin="builtin",
            capabilities=["transcribe"],
        )
        registry.register_plugin(
            plugin_id="whisper",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt_plugin",
        )

        tree = registry.get_plugin_tree("stt_plugin")
        assert tree["id"] == "stt_plugin"
        assert len(tree["children"]) == 1
        assert tree["children"][0]["id"] == "whisper"


class TestUnregister:
    """Test plugin unregistration."""

    def test_unregister_removes_from_parent(self):
        """Unregister removes plugin from parent's sub_plugins."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )

        assert "p2" in registry.get_plugin("p1").sub_plugins
        registry.unregister_plugin("p2")
        assert "p2" not in registry.get_plugin("p1").sub_plugins

    def test_unregister_plugin_not_found(self):
        """Raise error when unregistering missing plugin."""
        registry = HierarchicalRegistry()
        with pytest.raises(PluginNotFound):
            registry.unregister_plugin("missing")


class TestValidateHierarchy:
    """Test full hierarchy validation."""

    def test_validate_valid_hierarchy(self):
        """Validate correct hierarchy."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )

        is_valid, errors = registry.validate_hierarchy()
        assert is_valid is True
        assert len(errors) == 0

    def test_validate_detects_parent_mismatch(self):
        """Validation detects parent_id mismatch."""
        registry = HierarchicalRegistry()
        registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="p2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="p1",
        )

        # Corrupt the hierarchy (manual manipulation for test)
        registry.nodes["p2"].parent_id = "nonexistent"

        is_valid, errors = registry.validate_hierarchy()
        assert is_valid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
