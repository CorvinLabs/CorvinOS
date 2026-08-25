"""
ADR-0400: Task Graph Data Structures

Unified execution graph for task monitoring and visualization.
Frozen dataclasses with JSON serialization.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional, Set
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Node:
    """Any event in the execution graph."""
    id: str
    type: str  # "decision", "error", "checkpoint", "context", "metric", "subgoal"
    timestamp: str  # ISO format
    data: Dict[str, Any]  # Type-specific fields

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (JSON-safe)."""
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp,
            "data": self.data
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        """Construct from dictionary."""
        return cls(
            id=data["id"],
            type=data["type"],
            timestamp=data["timestamp"],
            data=data["data"]
        )


@dataclass(frozen=True)
class Edge:
    """Relationship between nodes."""
    from_id: str
    to_id: str
    edge_type: str  # "hard_dependency", "soft_dependency", "data_flow", "temporal"
    label: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (JSON-safe)."""
        return {
            "from_id": self.from_id,
            "to_id": self.to_id,
            "edge_type": self.edge_type,
            "label": self.label,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Edge":
        """Construct from dictionary."""
        return cls(
            from_id=data["from_id"],
            to_id=data["to_id"],
            edge_type=data["edge_type"],
            label=data["label"],
            metadata=data.get("metadata", {})
        )


@dataclass(frozen=True)
class TaskGraph:
    """Unified execution graph for a task."""
    task_id: str
    created_at: str  # ISO format
    nodes: Dict[str, Node]
    edges: List[Edge]
    nodes_by_type: Dict[str, List[str]]  # type → [node_ids]
    iterations: Dict[int, str]  # iteration_num → checkpoint_id

    def to_json(self) -> str:
        """Serialize to JSON string."""
        data = {
            "task_id": self.task_id,
            "created_at": self.created_at,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
            "nodes_by_type": self.nodes_by_type,
            "iterations": self.iterations
        }
        return json.dumps(data, indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "TaskGraph":
        """Deserialize from JSON string."""
        data = json.loads(json_str)
        nodes = {k: Node.from_dict(v) for k, v in data["nodes"].items()}
        edges = [Edge.from_dict(e) for e in data["edges"]]
        return cls(
            task_id=data["task_id"],
            created_at=data["created_at"],
            nodes=nodes,
            edges=edges,
            nodes_by_type=data["nodes_by_type"],
            iterations={int(k): v for k, v in data["iterations"].items()}
        )

    def validate_dag(self) -> bool:
        """
        Validate that graph is a DAG (no cycles).

        Returns:
            True if DAG (no cycles), False otherwise.
        """
        # Build adjacency list
        adj = {node_id: [] for node_id in self.nodes}
        for edge in self.edges:
            if edge.from_id in adj and edge.to_id in adj:
                adj[edge.from_id].append(edge.to_id)

        # DFS to detect cycles
        visited = set()
        rec_stack = set()

        def has_cycle(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for neighbor in adj.get(node_id, []):
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node_id)
            return False

        for node_id in self.nodes:
            if node_id not in visited:
                if has_cycle(node_id):
                    logger.error(f"Cycle detected in graph starting at {node_id}")
                    return False

        return True

    def get_node(self, node_id: str) -> Optional[Node]:
        """Get node by ID."""
        return self.nodes.get(node_id)

    def get_edges_from(self, node_id: str) -> List[Edge]:
        """Get all edges starting from node."""
        return [e for e in self.edges if e.from_id == node_id]

    def get_edges_to(self, node_id: str) -> List[Edge]:
        """Get all edges ending at node."""
        return [e for e in self.edges if e.to_id == node_id]

    def get_nodes_by_type(self, node_type: str) -> List[Node]:
        """Get all nodes of a given type."""
        node_ids = self.nodes_by_type.get(node_type, [])
        return [self.nodes[nid] for nid in node_ids if nid in self.nodes]

    def get_root_nodes(self) -> List[Node]:
        """Get all nodes with no incoming edges."""
        incoming = {e.to_id for e in self.edges}
        return [n for n_id, n in self.nodes.items() if n_id not in incoming]

    def get_leaf_nodes(self) -> List[Node]:
        """Get all nodes with no outgoing edges."""
        outgoing = {e.from_id for e in self.edges}
        return [n for n_id, n in self.nodes.items() if n_id not in outgoing]

    def get_stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "nodes_by_type": {k: len(v) for k, v in self.nodes_by_type.items()},
            "root_nodes": len(self.get_root_nodes()),
            "leaf_nodes": len(self.get_leaf_nodes()),
            "iterations": len(self.iterations)
        }


class TaskGraphBuilder:
    """Mutable builder for TaskGraph with cycle detection (DAG enforcement).

    Prevents adding edges that would create cycles, fail-closed.
    Maintains invariant: graph is always a DAG.
    """

    def __init__(self, task_id: str):
        """Initialize builder.

        Args:
            task_id: Task identifier for the graph
        """
        self.task_id = task_id
        self.created_at = datetime.utcnow().isoformat()
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []
        self.nodes_by_type: Dict[str, List[str]] = {}
        self.iterations: Dict[int, str] = {}

    def add_node(self, node: Node) -> None:
        """Add node to graph.

        Args:
            node: Node to add
        """
        self.nodes[node.id] = node
        if node.type not in self.nodes_by_type:
            self.nodes_by_type[node.type] = []
        self.nodes_by_type[node.type].append(node.id)
        logger.debug(f"Node added: {node.id} ({node.type})")

    def add_edge(self, edge: Edge) -> bool:
        """Add edge to graph, rejecting if it would create a cycle (fail-closed).

        Args:
            edge: Edge to add
            from_id and to_id must reference existing nodes

        Returns:
            True if edge was added, False if rejected due to cycle

        Raises:
            ValueError: If from_id or to_id does not exist
        """
        # Validate nodes exist
        if edge.from_id not in self.nodes:
            raise ValueError(f"Node {edge.from_id} not found")
        if edge.to_id not in self.nodes:
            raise ValueError(f"Node {edge.to_id} not found")

        # Check if adding this edge would create a cycle
        if self._would_create_cycle(edge.from_id, edge.to_id):
            logger.warning(
                f"Edge {edge.from_id} → {edge.to_id} rejected: would create cycle"
            )
            return False

        # Safe to add
        self.edges.append(edge)
        logger.debug(f"Edge added: {edge.from_id} → {edge.to_id} ({edge.edge_type})")
        return True

    def _would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """Check if adding edge from_id → to_id would create a cycle.

        Uses DFS to detect if to_id is already reachable from from_id.
        If so, adding the edge would create a cycle.

        Args:
            from_id: Source node ID
            to_id: Target node ID

        Returns:
            True if edge would create cycle, False otherwise
        """
        # Build current adjacency list
        adj: Dict[str, List[str]] = {node_id: [] for node_id in self.nodes}
        for edge in self.edges:
            adj[edge.from_id].append(edge.to_id)

        # DFS from to_id to see if we can reach from_id (would create cycle)
        visited = set()

        def can_reach_from_node(current: str, target: str) -> bool:
            """Check if we can reach target from current via existing edges."""
            if current == target:
                return True
            if current in visited:
                return False
            visited.add(current)

            for neighbor in adj.get(current, []):
                if can_reach_from_node(neighbor, target):
                    return True

            return False

        # If to_id can reach from_id, adding from_id → to_id creates cycle
        return can_reach_from_node(to_id, from_id)

    def add_iteration_checkpoint(self, iteration_num: int, checkpoint_id: str) -> None:
        """Record checkpoint for a given iteration.

        Args:
            iteration_num: Iteration number
            checkpoint_id: Node ID of checkpoint
        """
        if checkpoint_id not in self.nodes:
            raise ValueError(f"Node {checkpoint_id} not found")
        self.iterations[iteration_num] = checkpoint_id

    def build(self) -> TaskGraph:
        """Build immutable TaskGraph from accumulated state.

        Returns:
            Frozen TaskGraph

        Raises:
            RuntimeError: If graph is not a valid DAG
        """
        graph = TaskGraph(
            task_id=self.task_id,
            created_at=self.created_at,
            nodes=self.nodes,
            edges=self.edges,
            nodes_by_type=self.nodes_by_type,
            iterations=self.iterations,
        )

        # Final validation
        if not graph.validate_dag():
            raise RuntimeError("Graph is not a valid DAG")

        logger.info(f"TaskGraph built: {len(self.nodes)} nodes, {len(self.edges)} edges")
        return graph
