"""Main package manager coordinator.

High-level API orchestrating all package management operations:
installation, updates, removal, dependency resolution, validation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .lifecycle import PackageLifecycleManager, PackageLifecycleState
from .registry import PackageRegistry, RegistryEntry
from .resolver import DependencyResolver, ResolutionResult
from .validator import PackageValidator, ValidationResult


class PackageManager:
    """High-level package management coordinator.

    Provides unified interface for all package operations:
    - Install/update/remove packages
    - Resolve dependencies
    - Validate packages
    - Query registry
    """

    def __init__(self, corvin_home: Path | None = None):
        """Initialize package manager.

        Args:
            corvin_home: Override CORVIN_HOME for testing
        """
        self.corvin_home = corvin_home
        self.lifecycle = PackageLifecycleManager(corvin_home=corvin_home)
        self.registry = PackageRegistry(corvin_home=corvin_home)
        self.resolver = DependencyResolver()
        self.validator = PackageValidator()

    def install(
        self,
        file: Path,
        scope: str = "user",
        force: bool = False,
        auto_update_deps: bool = False,
    ) -> dict[str, Any]:
        """Install a package with full validation and dependency resolution.

        Args:
            file: Path to .awpkg file
            scope: Installation scope (user, project, session)
            force: Skip validation checks
            auto_update_deps: Automatically update dependencies if needed

        Returns:
            Dictionary with installation results:
                {
                    "success": bool,
                    "package": {"id": str, "version": str},
                    "installed_packages": [{"id": str, "version": str}, ...],
                    "warnings": [str, ...],
                    "errors": [str, ...],
                }

        Raises:
            RuntimeError: If installation fails critically
        """
        # TODO: implement
        pass

    def update(
        self,
        pkg_id: str,
        target_version: str | None = None,
        scope: str = "user",
        check_only: bool = False,
    ) -> dict[str, Any]:
        """Update a package to a new version.

        Args:
            pkg_id: Package identifier
            target_version: Specific version to update to, or None for latest
            scope: Package scope
            check_only: Check if update is available without applying

        Returns:
            Dictionary with update results

        Raises:
            RuntimeError: If update fails
        """
        # TODO: implement
        pass

    def remove(
        self,
        pkg_id: str,
        scope: str = "user",
        purge: bool = False,
    ) -> dict[str, Any]:
        """Remove a package.

        Args:
            pkg_id: Package identifier
            scope: Package scope
            purge: Also remove configuration and data files

        Returns:
            Dictionary with removal results

        Raises:
            RuntimeError: If removal fails
        """
        # TODO: implement
        pass

    def install_multiple(
        self,
        files: list[Path],
        scope: str = "user",
        stop_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        """Install multiple packages in dependency order.

        Args:
            files: Paths to .awpkg files
            scope: Installation scope
            stop_on_error: Stop on first error or continue

        Returns:
            List of installation results (one per file)
        """
        # TODO: implement
        pass

    def validate_all(self, scope: str | None = None) -> dict[str, ValidationResult]:
        """Validate all installed packages.

        Args:
            scope: Optional scope filter

        Returns:
            Dictionary of {pkg_id: ValidationResult}
        """
        # TODO: implement
        pass

    def check_updates(
        self,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """Check for available updates.

        Args:
            scope: Optional scope filter

        Returns:
            List of available updates:
                [
                    {"id": str, "current": str, "latest": str, "scope": str},
                    ...
                ]
        """
        # TODO: implement
        pass

    def list_installed(self, scope: str | None = None) -> list[PackageLifecycleState]:
        """List installed packages.

        Args:
            scope: Optional scope filter

        Returns:
            List of installed packages
        """
        # TODO: implement
        pass

    def search(self, query: str, scope: str | None = None) -> list[RegistryEntry]:
        """Search package registry.

        Args:
            query: Search query
            scope: Optional scope filter

        Returns:
            List of matching packages
        """
        # TODO: implement
        pass

    def get_info(self, pkg_id: str) -> dict[str, Any]:
        """Get detailed information about a package.

        Args:
            pkg_id: Package identifier

        Returns:
            Dictionary with package information:
                {
                    "id": str,
                    "name": str,
                    "version": str,
                    "description": str,
                    "author": str,
                    "license": str,
                    "dependencies": {...},
                    "is_installed": bool,
                    "available_versions": [str, ...],
                }
        """
        # TODO: implement
        pass

    def resolve_dependencies(
        self,
        pkg_id: str,
        version: str,
        scope: str = "user",
    ) -> ResolutionResult:
        """Resolve all dependencies for a package.

        Args:
            pkg_id: Package identifier
            version: Package version
            scope: Installation scope

        Returns:
            ResolutionResult with dependency graph
        """
        # TODO: implement
        pass

    def audit(self, scope: str | None = None) -> dict[str, Any]:
        """Run full audit of package system.

        Args:
            scope: Optional scope filter

        Returns:
            Audit report:
                {
                    "packages_scanned": int,
                    "issues_found": int,
                    "critical": [...],
                    "warnings": [...],
                    "timestamp": str,
                }
        """
        # TODO: implement
        pass
