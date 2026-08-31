"""E2E tests for Recursive Plugin Architecture (ADR-0345).

Tests cover:
1. DAG structure and cycle detection
2. Hierarchy validation (boot layer inheritance)
3. Work delegation routing
4. Budget management
5. Audit isolation (Tier 1/2)
6. Tree hash integrity
"""

import pytest
from datetime import datetime, timezone

from core.plugins.corvin_plugins.node import (
    PluginNode,
    WorkRequest,
    WorkTier,
    DelegationStrategy,
    BudgetConfig,
    ChildStatus,
    PluginCycleDetected,
    BootLayerMismatch,
    WorkUnhandleable,
    BudgetExhausted,
)
from core.plugins.corvin_plugins.graph import PluginGraph
from core.plugins.corvin_plugins.delegation import PluginWorkHandler


class MockAuditLog:
    """Mock audit log for testing."""

    def __init__(self):
        self.records = []

    def record(self, event):
        """Record an audit event."""
        self.records.append(event)

    def count_recent(self, event_type, plugin_id, window_sec):
        """Count recent events matching criteria."""
        return sum(
            1 for r in self.records
            if r.get("event") == event_type and r.get("plugin_id") == plugin_id
        )

    def get_all(self):
        """Get all recorded events."""
        return self.records


class MockQuarantineRegistry:
    """Mock quarantine registry for testing."""

    def __init__(self):
        self.quarantined = set()

    def quarantine(self, plugin_id, reason=None):
        """Quarantine a plugin."""
        self.quarantined.add(plugin_id)

    def is_quarantined(self, plugin_id):
        """Check if a plugin is quarantined."""
        return plugin_id in self.quarantined

    def release(self, plugin_id):
        """Release a quarantined plugin."""
        self.quarantined.discard(plugin_id)


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def audit_log():
    """Provide a mock audit log."""
    return MockAuditLog()


@pytest.fixture
def quarantine_registry():
    """Provide a mock quarantine registry."""
    return MockQuarantineRegistry()


@pytest.fixture
def graph(audit_log, quarantine_registry):
    """Provide a plugin graph."""
    return PluginGraph(audit_log=audit_log, quarantine_registry=quarantine_registry)


@pytest.fixture
def handler(graph, audit_log, quarantine_registry):
    """Provide a work handler."""
    return PluginWorkHandler(
        graph=graph,
        audit_log=audit_log,
        quarantine_registry=quarantine_registry,
    )


# ── Test Hierarchy & Boot Layer ────────────────────────────────────────────


def test_register_root_plugin(graph):
    """Test registering a root plugin (no parent)."""
    node = PluginNode(
        id="stt-plugin",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["transcribe_audio"],
    )
    graph.register_node(node)

    assert "stt-plugin" in graph.nodes
    assert graph.nodes["stt-plugin"].parent_id is None


def test_register_child_plugin_valid(graph):
    """Test registering a child plugin with matching boot layer."""
    parent = PluginNode(
        id="stt-plugin",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["transcribe_audio"],
    )
    graph.register_node(parent)

    child = PluginNode(
        id="whisper-backend",
        boot_layer="bundled",  # Must match parent
        origin="builtin",
        parent_id="stt-plugin",
        capabilities=["transcribe_audio"],
    )
    graph.register_node(child)
    graph.add_child("stt-plugin", "whisper-backend")

    assert graph.nodes["stt-plugin"].sub_plugins == ["whisper-backend"]
    assert graph.nodes["whisper-backend"].parent_id == "stt-plugin"


def test_register_child_boot_layer_mismatch(graph):
    """Test that child boot layer must match parent's."""
    parent = PluginNode(
        id="stt-plugin",
        boot_layer="bundled",
        origin="builtin",
    )
    graph.register_node(parent)

    child = PluginNode(
        id="whisper-backend",
        boot_layer="installed",  # Mismatch!
        origin="builtin",
        parent_id="stt-plugin",
    )

    with pytest.raises(BootLayerMismatch):
        graph.register_node(child)


def test_register_child_parent_not_found(graph):
    """Test that parent must exist before registering child."""
    child = PluginNode(
        id="whisper-backend",
        boot_layer="bundled",
        origin="builtin",
        parent_id="nonexistent-parent",
    )

    with pytest.raises(ValueError, match="Parent.*not registered"):
        graph.register_node(child)


# ── Test DAG & Cycle Detection ────────────────────────────────────────────


