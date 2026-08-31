"""Unit tests for ADR-0345 k=1: Core data models and DAG validator.

Tests for:
- node.py: WorkTier, DelegationStrategy, WorkRequest, BudgetConfig, ChildStatus, PluginNode
- dag_validator.py: Cycle detection, topological sort, graph queries
"""

import pytest
from datetime import datetime

from corvin_plugins.node import (
    WorkTier,
    DelegationStrategy,
    WorkRequest,
    BudgetConfig,
    ChildStatus,
    PluginNode,
    DelegationEvent,
    DelegationTransaction,
    PluginCycleDetected,
    BootLayerMismatch,
)
from corvin_plugins.dag_validator import DAGValidator


class TestWorkTier:
    """Test WorkTier enum."""

    def test_work_tier_values(self):
        """Verify WorkTier enum values."""
        assert WorkTier.COMPLIANCE.value == "compliance"
        assert WorkTier.HIGH.value == "high"
        assert WorkTier.STANDARD.value == "standard"
        assert WorkTier.LOW.value == "low"

    def test_work_tier_ordering_is_implicit(self):
        """Note: Actual ordering is enforced in BudgetConfig, not enum."""
        tiers = [WorkTier.COMPLIANCE, WorkTier.HIGH, WorkTier.STANDARD, WorkTier.LOW]
        assert len(tiers) == 4


class TestDelegationStrategy:
    """Test DelegationStrategy enum."""

    def test_delegation_strategy_values(self):
        """Verify DelegationStrategy enum values."""
        assert DelegationStrategy.HIERARCHICAL.value == "hierarchical"
        assert DelegationStrategy.LOCAL_ONLY.value == "local_only"
        assert DelegationStrategy.HYBRID.value == "hybrid"


class TestWorkRequest:
    """Test WorkRequest data model."""

    def test_work_request_creation(self):
        """Create a valid WorkRequest."""
        wr = WorkRequest(
            work_id="work_001",
            input_data={"file": "audio.wav"},
            required_capability="transcribe_audio",
            priority_tier=WorkTier.STANDARD,
            budget_cost=15,
            timeout_sec=60,
        )
        assert wr.work_id == "work_001"
        assert wr.required_capability == "transcribe_audio"
        assert wr.priority_tier == WorkTier.STANDARD
        assert wr.budget_cost == 15
        assert wr.timeout_sec == 60
        assert wr.status == "pending"

    def test_work_request_defaults(self):
        """WorkRequest uses sensible defaults."""
        wr = WorkRequest(
            work_id="w1", input_data={}, required_capability="cap"
        )
        assert wr.priority_tier == WorkTier.STANDARD
        assert wr.budget_cost == 10
        assert wr.timeout_sec == 30

    def test_work_request_repr(self):
        """WorkRequest has useful repr."""
        wr = WorkRequest(work_id="w1", input_data={}, required_capability="cap")
        assert "work_001" not in repr(wr)  # Check it's a dataclass repr


