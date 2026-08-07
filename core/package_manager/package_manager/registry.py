"""Package registry and catalog management.

Manages package metadata, versioning, and availability across scopes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PackageMetadata:
    """Package catalog metadata."""
    id: str
    name: str
    version: str
    description: str | None = None
    author: str | None = None
    license: str | None = None
    dependencies: dict[str, str] | None = None  # {pkg_id: version_constraint}
    components: dict[str, list[str]] | None = None  # component types -> file lists
    permissions: dict[str, Any] | None = None
    checksum: str | None = None  # SHA256 or similar
    scope: str = "user"


@dataclass
class RegistryEntry:
    """A package in the registry."""
    metadata: PackageMetadata
    available_versions: list[str]
    latest_version: str
    is_installed: bool = False
    installed_version: str | None = None
    available_locations: list[Path] | None = None  # where this pkg can be installed


class PackageRegistry:
    """Manage package catalog and availability."""

    def __init__(self, corvin_home: Path | None = None):
        """Initialize package registry.

        Args:
            corvin_home: Override CORVIN_HOME for testing
        """
        self.corvin_home = corvin_home

    def register_package(self, metadata: PackageMetadata) -> None:
        """Register a package in the registry.

        Args:
            metadata: Package metadata to register
        """
        # TODO: implement
        pass

    def unregister_package(self, pkg_id: str, version: str | None = None) -> None:
        """Unregister a package or specific version.

        Args:
            pkg_id: Package identifier
            version: Specific version to unregister, or None for all
        """
        # TODO: implement
        pass

    def lookup_package(self, pkg_id: str) -> RegistryEntry | None:
        """Look up a package in the registry.

        Args:
            pkg_id: Package identifier

        Returns:
            RegistryEntry if found, None otherwise
        """
        # TODO: implement
        pass

    def search_packages(self, query: str, scope: str | None = None) -> list[RegistryEntry]:
        """Search for packages by name, description, or id.

        Args:
            query: Search query string
            scope: Optional scope filter (user, project, session)

        Returns:
            List of matching RegistryEntry objects
        """
        # TODO: implement
        pass

    def list_all_packages(self, scope: str | None = None) -> list[RegistryEntry]:
        """List all packages in registry, optionally filtered by scope.

        Args:
            scope: Optional scope filter

        Returns:
            List of all RegistryEntry objects
        """
        # TODO: implement
        pass

    def get_available_versions(self, pkg_id: str) -> list[str]:
        """Get all available versions of a package.

        Args:
            pkg_id: Package identifier

        Returns:
            List of available version strings in order (newest first)
        """
        # TODO: implement
        pass

    def mark_installed(self, pkg_id: str, version: str, scope: str) -> None:
        """Mark a package version as installed.

        Args:
            pkg_id: Package identifier
            version: Installed version
            scope: Installation scope
        """
        # TODO: implement
        pass

    def mark_uninstalled(self, pkg_id: str, scope: str) -> None:
        """Mark a package as uninstalled.

        Args:
            pkg_id: Package identifier
            scope: Installation scope
        """
        # TODO: implement
        pass
