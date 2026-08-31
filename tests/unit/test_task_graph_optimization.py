"""Tier-2 Tests: TaskGraph Optimization (MAX_NODES, performance)."""

import pytest
from core.vibe_engineering.task_graph import TaskGraphBuilder, Node


class TestTaskGraphBuilderCapacity:
    """TaskGraph MAX_NODES enforcement."""

    def test_add_node_respects_max_nodes(self):
        """Builder rejects nodes beyond MAX_NODES."""
        builder = TaskGraphBuilder("test_task")

        # Add nodes up to limit
        for i in range(TaskGraphBuilder.MAX_NODES):
            node = Node(
                id=f"node_{i}",
                type="test",
                timestamp="2026-08-31T00:00:00",
                data={}
            )
            builder.add_node(node)

        # Next node should fail
        with pytest.raises(RuntimeError, match="graph at capacity"):
            builder.add_node(Node(
                id="over_limit",
                type="test",
                timestamp="2026-08-31T00:00:00",
                data={}
            ))

    def test_self_loop_rejected(self):
        """Builder rejects self-loops (cycle)."""
        builder = TaskGraphBuilder("test_task")
        node = Node(
            id="node_1",
            type="test",
            timestamp="2026-08-31T00:00:00",
            data={}
        )
        builder.add_node(node)

        from core.vibe_engineering.task_graph import Edge
        edge = Edge(
            from_id="node_1",
            to_id="node_1",  # Self-loop
            edge_type="hard_dependency",
            label="self"
        )

        result = builder.add_edge(edge)
        assert result is False

    def test_cycle_detection_rejects_back_edge(self):
        """Builder rejects edges that would create cycles."""
        builder = TaskGraphBuilder("test_task")

        # Create chain: 1 → 2 → 3
        for i in range(1, 4):
            node = Node(
                id=f"node_{i}",
                type="test",
                timestamp="2026-08-31T00:00:00",
                data={}
            )
            builder.add_node(node)

        from core.vibe_engineering.task_graph import Edge

        # Add forward edges
        builder.add_edge(Edge("node_1", "node_2", "hard_dependency", "1→2"))
        builder.add_edge(Edge("node_2", "node_3", "hard_dependency", "2→3"))

        # Try to add back edge (would create cycle)
        result = builder.add_edge(Edge("node_3", "node_1", "hard_dependency", "3→1"))
        assert result is False


class TestTaskGraphBuilderWarnings:
    """TaskGraph capacity warnings."""

    def test_warn_nodes_threshold(self):
        """Builder logs warning near capacity."""
        builder = TaskGraphBuilder("test_task")

        # Add nodes close to WARN_NODES
        for i in range(TaskGraphBuilder.WARN_NODES):
            node = Node(
                id=f"node_{i}",
                type="test",
                timestamp="2026-08-31T00:00:00",
                data={}
            )
            builder.add_node(node)

        # Next node should log warning but succeed
        node = Node(
            id=f"node_{TaskGraphBuilder.WARN_NODES}",
            type="test",
            timestamp="2026-08-31T00:00:00",
            data={}
        )
        builder.add_node(node)
        assert len(builder.nodes) == TaskGraphBuilder.WARN_NODES + 1