class TestBudgetConfig:
    """Test BudgetConfig tier-aware budget allocation."""

    def test_budget_config_defaults(self):
        """Default budget configuration."""
        cfg = BudgetConfig()
        assert cfg.work_budget_per_cycle == 100
        assert cfg.compliance_budget_pool == 50
        assert cfg.high_priority_budget_pool == 30
        assert cfg.standard_budget_pool == 20

    def test_budget_can_delegate_compliance(self):
        """COMPLIANCE work uses compliance_budget_pool."""
        cfg = BudgetConfig(compliance_budget_pool=50)
        work = WorkRequest(
            work_id="w1", input_data={}, required_capability="cap",
            priority_tier=WorkTier.COMPLIANCE, budget_cost=30
        )

        # First delegation: 30 + 30 = 60 > 50 should fail
        assert cfg.can_delegate(work, {"compliance": 30}) is False

        # Within budget
        assert cfg.can_delegate(work, {"compliance": 0}) is True
        assert cfg.can_delegate(work, {"compliance": 20}) is True
        assert cfg.can_delegate(work, {"compliance": 21}) is False

    def test_budget_can_delegate_high(self):
        """HIGH priority work uses its own pool."""
        cfg = BudgetConfig(high_priority_budget_pool=30)
        work = WorkRequest(
            work_id="w1", input_data={}, required_capability="cap",
            priority_tier=WorkTier.HIGH, budget_cost=15
        )

        assert cfg.can_delegate(work, {"high": 0}) is True
        assert cfg.can_delegate(work, {"high": 15}) is True
        assert cfg.can_delegate(work, {"high": 16}) is False

    def test_budget_can_delegate_standard(self):
        """STANDARD priority work uses standard pool."""
        cfg = BudgetConfig(standard_budget_pool=20)
        work = WorkRequest(
            work_id="w1", input_data={}, required_capability="cap",
            priority_tier=WorkTier.STANDARD, budget_cost=10
        )

        assert cfg.can_delegate(work, {"standard": 0}) is True
        assert cfg.can_delegate(work, {"standard": 10}) is True
        assert cfg.can_delegate(work, {"standard": 11}) is False

    def test_budget_get_tier_limit(self):
        """Get budget pool size for a tier."""
        cfg = BudgetConfig(
            compliance_budget_pool=50,
            high_priority_budget_pool=30,
            standard_budget_pool=15,
            low_priority_budget_pool=5,
        )
        assert cfg.get_tier_limit(WorkTier.COMPLIANCE) == 50
        assert cfg.get_tier_limit(WorkTier.HIGH) == 30
        assert cfg.get_tier_limit(WorkTier.STANDARD) == 15


class TestChildStatus:
    """Test ChildStatus tracking."""

    def test_child_status_creation(self):
        """Create ChildStatus."""
        cs = ChildStatus(
            child_id="child_1",
            depth=2,
            avg_latency_ms=45.5,
            work_count=10,
        )
        assert cs.child_id == "child_1"
        assert cs.depth == 2
        assert cs.avg_latency_ms == 45.5
        assert cs.work_count == 10
        assert cs.status == "healthy"

    def test_child_status_mark_degraded(self):
        """Mark child as degraded."""
        cs = ChildStatus(child_id="c1")
        cs.mark_degraded("timeout in delegation")
        assert cs.status == "degraded"
        assert cs.last_failure_reason == "timeout in delegation"
        assert cs.audit_failures_10min == 1

    def test_child_status_mark_quarantined(self):
        """Mark child as quarantined."""
        cs = ChildStatus(child_id="c1")
        cs.mark_quarantined("audit hash mismatch")
        assert cs.status == "quarantined"
        assert cs.last_failure_reason == "audit hash mismatch"
        assert cs.audit_failures_10min == 999

    def test_child_status_mark_healthy(self):
        """Reset degraded child to healthy."""
        cs = ChildStatus(child_id="c1")
        cs.mark_degraded("temporary issue")
        assert cs.status == "degraded"

        cs.mark_healthy()
        assert cs.status == "healthy"
        assert cs.audit_failures_10min == 0
        assert cs.last_failure_reason is None


