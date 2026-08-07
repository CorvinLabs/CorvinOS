"""Package registry — persisted tracking of installed skill packages (ADR-0268)."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


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
    """Manages package_registry.json — persistent state of installed packages."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.registry_path = self._get_registry_path()
        self._data: dict[str, InstalledPackage] = {}
        self._load()

    def _get_registry_path(self) -> Path:
        """Get path to package_registry.json for this tenant."""
        from pathlib import Path

        corvin_home = Path.home() / ".corvin"
        registry_path = (
            corvin_home
            / "tenants"
            / self.tenant_id
            / "packages"
            / "package_registry.json"
        )
        return registry_path

    def _load(self) -> None:
        """Load registry from disk."""
        if not self.registry_path.exists():
            self._data = {}
            return

        try:
            with open(self.registry_path, "r") as f:
                registry_data = json.load(f)
                packages = registry_data.get("packages", {})
                for pkg_id, pkg_data in packages.items():
                    self._data[pkg_id] = InstalledPackage.from_dict(pkg_data)
        except (json.JSONDecodeError, KeyError) as e:
            self._data = {}

    def _save(self) -> None:
        """Save registry to disk atomically."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)

        registry_data = {
            "version": "1.0",
            "packages": {pkg_id: pkg.to_dict() for pkg_id, pkg in self._data.items()},
        }

        temp_path = self.registry_path.with_suffix(".tmp")
        with open(temp_path, "w") as f:
            json.dump(registry_data, f, indent=2)

        temp_path.replace(self.registry_path)

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
