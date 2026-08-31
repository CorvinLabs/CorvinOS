"""DAG validation for recursive plugin architecture (ADR-0345).

Provides cycle detection, topological sorting, and graph validation for hierarchical
plugin trees. Ensures the plugin graph remains acyclic and respects boot-layer
constraints.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple
from collections import deque

from .node import PluginNode, PluginCycleDetected, BootLayerMismatch

log = logging.getLogger("corvin.plugins.dag_validator")


class DAGValidator:
    """Validates plugin DAG structure and integrity.

    Ensures:
    1. No cycles (acyclic property)
    2. Boot-layer inheritance (children match parent)
    3. Single parent per child
    4. All parent/child references exist
    5. Transitive closure validity
    """

    def __init__(self, nodes: Dict[str, PluginNode]):
        """Initialize validator with plugin nodes.

        Args:
            nodes: Dict mapping plugin_id -> PluginNode
        """
        self.nodes = nodes
        self._cycle_cache: Optional[Tuple[bool, List[str]]] = None

    def validate(self) -> Tuple[bool, List[str]]:
        """Perform full DAG validation.

        Returns:
            Tuple of (is_valid, error_messages)
            - is_valid: True if DAG is valid
            - error_messages: List of error descriptions (empty if valid)
        """
        errors: List[str] = []

        # Check for cycles
        has_cycle, cycle_path = self.detect_cycle()
        if has_cycle:
            errors.append(f"Cycle detected: {' -> '.join(cycle_path)}")

        # Check boot-layer inheritance
        for plugin_id, node in self.nodes.items():
            if node.parent_id:
                parent = self.nodes.get(node.parent_id)
                if not parent:
                    errors.append(
                        f"Plugin {plugin_id}: parent {node.parent_id} not found"
                    )
                elif parent.boot_layer != node.boot_layer:
                    errors.append(
                        f"Plugin {plugin_id}: boot_layer {node.boot_layer} "
                        f"!= parent boot_layer {parent.boot_layer}"
                    )

        # Check single parent constraint
        for plugin_id, node in self.nodes.items():
            if node.parent_id and plugin_id not in self.nodes.get(
                node.parent_id, PluginNode("_dummy", "bundled", "builtin")
            ).sub_plugins:
                errors.append(
                    f"Plugin {plugin_id}: parent_id set but not in parent's sub_plugins"
                )

        # Check parent-child consistency
        for plugin_id, node in self.nodes.items():
            for child_id in node.sub_plugins:
                if child_id not in self.nodes:
                    errors.append(
                        f"Plugin {plugin_id}: child {child_id} not found in nodes"
                    )
                else:
                    child = self.nodes[child_id]
                    if child.parent_id != plugin_id:
                        errors.append(
                            f"Plugin {plugin_id}: child {child_id}.parent_id "
                            f"is {child.parent_id}, not {plugin_id}"
                        )

        # Check fallback chain validity
        for plugin_id, node in self.nodes.items():
            for fallback_id in node.fallback_chain:
                if fallback_id not in self.nodes:
                    errors.append(
                        f"Plugin {plugin_id}: fallback {fallback_id} not found"
                    )
                elif fallback_id not in node.sub_plugins:
                    errors.append(
                        f"Plugin {plugin_id}: fallback {fallback_id} not in sub_plugins"
                    )

        is_valid = len(errors) == 0
        if is_valid:
            log.debug(f"DAG validation passed for {len(self.nodes)} nodes")
        else:
            log.warning(f"DAG validation failed: {len(errors)} errors")

        return is_valid, errors

    def detect_cycle(self) -> Tuple[bool, List[str]]:
        """Detect cycles in the plugin DAG using DFS.

        Returns:
            Tuple of (has_cycle, cycle_path)
            - has_cycle: True if a cycle exists
            - cycle_path: List of plugin IDs forming the cycle (empty if no cycle)

        Algorithm:
            White-Gray-Black DFS where:
            - White: unvisited
            - Gray: visiting (currently in recursion stack)
            - Black: visited (all descendants processed)

            A back edge (edge to a gray node) indicates a cycle.
        """
        if self._cycle_cache is not None:
            return self._cycle_cache

        color: Dict[str, str] = {node_id: "white" for node_id in self.nodes}
        parent: Dict[str, Optional[str]] = {node_id: None for node_id in self.nodes}
        cycle_path: List[str] = []

        def dfs(node_id: str, path: List[str]) -> bool:
            """DFS helper to detect cycles.

            Args:
                node_id: Current plugin ID
                path: Current path from root to node_id

            Returns:
                True if a cycle is found
            """
            nonlocal cycle_path

            color[node_id] = "gray"
            path.append(node_id)

            node = self.nodes.get(node_id)
            if not node:
                return False

            # Check all children
            for child_id in node.sub_plugins:
                if child_id not in self.nodes:
                    continue

                if color[child_id] == "gray":
                    # Back edge found — cycle detected
                    # Extract cycle from path
                    cycle_start_idx = path.index(child_id)
                    cycle_path = path[cycle_start_idx:] + [child_id]
                    return True

                if color[child_id] == "white":
                    if dfs(child_id, path.copy()):
                        return True

            color[node_id] = "black"
            return False

        # Run DFS from all unvisited nodes
        for node_id in self.nodes:
            if color[node_id] == "white":
                if dfs(node_id, []):
                    self._cycle_cache = (True, cycle_path)
                    return True, cycle_path

        self._cycle_cache = (False, [])
        return False, []

    def would_create_cycle(
        self, parent_id: Optional[str], child_id: str
    ) -> bool:
        """Check if adding parent_id -> child_id edge would create a cycle.

        Used during plugin registration to validate new relationships before
        they're added to the graph.

        Args:
            parent_id: The proposed parent plugin ID (or None for root)
            child_id: The child plugin ID

        Returns:
            True if adding this edge would create a cycle
        """
        if not parent_id:
            return False

        if parent_id not in self.nodes or child_id not in self.nodes:
            return False

        # Temporarily add the edge and check for cycles
        child_node = self.nodes[child_id]
        original_parent = child_node.parent_id

        # Simulate adding the edge
        child_node.parent_id = parent_id
        parent_node = self.nodes.get(parent_id)
        if parent_node and child_id not in parent_node.sub_plugins:
            parent_node.sub_plugins.append(child_id)

        # Check for cycle
        has_cycle, _ = self.detect_cycle()

        # Restore original state
        child_node.parent_id = original_parent
        if parent_node:
            if child_id in parent_node.sub_plugins:
                parent_node.sub_plugins.remove(child_id)

        return has_cycle

    def topological_sort(self) -> Tuple[bool, List[str]]:
        """Perform topological sort on plugin DAG.

        Returns:
            Tuple of (success, sorted_ids)
            - success: True if DAG is acyclic and sort succeeded
            - sorted_ids: List of plugin IDs in topological order (parents before children)
                         Empty if DAG has cycles

        Algorithm:
            Kahn's algorithm (BFS-based) with in-degree counting.
            Processes nodes with no dependencies first.
        """
        has_cycle, _ = self.detect_cycle()
        if has_cycle:
            log.error("Cannot topologically sort DAG with cycles")
            return False, []

        # Compute in-degrees
        in_degree: Dict[str, int] = {node_id: 0 for node_id in self.nodes}

        for node_id, node in self.nodes.items():
            for child_id in node.sub_plugins:
                if child_id in in_degree:
                    in_degree[child_id] += 1

        # Start with nodes that have no parents (in-degree = 0)
        queue = deque([node_id for node_id in self.nodes if in_degree[node_id] == 0])

        sorted_ids: List[str] = []

        while queue:
            node_id = queue.popleft()
            sorted_ids.append(node_id)

            node = self.nodes[node_id]
            for child_id in node.sub_plugins:
                if child_id in in_degree:
                    in_degree[child_id] -= 1
                    if in_degree[child_id] == 0:
                        queue.append(child_id)

        if len(sorted_ids) != len(self.nodes):
            log.error("Topological sort did not include all nodes (cycle exists)")
            return False, []

        log.debug(f"Topological sort succeeded: {len(sorted_ids)} nodes")
        return True, sorted_ids

    def get_ancestors(self, node_id: str) -> Set[str]:
        """Get all ancestor node IDs (nodes on path to root).

        Args:
            node_id: Plugin ID

        Returns:
            Set of ancestor plugin IDs (not including node_id itself)
        """
        ancestors: Set[str] = set()
        current = node_id
        visited: Set[str] = set()

        while current:
            if current in visited:
                # Cycle in parent chain (shouldn't happen if validated)
                log.warning(f"Cycle in parent chain starting from {node_id}")
                break

            visited.add(current)
            node = self.nodes.get(current)
            if not node or not node.parent_id:
                break

            ancestors.add(node.parent_id)
            current = node.parent_id

        return ancestors

    def get_descendants(self, node_id: str) -> Set[str]:
        """Get all descendant node IDs (all nodes under this one in tree).

        Args:
            node_id: Plugin ID

        Returns:
            Set of descendant plugin IDs (not including node_id itself)
        """
        descendants: Set[str] = set()
        stack = [node_id]
        visited: Set[str] = set()

        while stack:
            current = stack.pop()
            if current in visited:
                continue

            visited.add(current)
            node = self.nodes.get(current)
            if not node:
                continue

            for child_id in node.sub_plugins:
                if child_id not in descendants:
                    descendants.add(child_id)
                    stack.append(child_id)

        return descendants

    def get_tree_depth(self, node_id: str) -> int:
        """Get depth of node in tree (roots = 0).

        Args:
            node_id: Plugin ID

        Returns:
            Depth from root (0 = no parent, 1 = parent has no parent, etc.)
        """
        depth = 0
        current = node_id

        while current:
            node = self.nodes.get(current)
            if not node or not node.parent_id:
                break
            current = node.parent_id
            depth += 1

        return depth

    def get_tree_width(self, node_id: str) -> int:
        """Get maximum width of subtree rooted at node (max children at any level).

        Args:
            node_id: Plugin ID

        Returns:
            Maximum width of any level in the subtree
        """
        current_level = [node_id]
        max_width = 0

        while current_level:
            max_width = max(max_width, len(current_level))
            next_level = []

            for node_id in current_level:
                node = self.nodes.get(node_id)
                if node:
                    next_level.extend(node.sub_plugins)

            current_level = next_level

        return max_width

    def get_roots(self) -> List[str]:
        """Get all root plugins (those with no parent).

        Returns:
            List of root plugin IDs
        """
        return [
            node_id for node_id, node in self.nodes.items() if not node.parent_id
        ]

    def get_leaves(self) -> List[str]:
        """Get all leaf plugins (those with no children).

        Returns:
            List of leaf plugin IDs
        """
        return [node_id for node_id, node in self.nodes.items() if not node.sub_plugins]

    def get_path(self, from_id: str, to_id: str) -> Optional[List[str]]:
        """Find path between two nodes (if one exists).

        Uses BFS to find shortest path from from_id to to_id.

        Args:
            from_id: Starting plugin ID
            to_id: Target plugin ID

        Returns:
            List of plugin IDs forming path (including both endpoints),
            or None if no path exists
        """
        if from_id not in self.nodes or to_id not in self.nodes:
            return None

        if from_id == to_id:
            return [from_id]

        visited: Set[str] = set()
        queue = deque([(from_id, [from_id])])

        while queue:
            current, path = queue.popleft()

            if current in visited:
                continue

            visited.add(current)
            node = self.nodes.get(current)
            if not node:
                continue

            for child_id in node.sub_plugins:
                if child_id == to_id:
                    return path + [child_id]

                if child_id not in visited:
                    queue.append((child_id, path + [child_id]))

        return None

    def compute_tree_hash(self, node_id: str) -> str:
        """Compute SHA256 hash of plugin node and all descendants.

        Hash includes:
        1. Node identity (id, boot_layer, origin)
        2. All descendants' tree hashes (sorted for consistency)

        Used for integrity verification (GDPR Art. 30/32).

        Args:
            node_id: Plugin ID

        Returns:
            Hex-encoded SHA256 hash
        """
        import hashlib
        import json

        node = self.nodes.get(node_id)
        if not node:
            return ""

        # Self hash (immutable identity)
        self_data = {
            "id": node.id,
            "boot_layer": node.boot_layer,
            "origin": node.origin,
        }

        # Children hashes
        children_hashes = []
        for child_id in sorted(node.sub_plugins):
            child_tree_hash = self.compute_tree_hash(child_id)
            children_hashes.append(child_tree_hash)

        # Combine
        combined_data = {
            "self": self_data,
            "children": children_hashes,
        }

        tree_hash = hashlib.sha256(
            json.dumps(combined_data, sort_keys=True).encode()
        ).hexdigest()

        return tree_hash
