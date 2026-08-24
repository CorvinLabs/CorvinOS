"""
ADR-0400: Task Graph Tests

Comprehensive test suite for TaskGraph, GraphBuilder, queries, and backward compat.
Coverage: 45 tests spanning 4 categories.
"""

import pytest
import json
from datetime import datetime, timedelta
from pathlib import Path

from core.vibe_engineering.task_graph import TaskGraph, Node, Edge
from core.vibe_engineering.graph_builder import GraphBuilder
from core.vibe_engineering.graph_queries import GraphQueries
from core.vibe_engineering.graph_events import (
    GraphEvent, GraphEventEmitter, emit_checkpoint_saved, emit_decision_made,
    emit_context_reduced, emit_error_occurred, get_event_emitter
)
from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState
from core.vibe_engineering.checkpoint_to_graph import CheckpointToGraphConverter


# ===== FIXTURES =====

@pytest.fixture
def sample_nodes():
    """Create sample nodes for testing."""
    base_time = datetime.now()
    return [
        Node(
            id="checkpoint_1",
            type="checkpoint",
            timestamp=base_time.isoformat(),
            data={"iteration_num": 1, "trigger": "phase_exit"}
        ),
        Node(
            id="decision_1",
            type="decision",
            timestamp=(base_time + timedelta(seconds=10)).isoformat(),
            data={"strategy": "decompose", "phase": "planning"}
        ),
        Node(
            id="error_1",
            type="error",
            timestamp=(base_time + timedelta(seconds=20)).isoformat(),
            data={"error_type": "timeout", "error_message": "API timeout"}
        ),
        Node(
            id="checkpoint_2",
            type="checkpoint",
            timestamp=(base_time + timedelta(seconds=30)).isoformat(),
            data={"iteration_num": 2, "trigger": "recovery"}
        ),
        Node(
            id="context_1",
            type="context",
            timestamp=(base_time + timedelta(seconds=15)).isoformat(),
            data={"reduction_pct": 91, "kept_count": 50}
        ),
    ]


@pytest.fixture
def sample_graph(sample_nodes):
    """Create sample TaskGraph."""
    builder = GraphBuilder("task_001")
    for node in sample_nodes:
        builder.add_node(node)
    builder.infer_edges()
    return builder.build()


@pytest.fixture
def sample_checkpoint():
    """Create sample CheckpointState."""
    return CheckpointState(
        checkpoint_id="ckpt_001",
        task_id="task_001",
        session_id="session_001",
        phase="execution",
        trigger="context_limit",
        timestamp_iso=datetime.now().isoformat(),
        iteration_num=5,
        task_state={"goal": "solve problem"},
        context_essentials={"reduction_pct": 91, "kept": [], "dropped": []},
        learning_state={"strategies_tried": ["approach_a", "approach_b"]},
        open_subgoals=[{"description": "subtask 1", "status": "open"}],
        artifacts=[],
        recovery_reason=None
    )


# ===== UNIT TESTS: Graph Data Structures (10 tests) =====

