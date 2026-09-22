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


class BootLayer(Enum):
    """Valid boot layer values (ADR-0243)."""
    COMPLIANCE = "compliance"
    CORE = "core"
    BUNDLED = "bundled"
    INSTALLED = "installed"
    COMMUNITY = "community"


@dataclass
class PluginInfo:
    """Plugin metadata."""
    plugin_id: str
    name: str
    version: str
    boot_layer: str  # compliance, core, bundled, installed, community
    enabled: bool
    dependencies: List[str]
    dependents: List[str]
    installed_at: str
    updated_at: str
    tenant_id: str  # Tenant scope (for multi-tenant isolation)


class PluginManager:
    """Manage plugin lifecycle with audit logging.

    Enforces tenant isolation: each tenant's plugins are stored separately.
    Audit events are immutable and tenant-scoped.
    """

    def __init__(self, registry_path: Optional[Path] = None):
        """Initialize plugin manager."""
        self.registry_path = registry_path or Path.home() / ".corvin" / "plugins.json"
        self.plugins = self._load_registry()  # tenant_id -> plugin_id -> plugin_info
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

    def _validate_boot_layer(self, boot_layer: str) -> None:
        """Validate boot_layer against BootLayer enum.

        Fail-closed: reject any value not in the enum.

        Args:
            boot_layer: Boot layer string

        Raises:
            ValueError: If boot_layer is not a valid BootLayer
        """
        try:
            BootLayer(boot_layer)
        except ValueError:
            valid_layers = [bl.value for bl in BootLayer]
            raise ValueError(
                f"Invalid boot_layer '{boot_layer}'. Must be one of: {', '.join(valid_layers)}"
            )

    def _validate_tenant_id(self, tenant_id: Optional[str]) -> None:
        """Validate tenant_id is non-empty.

        Fail-closed: reject None or empty strings.

        Args:
            tenant_id: Tenant identifier

        Raises:
            ValueError: If tenant_id is None or empty
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("tenant_id must be a non-empty string")

    def _emit_audit_event(
        self,
        event_type: str,
        plugin_id: str,
        tenant_id: str,
        details: Dict[str, Any]
    ) -> None:
        """Emit immutable audit event (tenant-scoped).

        All events are immutable and scoped to a tenant (no cross-tenant leakage).

        Args:
            event_type: Type of audit event
            plugin_id: Plugin identifier
            tenant_id: Tenant scope (REQUIRED, fail-closed if missing)
            details: Additional event details

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        event = {
            "tenant_id": tenant_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "plugin_id": plugin_id,
            "operator_id": details.get("operator_id", "unknown"),
            "status": details.get("status", "success"),
            "reason": details.get("reason", ""),
        }
        self.audit_events.append(event)
        logger.info(f"Plugin audit event: {event_type} {plugin_id} (tenant={tenant_id})")

    async def install_plugin(
        self,
        plugin_id: str,
        name: str,
        version: str,
        boot_layer: str,
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Install a plugin from marketplace (tenant-scoped).

        Args:
            plugin_id: Plugin identifier
            name: Plugin name
            version: Version number
            boot_layer: Boot layer (bundled, installed, community, core, compliance)
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event

        Raises:
            ValueError: If tenant_id or boot_layer are invalid
        """
        # Fail-closed validation
        self._validate_tenant_id(tenant_id)
        self._validate_boot_layer(boot_layer)

        # Ensure tenant key exists
        if tenant_id not in self.plugins:
            self.plugins[tenant_id] = {}

        # Check if plugin already installed for this tenant
        if plugin_id in self.plugins[tenant_id]:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} already installed for tenant {tenant_id}",
            }

        # Create plugin entry (tenant-scoped)
        self.plugins[tenant_id][plugin_id] = {
            "name": name,
            "version": version,
            "boot_layer": boot_layer,
            "enabled": False,  # Default disabled
            "dependencies": [],
            "dependents": [],
            "installed_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "tenant_id": tenant_id,
        }

        # Save + audit
        self._save_registry()
        self._emit_audit_event(
            "plugin_installed",
            plugin_id,
            tenant_id,
            {
                "operator_id": operator_id,
                "version": version,
                "status": "success",
            }
        )

        return {
            "status": "success",
            "message": f"Plugin {plugin_id} installed for tenant {tenant_id}",
            "plugin": self.plugins[tenant_id][plugin_id],
        }

    async def enable_plugin(
        self,
        plugin_id: str,
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Enable an installed plugin (tenant-scoped).

        Args:
            plugin_id: Plugin ID
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Check tenant key exists
        if tenant_id not in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found for tenant {tenant_id}",
            }

        if plugin_id not in self.plugins[tenant_id]:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found for tenant {tenant_id}",
            }

        if self.plugins[tenant_id][plugin_id]["enabled"]:
            return {
                "status": "warning",
                "message": f"Plugin {plugin_id} already enabled",
            }

        # Enable + save
        self.plugins[tenant_id][plugin_id]["enabled"] = True
        self.plugins[tenant_id][plugin_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self._save_registry()

        # Audit
        self._emit_audit_event(
            "plugin_enabled",
            plugin_id,
            tenant_id,
            {
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
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Disable an enabled plugin (graceful, with dependency checking).

        Checks for dependent plugins first (cannot disable if others depend on it).
        Fail-closed: if dependents exist, deny the operation.

        Args:
            plugin_id: Plugin ID
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event (403 if dependents exist)

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Check tenant key exists
        if tenant_id not in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found for tenant {tenant_id}",
            }

        if plugin_id not in self.plugins[tenant_id]:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found for tenant {tenant_id}",
            }

        # CRITICAL: Check for dependent plugins (fail-closed)
        dependents = self.plugins[tenant_id][plugin_id].get("dependents", [])
        if dependents:
            # Fail-closed: deny if dependents exist
            logger.warning(
                f"Cannot disable plugin {plugin_id}: "
                f"dependents exist: {dependents} (tenant={tenant_id})"
            )
            return {
                "status": "error",
                "code": 403,
                "message": f"Cannot disable {plugin_id}: {len(dependents)} dependent plugin(s) exist: {', '.join(dependents)}",
                "dependents": dependents,
            }

        if not self.plugins[tenant_id][plugin_id]["enabled"]:
            return {
                "status": "warning",
                "message": f"Plugin {plugin_id} already disabled",
            }

        # Graceful shutdown: 30s timeout (simulated)
        logger.info(f"Gracefully shutting down plugin {plugin_id} (tenant={tenant_id})...")

        # Disable + save
        self.plugins[tenant_id][plugin_id]["enabled"] = False
        self.plugins[tenant_id][plugin_id]["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self._save_registry()

        # Audit
        self._emit_audit_event(
            "plugin_disabled",
            plugin_id,
            tenant_id,
            {
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
        tenant_id: str,
        operator_id: str = "unknown"
    ) -> Dict[str, Any]:
        """
        Uninstall a plugin (must be disabled first, tenant-scoped).

        Args:
            plugin_id: Plugin ID
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)
            operator_id: Operator ID for audit

        Returns:
            Result with status + audit event (403 if enabled)

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Check tenant key exists
        if tenant_id not in self.plugins:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found for tenant {tenant_id}",
            }

        if plugin_id not in self.plugins[tenant_id]:
            return {
                "status": "error",
                "message": f"Plugin {plugin_id} not found for tenant {tenant_id}",
            }

        # Cannot uninstall if enabled
        if self.plugins[tenant_id][plugin_id]["enabled"]:
            return {
                "status": "error",
                "code": 403,
                "message": f"Plugin {plugin_id} must be disabled first",
            }

        # Delete + save
        del self.plugins[tenant_id][plugin_id]
        self._save_registry()

        # Audit
        self._emit_audit_event(
            "plugin_uninstalled",
            plugin_id,
            tenant_id,
            {
                "operator_id": operator_id,
                "status": "success",
            }
        )

        return {
            "status": "success",
            "message": f"Plugin {plugin_id} uninstalled from tenant {tenant_id}",
        }

    async def list_plugins(self, tenant_id: str) -> List[PluginInfo]:
        """List all plugins for a tenant (tenant-scoped).

        Args:
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)

        Returns:
            List of PluginInfo for this tenant only

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Return empty list if tenant not found (no cross-tenant leakage)
        if tenant_id not in self.plugins:
            return []

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
                tenant_id=tenant_id,
            )
            for plugin_id, info in self.plugins[tenant_id].items()
        ]

    async def get_plugin(self, plugin_id: str, tenant_id: str) -> Optional[PluginInfo]:
        """Get plugin info by ID (tenant-scoped).

        Args:
            plugin_id: Plugin identifier
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)

        Returns:
            PluginInfo if found and belongs to tenant, None otherwise

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Return None if tenant not found (no cross-tenant leakage)
        if tenant_id not in self.plugins:
            return None

        if plugin_id not in self.plugins[tenant_id]:
            return None

        info = self.plugins[tenant_id][plugin_id]
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
            tenant_id=tenant_id,
        )

    def get_audit_log(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Get audit log for a tenant (tenant-scoped, immutable).

        Filters all audit events to only those for this tenant.
        No cross-tenant leakage.

        Args:
            tenant_id: Tenant ID (REQUIRED, fail-closed if missing)

        Returns:
            List of audit events for this tenant only

        Raises:
            ValueError: If tenant_id is invalid
        """
        self._validate_tenant_id(tenant_id)

        # Filter audit events by tenant_id (fail-closed: no leakage)
        return [
            event for event in self.audit_events
            if event.get("tenant_id") == tenant_id
        ]