def test_cycle_detection_simple(graph):
    """Test that simple cycles are detected."""
    a = PluginNode(id="a", boot_layer="bundled", origin="builtin")
    b = PluginNode(id="b", boot_layer="bundled", origin="builtin", parent_id="a")
    c = PluginNode(id="c", boot_layer="bundled", origin="builtin", parent_id="b")

    graph.register_node(a)
    graph.register_node(b)
    graph.register_node(c)
    graph.add_child("a", "b")
    graph.add_child("b", "c")

    # Try to create a cycle: c → a
    with pytest.raises(PluginCycleDetected):
        graph.add_child("c", "a")


def test_cycle_detection_self_loop(graph):
    """Test that self-loops are detected."""
    node = PluginNode(id="a", boot_layer="bundled", origin="builtin")
    graph.register_node(node)

    # Try to create self-loop
    with pytest.raises(PluginCycleDetected):
        graph.add_child("a", "a")


def test_verify_dag_integrity(graph):
    """Test DAG integrity verification."""
    a = PluginNode(id="a", boot_layer="bundled", origin="builtin")
    b = PluginNode(id="b", boot_layer="bundled", origin="builtin", parent_id="a")
    c = PluginNode(id="c", boot_layer="bundled", origin="builtin", parent_id="b")

    graph.register_node(a)
    graph.register_node(b)
    graph.register_node(c)
    graph.add_child("a", "b")
    graph.add_child("b", "c")

    assert graph.verify_dag_integrity() is True


# ── Test Tree Hash ────────────────────────────────────────────────────────


def test_tree_hash_single_node(graph):
    """Test tree hash computation for single node."""
    node = PluginNode(id="root", boot_layer="bundled", origin="builtin")
    graph.register_node(node)

    tree_hash = graph._compute_tree_hash("root")
    assert tree_hash  # Should have a hash
    assert len(tree_hash) == 64  # SHA256 hex


def test_tree_hash_parent_includes_children(graph):
    """Test that parent's tree hash includes children."""
    parent = PluginNode(id="parent", boot_layer="bundled", origin="builtin")
    child1 = PluginNode(
        id="child1", boot_layer="bundled", origin="builtin", parent_id="parent"
    )
    child2 = PluginNode(
        id="child2", boot_layer="bundled", origin="builtin", parent_id="parent"
    )

    graph.register_node(parent)
    graph.register_node(child1)
    graph.register_node(child2)
    graph.add_child("parent", "child1")
    graph.add_child("parent", "child2")

    parent_hash_1 = graph._compute_tree_hash("parent")

    # Modify a child (change status)
    child1.status = "degraded"

    parent_hash_2 = graph._compute_tree_hash("parent")

    # Hashes should differ (child modification affects parent hash)
    assert parent_hash_1 != parent_hash_2


def test_tree_hash_idempotent(graph):
    """Test that tree hash is idempotent for stable state."""
    node = PluginNode(id="node", boot_layer="bundled", origin="builtin")
    graph.register_node(node)

    hash1 = graph._compute_tree_hash("node")
    hash2 = graph._compute_tree_hash("node")

    assert hash1 == hash2


# ── Test Work Delegation ───────────────────────────────────────────────────


def test_local_work_handling(handler, graph):
    """Test plugin handling work locally."""
    plugin = PluginNode(
        id="local-handler",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["process_text"],
    )
    graph.register_node(plugin)

    work = WorkRequest(
        work_id="w1",
        input_data={"text": "hello"},
        required_capability="process_text",
    )

    result = handler.handle_work("local-handler", work)

    assert result["status"] == "success"
    assert result["work_id"] == "w1"
    assert result["handled_by"] == "local-handler"


def test_delegation_to_child(handler, graph):
    """Test delegating work to a child plugin."""
    parent = PluginNode(
        id="parent",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],  # Parent doesn't handle work
        delegation_strategy=DelegationStrategy.HIERARCHICAL,
    )
    child = PluginNode(
        id="child",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["transcribe"],
    )

    graph.register_node(parent)
    graph.register_node(child)
    graph.add_child("parent", "child")

    work = WorkRequest(
        work_id="w1",
        input_data={"audio": "data"},
        required_capability="transcribe",
    )

    result = handler.handle_work("parent", work)

    assert result["handled_by"] == "child"
    assert parent.current_budget_used["standard"] > 0


