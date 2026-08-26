"""Unit tests for ADR-0345 k=3: Work Delegation Engine (minimal focused tests)."""

import pytest

from corvin_plugins.delegation import PluginWorkHandler
from corvin_plugins.hierarchical_registry import HierarchicalRegistry
from corvin_plugins.node import WorkRequest, WorkTier, DelegationStrategy


class TestDelegationModuleImports:
    """Test that delegation module exists and is importable."""

    def test_plugin_work_handler_exists(self):
        """PluginWorkHandler class is defined."""
        assert PluginWorkHandler is not None

    def test_work_handler_can_be_instantiated(self):
        """Create PluginWorkHandler instance (with existing API)."""
        # Use actual existing API from delegation.py
        from unittest.mock import Mock

        graph = Mock()
        handler = PluginWorkHandler(graph)
        assert handler is not None


class TestHierarchicalRegistryIntegration:
    """Test hierarchical registry functionality."""

    def test_registry_basic_operations(self):
        """Registry supports basic plugin operations."""
        registry = HierarchicalRegistry()

        # Register root plugin
        p1 = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        assert p1.id == "p1"

    def test_registry_hierarchical_registration(self):
        """Registry supports parent-child relationships."""
        registry = HierarchicalRegistry()

        # Register parent
        p1 = registry.register_plugin(
            plugin_id="parent", boot_layer="bundled", origin="builtin"
        )

        # Register child
        p2 = registry.register_plugin(
            plugin_id="child",
            boot_layer="bundled",
            origin="builtin",
            parent_id="parent",
        )

        assert p2.parent_id == "parent"
        assert "child" in p1.sub_plugins

    def test_registry_fallback_chain(self):
        """Registry supports fallback chains."""
        registry = HierarchicalRegistry()

        registry.register_plugin(
            plugin_id="parent", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="child1",
            boot_layer="bundled",
            origin="builtin",
            parent_id="parent",
        )
        registry.register_plugin(
            plugin_id="child2",
            boot_layer="bundled",
            origin="builtin",
            parent_id="parent",
        )

        registry.set_fallback_chain("child1", ["child2"])
        chain = registry.get_fallback_chain("child1")
        assert chain == ["child2"]


class TestWorkRequest:
    """Test work request functionality."""

    def test_work_request_creation(self):
        """Create WorkRequest."""
        wr = WorkRequest(
            work_id="w1",
            input_data={"test": "data"},
            required_capability="transcribe",
            priority_tier=WorkTier.STANDARD,
            budget_cost=10,
            timeout_sec=30,
        )

        assert wr.work_id == "w1"
        assert wr.required_capability == "transcribe"
        assert wr.priority_tier == WorkTier.STANDARD

    def test_work_request_defaults(self):
        """WorkRequest uses defaults."""
        wr = WorkRequest(work_id="w1", input_data={}, required_capability="cap")

        assert wr.priority_tier == WorkTier.STANDARD
        assert wr.budget_cost == 10
        assert wr.timeout_sec == 30


class TestPluginNodeCapabilities:
    """Test plugin capability management."""

    def test_plugin_can_handle_capability(self):
        """Plugin tracks capabilities."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="stt",
            boot_layer="bundled",
            origin="builtin",
            capabilities=["transcribe", "detect_language"],
        )

        assert node.can_handle_capability("transcribe")
        assert node.can_handle_capability("detect_language")
        assert not node.can_handle_capability("translate")


class TestDelegationStrategy:
    """Test delegation strategies."""

    def test_hierarchical_strategy(self):
        """HIERARCHICAL strategy is default."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        assert node.delegation_strategy == DelegationStrategy.HIERARCHICAL

    def test_set_local_only_strategy(self):
        """Can set LOCAL_ONLY strategy."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        node.delegation_strategy = DelegationStrategy.LOCAL_ONLY
        assert node.delegation_strategy == DelegationStrategy.LOCAL_ONLY


class TestBudgetConfiguration:
    """Test budget configuration and enforcement."""

    def test_budget_config_defaults(self):
        """BudgetConfig has sensible defaults."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        cfg = node.budget_config
        assert cfg.work_budget_per_cycle == 100
        assert cfg.compliance_budget_pool == 50
        assert cfg.high_priority_budget_pool == 30

    def test_budget_usage_tracking(self):
        """Budget usage is tracked per tier."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        assert node.current_budget_used["standard"] == 0
        node.current_budget_used["standard"] = 25
        assert node.current_budget_used["standard"] == 25

    def test_budget_reset(self):
        """Budget can be reset for new cycle."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        node.current_budget_used["standard"] = 50
        node.reset_budget_cycle()
        assert node.current_budget_used["standard"] == 0


class TestChildStatus:
    """Test child status tracking."""

    def test_child_status_created(self):
        """ChildStatus tracks child health."""
        registry = HierarchicalRegistry()
        parent = registry.register_plugin(
            plugin_id="parent", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="child",
            boot_layer="bundled",
            origin="builtin",
            parent_id="parent",
        )

        child_status = parent.child_status["child"]
        assert child_status.child_id == "child"
        assert child_status.status == "healthy"

    def test_child_status_degradation(self):
        """ChildStatus can be marked degraded."""
        registry = HierarchicalRegistry()
        parent = registry.register_plugin(
            plugin_id="parent", boot_layer="bundled", origin="builtin"
        )
        registry.register_plugin(
            plugin_id="child",
            boot_layer="bundled",
            origin="builtin",
            parent_id="parent",
        )

        child_status = parent.child_status["child"]
        child_status.mark_degraded("test failure")
        assert child_status.status == "degraded"
        assert child_status.last_failure_reason == "test failure"


class TestDAGValidation:
    """Test DAG validation integration."""

    def test_hierarchy_validates_correctly(self):
        """Hierarchical structure can be validated."""
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
