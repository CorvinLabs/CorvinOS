"""
Phase 3: Plugin Dependency Resolution

Validates plugin dependencies form a DAG (no cycles).
Resolves installation order via topological sort.
"""

import logging
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass

from .canonical_manifest import CanonicalManifestManager, PluginEntry

logger = logging.getLogger(__name__)


@dataclass
class DependencyGraph:
    """Dependency graph for plugins"""
    plugins: Dict[str, PluginEntry]
    edges: Dict[str, Set[str]]  # plugin_id → set of dependencies
    reverse_edges: Dict[str, Set[str]]  # plugin_id → set of dependents


class DependencyResolver:
    """
    Resolves plugin dependencies.

    Detects circular dependencies (fail-closed).
    Computes installation order (topological sort).
    """

    def __init__(self, manifest_manager: Optional[CanonicalManifestManager] = None):
        """
        Initialize resolver.

        Args:
            manifest_manager: CanonicalManifestManager instance
        """
        self.manifest_manager = manifest_manager or CanonicalManifestManager()

    def build_dependency_graph(self) -> DependencyGraph:
        """
        Build dependency graph from canonical manifest.

        Returns: DependencyGraph with edges and reverse edges
        Raises: RuntimeError if graph has cycles
        """
        manifest = self.manifest_manager.load_manifest()

        plugins: Dict[str, PluginEntry] = {p.plugin_id: p for p in manifest.plugins}
        edges: Dict[str, Set[str]] = {}
        reverse_edges: Dict[str, Set[str]] = {}

        # Initialize edges
        for plugin_id in plugins:
            edges[plugin_id] = set()
            reverse_edges[plugin_id] = set()

        # Build edges from plugin dependencies
        for plugin_id, plugin in plugins.items():
            for dep in plugin.dependencies:
                if dep not in plugins:
                    logger.warning(f"Unknown dependency: {plugin_id} depends on {dep}")
                    continue

                edges[plugin_id].add(dep)
                reverse_edges[dep].add(plugin_id)

        # Check for cycles
        if self._has_cycle(edges):
            raise RuntimeError("❌ Circular dependency detected in plugin graph")

        logger.info(f"✅ Built dependency graph: {len(plugins)} plugins, valid DAG")

        return DependencyGraph(
            plugins=plugins,
            edges=edges,
            reverse_edges=reverse_edges,
        )

    def resolve_installation_order(self, plugin_ids: List[str]) -> List[str]:
        """
        Resolve installation order for given plugins.

        Returns plugins in topological order (dependencies first).

        Args:
            plugin_ids: List of plugin IDs to resolve

        Returns: Topologically sorted list of plugin IDs
        Raises: RuntimeError if graph has cycles or plugin not found
        """
        graph = self.build_dependency_graph()

        # Validate all plugins exist
        for plugin_id in plugin_ids:
            if plugin_id not in graph.plugins:
                raise RuntimeError(f"Plugin not found: {plugin_id}")

        # Collect all plugins to install (including transitive dependencies)
        all_to_install = set(plugin_ids)
        to_process = list(plugin_ids)

        while to_process:
            plugin_id = to_process.pop()
            for dep in graph.edges[plugin_id]:
                if dep not in all_to_install:
                    all_to_install.add(dep)
                    to_process.append(dep)

        # Topological sort
        sorted_order = self._topological_sort(all_to_install, graph)

        logger.info(f"✅ Resolved installation order: {len(sorted_order)} plugins")
        return sorted_order

    def resolve_uninstall_order(self, plugin_ids: List[str]) -> List[str]:
        """
        Resolve uninstall order for given plugins.

        Returns plugins in reverse topological order (dependents first).

        Args:
            plugin_ids: List of plugin IDs to resolve

        Returns: Reverse topologically sorted list of plugin IDs
        """
        graph = self.build_dependency_graph()

        # Validate all plugins exist
        for plugin_id in plugin_ids:
            if plugin_id not in graph.plugins:
                raise RuntimeError(f"Plugin not found: {plugin_id}")

        # Collect all plugins to uninstall (including reverse dependencies)
        all_to_uninstall = set(plugin_ids)
        to_process = list(plugin_ids)

        while to_process:
            plugin_id = to_process.pop()
            for dependent in graph.reverse_edges[plugin_id]:
                if dependent not in all_to_uninstall:
                    all_to_uninstall.add(dependent)
                    to_process.append(dependent)

        # Reverse topological sort
        sorted_order = self._topological_sort(all_to_uninstall, graph)
        sorted_order.reverse()  # Reverse for uninstall order

        logger.info(f"✅ Resolved uninstall order: {len(sorted_order)} plugins")
        return sorted_order

    def get_dependencies(self, plugin_id: str) -> Set[str]:
        """Get all transitive dependencies of a plugin"""
        graph = self.build_dependency_graph()

        if plugin_id not in graph.plugins:
            return set()

        visited = set()
        to_process = [plugin_id]

        while to_process:
            current = to_process.pop()
            for dep in graph.edges[current]:
                if dep not in visited:
                    visited.add(dep)
                    to_process.append(dep)

        return visited

    def get_dependents(self, plugin_id: str) -> Set[str]:
        """Get all transitive dependents of a plugin"""
        graph = self.build_dependency_graph()

        if plugin_id not in graph.plugins:
            return set()

        visited = set()
        to_process = [plugin_id]

        while to_process:
            current = to_process.pop()
            for dependent in graph.reverse_edges[current]:
                if dependent not in visited:
                    visited.add(dependent)
                    to_process.append(dependent)

        return visited

    def _has_cycle(self, edges: Dict[str, Set[str]]) -> bool:
        """
        Detect cycles in dependency graph using DFS.

        Returns: True if cycle detected, False otherwise
        """
        visited = set()
        rec_stack = set()

        def dfs(node: str) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for neighbor in edges.get(node, set()):
                if neighbor not in visited:
                    if dfs(neighbor):
                        return True
                elif neighbor in rec_stack:
                    return True

            rec_stack.remove(node)
            return False

        for node in edges:
            if node not in visited:
                if dfs(node):
                    return True

        return False

    def _topological_sort(self, plugin_ids: Set[str], graph: DependencyGraph) -> List[str]:
        """
        Topological sort of plugins.

        Returns plugins in order where dependencies come before dependents.
        """
        in_degree = {p: 0 for p in plugin_ids}

        # Count in-degree (number of dependencies)
        for plugin_id in plugin_ids:
            for dep in graph.edges[plugin_id]:
                if dep in plugin_ids:
                    in_degree[plugin_id] += 1

        # Kahn's algorithm
        queue = [p for p in plugin_ids if in_degree[p] == 0]
        result = []

        while queue:
            node = queue.pop(0)
            result.append(node)

            # For each plugin that depends on this one
            for dependent in graph.reverse_edges[node]:
                if dependent in plugin_ids:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        return result

    def validate_dag(self, plugin_ids: Optional[List[str]] = None) -> Tuple[bool, str]:
        """
        Validate that dependencies form a valid DAG.

        Args:
            plugin_ids: List of plugins to validate (None = all)

        Returns: (is_valid, message)
        """
        try:
            graph = self.build_dependency_graph()

            if plugin_ids:
                # Validate only specified plugins
                for plugin_id in plugin_ids:
                    if plugin_id not in graph.plugins:
                        return False, f"Plugin not found: {plugin_id}"
            else:
                plugin_ids = list(graph.plugins.keys())

            # Check that topological sort succeeds
            sorted_order = self._topological_sort(set(plugin_ids), graph)
            if len(sorted_order) != len(set(plugin_ids)):
                return False, "Topological sort incomplete (possible cycle)"

            return True, "✅ Valid DAG"

        except RuntimeError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Validation error: {e}"