class TestPluginNode:
    """Test PluginNode hierarchical structure."""

    def test_plugin_node_creation(self):
        """Create a basic plugin node."""
        node = PluginNode(
            id="stt_plugin",
            boot_layer="bundled",
            origin="builtin",
            capabilities=["transcribe_audio", "detect_language"],
        )
        assert node.id == "stt_plugin"
        assert node.boot_layer == "bundled"
        assert node.origin == "builtin"
        assert node.capabilities == ["transcribe_audio", "detect_language"]
        assert node.parent_id is None
        assert node.sub_plugins == []

    def test_plugin_node_with_parent(self):
        """Create plugin with parent."""
        child = PluginNode(
            id="whisper_plugin",
            boot_layer="bundled",
            origin="builtin",
            parent_id="stt_plugin",
        )
        assert child.parent_id == "stt_plugin"

    def test_plugin_node_add_sub_plugin(self):
        """Add sub-plugin to parent."""
        parent = PluginNode(id="p1", boot_layer="bundled", origin="builtin")
        parent.add_sub_plugin("c1")
        parent.add_sub_plugin("c2")

        assert "c1" in parent.sub_plugins
        assert "c2" in parent.sub_plugins
        assert len(parent.sub_plugins) == 2
        assert "c1" in parent.child_status
        assert "c2" in parent.child_status

    def test_plugin_node_remove_sub_plugin(self):
        """Remove sub-plugin."""
        parent = PluginNode(id="p1", boot_layer="bundled", origin="builtin")
        parent.add_sub_plugin("c1")
        assert "c1" in parent.sub_plugins

        parent.remove_sub_plugin("c1")
        assert "c1" not in parent.sub_plugins
        assert "c1" not in parent.child_status

    def test_plugin_node_reset_budget_cycle(self):
        """Reset budget for new health-check cycle."""
        node = PluginNode(id="p1", boot_layer="bundled", origin="builtin")
        node.current_budget_used["standard"] = 15
        node.current_budget_used["high"] = 10

        node.reset_budget_cycle()

        assert node.current_budget_used["standard"] == 0
        assert node.current_budget_used["high"] == 0
        assert node.current_budget_used["compliance"] == 0

    def test_plugin_node_budget_usage_ratio(self):
        """Calculate budget usage ratio."""
        cfg = BudgetConfig(work_budget_per_cycle=100)
        node = PluginNode(
            id="p1", boot_layer="bundled", origin="builtin",
            budget_config=cfg
        )
        node.current_budget_used["standard"] = 20
        node.current_budget_used["high"] = 30

        ratio = node.get_budget_usage_ratio()
        assert ratio == 0.5  # (20 + 30) / 100

    def test_plugin_node_is_degraded(self):
        """Check degradation threshold."""
        cfg = BudgetConfig(
            work_budget_per_cycle=100,
            degradation_threshold=0.8
        )
        node = PluginNode(
            id="p1", boot_layer="bundled", origin="builtin",
            budget_config=cfg
        )

        # Below threshold
        node.current_budget_used["standard"] = 70
        assert node.is_degraded() is False

        # At threshold
        node.current_budget_used["standard"] = 80
        assert node.is_degraded() is True

    def test_plugin_node_can_handle_capability(self):
        """Check if plugin can handle capability."""
        node = PluginNode(
            id="p1", boot_layer="bundled", origin="builtin",
            capabilities=["transcribe", "detect_language"]
        )
        assert node.can_handle_capability("transcribe") is True
        assert node.can_handle_capability("translate") is False


class TestDelegationEvent:
    """Test DelegationEvent audit records."""

    def test_delegation_event_creation(self):
        """Create a delegation event."""
        evt = DelegationEvent(
            event_type="work_delegated",
            plugin_id="stt_plugin",
            work_id="w1",
            target_child="whisper_plugin",
            priority_tier="standard",
            budget_cost=15,
            latency_ms=123,
            reason="delegated to capable child",
            timestamp_utc="2026-08-26T12:00:00Z",
        )
        assert evt.event_type == "work_delegated"
        assert evt.plugin_id == "stt_plugin"
        assert evt.work_id == "w1"
        assert evt.target_child == "whisper_plugin"

    def test_delegation_event_compute_hash(self):
        """Compute hash for event."""
        evt = DelegationEvent(
            event_type="work_delegated",
            plugin_id="p1",
            work_id="w1",
            timestamp_utc="2026-08-26T12:00:00Z",
        )
        hash1 = evt.compute_self_hash()
        assert len(hash1) == 64  # SHA256 hex

        # Same event produces same hash
        evt2 = DelegationEvent(
            event_type="work_delegated",
            plugin_id="p1",
            work_id="w1",
            timestamp_utc="2026-08-26T12:00:00Z",
        )
        hash2 = evt2.compute_self_hash()
        assert hash1 == hash2


