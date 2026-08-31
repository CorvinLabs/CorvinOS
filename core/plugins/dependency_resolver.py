"""
Plugin Dependency Resolution and Conflict Detection

Enables multi-plugin installation with dependency resolution,
version constraint validation, and conflict detection.

ADR-0385 Phase 2 — Multi-plugin support with safe dependency DAG traversal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set, Dict, Optional, Tuple
import logging

# Fallback version comparison if packaging not available
try:
    from packaging import version
except ImportError:
    class version:
        @staticmethod
        def parse(v):
            """Fallback version parser."""
            class Version:
                def __init__(self, v):
                    self.parts = [int(x) for x in v.split('.')[:3]]
                def __ge__(self, other):
                    return self.parts >= other.parts
                def __le__(self, other):
                    return self.parts <= other.parts
                def __eq__(self, other):
                    return self.parts == other.parts
            return Version(v)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DependencyConstraint:
    """Represents a version constraint (e.g., ">=1.0.0", "<2.0.0")."""
    operator: str  # ">=", "<=", "==", "~="
    version: str

    def matches(self, target_version: str) -> bool:
        """Check if target_version satisfies this constraint."""
        target = version.parse(target_version)
        constraint_ver = version.parse(self.version)

        if self.operator == ">=":
            return target >= constraint_ver
        elif self.operator == "<=":
            return target <= constraint_ver
        elif self.operator == "==":
            return target == constraint_ver
        elif self.operator == "~=":  # Compatible release
            return (target.major == constraint_ver.major and
                    target.minor == constraint_ver.minor and
                    target.micro >= constraint_ver.micro)
        return False


@dataclass(frozen=True)
class PluginDependency:
    """Represents a plugin dependency."""
    plugin_id: str
    constraint: Optional[DependencyConstraint] = None

    def satisfies(self, target_version: str) -> bool:
        """Check if target_version satisfies this dependency."""
        if not self.constraint:
            return True
        return self.constraint.matches(target_version)


@dataclass(frozen=True)
class ResolutionError:
    """Represents a dependency resolution failure."""
    plugin_id: str
    reason: str
    details: Optional[str] = None


class PluginDependencyResolver:
    """Resolve plugin dependencies and detect conflicts."""

    def __init__(self, plugins_dict: Dict):
        """Initialize with a dict of plugin_id -> PluginMetadata."""
        self.plugins = plugins_dict

    def parse_dependency_string(self, dep_string: str) -> PluginDependency:
        """Parse a dependency string like 'plugin-id>=1.0.0'."""
        for op in [">=", "<=", "==", "~="]:
            if op in dep_string:
                plugin_id, ver = dep_string.split(op, 1)
                return PluginDependency(
                    plugin_id=plugin_id.strip(),
                    constraint=DependencyConstraint(op, ver.strip())
                )
        return PluginDependency(plugin_id=dep_string.strip())

    def find_transitive_deps(self, plugin_id: str) -> Set[str]:
        """Find all plugins this plugin depends on (recursively)."""
        visited = set()
        to_visit = {plugin_id}

        while to_visit:
            current = to_visit.pop()
            if current in visited:
                continue
            visited.add(current)

            plugin = self.plugins.get(current)
            if not plugin:
                continue

            # Add direct dependencies
            for dep_str in plugin.depends_on:
                dep = self.parse_dependency_string(dep_str)
                if dep.plugin_id not in visited:
                    to_visit.add(dep.plugin_id)

        visited.discard(plugin_id)  # Don't include self
        return visited

    def resolve_install_order(self, plugin_ids: List[str]) -> Tuple[List[str], List[ResolutionError]]:
        """
        Topologically sort plugins by dependencies.

        Returns: (ordered_plugins, errors)
        """
        errors = []
        all_plugins = set(plugin_ids)

        # Add transitive dependencies
        for pid in plugin_ids:
            all_plugins.update(self.find_transitive_deps(pid))

        # Validate all plugins exist
        for pid in all_plugins:
            if pid not in self.plugins:
                errors.append(ResolutionError(
                    plugin_id=pid,
                    reason="Plugin not found in registry"
                ))

        if errors:
            return [], errors

        # Topological sort
        visited = set()
        order = []
        temp_mark = set()

        def visit(node: str, path: List[str]):
            if node in temp_mark:
                errors.append(ResolutionError(
                    plugin_id=node,
                    reason="Circular dependency detected",
                    details=f"Cycle: {' -> '.join(path + [node])}"
                ))
                return

            if node in visited:
                return

            temp_mark.add(node)
            plugin = self.plugins.get(node)

            if plugin:
                for dep_str in plugin.depends_on:
                    dep = self.parse_dependency_string(dep_str)
                    visit(dep.plugin_id, path + [node])

            temp_mark.discard(node)
            visited.add(node)
            order.append(node)

        for pid in all_plugins:
            if pid not in visited:
                visit(pid, [])

        return order, errors

    def detect_version_conflicts(self, plugin_ids: List[str]) -> List[ResolutionError]:
        """Detect version constraint incompatibilities."""
        errors = []
        all_plugins = set(plugin_ids)

        # Collect all transitive deps
        for pid in plugin_ids:
            all_plugins.update(self.find_transitive_deps(pid))

        # Check constraints
        for plugin_id in all_plugins:
            plugin = self.plugins.get(plugin_id)
            if not plugin:
                continue

            for dep_str in plugin.depends_on:
                dep = self.parse_dependency_string(dep_str)
                dep_plugin = self.plugins.get(dep.plugin_id)

                if not dep_plugin:
                    errors.append(ResolutionError(
                        plugin_id=plugin_id,
                        reason=f"Dependency not found: {dep.plugin_id}"
                    ))
                    continue

                # Check version constraint
                if dep.constraint and not dep.satisfies(dep_plugin.version):
                    errors.append(ResolutionError(
                        plugin_id=plugin_id,
                        reason=f"Version conflict: {dep.plugin_id} {dep_plugin.version} doesn't satisfy {dep_str}",
                        details=f"Expected: {dep_str}, Found: {dep_plugin.version}"
                    ))

        return errors

    def detect_mutual_exclusions(self, plugin_ids: List[str]) -> List[Tuple[str, str]]:
        """Detect plugins that conflict with each other."""
        conflicts = []

        # Define mutual exclusions (plugins that cannot coexist)
        mutual_exclusions = {
            'com.corvinlabs.slack-notifier': ['legacy-notifier'],
            'legacy-notifier': ['com.corvinlabs.slack-notifier'],
        }

        for pid in plugin_ids:
            exclusions = mutual_exclusions.get(pid, [])
            for excluded in exclusions:
                if excluded in plugin_ids:
                    conflicts.append((pid, excluded))

        return conflicts

    def validate_multi_install(self, plugin_ids: List[str]) -> Tuple[List[str], List[ResolutionError]]:
        """
        Full validation for multi-plugin installation.

        Returns: (ordered_install_list, errors)
        """
        errors = []

        # Resolve order
        order, order_errors = self.resolve_install_order(plugin_ids)
        errors.extend(order_errors)

        # Check version conflicts
        version_errors = self.detect_version_conflicts(plugin_ids)
        errors.extend(version_errors)

        # Check mutual exclusions
        mutual_conflicts = self.detect_mutual_exclusions(plugin_ids)
        for p1, p2 in mutual_conflicts:
            errors.append(ResolutionError(
                plugin_id=p1,
                reason=f"Mutual exclusion: cannot install with {p2}",
                details=f"{p1} and {p2} are incompatible"
            ))

        return order if not errors else [], errors