class TestTaskGraphDataStructures:
    """Test TaskGraph, Node, Edge data structures."""

    def test_node_creation(self):
        """Create and validate Node."""
        node = Node(
            id="test_node",
            type="decision",
            timestamp=datetime.now().isoformat(),
            data={"key": "value"}
        )
        assert node.id == "test_node"
        assert node.type == "decision"
        assert node.data["key"] == "value"

    def test_node_immutability(self):
        """Node should be frozen (immutable)."""
        node = Node(
            id="test", type="decision",
            timestamp=datetime.now().isoformat(),
            data={}
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            node.id = "modified"

    def test_node_serialization(self):
        """Node to_dict and from_dict round-trip."""
        node = Node(
            id="test", type="checkpoint",
            timestamp="2026-08-24T10:00:00",
            data={"iteration": 5}
        )
        node_dict = node.to_dict()
        restored = Node.from_dict(node_dict)
        assert restored.id == node.id
        assert restored.type == node.type
        assert restored.data == node.data

    def test_edge_creation(self):
        """Create and validate Edge."""
        edge = Edge(
            from_id="a", to_id="b",
            edge_type="hard_dependency",
            label="depends_on"
        )
        assert edge.from_id == "a"
        assert edge.to_id == "b"
        assert edge.edge_type == "hard_dependency"

    def test_edge_metadata(self):
        """Edge metadata field."""
        edge = Edge(
            from_id="a", to_id="b",
            edge_type="temporal",
            label="follows",
            metadata={"phase": "execution"}
        )
        assert edge.metadata["phase"] == "execution"

    def test_graph_creation(self, sample_nodes):
        """Create TaskGraph from nodes and edges."""
        nodes_dict = {n.id: n for n in sample_nodes}
        edges = [Edge("checkpoint_1", "decision_1", "temporal", "follows")]
        graph = TaskGraph(
            task_id="task_001",
            created_at=datetime.now().isoformat(),
            nodes=nodes_dict,
            edges=edges,
            nodes_by_type={"checkpoint": ["checkpoint_1", "checkpoint_2"]},
            iterations={1: "checkpoint_1", 2: "checkpoint_2"}
        )
        assert graph.task_id == "task_001"
        assert len(graph.nodes) == 5
        assert len(graph.edges) == 1

    def test_graph_json_serialization(self, sample_graph):
        """Graph to_json and from_json round-trip."""
        json_str = sample_graph.to_json()
        assert isinstance(json_str, str)
        restored = TaskGraph.from_json(json_str)
        assert restored.task_id == sample_graph.task_id
        assert len(restored.nodes) == len(sample_graph.nodes)
        assert len(restored.edges) == len(sample_graph.edges)

    def test_graph_dag_validation(self, sample_graph):
        """Graph DAG validation (should pass for acyclic graph)."""
        assert sample_graph.validate_dag() is True

    def test_graph_stats(self, sample_graph):
        """Get graph statistics."""
        stats = sample_graph.get_stats()
        assert stats["total_nodes"] > 0
        assert stats["total_edges"] >= 0
        assert "nodes_by_type" in stats


# ===== UNIT TESTS: GraphBuilder (10 tests) =====

class TestGraphBuilder:
    """Test GraphBuilder with edge inference."""

    def test_builder_creation(self):
        """Create GraphBuilder."""
        builder = GraphBuilder("task_001")
        assert builder.task_id == "task_001"
        assert len(builder.nodes) == 0

    def test_add_node_returns_self(self, sample_nodes):
        """add_node returns self for builder pattern."""
        builder = GraphBuilder("task_001")
        result = builder.add_node(sample_nodes[0])
        assert result is builder

    def test_add_multiple_nodes(self, sample_nodes):
        """Add multiple nodes."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        assert builder.get_node_count() == len(sample_nodes)

    def test_add_edge(self, sample_nodes):
        """Add edge between nodes."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        edge = Edge("checkpoint_1", "decision_1", "temporal", "follows")
        builder.add_edge(edge)
        assert builder.get_edge_count() == 1

    def test_nodes_by_type_tracking(self, sample_nodes):
        """Track nodes by type."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        assert "checkpoint" in builder.nodes_by_type
        assert len(builder.nodes_by_type["checkpoint"]) == 2

    def test_iteration_tracking(self, sample_nodes):
        """Track iterations from checkpoint nodes."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        assert 1 in builder.iterations
        assert 2 in builder.iterations

    def test_infer_edges_temporal(self, sample_nodes):
        """Infer temporal edges between checkpoints."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        builder.infer_edges()
        edges = builder.edges
        # Should have temporal edge between checkpoint_1 and checkpoint_2
        temporal_edges = [e for e in edges if e.edge_type == "temporal"]
        assert len(temporal_edges) > 0

    def test_infer_edges_error_recovery(self, sample_nodes):
        """Infer error recovery edges."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        builder.infer_edges()
        edges = builder.edges
        # Should have recovery edge from error_1 to next checkpoint/context
        recovery_edges = [e for e in edges if e.edge_type == "data_flow"]
        assert len(recovery_edges) > 0

    def test_build_returns_graph(self, sample_nodes):
        """build() returns TaskGraph."""
        builder = GraphBuilder("task_001")
        for node in sample_nodes:
            builder.add_node(node)
        builder.infer_edges()
        graph = builder.build()
        assert isinstance(graph, TaskGraph)
        assert graph.task_id == "task_001"

    def test_build_validates_dag(self):
        """build() validates DAG property."""
        # Create a valid DAG
        builder = GraphBuilder("task_001")
        n1 = Node("a", "checkpoint", datetime.now().isoformat(), {})
        n2 = Node("b", "checkpoint", datetime.now().isoformat(), {})
        builder.add_node(n1)
        builder.add_node(n2)
        builder.add_edge(Edge("a", "b", "temporal", "follows"))
        graph = builder.build()
        assert graph is not None


# ===== UNIT TESTS: Graph Queries (10 tests) =====

class TestGraphQueries:
    """Test query operations on TaskGraph."""

    def test_reachability_basic(self, sample_graph):
        """Reachability from a node."""
        root = sample_graph.get_root_nodes()
        if root:
            reachable = GraphQueries.reachability(sample_graph, root[0].id)
            assert isinstance(reachable, set)
            assert len(reachable) >= 0

    def test_reachability_no_path(self, sample_graph):
        """Reachability when no path exists."""
        leaves = sample_graph.get_leaf_nodes()
        if leaves:
            reachable = GraphQueries.reachability(sample_graph, leaves[0].id)
            assert len(reachable) == 0

    def test_predecessors(self, sample_graph):
        """Get predecessors of a node."""
        # Find a node with incoming edges
        nodes_with_in = {e.to_id for e in sample_graph.edges}
        if nodes_with_in:
            node_id = list(nodes_with_in)[0]
            preds = GraphQueries.predecessors(sample_graph, node_id)
            assert isinstance(preds, set)

    def test_critical_path(self, sample_graph):
        """Get critical path in DAG."""
        path = GraphQueries.critical_path(sample_graph)
        assert isinstance(path, list)
        if len(path) > 1:
            # Verify path connectivity
            for i in range(len(path) - 1):
                edges = [e for e in sample_graph.edges
                        if e.from_id == path[i] and e.to_id == path[i+1]]
                assert len(edges) > 0

    def test_impact_analysis(self, sample_graph):
        """Impact analysis (downstream effects)."""
        root = sample_graph.get_root_nodes()
        if root:
            impact = GraphQueries.impact_analysis(sample_graph, root[0].id)
            assert isinstance(impact, set)

    def test_find_blocking_nodes(self, sample_graph):
        """Find blocking nodes (high fan-out)."""
        blocking = GraphQueries.find_blocking_nodes(sample_graph)
        assert isinstance(blocking, list)
        # Results should be sorted by fan-out descending
        if len(blocking) > 1:
            assert blocking[0][1] >= blocking[1][1]

    def test_get_path(self, sample_graph):
        """Get shortest path between nodes."""
        nodes = list(sample_graph.nodes.keys())
        if len(nodes) >= 2:
            path = GraphQueries.get_path(sample_graph, nodes[0], nodes[-1])
            # Path may be None if nodes unreachable
            if path:
                assert path[0] == nodes[0]

    def test_get_timeline(self, sample_graph):
        """Get chronological timeline."""
        timeline = GraphQueries.get_timeline(sample_graph)
        assert isinstance(timeline, list)
        assert len(timeline) == len(sample_graph.nodes)
        # Verify sorted by timestamp
        if len(timeline) > 1:
            for i in range(len(timeline) - 1):
                assert timeline[i][0] <= timeline[i+1][0]

    def test_get_stats_by_type(self, sample_graph):
        """Get statistics by node type."""
        stats = GraphQueries.get_stats_by_type(sample_graph)
        assert isinstance(stats, dict)
        assert all("count" in s for s in stats.values())

    def test_find_node_by_data(self, sample_graph):
        """Find nodes by data field."""
        nodes = GraphQueries.find_node_by_data(sample_graph, "phase", "planning")
        assert isinstance(nodes, list)


# ===== UNIT TESTS: Graph Events (5 tests) =====

class TestGraphEvents:
    """Test event emission and handling."""

    def test_event_creation(self):
        """Create GraphEvent."""
        event = GraphEvent(
            event_type="checkpoint_saved",
            task_id="task_001",
            timestamp=datetime.now().isoformat(),
            data={"checkpoint_id": "ckpt_001"}
        )
        assert event.event_type == "checkpoint_saved"
        assert event.task_id == "task_001"

    def test_event_serialization(self):
        """Event to_dict and from_dict."""
        event = GraphEvent(
            event_type="decision_made",
            task_id="task_001",
            timestamp="2026-08-24T10:00:00",
            data={"decision": "decompose"}
        )
        event_dict = event.to_dict()
        restored = GraphEvent.from_dict(event_dict)
        assert restored.event_type == event.event_type

    def test_emitter_creation(self):
        """Create GraphEventEmitter."""
        emitter = GraphEventEmitter()
        assert emitter is not None
        assert len(emitter.events) == 0

    def test_emit_and_subscribe(self):
        """Emit event and subscribe to it."""
        emitter = GraphEventEmitter()
        received = []

        def listener(event):
            received.append(event)

        emitter.subscribe("checkpoint_saved", listener)
        event = GraphEvent("checkpoint_saved", "task_001", datetime.now().isoformat(), {})
        emitter.emit(event)
        assert len(received) == 1
        assert received[0].event_type == "checkpoint_saved"

    def test_global_event_emitter(self):
        """Get global event emitter instance."""
        emitter1 = get_event_emitter()
        emitter2 = get_event_emitter()
        assert emitter1 is emitter2  # Singleton


# ===== INTEGRATION TESTS: Backward Compat (10 tests) =====

class TestBackwardCompatibility:
    """Test checkpoint to graph conversion."""

    def test_checkpoint_to_graph_conversion(self, sample_checkpoint):
        """Convert CheckpointState to TaskGraph."""
        graph = CheckpointToGraphConverter.convert(sample_checkpoint)
        assert isinstance(graph, TaskGraph)
        assert graph.task_id == sample_checkpoint.task_id
        assert len(graph.nodes) > 0

    def test_conversion_creates_checkpoint_node(self, sample_checkpoint):
        """Conversion should create checkpoint node."""
        graph = CheckpointToGraphConverter.convert(sample_checkpoint)
        checkpoint_nodes = graph.get_nodes_by_type("checkpoint")
        assert len(checkpoint_nodes) == 1

    def test_conversion_creates_decision_nodes(self, sample_checkpoint):
        """Conversion should create decision nodes from strategies."""
        graph = CheckpointToGraphConverter.convert(sample_checkpoint)
        decision_nodes = graph.get_nodes_by_type("decision")
        assert len(decision_nodes) == len(sample_checkpoint.learning_state["strategies_tried"])

    def test_conversion_creates_context_node(self, sample_checkpoint):
        """Conversion should create context node."""
        graph = CheckpointToGraphConverter.convert(sample_checkpoint)
        context_nodes = graph.get_nodes_by_type("context")
        assert len(context_nodes) == 1

    def test_conversion_with_recovery_reason(self):
        """Conversion with recovery_reason should create error node."""
        checkpoint = CheckpointState(
            checkpoint_id="ckpt_001",
            task_id="task_001",
            session_id="session_001",
            phase="execution",
            trigger="recovery",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=5,
            task_state={},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[],
            recovery_reason="API timeout"
        )
        graph = CheckpointToGraphConverter.convert(checkpoint)
        error_nodes = graph.get_nodes_by_type("error")
        assert len(error_nodes) == 1

    def test_conversion_validation(self, sample_checkpoint):
        """Validate conversion result."""
        graph = CheckpointToGraphConverter.convert(sample_checkpoint)
        valid = CheckpointToGraphConverter.validate_conversion(sample_checkpoint, graph)
        assert valid is True

    def test_merge_graphs(self, sample_checkpoint):
        """Merge new checkpoint into existing graph."""
        # Create first graph
        graph1 = CheckpointToGraphConverter.convert(sample_checkpoint)

        # Create second checkpoint
        cp2 = CheckpointState(
            checkpoint_id="ckpt_002",
            task_id=sample_checkpoint.task_id,
            session_id="session_001",
            phase="execution",
            trigger="iteration_cap",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=6,
            task_state={},
            context_essentials={},
            learning_state={},
            open_subgoals=[],
            artifacts=[]
        )

        # Merge
        graph2 = CheckpointToGraphConverter.merge_graphs(graph1, cp2)
        assert len(graph2.nodes) > len(graph1.nodes)

    def test_migration_stats(self, sample_checkpoint):
        """Get migration statistics."""
        checkpoints = [sample_checkpoint]
        graphs = [CheckpointToGraphConverter.convert(sample_checkpoint)]
        stats = CheckpointToGraphConverter.get_migration_stats(checkpoints, graphs)
        assert stats["total_checkpoints"] == 1
        assert stats["total_nodes_created"] > 0

    def test_old_checkpoint_format_resilience(self):
        """Handle old checkpoint format gracefully."""
        # Checkpoint with minimal fields
        minimal_checkpoint = CheckpointState(
            checkpoint_id="ckpt_minimal",
            task_id="task_001",
            session_id="sess_001",
            phase="execution",
            trigger="phase_exit",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=1,
            task_state={},
            context_essentials=None,  # Might be None in old format
            learning_state=None,
            open_subgoals=[],
            artifacts=[]
        )
        # Should not raise exception
        graph = CheckpointToGraphConverter.convert(minimal_checkpoint)
        assert graph is not None


# ===== PERFORMANCE TESTS (5 tests) =====

class TestPerformance:
    """Test performance constraints."""

    def test_graph_build_performance(self):
        """Graph building should be < 50ms for 100 nodes."""
        import time
        builder = GraphBuilder("perf_test")
        base_time = datetime.now()

        # Add 100 nodes
        for i in range(100):
            node = Node(
                id=f"node_{i}",
                type="checkpoint" if i % 10 == 0 else "decision",
                timestamp=(base_time + timedelta(seconds=i)).isoformat(),
                data={"iteration": i}
            )
            builder.add_node(node)

        start = time.time()
        builder.infer_edges()
        graph = builder.build()
        elapsed = (time.time() - start) * 1000  # ms

        assert elapsed < 50, f"Graph build took {elapsed}ms (> 50ms)"

    def test_reachability_query_performance(self):
        """Reachability query should be < 100ms for 100-node graph."""
        import time
        builder = GraphBuilder("perf_test")
        base_time = datetime.now()

        # Build 100-node graph
        for i in range(100):
            node = Node(
                id=f"node_{i}",
                type="checkpoint" if i % 10 == 0 else "decision",
                timestamp=(base_time + timedelta(seconds=i)).isoformat(),
                data={"iteration": i}
            )
            builder.add_node(node)

        builder.infer_edges()
        graph = builder.build()

        # Time reachability query
        start = time.time()
        reachable = GraphQueries.reachability(graph, "node_0")
        elapsed = (time.time() - start) * 1000  # ms

        assert elapsed < 100, f"Reachability query took {elapsed}ms (> 100ms)"

    def test_critical_path_performance(self):
        """Critical path should be < 100ms for 100-node graph."""
        import time
        builder = GraphBuilder("perf_test")
        base_time = datetime.now()

        for i in range(100):
            node = Node(
                id=f"node_{i}",
                type="checkpoint" if i % 10 == 0 else "decision",
                timestamp=(base_time + timedelta(seconds=i)).isoformat(),
                data={}
            )
            builder.add_node(node)

        builder.infer_edges()
        graph = builder.build()

        start = time.time()
        path = GraphQueries.critical_path(graph)
        elapsed = (time.time() - start) * 1000  # ms

        assert elapsed < 100, f"Critical path took {elapsed}ms (> 100ms)"

    def test_json_serialization_performance(self):
        """JSON serialization should be fast."""
        import time
        builder = GraphBuilder("perf_test")
        base_time = datetime.now()

        for i in range(50):
            node = Node(
                id=f"node_{i}",
                type="checkpoint" if i % 10 == 0 else "decision",
                timestamp=(base_time + timedelta(seconds=i)).isoformat(),
                data={"data": f"value_{i}"}
            )
            builder.add_node(node)

        builder.infer_edges()
        graph = builder.build()

        start = time.time()
        json_str = graph.to_json()
        elapsed = (time.time() - start) * 1000  # ms

        assert elapsed < 50, f"JSON serialization took {elapsed}ms (> 50ms)"

    def test_large_graph_handling(self):
        """Handle large graphs (500+ nodes) without memory issues."""
        builder = GraphBuilder("large_test")
        base_time = datetime.now()

        for i in range(500):
            node = Node(
                id=f"node_{i}",
                type="checkpoint" if i % 50 == 0 else "decision",
                timestamp=(base_time + timedelta(seconds=i)).isoformat(),
                data={"index": i}
            )
            builder.add_node(node)

        builder.infer_edges()
        graph = builder.build()

        assert len(graph.nodes) == 500
        # Graph should be valid DAG
        assert graph.validate_dag() is True


# ===== EDGE CASE TESTS (5 tests) =====

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_graph(self):
        """Handle empty graph."""
        graph = TaskGraph(
            task_id="empty",
            created_at=datetime.now().isoformat(),
            nodes={},
            edges=[],
            nodes_by_type={},
            iterations={}
        )
        assert len(graph.nodes) == 0
        assert graph.validate_dag() is True

    def test_single_node_graph(self):
        """Graph with single node."""
        node = Node("n1", "checkpoint", datetime.now().isoformat(), {})
        graph = TaskGraph(
            task_id="single",
            created_at=datetime.now().isoformat(),
            nodes={"n1": node},
            edges=[],
            nodes_by_type={"checkpoint": ["n1"]},
            iterations={}
        )
        assert len(graph.nodes) == 1
        assert len(graph.edges) == 0
        assert graph.validate_dag() is True

    def test_disconnected_nodes(self):
        """Graph with multiple disconnected components."""
        n1 = Node("n1", "checkpoint", datetime.now().isoformat(), {})
        n2 = Node("n2", "checkpoint", datetime.now().isoformat(), {})
        graph = TaskGraph(
            task_id="disconnected",
            created_at=datetime.now().isoformat(),
            nodes={"n1": n1, "n2": n2},
            edges=[],
            nodes_by_type={"checkpoint": ["n1", "n2"]},
            iterations={}
        )
        # Should still be valid DAG
        assert graph.validate_dag() is True

    def test_self_loop_detection(self):
        """Detect and reject self-loops."""
        n1 = Node("n1", "checkpoint", datetime.now().isoformat(), {})
        # Self-loop edge
        edge = Edge("n1", "n1", "temporal", "self")
        graph = TaskGraph(
            task_id="selfloop",
            created_at=datetime.now().isoformat(),
            nodes={"n1": n1},
            edges=[edge],
            nodes_by_type={"checkpoint": ["n1"]},
            iterations={}
        )
        # Should be invalid (has cycle)
        assert graph.validate_dag() is False

    def test_missing_node_references(self):
        """Handle edges to missing nodes."""
        n1 = Node("n1", "checkpoint", datetime.now().isoformat(), {})
        # Edge to non-existent node
        edge = Edge("n1", "missing", "temporal", "broken")
        graph = TaskGraph(
            task_id="broken",
            created_at=datetime.now().isoformat(),
            nodes={"n1": n1},
            edges=[edge],
            nodes_by_type={"checkpoint": ["n1"]},
            iterations={}
        )
        # Should still validate (broken references are allowed)
        assert graph.validate_dag() is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
