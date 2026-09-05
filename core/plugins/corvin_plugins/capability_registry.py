"""Capability Registry: Index plugins by capability type (ADR-0610)."""

from __future__ import annotations

import logging
from typing import Optional

from .manifest_capabilities import Capability, CapabilityType, PluginCapabilitiesManifest

log = logging.getLogger(__name__)


class CapabilityRegistry:
    """Index and query plugin capabilities."""

    def __init__(self):
        """Initialize registry (empty)."""
        self._plugins: dict[str, PluginCapabilitiesManifest] = {}  # plugin_id → manifest
        self._by_type: dict[CapabilityType, list[tuple[str, Capability]]] = {}  # type → [(plugin_id, cap), ...]

    def register_manifest(self, manifest: PluginCapabilitiesManifest) -> None:
        """Register a plugin's capabilities manifest."""
        plugin_id = manifest.plugin_id

        # Store manifest
        self._plugins[plugin_id] = manifest

        # Index by type
        for cap in manifest.capabilities:
            if cap.type not in self._by_type:
                self._by_type[cap.type] = []
            self._by_type[cap.type].append((plugin_id, cap))

        log.debug(f"Registered {len(manifest.capabilities)} capabilities for plugin {plugin_id}")

    def capabilities_by_type(self, cap_type: CapabilityType) -> list[tuple[str, Capability]]:
        """Get all (plugin_id, capability) pairs for a given type."""
        return self._by_type.get(cap_type, [])

    def capabilities_by_plugin(self, plugin_id: str) -> list[Capability]:
        """Get all capabilities for a plugin."""
        manifest = self._plugins.get(plugin_id)
        return manifest.capabilities if manifest else []

    def get_capability(self, plugin_id: str, capability_id: str) -> Optional[Capability]:
        """Get a specific capability."""
        manifest = self._plugins.get(plugin_id)
        if manifest:
            return manifest.get_capability(capability_id)
        return None

    def find_implementations(
        self,
        cap_type: CapabilityType,
        min_version: str = "1.0",
        tenant_id: Optional[str] = None,
    ) -> list[tuple[str, Capability]]:
        """
        Find all plugins implementing a capability type, with version filtering.

        Args:
            cap_type: Capability type to search for
            min_version: Minimum capabilities_version (semver)
            tenant_id: (Optional) Filter by tenant (for future multi-tenant support)

        Returns:
            List of (plugin_id, capability) tuples
        """
        implementations = []

        for plugin_id, cap in self.capabilities_by_type(cap_type):
            manifest = self._plugins.get(plugin_id)
            if not manifest:
                continue

            # Version check: plugin.capabilities_version >= min_version
            if self._compare_versions(manifest.capabilities_version, min_version) >= 0:
                implementations.append((plugin_id, cap))

        return implementations

    def _compare_versions(self, v1: str, v2: str) -> int:
        """Compare semver versions. Returns 1 if v1 > v2, -1 if v1 < v2, 0 if equal."""
        parts1 = [int(x) for x in v1.split(".")[:3]]
        parts2 = [int(x) for x in v2.split(".")[:3]]

        # Pad with zeros
        while len(parts1) < 3:
            parts1.append(0)
        while len(parts2) < 3:
            parts2.append(0)

        if parts1 > parts2:
            return 1
        elif parts1 < parts2:
            return -1
        else:
            return 0

    def get_plugin_manifest(self, plugin_id: str) -> Optional[PluginCapabilitiesManifest]:
        """Get full manifest for a plugin."""
        return self._plugins.get(plugin_id)

    def all_plugins(self) -> dict[str, PluginCapabilitiesManifest]:
        """Get all registered plugin manifests."""
        return dict(self._plugins)

    def clear(self) -> None:
        """Clear registry (for testing)."""
        self._plugins.clear()
        self._by_type.clear()


# Global registry instance
_global_registry: Optional[CapabilityRegistry] = None


def get_registry() -> CapabilityRegistry:
    """Get or create global registry instance."""
    global _global_registry
    if _global_registry is None:
        _global_registry = CapabilityRegistry()
    return _global_registry
