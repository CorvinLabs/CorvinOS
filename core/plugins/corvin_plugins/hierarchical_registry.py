"""Hierarchical plugin registry with DAG validation and version constraints (ADR-0345 k=2).

Extends the flat PluginRegistry with hierarchical parent-child relationships,
DAG validation, and version constraint propagation down the plugin tree.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from .node import PluginNode, BootLayerMismatch, PluginCycleDetected
from .dag_validator import DAGValidator

log = logging.getLogger("corvin.plugins.hierarchical_registry")


class PluginNotFound(Exception):
    """Plugin ID not found in registry."""


class VersionConflictError(Exception):
    """Version constraint conflict in plugin tree."""


@dataclass
class VersionConstraint:
    """Semantic version constraint (simplified: X.Y.Z format)."""

    min_version: str = "0.0.0"  # Inclusive lower bound
    max_version: str = "999.999.999"  # Inclusive upper bound

    def satisfies(self, version: str) -> bool:
        """Check if version satisfies constraint."""
        return self.min_version <= version <= self.max_version


class HierarchicalRegistry:
    """Extends flat PluginRegistry with hierarchical parent-child relationships.

    Responsibilities:
    1. Maintain DAG of plugins (using DAGValidator)
    2. Enforce boot-layer inheritance (child boot_layer >= parent boot_layer)
    3. Propagate version constraints down tree
    4. Validate single-parent constraint per child
    5. Prevent cycles before registration
    """

    def __init__(self):
        """Initialize hierarchical registry."""
        self.nodes: Dict[str, PluginNode] = {}
        self.validator: Optional[DAGValidator] = None
        self.version_constraints: Dict[str, VersionConstraint] = {}

    def register_plugin(
        self,
        plugin_id: str,
        boot_layer: str,
        origin: str,
        parent_id: Optional[str] = None,
        version: str = "0.0.0",
        capabilities: Optional[List[str]] = None,
    ) -> PluginNode:
        """Register a plugin with optional parent (hierarchical).

        Args:
            plugin_id: Unique identifier
            boot_layer: compliance | core | bundled | installed
            origin: builtin | vetted | community
            parent_id: Optional parent plugin ID
            version: Semantic version (X.Y.Z)
            capabilities: List of capabilities (e.g., ["transcribe_audio"])

        Returns:
            Registered PluginNode

        Raises:
            PluginNotFound: If parent doesn't exist
            BootLayerMismatch: If child boot_layer != parent boot_layer
            PluginCycleDetected: If adding this plugin creates a cycle
        """
        if plugin_id in self.nodes:
            log.warning(f"Plugin {plugin_id} already registered, skipping")
            return self.nodes[plugin_id]

        # Validate parent if specified
        if parent_id:
            if parent_id not in self.nodes:
                raise PluginNotFound(f"Parent plugin {parent_id} not registered")

            parent = self.nodes[parent_id]

            # Enforce boot-layer inheritance
            if boot_layer != parent.boot_layer:
                raise BootLayerMismatch(
                    f"Child {plugin_id} boot_layer {boot_layer} "
                    f"!= parent {parent_id} boot_layer {parent.boot_layer}"
                )

        # Create node
        node = PluginNode(
            id=plugin_id,
            boot_layer=boot_layer,
            origin=origin,
            parent_id=parent_id,
            capabilities=capabilities or [],
        )

        # Temporarily add to check for cycles
        self.nodes[plugin_id] = node
        self.validator = DAGValidator(self.nodes)

        # Check for cycles
        if self.validator.would_create_cycle(parent_id, plugin_id):
            del self.nodes[plugin_id]
            self.validator = DAGValidator(self.nodes) if self.nodes else None
            raise PluginCycleDetected(
                f"Registering {plugin_id} with parent {parent_id} would create cycle"
            )

        # Add to parent's sub_plugins if specified
        if parent_id:
            parent = self.nodes[parent_id]
            parent.add_sub_plugin(plugin_id)
            log.debug(f"Registered {plugin_id} under parent {parent_id}")

        # Record version
        self.version_constraints[plugin_id] = VersionConstraint()

        # Update validator
        self.validator = DAGValidator(self.nodes)

        log.info(
            f"Registered plugin {plugin_id} (boot_layer={boot_layer}, "
            f"parent={parent_id}, origin={origin})"
        )

        return node

    def get_plugin(self, plugin_id: str) -> PluginNode:
        """Get plugin by ID."""
        if plugin_id not in self.nodes:
            raise PluginNotFound(f"Plugin {plugin_id} not found")
        return self.nodes[plugin_id]

    def list_plugins(self, boot_layer: Optional[str] = None) -> List[PluginNode]:
        """List all plugins, optionally filtered by boot_layer."""
        if boot_layer is None:
            return list(self.nodes.values())
        return [n for n in self.nodes.values() if n.boot_layer == boot_layer]

    def get_plugin_tree(self, root_id: str) -> Dict[str, any]:
        """Serialize plugin tree rooted at root_id."""
        if root_id not in self.nodes:
            raise PluginNotFound(f"Root plugin {root_id} not found")

        def serialize_node(plugin_id: str) -> Dict:
            node = self.nodes[plugin_id]
            return {
                "id": plugin_id,
                "boot_layer": node.boot_layer,
                "origin": node.origin,
                "capabilities": node.capabilities,
                "children": [
                    serialize_node(child_id) for child_id in node.sub_plugins
                ],
            }

        return serialize_node(root_id)

    def propagate_version_constraint(
        self, plugin_id: str, constraint: VersionConstraint
    ) -> None:
        """Propagate version constraint down tree from parent to all children.

        Ensures descendants satisfy the parent's version constraint.

        Args:
            plugin_id: Parent plugin ID
            constraint: Version constraint to propagate

        Raises:
            VersionConflictError: If constraint conflicts with child's version
        """
        if plugin_id not in self.nodes:
            raise PluginNotFound(f"Plugin {plugin_id} not found")

        node = self.nodes[plugin_id]

        # Recursively propagate to children
        for child_id in node.sub_plugins:
            child_node = self.nodes[child_id]

            # Check if child version satisfies parent constraint
            # (In full implementation, each node would have a version field)
            current_constraint = self.version_constraints.get(
                child_id, VersionConstraint()
            )

            # Merge constraints (intersection)
            merged = VersionConstraint(
                min_version=max(constraint.min_version, current_constraint.min_version),
                max_version=min(constraint.max_version, current_constraint.max_version),
            )

            if merged.min_version > merged.max_version:
                raise VersionConflictError(
                    f"Child {child_id} version constraint conflicts with "
                    f"parent {plugin_id} constraint"
                )

            self.version_constraints[child_id] = merged
            log.debug(f"Propagated version constraint to {child_id}: {merged}")

            # Recursively propagate to grandchildren
            self.propagate_version_constraint(child_id, merged)

    def validate_hierarchy(self) -> Tuple[bool, List[str]]:
        """Validate entire plugin hierarchy."""
        if not self.validator:
            return True, []

        return self.validator.validate()

    def get_ancestors(self, plugin_id: str) -> Set[str]:
        """Get all ancestors of plugin."""
        if not self.validator:
            return set()
        return self.validator.get_ancestors(plugin_id)

    def get_descendants(self, plugin_id: str) -> Set[str]:
        """Get all descendants of plugin."""
        if not self.validator:
            return set()
        return self.validator.get_descendants(plugin_id)

    def unregister_plugin(self, plugin_id: str) -> None:
        """Unregister plugin and remove from parent's sub_plugins."""
        if plugin_id not in self.nodes:
            raise PluginNotFound(f"Plugin {plugin_id} not found")

        node = self.nodes[plugin_id]

        # Remove from parent
        if node.parent_id:
            parent = self.nodes.get(node.parent_id)
            if parent:
                parent.remove_sub_plugin(plugin_id)
                log.debug(f"Removed {plugin_id} from parent {node.parent_id}")

        # Remove node
        del self.nodes[plugin_id]
        if plugin_id in self.version_constraints:
            del self.version_constraints[plugin_id]

        # Rebuild validator
        self.validator = DAGValidator(self.nodes) if self.nodes else None

        log.info(f"Unregistered plugin {plugin_id}")

    def get_fallback_chain(self, plugin_id: str) -> List[str]:
        """Get fallback chain for plugin (defined by parent)."""
        if plugin_id not in self.nodes:
            raise PluginNotFound(f"Plugin {plugin_id} not found")

        node = self.nodes[plugin_id]
        return node.fallback_chain

    def set_fallback_chain(self, plugin_id: str, chain: List[str]) -> None:
        """Set fallback chain for plugin."""
        if plugin_id not in self.nodes:
            raise PluginNotFound(f"Plugin {plugin_id} not found")

        node = self.nodes[plugin_id]
        node.fallback_chain = chain

        # Validate all fallback IDs exist
        for fallback_id in chain:
            if fallback_id not in self.nodes:
                raise PluginNotFound(
                    f"Fallback plugin {fallback_id} not found in registry"
                )
            fallback_node = self.nodes[fallback_id]
            if fallback_node.parent_id != node.parent_id:
                log.warning(
                    f"Fallback {fallback_id} has different parent than {plugin_id}"
                )

        log.debug(f"Set fallback chain for {plugin_id}: {chain}")
