"""
ADR-0400: GraphBuilder

Builder pattern for constructing TaskGraphs with automatic edge inference.
Handles temporal ordering, dependency detection, and data flow.
"""

from typing import Dict, List, Optional, Set
from datetime import datetime
import logging
from .task_graph import TaskGraph, Node, Edge

logger = logging.getLogger(__name__)


class GraphBuilder:
    """
    Build TaskGraph incrementally with automatic edge inference.

    Supports builder pattern: add_node() → infer_edges() → build()
    """

    def __init__(self, task_id: str, created_at: Optional[str] = None):
        """
        Initialize builder.

        Args:
            task_id: Task identifier
            created_at: ISO timestamp (defaults to now)
        """
        self.task_id = task_id
        self.created_at = created_at or datetime.now().isoformat()
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self.nodes_by_type: Dict[str, List[str]] = {}
        self.iterations: Dict[int, str] = {}

    def add_node(self, node: Node) -> "GraphBuilder":
        """
        Add node to graph.

        Args:
            node: Node to add

        Returns:
            self (builder pattern)
        """
        if node.id in self.nodes:
            logger.warning(f"Node {node.id} already exists, overwriting")

        self.nodes[node.id] = node

        # Track by type
        if node.type not in self.nodes_by_type:
            self.nodes_by_type[node.type] = []
        if node.id not in self.nodes_by_type[node.type]:
            self.nodes_by_type[node.type].append(node.id)

        # Track iterations (checkpoint nodes)
        if node.type == "checkpoint" and "iteration_num" in node.data:
            iter_num = node.data["iteration_num"]
            self.iterations[iter_num] = node.id

        logger.debug(f"Added node: {node.id} (type={node.type})")
        return self

    def add_edge(self, edge: Edge) -> "GraphBuilder":
        """
        Add edge to graph.

        Args:
            edge: Edge to add

        Returns:
            self (builder pattern)
        """
        # Validate that nodes exist
        if edge.from_id not in self.nodes:
            logger.warning(f"Edge source node not found: {edge.from_id}")
        if edge.to_id not in self.nodes:
            logger.warning(f"Edge target node not found: {edge.to_id}")

        # Check for duplicate edges
        existing = [
            e for e in self.edges
            if e.from_id == edge.from_id and e.to_id == edge.to_id
        ]
        if existing:
            logger.debug(f"Edge {edge.from_id} → {edge.to_id} already exists")
            return self

        self.edges.append(edge)
        logger.debug(f"Added edge: {edge.from_id} → {edge.to_id} ({edge.edge_type})")
        return self

    def infer_edges(self) -> "GraphBuilder":
        """
        Automatically infer edges based on node types and timestamps.

        Inference rules:
        1. Temporal edges: between consecutive checkpoints (in time order)
        2. Error recovery: error node → recovery/retry node
        3. Decision dependencies: related decisions in same phase
        4. Context flow: context_reduced → checkpoint
        5. Metric references: metric → related nodes

        Returns:
            self (builder pattern)
        """
        # Sort nodes by timestamp for temporal ordering
        sorted_nodes = sorted(
            self.nodes.values(),
            key=lambda n: n.timestamp
        )

        # Rule 1: Temporal edges between consecutive nodes of same type
        checkpoints = [n for n in sorted_nodes if n.type == "checkpoint"]
        for i in range(len(checkpoints) - 1):
            from_node = checkpoints[i]
            to_node = checkpoints[i + 1]
            edge = Edge(
                from_id=from_node.id,
                to_id=to_node.id,
                edge_type="temporal",
                label=f"iteration {i} → {i + 1}",
                metadata={"phase": "checkpoint_sequence"}
            )
            self.add_edge(edge)

        # Rule 2: Error recovery edges (error → recovery action)
        errors = [n for n in sorted_nodes if n.type == "error"]
        for error_node in errors:
            # Find nearest recovery action after error
            error_time = datetime.fromisoformat(error_node.timestamp)
            candidates = [
                n for n in sorted_nodes
                if n.type in ("checkpoint", "context")
                and datetime.fromisoformat(n.timestamp) > error_time
            ]
            if candidates:
                recovery = candidates[0]
                edge = Edge(
                    from_id=error_node.id,
                    to_id=recovery.id,
                    edge_type="data_flow",
                    label="recovery",
                    metadata={"error_type": error_node.data.get("error_type")}
                )
                self.add_edge(edge)

        # Rule 3: Decision dependencies (soft dependencies within same phase)
        decisions = [n for n in sorted_nodes if n.type == "decision"]
        for i in range(len(decisions) - 1):
            from_dec = decisions[i]
            to_dec = decisions[i + 1]

            # Check if in same phase
            from_phase = from_dec.data.get("phase")
            to_phase = to_dec.data.get("phase")

            if from_phase and to_phase and from_phase == to_phase:
                # High confidence (same phase)
                edge_type = "soft_dependency"
            else:
                # Lower confidence (different phases)
                edge_type = "soft_dependency"

            edge = Edge(
                from_id=from_dec.id,
                to_id=to_dec.id,
                edge_type=edge_type,
                label="decision sequence",
                metadata={"confidence": 0.7}
            )
            self.add_edge(edge)

        # Rule 4: Context flow to checkpoints
        contexts = [n for n in sorted_nodes if n.type == "context"]
        for context_node in contexts:
            # Find nearest checkpoint after context
            context_time = datetime.fromisoformat(context_node.timestamp)
            candidates = [
                n for n in checkpoints
                if datetime.fromisoformat(n.timestamp) > context_time
            ]
            if candidates:
                checkpoint = candidates[0]
                edge = Edge(
                    from_id=context_node.id,
                    to_id=checkpoint.id,
                    edge_type="data_flow",
                    label="context snapshot",
                    metadata={"reduction_pct": context_node.data.get("reduction_pct")}
                )
                self.add_edge(edge)

        # Rule 5: Metric references (optional soft edges to related nodes)
        metrics = [n for n in sorted_nodes if n.type == "metric"]
        for metric_node in metrics:
            # Metrics typically reference the preceding checkpoint
            metric_time = datetime.fromisoformat(metric_node.timestamp)
            candidates = [
                n for n in checkpoints
                if datetime.fromisoformat(n.timestamp) <= metric_time
            ]
            if candidates:
                checkpoint = candidates[-1]  # Most recent before metric
                if checkpoint.id != metric_node.id:
                    edge = Edge(
                        from_id=checkpoint.id,
                        to_id=metric_node.id,
                        edge_type="soft_dependency",
                        label="measurement point",
                        metadata={"metric_type": metric_node.data.get("metric_type")}
                    )
                    self.add_edge(edge)

        logger.info(f"Edge inference complete: {len(self.edges)} edges created")
        return self

    def build(self) -> TaskGraph:
        """
        Build and return TaskGraph.

        Validates DAG constraint before returning.

        Returns:
            TaskGraph instance

        Raises:
            ValueError if graph contains cycles
        """
        graph = TaskGraph(
            task_id=self.task_id,
            created_at=self.created_at,
            nodes=self.nodes,
            edges=self.edges,
            nodes_by_type=self.nodes_by_type,
            iterations=self.iterations
        )

        # Validate DAG
        if not graph.validate_dag():
            raise ValueError("Graph contains cycles, cannot build")

        logger.info(
            f"Built TaskGraph: {len(self.nodes)} nodes, "
            f"{len(self.edges)} edges, {len(self.iterations)} iterations"
        )
        return graph

    def get_node_count(self) -> int:
        """Return number of nodes."""
        return len(self.nodes)

    def get_edge_count(self) -> int:
        """Return number of edges."""
        return len(self.edges)

    def get_type_counts(self) -> Dict[str, int]:
        """Return counts of nodes by type."""
        return {k: len(v) for k, v in self.nodes_by_type.items()}
