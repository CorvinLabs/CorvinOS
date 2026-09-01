"""
Plugin Dependency Graph & Resolution — Phase 2

Topological sort + circular dependency detection.
Dependencies auto-injected at boot, fail-closed if missing.
"""

from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass
import threading


@dataclass
class PluginDependency:
    """A plugin's dependencies."""
    plugin_id: str
    depends_on: Dict[str, str]  # {local_name: plugin_id}


class DependencyGraph:
    """Manages plugin dependency resolution."""

    def __init__(self):
        self.plugins: Dict[str, PluginDependency] = {}
        self.resolved_order: List[str] = []
        self.metrics = {
            "total_plugins": 0,
            "circular_deps": 0,
            "unmet_deps": 0,
        }
        self._lock = threading.Lock()

    def register_plugin(self, dep: PluginDependency):
        """Register plugin with its dependencies."""
        with self._lock:
            self.plugins[dep.plugin_id] = dep
            self.metrics["total_plugins"] = len(self.plugins)

    def _find_cycle(self, plugin_id: str, visited: Set[str], rec_stack: Set[str]) -> Optional[List[str]]:
        """Find cycle in dependency graph (DFS)."""
        visited.add(plugin_id)
        rec_stack.add(plugin_id)

        for dep_plugin_id in self.plugins[plugin_id].depends_on.values():
            if dep_plugin_id not in self.plugins:
                continue

            if dep_plugin_id not in visited:
                cycle = self._find_cycle(dep_plugin_id, visited, rec_stack)
                if cycle:
                    return cycle
            elif dep_plugin_id in rec_stack:
                return [plugin_id, dep_plugin_id]

        rec_stack.remove(plugin_id)
        return None

    def detect_cycles(self) -> List[List[str]]:
        """Detect all circular dependencies."""
        visited: Set[str] = set()
        cycles: List[List[str]] = []

        for plugin_id in self.plugins.keys():
            if plugin_id not in visited:
                cycle = self._find_cycle(plugin_id, visited, set())
                if cycle:
                    cycles.append(cycle)
                    self.metrics["circular_deps"] += 1

        return cycles

    def _topological_sort_dfs(
        self,
        plugin_id: str,
        visited: Set[str],
        rec_stack: Set[str],
        order: List[str],
    ) -> bool:
        """Topological sort (DFS with cycle detection)."""
        visited.add(plugin_id)
        rec_stack.add(plugin_id)

        if plugin_id in self.plugins:
            for dep_plugin_id in self.plugins[plugin_id].depends_on.values():
                if dep_plugin_id not in self.plugins:
                    # Unmet dependency
                    self.metrics["unmet_deps"] += 1
                    continue

                if dep_plugin_id not in visited:
                    if not self._topological_sort_dfs(dep_plugin_id, visited, rec_stack, order):
                        return False
                elif dep_plugin_id in rec_stack:
                    # Cycle detected
                    return False

        rec_stack.remove(plugin_id)
        order.append(plugin_id)
        return True

    def resolve_order(self) -> Optional[List[str]]:
        """
        Get boot order (dependencies first).
        Returns: List of plugin_ids in boot order, or None if cycles detected.
        """
        with self._lock:
            cycles = self.detect_cycles()
            if cycles:
                print(f"ERROR: Circular dependencies detected: {cycles}")
                return None

            visited: Set[str] = set()
            order: List[str] = []

            for plugin_id in self.plugins.keys():
                if plugin_id not in visited:
                    if not self._topological_sort_dfs(plugin_id, visited, set(), order):
                        print(f"ERROR: Circular dependency detected during sort")
                        return None

            self.resolved_order = order
            return order

    def get_dependencies(self, plugin_id: str) -> Dict[str, str]:
        """Get dependencies for a plugin."""
        with self._lock:
            if plugin_id not in self.plugins:
                return {}
            return self.plugins[plugin_id].depends_on

    def validate_all_available(self) -> bool:
        """Check all dependencies are available."""
        with self._lock:
            for plugin_id, dep in self.plugins.items():
                for dep_plugin_id in dep.depends_on.values():
                    if dep_plugin_id not in self.plugins:
                        print(f"ERROR: Plugin {plugin_id} depends on {dep_plugin_id} (not available)")
                        self.metrics["unmet_deps"] += 1
                        return False
            return True

    def inject_dependencies(
        self,
        plugin_id: str,
        plugin_instance: Any,
        available_plugins: Dict[str, Any],
    ) -> bool:
        """
        Inject dependencies into plugin instance.
        Returns: True if successful, False if dependencies missing.
        """
        with self._lock:
            if plugin_id not in self.plugins:
                return True  # No dependencies declared

            deps = self.plugins[plugin_id].depends_on

            for local_name, dep_plugin_id in deps.items():
                if dep_plugin_id not in available_plugins:
                    print(f"ERROR: Cannot inject {local_name} ({dep_plugin_id}) into {plugin_id}")
                    return False

                # Validate property setter exists before injecting
                plugin_class = type(plugin_instance)
                if hasattr(plugin_class, local_name):
                    prop = getattr(plugin_class, local_name)
                    if isinstance(prop, property) and not prop.fset:
                        raise RuntimeError(f"{local_name} is read-only property on {plugin_id}")

                # Inject as attribute
                setattr(plugin_instance, local_name, available_plugins[dep_plugin_id])

            return True

    def get_metrics(self) -> Dict[str, Any]:
        """Get dependency graph metrics."""
        return {
            **self.metrics,
            "resolved_order_length": len(self.resolved_order),
        }


# Global dependency graph
_graph: Optional[DependencyGraph] = None
_graph_lock = threading.Lock()


def get_dependency_graph() -> DependencyGraph:
    """Get or create global dependency graph."""
    global _graph
    with _graph_lock:
        if _graph is None:
            _graph = DependencyGraph()
        return _graph
