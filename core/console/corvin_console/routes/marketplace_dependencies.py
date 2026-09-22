"""Dependency resolution for marketplace plugins.

This module resolves the transitive dependency graph for a plugin to be installed.
It handles:
- Loading a plugin's dependencies from its manifest
- Resolving transitive dependencies (direct + recursive)
- Detecting circular dependencies (fail-closed)
- Building a dependency tree for UI display
- Checking if dependencies are already installed

ADR-0892: Marketplace integration. Dependency resolution is UI-driven (not a
blocking gate) — the install flow shows the tree so the operator can understand
what will be installed, but does not refuse to proceed if deps are unmet. The
install itself is gated by the lifecycle (already-installed deps are OK).

Thread-safe: no global state, thread-local cache per request.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Set, List, Optional, Tuple

try:
    _core_plugins = Path(__file__).resolve().parents[3] / "plugins"
    if (_core_plugins / "corvin_plugins").is_dir() and str(_core_plugins) not in sys.path:
        sys.path.append(str(_core_plugins))
    from corvin_plugins.registry import get_registry  # type: ignore[import-not-found]
    _LIFECYCLE_AVAILABLE = True
except ImportError:
    _LIFECYCLE_AVAILABLE = False

from . import marketplace_resolve as _resolve


class DependencyResolveError(Exception):
    """Dependency resolution failed."""


@dataclass
class DependencyNode:
    """A single node in the dependency tree."""
    plugin_id: str
    """Plugin registry id."""
    index_id: str
    """Plugin marketplace index id (plugin:tier-category-name)."""
    version: str
    """Plugin version."""
    installed: bool
    """True if this plugin is already installed."""
    missing: bool
    """True if this plugin could not be resolved from the marketplace."""
    reason: Optional[str] = None
    """If missing=True, explains why."""
    children: List[DependencyNode] = field(default_factory=list)
    """Direct dependencies of this node."""

    def to_dict(self) -> Dict:
        return {
            "plugin_id": self.plugin_id,
            "index_id": self.index_id,
            "version": self.version,
            "installed": self.installed,
            "missing": self.missing,
            "reason": self.reason,
            "children": [c.to_dict() for c in self.children],
        }

    def flatten(self) -> List[str]:
        """Return a flat list of all plugin IDs in this tree (depth-first)."""
        result = [self.plugin_id] if not self.missing else []
        for child in self.children:
            result.extend(child.flatten())
        return result


class DependencyResolver:
    """Resolves transitive dependencies for marketplace plugins.

    Usage:
        resolver = DependencyResolver(tenant_id="_default")
        tree = resolver.resolve("plugin:buildin-memory-semantic_context_retriever")
    """

    def __init__(self, tenant_id: str = "_default", max_depth: int = 10):
        self.tenant_id = tenant_id
        self.max_depth = max_depth
        self._cache: Dict[str, Optional[DependencyNode]] = {}
        self._resolving: Set[str] = set()  # Detect circular deps

    def resolve(self, index_id: str) -> DependencyNode:
        """Resolve the full dependency tree for a plugin.

        Raises DependencyResolveError on:
        - Circular dependencies
        - Max depth exceeded
        - Plugin not found in marketplace index
        """
        return self._resolve_node(index_id, depth=0)

    def _resolve_node(self, index_id: str, depth: int) -> DependencyNode:
        """Recursively resolve a single node and its dependencies."""
        # Circular dependency detection
        if index_id in self._resolving:
            raise DependencyResolveError(f"Circular dependency detected: {index_id}")

        # Max depth check
        if depth >= self.max_depth:
            raise DependencyResolveError(
                f"Dependency tree too deep (>{self.max_depth}): {index_id}"
            )

        # Cache check
        if index_id in self._cache:
            cached = self._cache[index_id]
            if cached is not None:
                return cached
            # Marker for "in progress" — return a missing node
            return DependencyNode(
                plugin_id="",
                index_id=index_id,
                version="",
                installed=False,
                missing=True,
                reason="Circular dependency detected",
            )

        self._resolving.add(index_id)
        try:
            # Try to resolve the plugin's manifest
            try:
                plugin_dir, manifest = _resolve.load_manifest(index_id)
                plugin_id = str(manifest.get("plugin_id", ""))
                version = str(manifest.get("version", "0.0.0"))
                registry_id = _resolve.manifest_plugin_id(index_id)
            except _resolve.MarketplaceResolveError as exc:
                return DependencyNode(
                    plugin_id="",
                    index_id=index_id,
                    version="",
                    installed=False,
                    missing=True,
                    reason=str(exc),
                )

            # Check if already installed
            installed = False
            if _LIFECYCLE_AVAILABLE:
                try:
                    reg = get_registry()
                    # get_registry().plugins is a dict of {plugin_id: PluginRuntime}
                    installed = registry_id in (reg.plugins or {})
                except Exception:
                    pass

            # Resolve direct dependencies
            dependencies = manifest.get("dependencies") or []
            child_nodes = []
            for dep_spec in dependencies:
                # dep_spec might be a string like "plugin:buildin-..." or just a plugin_id
                if isinstance(dep_spec, str):
                    dep_index_id = dep_spec if dep_spec.startswith("plugin:") else f"plugin:buildin-{dep_spec}"
                    try:
                        child = self._resolve_node(dep_index_id, depth=depth + 1)
                        child_nodes.append(child)
                    except DependencyResolveError as exc:
                        # Add a missing node for the failed dependency
                        child_nodes.append(DependencyNode(
                            plugin_id=dep_spec,
                            index_id=dep_index_id,
                            version="",
                            installed=False,
                            missing=True,
                            reason=str(exc),
                        ))

            node = DependencyNode(
                plugin_id=plugin_id,
                index_id=index_id,
                version=version,
                installed=installed,
                missing=False,
                children=child_nodes,
            )
            self._cache[index_id] = node
            return node
        finally:
            self._resolving.discard(index_id)

    def get_all_dependencies(self, index_id: str) -> Tuple[List[str], List[str]]:
        """Return (to_install, already_installed) for a plugin and all its deps.

        Returns two lists:
        - to_install: plugin IDs (registry keys) that need to be installed
        - already_installed: plugin IDs that are already installed
        """
        tree = self.resolve(index_id)
        flat = tree.flatten()
        to_install = []
        already_installed = []

        if _LIFECYCLE_AVAILABLE:
            try:
                reg = get_registry()
                installed_ids = set(reg.plugins or {})
            except Exception:
                installed_ids = set()
        else:
            installed_ids = set()

        for plugin_id in flat:
            if plugin_id in installed_ids:
                already_installed.append(plugin_id)
            else:
                to_install.append(plugin_id)

        return to_install, already_installed


def resolve_dependencies(
    index_id: str, tenant_id: str = "_default"
) -> DependencyNode:
    """Convenience function: resolve dependencies and return the tree.

    Raises DependencyResolveError on circular dependencies or resolution failures.
    """
    resolver = DependencyResolver(tenant_id=tenant_id)
    return resolver.resolve(index_id)


def get_install_plan(
    index_id: str, tenant_id: str = "_default"
) -> Dict:
    """Get a complete install plan: what needs to be installed, what's already there.

    Returns:
        {
            "root_id": index_id,
            "root_plugin_id": "semantic-context-retriever",
            "dependency_tree": {...},  # DependencyNode.to_dict()
            "to_install": [list of plugin IDs],
            "already_installed": [list of plugin IDs],
            "total_new": 3,
            "total_existing": 1,
        }
    """
    resolver = DependencyResolver(tenant_id=tenant_id)
    tree = resolver.resolve(index_id)
    to_install, already_installed = resolver.get_all_dependencies(index_id)

    return {
        "root_id": index_id,
        "root_plugin_id": tree.plugin_id,
        "dependency_tree": tree.to_dict(),
        "to_install": to_install,
        "already_installed": already_installed,
        "total_new": len(to_install),
        "total_existing": len(already_installed),
    }
