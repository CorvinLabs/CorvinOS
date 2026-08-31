"""Package lifecycle management — install, update, remove, status tracking.

Coordinates with awpkg.installer for low-level operations.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class PackageLifecycleState:
    """Track package lifecycle state."""
    id: str
    version: str
    scope: str
    installed_at: str  # ISO 8601 timestamp
    updated_at: str | None = None
    status: str = "active"  # active, disabled, pending_update, error
    metadata: dict[str, Any] | None = None


class PackageLifecycleManager:
    """High-level package lifecycle operations."""

    def __init__(self, corvin_home: Path | None = None):
        """Initialize lifecycle manager.

        Args:
            corvin_home: Override CORVIN_HOME for testing
        """
        self.corvin_home = corvin_home

    def install_and_track(self, file: Path, scope: str) -> PackageLifecycleState:
        """Install a package and track its lifecycle state.

        Args:
            file: Path to .awpkg file
            scope: Installation scope (user, project, session)

        Returns:
            PackageLifecycleState tracking the installed package

        Raises:
            RuntimeError: If installation fails
        """
        # TODO: implement
        pass

    def update_package(self, pkg_id: str, new_version: Path, scope: str) -> PackageLifecycleState:
        """Update an existing package to a new version.

        Args:
            pkg_id: Package identifier
            new_version: Path to new .awpkg file
            scope: Package scope

        Returns:
            Updated PackageLifecycleState

        Raises:
            RuntimeError: If update fails
        """
        # TODO: implement
        pass

    def remove_and_untrack(self, pkg_id: str, scope: str) -> None:
        """Remove a package and clean up tracking state.

        Args:
            pkg_id: Package identifier
            scope: Package scope

        Raises:
            RuntimeError: If removal fails
        """
        # TODO: implement
        pass

    def get_lifecycle_state(self, pkg_id: str, scope: str) -> PackageLifecycleState | None:
        """Get current lifecycle state of a package.

        Args:
            pkg_id: Package identifier
            scope: Package scope

        Returns:
            PackageLifecycleState if package is tracked, None otherwise
        """
        # TODO: implement
        pass

    def list_tracked_packages(self, scope: str | None = None) -> list[PackageLifecycleState]:
        """List all tracked packages, optionally filtered by scope.

        Args:
            scope: Optional scope filter (user, project, session)

        Returns:
            List of tracked PackageLifecycleState objects
        """
        # TODO: implement
        pass
