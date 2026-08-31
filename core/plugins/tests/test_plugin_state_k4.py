"""Unit tests for ADR-0345 k=4: Distributed Plugin State (10 focused tests)."""

import pytest

from corvin_plugins.plugin_state import (
    PluginStateSnapshot,
    PluginStateStore,
)
from corvin_plugins.hierarchical_registry import HierarchicalRegistry


class TestPluginStateSnapshot:
    """Test state snapshot structure."""

    def test_snapshot_creation(self):
        """Create plugin state snapshot."""
        snap = PluginStateSnapshot(
            plugin_id="p1",
            timestamp_utc="2026-08-26T12:00:00Z",
            status="healthy",
            budget_used={"standard": 20},
            child_health={"child1": "healthy"},
            work_count=100,
            avg_latency_ms=50.0,
        )
        assert snap.plugin_id == "p1"
        assert snap.status == "healthy"

    def test_snapshot_to_dict(self):
        """Serialize snapshot to dict."""
        snap = PluginStateSnapshot(
            plugin_id="p1",
            timestamp_utc="2026-08-26T12:00:00Z",
            status="healthy",
            budget_used={"standard": 20},
            child_health={},
            work_count=100,
            avg_latency_ms=50.0,
        )
        d = snap.to_dict()
        assert d["plugin_id"] == "p1"
        assert d["status"] == "healthy"

    def test_snapshot_to_json(self):
        """Serialize snapshot to JSON."""
        snap = PluginStateSnapshot(
            plugin_id="p1",
            timestamp_utc="2026-08-26T12:00:00Z",
            status="healthy",
            budget_used={},
            child_health={},
            work_count=0,
            avg_latency_ms=0.0,
        )
        json_str = snap.to_json()
        assert isinstance(json_str, str)
        assert "p1" in json_str


class TestPluginStateStore:
    """Test state store operations."""

    def test_initialize_store(self):
        """Initialize state store."""
        store = PluginStateStore()
        assert len(store.snapshots) == 0

    def test_checkpoint_plugin_state(self):
        """Checkpoint plugin state."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()
        snapshot = store.checkpoint("p1", node)

        assert snapshot.plugin_id == "p1"
        assert snapshot.status == "ready"
        assert "p1" in store.snapshots

    def test_restore_plugin_state(self):
        """Restore plugin state from snapshot."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        # Modify state
        node.status = "degraded"
        node.current_budget_used["standard"] = 50

        # Checkpoint
        store = PluginStateStore()
        store.checkpoint("p1", node)

        # Change state
        node.status = "healthy"
        node.current_budget_used["standard"] = 0

        # Restore
        success = store.restore("p1", node)
        assert success is True
        assert node.status == "degraded"
        assert node.current_budget_used["standard"] == 50

    def test_get_latest_snapshot(self):
        """Get most recent snapshot."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()
        snap1 = store.checkpoint("p1", node)

        node.status = "degraded"
        snap2 = store.checkpoint("p1", node)

        latest = store.get_latest_snapshot("p1")
        assert latest.timestamp_utc == snap2.timestamp_utc
        assert latest.status == "degraded"

    def test_get_snapshots_history(self):
        """Get snapshot history."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()

        # Create multiple snapshots
        for i in range(5):
            store.checkpoint("p1", node)

        history = store.get_snapshots("p1", limit=10)
        assert len(history) == 5

    def test_restore_nonexistent_plugin(self):
        """Restore fails for plugin with no snapshots."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()
        success = store.restore("unknown", node)
        assert success is False

    def test_snapshots_trimmed(self):
        """Old snapshots are trimmed to prevent memory bloat."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()

        # Create many snapshots
        for _ in range(150):
            store.checkpoint("p1", node)

        snapshots = store.get_snapshots("p1", limit=200)
        # Should be trimmed to ~50 (keeps every 3rd when reaching 100)
        assert len(snapshots) <= 100
        assert len(snapshots) >= 50

    def test_clear_snapshots(self):
        """Clear snapshots."""
        registry = HierarchicalRegistry()
        node = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()
        store.checkpoint("p1", node)

        assert len(store.snapshots) == 1
        store.clear("p1")
        assert len(store.snapshots) == 0

    def test_clear_all_snapshots(self):
        """Clear all snapshots."""
        registry = HierarchicalRegistry()
        p1 = registry.register_plugin(
            plugin_id="p1", boot_layer="bundled", origin="builtin"
        )
        p2 = registry.register_plugin(
            plugin_id="p2", boot_layer="bundled", origin="builtin"
        )

        store = PluginStateStore()
        store.checkpoint("p1", p1)
        store.checkpoint("p2", p2)

        assert len(store.snapshots) == 2
        store.clear()
        assert len(store.snapshots) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
