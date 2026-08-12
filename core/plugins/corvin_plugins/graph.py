"""Plugin DAG (Directed Acyclic Graph) management (ADR-0345).

This module manages the hierarchical structure of plugins, enforces DAG constraints,
and tracks tree integrity through hash chains.

Key responsibilities:
- Register plugins with hierarchy validation
- Detect and prevent cycles
- Link parent-child relationships
- Compute tree hashes (transitive integrity)
- Reset budget cycles
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set
from hashlib import sha256
import json
from datetime import datetime, timezone

from .node import (
    PluginNode,
    BootLayerMismatch,
    PluginCycleDetected,
)

log = logging.getLogger("corvin.plugins.graph")


def now_utc() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class PluginGraph:
    """Complete plugin DAG with audit & delegation support (ADR-0345)."""

    def __init__(self, audit_log=None, quarantine_registry=None):
        """Initialize the plugin graph.

        Args:
            audit_log: Optional audit logger for recording events
            quarantine_registry: Optional quarantine system for isolating broken plugins
        """
        self.nodes: Dict[str, PluginNode] = {}
        self.audit_log = audit_log
        self.quarantine_registry = quarantine_registry

    def register_node(self, node: PluginNode) -> None:
        """Register a plugin (root or sub-plugin).

        Validates boot layer inheritance, checks for cycles, initializes budget tracking.

        Args:
            node: The PluginNode to register

        Raises:
            BootLayerMismatch: If child's boot_layer != parent's
            PluginCycleDetected: If registering would create a cycle
        """
        # Step 1: Validate boot_layer inheritance
        if node.parent_id:
            parent = self.nodes.get(node.parent_id)
            if not parent:
                raise ValueError(f"Parent {node.parent_id} not registered")
            if node.boot_layer != parent.boot_layer:
                raise BootLayerMismatch(
                    f"Child {node.id} boot_layer {node.boot_layer} != "
                    f"parent boot_layer {parent.boot_layer}"
                )

        # Step 2: Check for cycles
        if self._would_create_cycle(node.parent_id, node.id):
            raise PluginCycleDetected(
                f"Registering {node.id} with parent {node.parent_id} creates cycle"
            )

        # Step 3: Initialize budget tracking
        node.current_budget_used = {
            "compliance": 0,
            "high": 0,
            "standard": 0,
            "low": 0,
        }
        node.child_status = {}

        # Step 4: Register
        self.nodes[node.id] = node

        # Step 5: Audit
        if self.audit_log:
            self.audit_log.record({
                "event": "plugin_registered",
                "plugin_id": node.id,
                "parent_id": node.parent_id,
                "boot_layer": node.boot_layer,
                "timestamp": now_utc()
            })

        log.info(f"Registered plugin {node.id} (parent={node.parent_id})")

    def add_child(self, parent_id: str, child_id: str) -> None:
        """Link parent → child in graph.

        Args:
            parent_id: ID of parent plugin
            child_id: ID of child plugin

        Raises:
            ValueError: If either plugin not found
            PluginCycleDetected: If would create cycle
        """
        parent = self.nodes.get(parent_id)
        child = self.nodes.get(child_id)

        if not parent:
            raise ValueError(f"Parent {parent_id} not found")
        if not child:
            raise ValueError(f"Child {child_id} not found")

        if child_id in parent.sub_plugins:
            return  # Already linked

        # Check if child already has the right parent_id
        if child.parent_id == parent_id:
            # Consistent: child's parent_id matches, just add to sub_plugins
            parent.sub_plugins.append(child_id)
            parent.child_status[child_id] = self._create_child_status(child_id, child)
            parent.tree_hash = self._compute_tree_hash(parent_id)
            log.info(f"Linked {parent_id} → {child_id} (already had parent_id)")
            return

        # Different parent_id: would this create a cycle?
        if self._would_create_cycle(parent_id, child_id):
            raise PluginCycleDetected(
                f"Adding {child_id} as child of {parent_id} would create cycle"
            )

        # Only add to graph after cycle check passes
        parent.sub_plugins.append(child_id)
        parent.child_status[child_id] = self._create_child_status(child_id, child)

        # Safe to compute tree hash now (no cycles)
        parent.tree_hash = self._compute_tree_hash(parent_id)

        log.info(f"Linked {parent_id} → {child_id}")

    def remove_child(self, parent_id: str, child_id: str) -> None:
        """Unlink parent → child in graph.

        Args:
            parent_id: ID of parent plugin
            child_id: ID of child plugin
        """
        parent = self.nodes.get(parent_id)
        if not parent or child_id not in parent.sub_plugins:
            return

        parent.sub_plugins.remove(child_id)
        parent.child_status.pop(child_id, None)
        parent.tree_hash = self._compute_tree_hash(parent_id)

        log.info(f"Unlinked {parent_id} ↛ {child_id}")

    def _create_child_status(self, child_id: str, child_node: PluginNode):
        """Create a ChildStatus entry for tracking child health/capacity."""
        from .node import ChildStatus
        return ChildStatus(child_id=child_id, depth=self._compute_depth(child_id))

    def _compute_depth(self, plugin_id: str) -> int:
        """Compute depth of a plugin in the tree (root=0)."""
        node = self.nodes.get(plugin_id)
        if not node or not node.parent_id:
            return 0
        parent_depth = self._compute_depth(node.parent_id)
        return parent_depth + 1

    def _would_create_cycle(
        self, parent_id: Optional[str], child_id: str
    ) -> bool:
        """DFS cycle detection.

        Check if adding parent_id → child_id would create a cycle.
        A cycle exists if child_id can reach parent_id through existing edges
        (because adding parent_id → child_id would complete the cycle).
        """
        if not parent_id:
            return False

        if parent_id == child_id:
            return True  # Self-loop is a cycle

        visited: Set[str] = set()

        def dfs(node_id: str) -> bool:
            """DFS to find if we can reach parent_id from child_id."""
            if node_id in visited:
                return False
            if node_id == parent_id:
                return True  # Found parent! Adding parent->child would create cycle

            visited.add(node_id)
            node = self.nodes.get(node_id)
            if not node:
                return False

            # Try to reach parent through this node's children
            for sub_id in node.sub_plugins:
                if dfs(sub_id):
                    return True

            # Also check parent link (in case of existing parent relationship)
            if node.parent_id and node.parent_id not in visited:
                if dfs(node.parent_id):
                    return True

            return False

        # Start DFS from child_id to see if it can reach parent_id
        return dfs(child_id)

    def _compute_tree_hash(self, plugin_id: str) -> str:
        """Hash of node + all descendants (transitive integrity).

        Args:
            plugin_id: ID of plugin to hash

        Returns:
            Hex-encoded SHA256 hash
        """
        node = self.nodes.get(plugin_id)
        if not node:
            return ""

        # Self hash (node identity + state snapshot)
        self_data = {
            "id": node.id,
            "boot_layer": node.boot_layer,
            "origin": node.origin,
            "capabilities": sorted(node.capabilities),
            "status": node.status,
        }
        self_hash = sha256(
            json.dumps(self_data, sort_keys=True).encode()
        ).hexdigest()

        # Children hashes (recursively)
        children_hashes = []
        for child_id in sorted(node.sub_plugins):
            child_tree_hash = self._compute_tree_hash(child_id)
            children_hashes.append(child_tree_hash)

        # Tree hash = self + all children
        tree_data = {
            "self": self_hash,
            "children": children_hashes,
        }
        tree_hash = sha256(
            json.dumps(tree_data, sort_keys=True).encode()
        ).hexdigest()

        return tree_hash

    def reset_budget_cycle(self, plugin_id: str) -> None:
        """Reset budget counters for new health-check cycle.

        Recursively resets budget for plugin and all descendants.

        Args:
            plugin_id: ID of plugin to reset
        """
        node = self.nodes.get(plugin_id)
        if not node:
            return

        node.current_budget_used = {
            "compliance": 0,
            "high": 0,
            "standard": 0,
            "low": 0,
        }

        # Reset children too
        for child_id in node.sub_plugins:
            self.reset_budget_cycle(child_id)

    def get_node(self, plugin_id: str) -> Optional[PluginNode]:
        """Get a plugin node by ID."""
        return self.nodes.get(plugin_id)

    def get_all_nodes(self) -> Dict[str, PluginNode]:
        """Get all registered nodes."""
        return dict(self.nodes)

    def get_children(self, plugin_id: str) -> List[PluginNode]:
        """Get all direct children of a plugin."""
        node = self.nodes.get(plugin_id)
        if not node:
            return []
        return [self.nodes[cid] for cid in node.sub_plugins if cid in self.nodes]

    def get_descendants(self, plugin_id: str) -> List[PluginNode]:
        """Get all descendants (children, grandchildren, etc.) of a plugin."""
        node = self.nodes.get(plugin_id)
        if not node:
            return []

        descendants = []
        for child_id in node.sub_plugins:
            child_node = self.nodes.get(child_id)
            if child_node:
                descendants.append(child_node)
                descendants.extend(self.get_descendants(child_id))

        return descendants

    def get_root_plugins(self) -> List[PluginNode]:
        """Get all root plugins (no parent)."""
        return [node for node in self.nodes.values() if node.parent_id is None]

    def verify_dag_integrity(self) -> bool:
        """Verify the graph is a valid DAG (no cycles, no orphans)."""
        # Check for cycles
        for node_id in self.nodes:
            if self._has_cycle_from(node_id):
                log.error(f"Cycle detected from {node_id}")
                return False

        # Check for orphans (children pointing to non-existent parents)
        for node_id, node in self.nodes.items():
            if node.parent_id and node.parent_id not in self.nodes:
                log.error(f"Orphan node {node_id}: parent {node.parent_id} not found")
                return False

        return True

    def _has_cycle_from(self, start_id: str) -> bool:
        """Check if there's a cycle reachable from start_id using DFS."""
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(node_id: str) -> bool:
            if node_id in rec_stack:
                return True  # Cycle found
            if node_id in visited:
                return False

            visited.add(node_id)
            rec_stack.add(node_id)

            node = self.nodes.get(node_id)
            if node:
                for child_id in node.sub_plugins:
                    if dfs(child_id):
                        return True

            rec_stack.discard(node_id)
            return False

        return dfs(start_id)

    def to_dict(self) -> Dict:
        """Serialize entire graph to dict."""
        return {
            "nodes": {
                node_id: node.to_dict() for node_id, node in self.nodes.items()
            }
        }