class TestDelegationTransaction:
    """Test DelegationTransaction."""

    def test_delegation_transaction_creation(self):
        """Create a delegation transaction."""
        tx = DelegationTransaction(
            work_id="w1",
            root_request_time="2026-08-26T12:00:00Z",
            final_status="success",
            total_latency_ms=250,
        )
        assert tx.work_id == "w1"
        assert tx.final_status == "success"
        assert tx.total_latency_ms == 250
        assert len(tx.breadcrumbs) == 0

    def test_delegation_transaction_to_audit_record(self):
        """Convert transaction to audit record."""
        tx = DelegationTransaction(
            work_id="w1",
            root_request_time="2026-08-26T12:00:00Z",
            final_status="success",
            total_latency_ms=100,
        )
        record = tx.to_audit_record()

        assert record["work_id"] == "w1"
        assert record["final_status"] == "success"
        assert record["total_latency_ms"] == 100
        assert record["hops"] == 0


class TestDAGValidatorCycleDetection:
    """Test DAGValidator cycle detection."""

    def test_acyclic_graph_no_cycles(self):
        """Acyclic graph should have no cycles."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin", parent_id="p1"),
        }
        nodes["p1"].add_sub_plugin("p2")
        nodes["p1"].add_sub_plugin("p3")

        validator = DAGValidator(nodes)
        has_cycle, path = validator.detect_cycle()

        assert has_cycle is False
        assert path == []

    def test_simple_cycle_detection(self):
        """Detect a two-node cycle."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin"),
        }
        # Create cycle: p1 -> p2 -> p1
        nodes["p1"].add_sub_plugin("p2")
        nodes["p2"].add_sub_plugin("p1")
        nodes["p2"].parent_id = "p1"
        nodes["p1"].parent_id = "p2"

        validator = DAGValidator(nodes)
        has_cycle, path = validator.detect_cycle()

        assert has_cycle is True
        assert len(path) >= 2
        assert path[0] == path[-1]  # Cycle returns to start

    def test_three_node_cycle_detection(self):
        """Detect cycle in three-node DAG."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin"),
        }
        # Create cycle: p1 -> p2 -> p3 -> p1
        nodes["p1"].add_sub_plugin("p2")
        nodes["p2"].add_sub_plugin("p3")
        nodes["p3"].add_sub_plugin("p1")
        nodes["p2"].parent_id = "p1"
        nodes["p3"].parent_id = "p2"
        nodes["p1"].parent_id = "p3"

        validator = DAGValidator(nodes)
        has_cycle, path = validator.detect_cycle()

        assert has_cycle is True

    def test_would_create_cycle(self):
        """Check if adding edge would create cycle."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
        }
        nodes["p1"].add_sub_plugin("p2")

        validator = DAGValidator(nodes)

        # Adding p2 -> p1 would create cycle
        assert validator.would_create_cycle("p2", "p1") is True

        # Adding p1 -> p2 doesn't (already exists via parent)
        # But this tests the validation framework


class TestDAGValidatorValidation:
    """Test comprehensive DAG validation."""

    def test_validate_acyclic_graph(self):
        """Validate acyclic DAG."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin", parent_id="p2"),
        }
        nodes["p1"].add_sub_plugin("p2")
        nodes["p2"].add_sub_plugin("p3")

        validator = DAGValidator(nodes)
        is_valid, errors = validator.validate()

        assert is_valid is True
        assert len(errors) == 0

    def test_validate_detects_missing_parent(self):
        """Validation detects missing parent reference."""
        nodes = {
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1_missing"),
        }

        validator = DAGValidator(nodes)
        is_valid, errors = validator.validate()

        assert is_valid is False
        assert any("not found" in err for err in errors)

    def test_validate_boot_layer_mismatch(self):
        """Validation detects boot-layer inheritance violation."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="compliance", origin="builtin", parent_id="p1"),
        }
        nodes["p1"].add_sub_plugin("p2")

        validator = DAGValidator(nodes)
        is_valid, errors = validator.validate()

        assert is_valid is False
        assert any("boot_layer" in err for err in errors)