def test_delegation_with_no_capable_children(handler, graph):
    """Test that work fails when no children have required capability."""
    parent = PluginNode(
        id="parent",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],
        delegation_strategy=DelegationStrategy.HIERARCHICAL,
    )
    child = PluginNode(
        id="child",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["image_process"],  # Different capability
    )

    graph.register_node(parent)
    graph.register_node(child)
    graph.add_child("parent", "child")

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="audio_transcribe",  # Not available
    )

    with pytest.raises(WorkUnhandleable):
        handler.handle_work("parent", work)


def test_delegation_strategy_local_only(handler, graph):
    """Test LOCAL_ONLY strategy prevents delegation."""
    plugin = PluginNode(
        id="local-only",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],
        delegation_strategy=DelegationStrategy.LOCAL_ONLY,
    )
    graph.register_node(plugin)

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="something",
    )

    with pytest.raises(WorkUnhandleable, match="delegation disabled"):
        handler.handle_work("local-only", work)


# ── Test Budget Management ────────────────────────────────────────────────


def test_budget_tracking(handler, graph):
    """Test that budget usage is tracked per tier."""
    plugin = PluginNode(
        id="plugin",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["work"],
        budget_config=BudgetConfig(
            compliance_budget_pool=50,
            high_priority_budget_pool=30,
            standard_budget_pool=20,
        ),
    )
    graph.register_node(plugin)

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="work",
        priority_tier=WorkTier.STANDARD,
        budget_cost=10,
    )

    handler.handle_work("plugin", work)

    assert plugin.current_budget_used["standard"] == 10


def test_budget_exhaustion(handler, graph):
    """Test that budget exhaustion is enforced."""
    plugin = PluginNode(
        id="plugin",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["work"],
        budget_config=BudgetConfig(standard_budget_pool=10),  # Very limited
    )
    graph.register_node(plugin)

    # First work: succeeds
    work1 = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="work",
        priority_tier=WorkTier.STANDARD,
        budget_cost=9,
    )
    handler.handle_work("plugin", work1)
    assert plugin.current_budget_used["standard"] == 9

    # Second work: would exceed budget, should fail
    work2 = WorkRequest(
        work_id="w2",
        input_data={},
        required_capability="work",
        priority_tier=WorkTier.STANDARD,
        budget_cost=5,
    )

    with pytest.raises(BudgetExhausted):
        handler.handle_work("plugin", work2)


def test_tier_aware_budget(handler, graph):
    """Test that different tiers have separate budget pools."""
    plugin = PluginNode(
        id="plugin",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["work"],
        budget_config=BudgetConfig(
            compliance_budget_pool=50,
            high_priority_budget_pool=30,
            standard_budget_pool=10,
        ),
    )
    graph.register_node(plugin)

    # Use up standard budget
    work1 = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="work",
        priority_tier=WorkTier.STANDARD,
        budget_cost=10,
    )
    handler.handle_work("plugin", work1)

    # HIGH priority work should still succeed (separate pool)
    work2 = WorkRequest(
        work_id="w2",
        input_data={},
        required_capability="work",
        priority_tier=WorkTier.HIGH,
        budget_cost=10,
    )
    result = handler.handle_work("plugin", work2)
    assert result["status"] == "success"
    assert plugin.current_budget_used["standard"] == 10
    assert plugin.current_budget_used["high"] == 10


def test_budget_cycle_reset(graph):
    """Test that budget resets for new cycle."""
    plugin = PluginNode(
        id="plugin",
        boot_layer="bundled",
        origin="builtin",
        budget_config=BudgetConfig(standard_budget_pool=100),
    )
    graph.register_node(plugin)

    # Use budget
    plugin.current_budget_used["standard"] = 50

    # Reset cycle
    graph.reset_budget_cycle("plugin")

    assert plugin.current_budget_used["standard"] == 0
    assert plugin.current_budget_used["high"] == 0


# ── Test Audit Trail ──────────────────────────────────────────────────────


def test_audit_trail_local_work(handler, graph, audit_log):
    """Test that local work is audited."""
    plugin = PluginNode(
        id="plugin",
        boot_layer="bundled",
        origin="builtin",
        capabilities=["work"],
    )
    graph.register_node(plugin)

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="work",
    )

    handler.handle_work("plugin", work)

    # Check audit log
    events = audit_log.get_all()
    work_events = [e for e in events if "work" in e.get("event", "")]
    assert len(work_events) > 0
    assert any("handled_locally" in e.get("event", "") for e in events)


