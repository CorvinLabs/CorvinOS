"""
Plugin Management Routes — Install/enable/disable/uninstall plugins.

PUT    /v1/console/control-plane/plugins/install
GET    /v1/console/control-plane/plugins
PATCH  /v1/console/control-plane/plugins/<id>/enable
PATCH  /v1/console/control-plane/plugins/<id>/disable
DELETE /v1/console/control-plane/plugins/<id>
GET    /v1/console/control-plane/plugins/audit-log

ADR-2029: User-Centric CorvinOS Control Plane
"""

import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class PluginLifecycleState(Enum):
    """Plugin lifecycle states."""
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNINSTALLED = "uninstalled"


@dataclass
class PluginInfo:
    """Plugin metadata."""
    plugin_id: str
    name: str
    version: str
    boot_layer: str  # compliance, core, bundled, installed
    enabled: bool
    dependencies: List[str]
    dependents: List[str]
    installed_at: str
    updated_at: str


class PluginManager:
    """Manage plugin lifecycle with audit logging."""

    def __init__(self, registry_path: Optional[Path] = None):
        """Initialize plugin manager."""
        self.registry_path = registry_path or Path.home() / ".corvin" / "plugins.json"
        self.plugins = self._load_registry()
        self.audit_events = []

    def _load_registry(self) -> Dict[str, Any]:
        """Load plugin registry from disk."""
        if self.registry_path.exists():
            try:
                with open(self.registry_path) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load registry: {e}")
        return {}

    def _save_registry(self) -> None:
        """Save plugin registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.registry_path, "w") as f:
            json.dump(self.plugins, f, indent=2)

    def _emit_audit_event(self, event_type: str, plugin_id: str, details: Dict[str, Any]) -> None:
        """Emit audit event (immutable, for chain logging)."""
        event = {
            "tenant_id": details.get("tenant_id", "default"),
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "plugin_id": plugin_id,
            "operator_id": details.get("operator_id", "unknown"),
            "status": details.get("status", "success"),
            "reason": details.get("reason", ""),
        }
        self.audit_events.append(event)
        logger.info(f"Plugin audit event: {event_type} {plugin_id}")

    async def install_plugin(
        self,
        plugin_id: str,
        name: str,
        version: str,
        boot_layer: str,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Install a plugin from marketplace.

        Args:
            plugin_id: Plugin identifier
            name: Plugin name
            version: Version number
            boot_layer: Boot layer (bundled, installed, community)
            tenant_id: Tenant ID
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event
        """
        if plugin_id in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} already installed",
            }

        # Create plugin entry
        self.plugins[plugin_id] = {
            "name": name,
            "version": version,
            "boot_layer": boot_layer,
            "enabled": False,  # Default disabled
            "dependencies": [],
            "dependents": [],
            "installed_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        # Save + audit
        self._save_registry()
        self._emit_audit_event(
            "plugin_installed",
            plugin_id,
            {
                "tenant_id": tenant_id,
                "operator_id": operator_id,
                "version": version,
                "status": "success",
            }
        )

        return {
            "status": "success",
            "message": f"Plugin {plugin_id} installed",
            "plugin": self.plugins[plugin_id],
        }

    async def enable_plugin(
        self,
        plugin_id: str,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Enable an installed plugin.

        Args:
            plugin_id: Plugin ID
            tenant_id: Tenant ID
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event
        """
        if plugin_id not in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found",
            }

        if self.plugins[plugin_id]["enabled"]:
            return {
                "status": "warning",
                "message": f"Plugin {plugin_id} already enabled",
            }

        # Enable + save
        self.plugins[plugin_id]["enabled"] = True
        self.plugins[plugin_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self._save_registry()

        # Audit
        self._emit_audit_event(
            "plugin_enabled",
            plugin_id,
            {
                "tenant_id": tenant_id,
                "operator_id": operator_id,
                "status": "success",
            }
        )

        return {
            "status": "success",
            "message": f"Plugin {plugin_id} enabled",
        }

    async def disable_plugin(
        self,
        plugin_id: str,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Disable an enabled plugin (graceful).

        Checks for dependent plugins first (cannot disable if others depend on it).

        Args:
            plugin_id: Plugin ID
            tenant_id: Tenant ID
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event (403 if dependencies exist)
        """
        if plugin_id not in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found",
            }

        # Check for dependents
        dependents = self.plugins[plugin_id].get("dependents", [])
        if dependents:
            return {
                "status": "error",
                "code": 403,
                "message": f"Cannot disable {plugin_id}: dependents exist: {dependents}",
            }

        if not self.plugins[plugin_id]["enabled"]:
            return {
                "status": "warning",
                "message": f"Plugin {plugin_id} already disabled",
            }

        # Graceful shutdown: 30s timeout (simulated)
        logger.info(f"Gracefully shutting down plugin {plugin_id}...")

        # Disable + save
        self.plugins[plugin_id]["enabled"] = False
        self.plugins[plugin_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self._save_registry()

        # Audit
        self._emit_audit_event(
            "plugin_disabled",
            plugin_id,
            {
                "tenant_id": tenant_id,
                "operator_id": operator_id,
                "status": "success",
            }
        )

        return {
            "status": "success",
            "message": f"Plugin {plugin_id} disabled",
        }

    async def uninstall_plugin(
        self,
        plugin_id: str,
        tenant_id: str = "default",
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Uninstall a plugin (must be disabled first).

        Args:
            plugin_id: Plugin ID
            tenant_id: Tenant ID
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event (403 if enabled)
        """
        if plugin_id not in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found",
            }

        # Cannot uninstall if enabled
        if self.plugins[plugin_id]["enabled"]:
            return {
                "status": "error",
                "code": 403,
                "message": f"Plugin {plugin_id} must be disabled first",
            }

        # Delete + save
        del self.plugins[plugin_id]
        self._save_registry()

        # Audit
        self._emit_audit_event(
            "plugin_uninstalled",
            plugin_id,
            {
                "tenant_id": tenant_id,
                "operator_id": operator_id,
                "status": "success",
            }
        )

        return {
            "status": "success",
            "message": f"Plugin {plugin_id} uninstalled",
        }

    async def list_plugins(self) -> List[PluginInfo]:
        """List all plugins."""
        return [
            PluginInfo(
                plugin_id=plugin_id,
                name=info["name"],
                version=info["version"],
                boot_layer=info["boot_layer"],
                enabled=info["enabled"],
                dependencies=info.get("dependencies", []),
                dependents=info.get("dependents", []),
                installed_at=info["installed_at"],
                updated_at=info["updated_at"],
            )
            for plugin_id, info in self.plugins.items()
        ]

    async def get_plugin(self, plugin_id: str) -> Optional[PluginInfo]:
        """Get plugin info by ID."""
        if plugin_id not in self.plugins:
            return None

        info = self.plugins[plugin_id]
        return PluginInfo(
            plugin_id=plugin_id,
            name=info["name"],
            version=info["version"],
            boot_layer=info["boot_layer"],
            enabled=info["enabled"],
            dependencies=info.get("dependencies", []),
            dependents=info.get("dependents", []),
            installed_at=info["installed_at"],
            updated_at=info["updated_at"],
        )

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log (immutable)."""
        return list(self.audit_events)
