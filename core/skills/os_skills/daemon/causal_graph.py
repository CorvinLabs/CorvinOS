"""CausalGraph — models DataSource → Skill → Outcome relationships."""

from dataclasses import dataclass, field
from typing import Dict, Set, List, Optional, Tuple
from datetime import datetime


@dataclass(frozen=True)
class CausalNode:
    """A node in the causal graph (source or skill)."""
    node_id: str
    node_type: str  # "source" or "skill"
    description: str = ""
    added_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass(frozen=True)
class CausalEdge:
    """An edge in the causal graph (dependency)."""
    from_node: str
    to_node: str
    edge_type: str  # "uses" (source→skill) or "produces" (skill→outcome)
    weight: float = 0.5  # Initial influence weight
    added_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class CausalGraph:
    """
    Models the causal relationships: DataSource → Skill → Outcome.

    A DAG (directed acyclic graph) with three node types:
    - Sources: data sources (memory:tier2, rag, files, etc.)
    - Skills: generated skills
    - Outcomes: measured results (quality, latency, etc.)

    Edges represent dependencies and influence.

    Used to:
    1. Understand which sources matter most (influence computation)
    2. Prioritize skill regeneration (sources with high influence first)
    3. Detect cycles (assert the graph is acyclic)
    """

    def __init__(self):
        """Initialize graph."""
        self.nodes: Dict[str, CausalNode] = {}  # {node_id: CausalNode}
        self.edges: Dict[Tuple[str, str], CausalEdge] = {}  # {(from, to): CausalEdge}
        self.node_types: Dict[str, Set[str]] = {
            "source": set(),
            "skill": set(),
            "outcome": set(),
        }

    def add_node(self, node_id: str, node_type: str, description: str = "") -> None:
        """Add a node to the graph."""
        if node_type not in ["source", "skill", "outcome"]:
            raise ValueError(f"Invalid node type: {node_type}")

        node = CausalNode(node_id=node_id, node_type=node_type, description=description)
        self.nodes[node_id] = node
        self.node_types[node_type].add(node_id)

    def add_edge(
        self, from_node: str, to_node: str, edge_type: str, weight: float = 0.5
    ) -> None:
        """Add an edge (dependency) to the graph."""
        if from_node not in self.nodes:
            raise ValueError(f"From node {from_node} not in graph")
        if to_node not in self.nodes:
            raise ValueError(f"To node {to_node} not in graph")

        edge = CausalEdge(
            from_node=from_node,
            to_node=to_node,
            edge_type=edge_type,
            weight=weight,
        )
        self.edges[(from_node, to_node)] = edge

        # Check for cycles after adding edge
        if self._has_cycle():
            del self.edges[(from_node, to_node)]
            raise ValueError(f"Adding edge {from_node}→{to_node} creates a cycle")

    def get_node(self, node_id: str) -> Optional[CausalNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def get_outgoing_edges(self, node_id: str) -> List[CausalEdge]:
        """Get all outgoing edges from a node."""
        return [
            edge for (from_id, _), edge in self.edges.items()
            if from_id == node_id
        ]

    def get_incoming_edges(self, node_id: str) -> List[CausalEdge]:
        """Get all incoming edges to a node."""
        return [
            edge for (_, to_id), edge in self.edges.items()
            if to_id == node_id
        ]

    def compute_influence(self, source_id: str) -> float:
        """
        Compute the influence of a source on outcomes.

        Influence = sum of weights on all paths source → ... → outcome.
        Uses simple path-following (not full graph algorithm).

        Args:
            source_id: The source to measure

        Returns:
            Influence score (0–1)
        """
        if source_id not in self.nodes:
            return 0.0
        if self.nodes[source_id].node_type != "source":
            return 0.0

        # BFS: find all paths from source to outcomes
        visited = set()
        total_influence = 0.0

        def dfs(node_id: str, path_weight: float) -> None:
            nonlocal total_influence

            if node_id in visited:
                return
            visited.add(node_id)

            if self.nodes[node_id].node_type == "outcome":
                total_influence += path_weight
                return

            for edge in self.get_outgoing_edges(node_id):
                dfs(edge.to_node, path_weight * edge.weight)

        dfs(source_id, 1.0)
        return min(total_influence, 1.0)  # Cap at 1.0

    def get_top_sources_by_influence(self, k: int = 5) -> List[Tuple[str, float]]:
        """Get top K sources by influence."""
        influences = [
            (source_id, self.compute_influence(source_id))
            for source_id in self.node_types["source"]
        ]
        influences.sort(key=lambda x: x[1], reverse=True)
        return influences[:k]

    def detect_new_nodes(self, current_sources: Set[str], current_skills: Set[str]) -> Tuple[Set[str], Set[str]]:
        """
        Detect new sources or skills (not yet in the graph).

        Args:
            current_sources: All known source IDs
            current_skills: All known skill IDs

        Returns:
            (new_sources, new_skills)
        """
        existing_sources = self.node_types["source"]
        existing_skills = self.node_types["skill"]

        new_sources = current_sources - existing_sources
        new_skills = current_skills - existing_skills

        return new_sources, new_skills

    def _has_cycle(self) -> bool:
        """Check if the graph has cycles (using DFS)."""
        visited = set()
        rec_stack = set()

        def has_cycle_dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for edge in self.get_outgoing_edges(node_id):
                if edge.to_node not in visited:
                    if has_cycle_dfs(edge.to_node):
                        return True
                elif edge.to_node in rec_stack:
                    return True

            rec_stack.remove(node_id)
            return False

        for node_id in self.nodes:
            if node_id not in visited:
                if has_cycle_dfs(node_id):
                    return True

        return False

    def validate_dag(self) -> bool:
        """Validate that the graph is a DAG (no cycles)."""
        return not self._has_cycle()

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self.nodes = {}
        self.edges = {}
        self.node_types = {"source": set(), "skill": set(), "outcome": set()}

    def to_dict(self) -> Dict:
        """Convert to serializable dict."""
        return {
            "nodes": {
                node_id: {
                    "node_type": node.node_type,
                    "description": node.description,
                    "added_at": node.added_at,
                }
                for node_id, node in self.nodes.items()
            },
            "edges": [
                {
                    "from": edge.from_node,
                    "to": edge.to_node,
                    "type": edge.edge_type,
                    "weight": edge.weight,
                    "added_at": edge.added_at,
                }
                for edge in self.edges.values()
            ],
        }