def test_audit_trail_delegation(handler, graph, audit_log):
    """Test that delegation is audited."""
    parent = PluginNode(
        id="parent",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],
    )
    child = PluginNode(
        id="child",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["work"],
    )

    graph.register_node(parent)
    graph.register_node(child)
    graph.add_child("parent", "child")

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="work",
    )

    handler.handle_work("parent", work)

    # Check audit log
    events = audit_log.get_all()
    assert len(events) > 0


def test_plugin_registration_audit(graph, audit_log):
    """Test that plugin registration is audited."""
    plugin = PluginNode(
        id="plugin",
        boot_layer="bundled",
        origin="builtin",
    )
    graph.register_node(plugin)

    events = audit_log.get_all()
    reg_events = [e for e in events if e.get("event") == "plugin_registered"]
    assert len(reg_events) == 1
    assert reg_events[0]["plugin_id"] == "plugin"


# ── Test Multi-Level Delegation ────────────────────────────────────────────


def test_multi_level_delegation(handler, graph):
    """Test work flowing through multiple levels: parent -> child -> grandchild."""
    parent = PluginNode(
        id="parent",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],
    )
    child = PluginNode(
        id="child",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=[],
    )
    grandchild = PluginNode(
        id="grandchild",
        boot_layer="bundled",
        origin="builtin",
        parent_id="child",
        capabilities=["process"],
    )

    graph.register_node(parent)
    graph.register_node(child)
    graph.register_node(grandchild)
    graph.add_child("parent", "child")
    graph.add_child("child", "grandchild")

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="process",
    )

    result = handler.handle_work("parent", work)

    assert result["handled_by"] == "grandchild"
    # Budget should be tracked up the chain
    assert parent.current_budget_used["standard"] > 0


# ── Test Fallback Chains ───────────────────────────────────────────────────


def test_fallback_chain(handler, graph, quarantine_registry):
    """Test automatic failover with fallback chains."""
    parent = PluginNode(
        id="parent",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],
        fallback_chain=["child1", "child2"],
    )
    child1 = PluginNode(
        id="child1",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["work"],
    )
    child2 = PluginNode(
        id="child2",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["work"],
    )

    graph.register_node(parent)
    graph.register_node(child1)
    graph.register_node(child2)
    graph.add_child("parent", "child1")
    graph.add_child("parent", "child2")

    # Quarantine child1
    quarantine_registry.quarantine("child1")

    work = WorkRequest(
        work_id="w1",
        input_data={},
        required_capability="work",
    )

    # Should fallback to child2
    result = handler.handle_work("parent", work)
    assert result is not None


# ── Test Depth & Scoring ──────────────────────────────────────────────────


def test_compute_depth(graph):
    """Test that plugin depth is computed correctly."""
    root = PluginNode(id="root", boot_layer="bundled", origin="builtin")
    child = PluginNode(
        id="child", boot_layer="bundled", origin="builtin", parent_id="root"
    )
    grandchild = PluginNode(
        id="grandchild",
        boot_layer="bundled",
        origin="builtin",
        parent_id="child",
    )

    graph.register_node(root)
    graph.register_node(child)
    graph.register_node(grandchild)

    assert graph._compute_depth("root") == 0
    assert graph._compute_depth("child") == 1
    assert graph._compute_depth("grandchild") == 2


def test_child_scoring(handler, graph):
    """Test that child scoring works correctly."""
    parent = PluginNode(
        id="parent",
        boot_layer="bundled",
        origin="builtin",
        capabilities=[],
        budget_config=BudgetConfig(max_concurrent_children=2),
    )
    child1 = PluginNode(
        id="child1",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["work"],
    )
    child2 = PluginNode(
        id="child2",
        boot_layer="bundled",
        origin="builtin",
        parent_id="parent",
        capabilities=["work"],
    )

    graph.register_node(parent)
    graph.register_node(child1)
    graph.register_node(child2)
    graph.add_child("parent", "child1")
    graph.add_child("parent", "child2")

    # Simulate child1 being busy
    parent.child_status["child1"].is_busy = True
    parent.child_status["child2"].is_busy = False

    work = WorkRequest(work_id="w1", input_data={}, required_capability="work")

    # Score child1 (busy)
    score1 = handler._score_child(parent, "child1", work)
    # Score child2 (idle)
    score2 = handler._score_child(parent, "child2", work)

    # Idle child should have lower score (better)
    assert score2 < score1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
