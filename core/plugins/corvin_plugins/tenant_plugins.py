"""Tenant-scoped plugin installation and persistence (ADR-0243 Phase 1c).

A TenantPluginRegistry manages user-installed plugins in ~/.corvin/tenants/<id>/plugins/.
Separate from the global registry lifecycle (registry.py); this module only handles
persistence and discovery, never runtime lifecycle.

Plugins are discovered from:
1. The plugins/ directory (this module)
2. spec.plugins.installed in tenant.corvin.yaml (for backward compat)

Installation layout:
    ~/.corvin/tenants/<id>/plugins/
    ├── registry.yaml       # Index: { plugins: [ { id, version, enabled, ... } ] }
    └── installed/
        └── <plugin-id>/
            ├── manifest.json   # Entry point metadata
            ├── plugin.py       # Plugin implementation
            ├── requirements.txt
            └── ... other files
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("corvin.plugins.tenant_plugins")


# ── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class TenantPluginEntry:
    """One installed plugin in the tenant registry."""

    plugin_id: str
    version: str
    display_name: str = ""
    enabled: bool = True
    installed_at: Optional[str] = None
    installed_by: Optional[str] = None
    boot_layer: str = "installed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TenantPluginEntry:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class TenantPluginRegistry:
    """Manage plugins stored in tenant/plugins/."""

    tenant_id: str = "_default"
    plugins: List[TenantPluginEntry] = field(default_factory=list)
    schema_version: str = "1.0"

    @property
    def plugins_dir(self) -> Path:
        """Root directory for tenant plugins."""
        from corvinOS.shared.paths import tenant_home

        return tenant_home(self.tenant_id) / "plugins"

    @property
    def installed_dir(self) -> Path:
        """Directory containing installed plugin subdirectories."""
        return self.plugins_dir / "installed"

    @property
    def registry_path(self) -> Path:
        """Path to registry.yaml."""
        return self.plugins_dir / "registry.yaml"

    def _ensure_dirs(self) -> None:
        """Create plugin directories if missing."""
        self.installed_dir.mkdir(parents=True, exist_ok=True)

    def load_registry(self) -> None:
        """Load registry from disk; defaults to empty list if file doesn't exist."""
        if self.registry_path.exists():
            try:
                import yaml

                data = yaml.safe_load(self.registry_path.read_text(encoding="utf-8"))
                if data and isinstance(data, dict):
                    self.plugins = [
                        TenantPluginEntry.from_dict(p)
                        for p in data.get("plugins", [])
                    ]
                    self.schema_version = data.get("schema_version", "1.0")
            except Exception as exc:
                log.warning(f"failed to load registry from {self.registry_path}: {exc}")
                self.plugins = []
        else:
            self.plugins = []

    def save_registry(self) -> None:
        """Persist registry to disk."""
        self._ensure_dirs()
        import yaml

        data = {
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "plugins": [p.to_dict() for p in self.plugins],
        }
        self.registry_path.write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        log.debug(f"saved plugin registry to {self.registry_path}")

    def register_plugin(
        self,
        plugin_id: str,
        plugin_path: Path,
        metadata: Dict[str, Any],
        *,
        installed_by: Optional[str] = None,
    ) -> None:
        """Register and install a plugin to this tenant.

        Copies plugin_path into installed/<plugin_id>/ and adds an entry to the registry.
        Raises an error if the plugin is already installed.
        """
        self.load_registry()

        # Check if already registered
        if any(p.plugin_id == plugin_id for p in self.plugins):
            raise ValueError(f"plugin {plugin_id!r} is already installed")

        # Ensure source exists
        if not plugin_path.is_dir():
            raise ValueError(f"plugin directory not found: {plugin_path}")

        # Install to tenant
        self._ensure_dirs()
        dest = self.installed_dir / plugin_id
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(plugin_path, dest)

        # Register in index
        entry = TenantPluginEntry(
            plugin_id=plugin_id,
            version=metadata.get("version", "0.1.0"),
            display_name=metadata.get("display_name", plugin_id),
            enabled=True,
            installed_at=datetime.now(timezone.utc).isoformat() + "Z",
            installed_by=installed_by,
            boot_layer=metadata.get("boot_layer", "installed"),
        )
        self.plugins.append(entry)
        self.save_registry()
        log.info(f"registered plugin {plugin_id}@{entry.version} in tenant {self.tenant_id}")

    def unregister_plugin(self, plugin_id: str) -> None:
        """Remove a plugin from the tenant.

        Deletes the plugin directory and removes the registry entry.
        Raises an error if the plugin is not found.
        """
        self.load_registry()

        entry = next((p for p in self.plugins if p.plugin_id == plugin_id), None)
        if entry is None:
            raise ValueError(f"plugin {plugin_id!r} not found in registry")

        # Remove directory
        dest = self.installed_dir / plugin_id
        if dest.exists():
            shutil.rmtree(dest)

        # Remove from registry
        self.plugins = [p for p in self.plugins if p.plugin_id != plugin_id]
        self.save_registry()
        log.info(f"unregistered plugin {plugin_id} from tenant {self.tenant_id}")

    def list_plugins(self) -> List[TenantPluginEntry]:
        """Return all registered plugins."""
        self.load_registry()
        return list(self.plugins)

    def get_plugin_path(self, plugin_id: str) -> Optional[Path]:
        """Return the installation path for a plugin, or None if not found."""
        path = self.installed_dir / plugin_id
        return path if path.is_dir() else None

    def get_plugin_entry(self, plugin_id: str) -> Optional[TenantPluginEntry]:
        """Return the registry entry for a plugin, or None if not found."""
        self.load_registry()
        return next((p for p in self.plugins if p.plugin_id == plugin_id), None)

    def enable_plugin(self, plugin_id: str) -> None:
        """Enable a registered plugin."""
        self.load_registry()
        entry = next((p for p in self.plugins if p.plugin_id == plugin_id), None)
        if entry is None:
            raise ValueError(f"plugin {plugin_id!r} not found")
        entry.enabled = True
        self.save_registry()
        log.info(f"enabled plugin {plugin_id}")

    def disable_plugin(self, plugin_id: str) -> None:
        """Disable a registered plugin."""
        self.load_registry()
        entry = next((p for p in self.plugins if p.plugin_id == plugin_id), None)
        if entry is None:
            raise ValueError(f"plugin {plugin_id!r} not found")
        entry.enabled = False
        self.save_registry()
        log.info(f"disabled plugin {plugin_id}")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize registry state."""
        return {
            "tenant_id": self.tenant_id,
            "schema_version": self.schema_version,
            "plugins": [p.to_dict() for p in self.plugins],
        }


def get_tenant_registry(tenant_id: Optional[str] = None) -> TenantPluginRegistry:
    """Get or create a tenant plugin registry."""
    from corvinOS.shared.paths import _resolve_tenant_id

    resolved_id = _resolve_tenant_id(tenant_id)
    registry = TenantPluginRegistry(tenant_id=resolved_id)
    registry.load_registry()
    return registry


__all__ = [
    "TenantPluginEntry",
    "TenantPluginRegistry",
    "get_tenant_registry",
]