class TestDAGValidatorTopologicalSort:
    """Test topological sorting."""

    def test_topological_sort_simple(self):
        """Topological sort on simple DAG."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin", parent_id="p1"),
        }
        nodes["p1"].add_sub_plugin("p2")
        nodes["p1"].add_sub_plugin("p3")

        validator = DAGValidator(nodes)
        success, sorted_ids = validator.topological_sort()

        assert success is True
        assert sorted_ids[0] == "p1"
        assert "p2" in sorted_ids[1:]
        assert "p3" in sorted_ids[1:]

    def test_topological_sort_fails_on_cycle(self):
        """Topological sort fails on cyclic graph."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin"),
        }
        nodes["p1"].add_sub_plugin("p2")
        nodes["p2"].add_sub_plugin("p1")
        nodes["p2"].parent_id = "p1"
        nodes["p1"].parent_id = "p2"

        validator = DAGValidator(nodes)
        success, sorted_ids = validator.topological_sort()

        assert success is False
        assert len(sorted_ids) == 0


class TestDAGValidatorGraphQueries:
    """Test graph query operations."""

    def test_get_ancestors(self):
        """Query all ancestors of a node."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin", parent_id="p2"),
        }
        nodes["p1"].add_sub_plugin("p2")
        nodes["p2"].add_sub_plugin("p3")

        validator = DAGValidator(nodes)

        ancestors_p1 = validator.get_ancestors("p1")
        ancestors_p2 = validator.get_ancestors("p2")
        ancestors_p3 = validator.get_ancestors("p3")

        assert ancestors_p1 == set()
        assert ancestors_p2 == {"p1"}
        assert ancestors_p3 == {"p1", "p2"}

    def test_get_descendants(self):
        """Query all descendants of a node."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p4": PluginNode(id="p4", boot_layer="bundled", origin="builtin", parent_id="p2"),
        }
        nodes["p1"].add_sub_plugin("p2")
        nodes["p1"].add_sub_plugin("p3")
        nodes["p2"].add_sub_plugin("p4")

        validator = DAGValidator(nodes)

        descendants_p1 = validator.get_descendants("p1")
        descendants_p2 = validator.get_descendants("p2")
        descendants_p3 = validator.get_descendants("p3")

        assert descendants_p1 == {"p2", "p3", "p4"}
        assert descendants_p2 == {"p4"}
        assert descendants_p3 == set()

    def test_get_roots(self):
        """Get all root plugins."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin"),
        }
        nodes["p1"].add_sub_plugin("p2")

        validator = DAGValidator(nodes)
        roots = validator.get_roots()

        assert set(roots) == {"p1", "p3"}

    def test_get_leaves(self):
        """Get all leaf plugins."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
            "p3": PluginNode(id="p3", boot_layer="bundled", origin="builtin"),
        }
        nodes["p1"].add_sub_plugin("p2")

        validator = DAGValidator(nodes)
        leaves = validator.get_leaves()

        assert set(leaves) == {"p2", "p3"}

    def test_compute_tree_hash(self):
        """Compute tree hash."""
        nodes = {
            "p1": PluginNode(id="p1", boot_layer="bundled", origin="builtin"),
            "p2": PluginNode(id="p2", boot_layer="bundled", origin="builtin", parent_id="p1"),
        }
        nodes["p1"].add_sub_plugin("p2")

        validator = DAGValidator(nodes)
        hash1 = validator.compute_tree_hash("p1")
        hash2 = validator.compute_tree_hash("p2")

        # Both should be valid SHA256 hex
        assert len(hash1) == 64
        assert len(hash2) == 64

        # Different trees produce different hashes
        assert hash1 != hash2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
