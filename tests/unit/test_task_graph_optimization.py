"""Tier-2 Tests: TaskGraphBuilder DAG invariants (self-loop + cycle rejection).

``TaskGraphBuilder`` has no node-capacity cap: the former ``MAX_NODES`` /
``WARN_NODES`` tests here pinned a feature that never existed in
``core/vibe_engineering/task_graph.py`` and were removed on 2026-09-07.
"""

import pytest
from core.vibe_engineering.task_graph import TaskGraphBuilder, Node


class TestTaskGraphBuilderDAG:
    """TaskGraph DAG enforcement (fail-closed on cycles)."""

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
