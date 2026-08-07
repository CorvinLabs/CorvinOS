"""Package registry — persisted tracking of installed skill packages (ADR-0268)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class InstalledPackage:
    """Metadata for an installed package."""

    id: str
    version: str
    path: str
    manifest: dict[str, Any]
    installed_at: str
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> InstalledPackage:
        return InstalledPackage(**data)


class PackageRegistry:
    """
    Manages package_registry.json — persistent state of installed packages.

    The registry tracks all installed packages per tenant, with persistence
    to disk via atomic writes. Supports both instance-based API (tenant_id
    bound at init) and class-level API (tenant_id per call).
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize registry for a given tenant."""
        self.tenant_id = tenant_id
        self.registry_path = self._get_registry_path()
        self._data: dict[str, InstalledPackage] = {}
        self._load()

    def _get_registry_path(self) -> Path:
        """Get path to package_registry.json for this tenant."""
        corvin_home = Path.home() / ".corvin"
        registry_path = (
            corvin_home
            / "tenants"
            / self.tenant_id
            / "packages"
            / "package_registry.json"
        )
        return registry_path

    @staticmethod
    def _get_registry_path_for_tenant(tenant_id: str) -> Path:
        """Get path to package_registry.json for a given tenant (class-level helper)."""
        corvin_home = Path.home() / ".corvin"
        return (
            corvin_home
            / "tenants"
            / tenant_id
            / "packages"
            / "package_registry.json"
        )

    def _load(self) -> None:
        """Load registry from disk, gracefully handling missing/corrupt files."""
        if not self.registry_path.exists():
            self._data = {}
            return

        try:
            with open(self.registry_path, "r") as f:
                registry_data = json.load(f)
                packages = registry_data.get("packages", {})
                for pkg_id, pkg_data in packages.items():
                    self._data[pkg_id] = InstalledPackage.from_dict(pkg_data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                f"Failed to load registry from {self.registry_path}: {e}; starting with empty registry"
            )
            self._data = {}

    def _save(self) -> None:
        """Save registry to disk atomically (write to temp, then move)."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

        registry_data = {
            "version": "1.0",
            "packages": {pkg_id: pkg.to_dict() for pkg_id, pkg in self._data.items()},
        }

        temp_path = self.registry_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w") as f:
                json.dump(registry_data, f, indent=2)
            temp_path.replace(self.registry_path)
        except Exception as e:
            logger.error(f"Failed to save registry to {self.registry_path}: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    # Instance-based API (tenant_id bound at initialization)

    def register_package(self, pkg: InstalledPackage) -> None:
        """Register a package in the registry."""
        self._data[pkg.id] = pkg
        self._save()

    def unregister_package(self, package_id: str) -> None:
        """Unregister a package from the registry."""
        if package_id in self._data:
            del self._data[package_id]
            self._save()

    def get_package(self, package_id: str) -> InstalledPackage | None:
        """Get metadata for an installed package."""
        return self._data.get(package_id)

    def get_all_packages(self) -> dict[str, InstalledPackage]:
        """Get all installed packages."""
        return dict(self._data)

    def list_package_ids(self) -> list[str]:
        """List all installed package IDs."""
        return list(self._data.keys())

    def has_package(self, package_id: str) -> bool:
        """Check if a package is installed."""
        return package_id in self._data

    def get_installed_versions(self) -> dict[str, str]:
        """Get {package_id: version} for all installed packages (for dependency checking)."""
        return {pkg_id: pkg.version for pkg_id, pkg in self._data.items()}

    # Class-level API (supports functional/multi-tenant patterns)

    @classmethod
    def load_registry(cls, tenant_id: str = "_default") -> dict[str, InstalledPackage]:
        """
        Load registry data for a tenant from disk.

        Args:
            tenant_id: Tenant ID to load registry for (default: "_default")

        Returns:
            Dictionary of {package_id: InstalledPackage} for this tenant.
            Returns empty dict if registry file doesn't exist or is corrupt.
        """
        registry_path = cls._get_registry_path_for_tenant(tenant_id)

        if not registry_path.exists():
            return {}

        try:
            with open(registry_path, "r") as f:
                registry_data = json.load(f)
                packages = registry_data.get("packages", {})
                return {
                    pkg_id: InstalledPackage.from_dict(pkg_data)
                    for pkg_id, pkg_data in packages.items()
                }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(
                f"Failed to load registry from {registry_path}: {e}; returning empty registry"
            )
            return {}

    @classmethod
    def save_registry(
        cls, tenant_id: str, registry: dict[str, InstalledPackage]
    ) -> None:
        """
        Save registry data for a tenant to disk atomically.

        Args:
            tenant_id: Tenant ID to save registry for
            registry: Dictionary of {package_id: InstalledPackage} to persist
        """
        registry_path = cls._get_registry_path_for_tenant(tenant_id)
        registry_path.parent.mkdir(parents=True, exist_ok=True)

        registry_data = {
            "version": "1.0",
            "packages": {pkg_id: pkg.to_dict() for pkg_id, pkg in registry.items()},
        }

        temp_path = registry_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w") as f:
                json.dump(registry_data, f, indent=2)
            temp_path.replace(registry_path)
        except Exception as e:
            logger.error(f"Failed to save registry to {registry_path}: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    @classmethod
    def register_package_cls(
        cls, package_id: str, metadata: dict[str, Any], tenant_id: str = "_default"
    ) -> None:
        """
        Register a package by loading, updating, and saving the registry.

        Args:
            package_id: ID of the package to register
            metadata: Dictionary of package metadata (version, path, manifest, etc.)
            tenant_id: Tenant ID (default: "_default")

        Note:
            This is a functional API alternative to instantiating PackageRegistry
            and calling register_package(). For most use cases, use the instance
            API via PackageRegistry(tenant_id).register_package(pkg).
        """
        registry = cls.load_registry(tenant_id)
        pkg = InstalledPackage(
            id=package_id,
            version=metadata.get("version", "0.0.0"),
            path=metadata.get("path", ""),
            manifest=metadata.get("manifest", {}),
            installed_at=metadata.get(
                "installed_at", datetime.utcnow().isoformat()
            ),
            enabled=metadata.get("enabled", True),
        )
        registry[package_id] = pkg
        cls.save_registry(tenant_id, registry)

    @classmethod
    def unregister_package_cls(
        cls, package_id: str, tenant_id: str = "_default"
    ) -> None:
        """
        Unregister a package by loading, updating, and saving the registry.

        Args:
            package_id: ID of the package to unregister
            tenant_id: Tenant ID (default: "_default")

        Note:
            This is a functional API alternative to the instance API.
            For most use cases, use PackageRegistry(tenant_id).unregister_package(pkg_id).
        """
        registry = cls.load_registry(tenant_id)
        if package_id in registry:
            del registry[package_id]
            cls.save_registry(tenant_id, registry)

    @classmethod
    def get_package_cls(
        cls, package_id: str, tenant_id: str = "_default"
    ) -> InstalledPackage | None:
        """
        Get metadata for a specific installed package.

        Args:
            package_id: ID of the package to retrieve
            tenant_id: Tenant ID (default: "_default")

        Returns:
            InstalledPackage if found, None otherwise.
        """
        registry = cls.load_registry(tenant_id)
        return registry.get(package_id)

    @classmethod
    def get_all_packages_cls(
        cls, tenant_id: str = "_default"
    ) -> dict[str, InstalledPackage]:
        """
        Get all installed packages for a tenant.

        Args:
            tenant_id: Tenant ID (default: "_default")

        Returns:
            Dictionary of {package_id: InstalledPackage} for this tenant.
        """
        return cls.load_registry(tenant_id)
