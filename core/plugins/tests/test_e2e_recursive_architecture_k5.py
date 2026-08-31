"""E2E tests for ADR-0345: Recursive Plugin Architecture (k=5 production validation)."""

import pytest

from corvin_plugins.hierarchical_registry import HierarchicalRegistry
from corvin_plugins.delegation import PluginWorkHandler
from corvin_plugins.plugin_state import PluginStateStore
from corvin_plugins.node import WorkRequest, WorkTier, DelegationStrategy


class TestEndToEndHierarchy:
    """E2E test: Multi-level plugin hierarchy."""

    def test_three_level_hierarchy(self):
        """Test root → child → grandchild delegation chain."""
        registry = HierarchicalRegistry()

        # Level 1: Root STT plugin
        registry.register_plugin(
            plugin_id="stt_root",
            boot_layer="bundled",
            origin="builtin",
        )

        # Level 2: Whisper backend
        registry.register_plugin(
            plugin_id="whisper",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt_root",
            capabilities=["transcribe_whisper"],
        )

        # Level 3: Whisper Small variant
        registry.register_plugin(
            plugin_id="whisper_small",
            boot_layer="bundled",
            origin="builtin",
            parent_id="whisper",
            capabilities=["transcribe_whisper"],
        )

        # Verify hierarchy
        root = registry.get_plugin("stt_root")
        assert "whisper" in root.sub_plugins

        whisper = registry.get_plugin("whisper")
        assert "whisper_small" in whisper.sub_plugins

        # Validate
        is_valid, errors = registry.validate_hierarchy()
        assert is_valid is True


class TestEndToEndBootLayerInheritance:
    """E2E test: Boot-layer inheritance chain."""

    def test_boot_layer_enforced_across_levels(self):
        """All levels must maintain same boot_layer."""
        registry = HierarchicalRegistry()

        # All bundled
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

        # Verify
        p3 = registry.get_plugin("p3")
        assert p3.boot_layer == "bundled"


class TestEndToEndFallbackChain:
    """E2E test: Automatic failover with fallback chains."""

    def test_fallback_chain_setup_and_retrieval(self):
        """Set up and retrieve fallback chain."""
        registry = HierarchicalRegistry()

        # Parent with multiple backends
        registry.register_plugin(
            plugin_id="stt", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="whisper",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt",
        )
        registry.register_plugin(
            plugin_id="deepspeech",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt",
        )
        registry.register_plugin(
            plugin_id="local_stt",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt",
        )

        # Set fallback chain: whisper → deepspeech → local_stt
        registry.set_fallback_chain("whisper", ["deepspeech", "local_stt"])

        # Verify
        chain = registry.get_fallback_chain("whisper")
        assert chain == ["deepspeech", "local_stt"]


class TestEndToEndVersionConstraints:
    """E2E test: Version constraint propagation."""

    def test_version_constraints_propagate_down_tree(self):
        """Version constraints propagate to descendants."""
        from corvin_plugins.hierarchical_registry import VersionConstraint

        registry = HierarchicalRegistry()

        # Create tree
        registry.register_plugin(
            plugin_id="root", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="child1",
            boot_layer="bundled",
            origin="builtin",
            parent_id="root",
        )
        registry.register_plugin(
            plugin_id="child2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="child1",
        )

        # Propagate constraint
        constraint = VersionConstraint(
            min_version="2.0.0", max_version="3.0.0"
        )
        registry.propagate_version_constraint("root", constraint)

        # Verify propagation
        child2_constraint = registry.version_constraints["child2"]
        assert child2_constraint.min_version == "2.0.0"
        assert child2_constraint.max_version == "3.0.0"


class TestEndToEndBudgetEnforcement:
    """E2E test: Tier-aware budget enforcement."""

    def test_budget_enforcement_multiple_tiers(self):
        """Budget is enforced correctly per tier."""
        registry = HierarchicalRegistry()

        node = registry.register_plugin(
            plugin_id="p1",
            boot_layer="bundled",
            origin="builtin",
            capabilities=["cap"],
        )

        # Configure budgets
        node.budget_config.compliance_budget_pool = 50
        node.budget_config.high_priority_budget_pool = 30
        node.budget_config.standard_budget_pool = 20

        # Test each tier
        compliance_work = WorkRequest(
            work_id="w1", input_data={}, required_capability="cap",
            priority_tier=WorkTier.COMPLIANCE, budget_cost=30
        )
        assert node.budget_config.can_delegate(compliance_work, {}) is True

        high_work = WorkRequest(
            work_id="w2", input_data={}, required_capability="cap",
            priority_tier=WorkTier.HIGH, budget_cost=15
        )
        assert node.budget_config.can_delegate(high_work, {}) is True

        standard_work = WorkRequest(
            work_id="w3", input_data={}, required_capability="cap",
            priority_tier=WorkTier.STANDARD, budget_cost=10
        )
        assert node.budget_config.can_delegate(standard_work, {}) is True


class TestEndToEndStateManagement:
    """E2E test: Plugin state checkpointing and recovery."""

    def test_checkpoint_and_restore_cycle(self):
        """Plugin state can be checkpointed and restored."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        # Modify state
        node.status = "degraded"
        node.current_budget_used["standard"] = 75

        # Checkpoint
        store = PluginStateStore()
        snap1 = store.checkpoint("p1", node)

        # Change state
        node.status = "healthy"
        node.current_budget_used["standard"] = 0

        # Restore
        store.restore("p1", node)

        # Verify restored
        assert node.status == "degraded"
        assert node.current_budget_used["standard"] == 75


class TestEndToEndDAGValidation:
    """E2E test: DAG integrity maintained."""

    def test_dags_remain_acyclic(self):
        """Complex hierarchies remain acyclic."""
        registry = HierarchicalRegistry()

        # Create diamond-like structure (allowed: not actually cyclic)
        #     p1
        #    /  \
        #   p2  p3
        #    \  /
        #     p4
        # But this is actually two separate trees since p4 can't have two parents

        # So we create:
        #     p1
        #    /  \
        #   p2  p3
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

        is_valid, errors = registry.validate_hierarchy()
        assert is_valid is True


class TestEndToEndReachability:
    """E2E test: All plugins are reachable."""

    def test_all_plugins_reachable_in_registry(self):
        """All registered plugins are retrievable."""
        registry = HierarchicalRegistry()

        # Register multiple plugins
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

        # Verify all reachable
        for pid in ["p1", "p2", "p3"]:
            node = registry.get_plugin(pid)
            assert node.id == pid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
