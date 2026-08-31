"""
ADR-0400: Graph Query API

Efficient queries on TaskGraph: reachability, critical path, impact analysis.
All queries guaranteed < 100ms for 100-node graphs.
"""

from typing import Set, List, Dict, Any, Optional, Tuple
from collections import deque, defaultdict
import logging
from .task_graph import TaskGraph, Node, Edge

logger = logging.getLogger(__name__)


class GraphQueries:
    """
    Query API for TaskGraph analysis.

    Provides: reachability, critical path, impact analysis, etc.
    """

    @staticmethod
    def reachability(graph: TaskGraph, node_id: str) -> Set[str]:
        """
        Get all nodes reachable from given node via BFS.

        Args:
            graph: TaskGraph to query
            node_id: Starting node ID

        Returns:
            Set of all reachable node IDs

        Time complexity: O(V + E) where V = nodes, E = edges
        """
        if node_id not in graph.nodes:
            logger.warning(f"Node {node_id} not found in graph")
            return set()

        visited = set()
        queue = deque([node_id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue

            visited.add(current)

            # Add all outgoing neighbors
            for edge in graph.get_edges_from(current):
                if edge.to_id not in visited:
                    queue.append(edge.to_id)

        reachable = visited - {node_id}  # Exclude starting node
        logger.debug(f"Reachability from {node_id}: {len(reachable)} nodes")
        return reachable

    @staticmethod
    def predecessors(graph: TaskGraph, node_id: str) -> Set[str]:
        """
        Get all nodes that can reach given node.

        Reverse of reachability (walk backwards).

        Args:
            graph: TaskGraph to query
            node_id: Target node ID

        Returns:
            Set of all predecessor node IDs
        """
        if node_id not in graph.nodes:
            logger.warning(f"Node {node_id} not found in graph")
            return set()

        visited = set()
        queue = deque([node_id])

        while queue:
            current = queue.popleft()
            if current in visited:
                continue

            visited.add(current)

            # Add all incoming neighbors
            for edge in graph.get_edges_to(current):
                if edge.from_id not in visited:
                    queue.append(edge.from_id)

        predecessors = visited - {node_id}  # Exclude target node
        logger.debug(f"Predecessors of {node_id}: {len(predecessors)} nodes")
        return predecessors

    @staticmethod
    def critical_path(graph: TaskGraph) -> List[str]:
        """
        Find longest path (critical path) in DAG.

        Uses topological sort + dynamic programming.
        Longest path represents critical execution sequence.

        Args:
            graph: TaskGraph to query

        Returns:
            List of node IDs in longest path

        Time complexity: O(V + E)
        """
        if not graph.nodes:
            return []

        # Topological sort using DFS
        visited = set()
        rec_stack = set()
        topo_order = []

        def dfs(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)

            for edge in graph.get_edges_from(node_id):
                if edge.to_id not in visited:
                    if not dfs(edge.to_id):
                        return False
                elif edge.to_id in rec_stack:
                    return False

            rec_stack.remove(node_id)
            topo_order.append(node_id)
            return True

        for node_id in graph.nodes:
            if node_id not in visited:
                if not dfs(node_id):
                    logger.warning("Graph contains cycles, cannot compute critical path")
                    return []

        topo_order.reverse()

        # Dynamic programming to find longest path
        distances = {node_id: 0 for node_id in graph.nodes}
        parents = {node_id: None for node_id in graph.nodes}

        for node_id in topo_order:
            for edge in graph.get_edges_from(node_id):
                to_id = edge.to_id
                new_dist = distances[node_id] + 1
                if new_dist > distances[to_id]:
                    distances[to_id] = new_dist
                    parents[to_id] = node_id

        # Find node with max distance (end of critical path)
        max_dist_node = max(distances, key=distances.get)

        # Reconstruct path by following parents
        path = []
        current = max_dist_node
        while current is not None:
            path.append(current)
            current = parents[current]

        path.reverse()
        logger.debug(f"Critical path length: {len(path)} nodes")
        return path

    @staticmethod
    def impact_analysis(graph: TaskGraph, node_id: str) -> Set[str]:
        """
        Find all nodes affected by changes to given node.

        Equivalent to reachability (downstream impact).

        Args:
            graph: TaskGraph to query
            node_id: Node whose impact to analyze

        Returns:
            Set of node IDs affected by this node
        """
        return GraphQueries.reachability(graph, node_id)

    @staticmethod
    def dependency_graph(graph: TaskGraph) -> Dict[str, List[str]]:
        """
        Get dependency graph (adjacency list).

        Args:
            graph: TaskGraph to query

        Returns:
            Dict mapping node IDs to list of direct dependencies
        """
        deps = defaultdict(list)
        for edge in graph.edges:
            if edge.edge_type in ("hard_dependency", "soft_dependency"):
                deps[edge.from_id].append(edge.to_id)
        return dict(deps)

    @staticmethod
    def find_blocking_nodes(graph: TaskGraph) -> List[Tuple[str, int]]:
        """
        Find nodes with high fan-out (potentially blocking).

        Returns nodes that many other nodes depend on.

        Args:
            graph: TaskGraph to query

        Returns:
            List of (node_id, fan_out_count) sorted by fan_out descending
        """
        fan_out = defaultdict(int)
        for edge in graph.edges:
            if edge.edge_type in ("hard_dependency", "soft_dependency"):
                fan_out[edge.from_id] += 1

        blocking = sorted(fan_out.items(), key=lambda x: x[1], reverse=True)
        logger.debug(f"Found {len(blocking)} blocking nodes")
        return blocking

    @staticmethod
    def has_cycle(graph: TaskGraph) -> bool:
        """
        Check if graph contains any cycles (fast boolean check).

        Uses DFS with recursion stack to detect cycles.
        Fails-closed: returns True if any cycle is found.

        Args:
            graph: TaskGraph to query

        Returns:
            True if graph contains cycles, False if DAG

        Time complexity: O(V + E)
        """
        visited = set()
        rec_stack = set()

        def dfs(node_id: str) -> bool:
            """DFS helper: returns True if cycle found from this node."""
            visited.add(node_id)
            rec_stack.add(node_id)

            for edge in graph.get_edges_from(node_id):
                to_id = edge.to_id
                if to_id not in visited:
                    if dfs(to_id):
                        return True
                elif to_id in rec_stack:
                    # Back edge: cycle detected
                    logger.warning(f"Cycle detected: {node_id} → {to_id}")
                    return True

            rec_stack.remove(node_id)
            return False

        # Check all nodes
        for node_id in graph.nodes:
            if node_id not in visited:
                if dfs(node_id):
                    logger.debug("Graph contains cycles")
                    return True

        logger.debug("Graph is acyclic (DAG)")
        return False

    @staticmethod
    def find_cycles(graph: TaskGraph) -> List[List[str]]:
        """
        Find all cycles in graph (if any).

        For DAG, returns empty list.

        Args:
            graph: TaskGraph to query

        Returns:
            List of cycles (each cycle is a list of node IDs)
        """
        cycles = []
        visited = set()
        rec_stack = set()
        path_stack = []

        def dfs(node_id: str):
            visited.add(node_id)
            rec_stack.add(node_id)
            path_stack.append(node_id)

            for edge in graph.get_edges_from(node_id):
                to_id = edge.to_id
                if to_id not in visited:
                    dfs(to_id)
                elif to_id in rec_stack:
                    # Found cycle
                    cycle_start = path_stack.index(to_id)
                    cycle = path_stack[cycle_start:] + [to_id]
                    cycles.append(cycle)

            path_stack.pop()
            rec_stack.remove(node_id)

        for node_id in graph.nodes:
            if node_id not in visited:
                dfs(node_id)

        logger.debug(f"Found {len(cycles)} cycles in graph")
        return cycles

    @staticmethod
    def get_path(graph: TaskGraph, from_id: str, to_id: str) -> Optional[List[str]]:
        """
        Find shortest path between two nodes (BFS).

        Args:
            graph: TaskGraph to query
            from_id: Starting node ID
            to_id: Target node ID

        Returns:
            List of node IDs in path, or None if unreachable
        """
        if from_id not in graph.nodes or to_id not in graph.nodes:
            return None

        if from_id == to_id:
            return [from_id]

        visited = set()
        queue = deque([(from_id, [from_id])])

        while queue:
            current, path = queue.popleft()
            if current in visited:
                continue
            visited.add(current)

            for edge in graph.get_edges_from(current):
                to_node = edge.to_id
                if to_node == to_id:
                    return path + [to_node]
                if to_node not in visited:
                    queue.append((to_node, path + [to_node]))

        logger.debug(f"No path found from {from_id} to {to_id}")
        return None

    @staticmethod
    def get_stats_by_type(graph: TaskGraph) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for each node type.

        Args:
            graph: TaskGraph to query

        Returns:
            Dict mapping node type to stats
        """
        stats = {}
        for node_type, node_ids in graph.nodes_by_type.items():
            edges_from = sum(
                1 for edge in graph.edges
                if edge.from_id in node_ids
            )
            edges_to = sum(
                1 for edge in graph.edges
                if edge.to_id in node_ids
            )
            stats[node_type] = {
                "count": len(node_ids),
                "edges_from": edges_from,
                "edges_to": edges_to,
                "avg_edges": (edges_from + edges_to) / len(node_ids) if node_ids else 0
            }
        return stats

    @staticmethod
    def get_timeline(graph: TaskGraph) -> List[Tuple[str, str, str]]:
        """
        Get timeline of events (sorted by timestamp).

        Args:
            graph: TaskGraph to query

        Returns:
            List of (timestamp, node_id, node_type) sorted chronologically
        """
        timeline = [
            (node.timestamp, node.id, node.type)
            for node in graph.nodes.values()
        ]
        timeline.sort(key=lambda x: x[0])
        return timeline

    @staticmethod
    def find_node_by_data(
        graph: TaskGraph,
        key: str,
        value: Any
    ) -> List[Node]:
        """
        Find nodes where data[key] == value.

        Args:
            graph: TaskGraph to query
            key: Data key to search
            value: Value to match

        Returns:
            List of matching nodes
        """
        results = []
        for node in graph.nodes.values():
            if node.data.get(key) == value:
                results.append(node)
        return results
