"""Plugin Panel Auto-Registration — Phase 3 Integration.

When a plugin installs successfully, this module registers its Console panel
automatically, making the plugin's settings panel appear in the sidebar.

ADR-0455: Plugin Panel Auto-Registration
- Auto-register panels from plugin manifest.yaml
- Store registry on disk (~/.corvin/plugins/panel_registry.json)
- Support enable/disable without uninstall
- Audit trail for all panel operations
- Tenant isolation (per GDPR Art. 5)
- Graceful degradation if console unavailable
"""

from __future__ import annotations

import json
import logging
import hashlib
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


# Content-free keys a panel audit record may carry (registered with the core
# writer's default-deny floor, see forge.security_events.register_event_allowlist).
_PANEL_AUDIT_KEYS = frozenset({"panel_id", "plugin_id", "label", "tenant_id", "audit_ref"})
_PANEL_AUDIT_EVENTS = (
    "panel_registered", "panel_enabled", "panel_disabled",
    "panel_unregistered", "panel_unregistered_on_plugin_uninstall",
)
_panel_allowlists_registered = False


def _register_panel_audit_allowlists() -> None:
    global _panel_allowlists_registered
    if _panel_allowlists_registered:
        return
    try:
        # Resolving the core writer first puts operator/bridges/shared (and
        # with it the forge package) on sys.path exactly like every other
        # core-chain emitter does; only then is ``forge`` importable here.
        from core.learning.event_persistence import _resolve_core_audit  # noqa: PLC0415

        _resolve_core_audit()
        from forge import security_events as _se  # type: ignore[import-not-found]  # noqa: PLC0415

        for ev in _PANEL_AUDIT_EVENTS:
            _se.register_event_allowlist(f"plugin.panel.{ev}", set(_PANEL_AUDIT_KEYS))
        _panel_allowlists_registered = True
    except Exception as e:  # noqa: BLE001
        logger.error("plugin.panel audit allowlist registration failed: %s", e)


@dataclass
class PanelEntry:
    """Panel registry entry for installed plugin panel."""
    panel_id: str          # Unique panel ID (e.g., "plugin-security-settings")
    plugin_id: str         # Which plugin provides this panel
    label: str             # Display label ("Security Settings")
    route: str             # URL route ("/settings/security")
    icon: str              # Icon name for sidebar ("Shield")
    group: str             # Sidebar group ("settings")
    enabled: bool = True   # Can be disabled without uninstall
    registered_at: int = None
    enabled_at: Optional[int] = None
    disabled_at: Optional[int] = None

    def __post_init__(self):
        if self.registered_at is None:
            self.registered_at = int(time.time())


class PluginPanelRegistry:
    """Auto-registration for plugin-supplied Console panels.

    Phase 3 mechanism: when a plugin installs, its manifest.yaml declares
    console.settings_panel, and this registry:
    1. Reads the panel spec
    2. Stores it in panel_registry.json
    3. Updates Console capability manifest
    4. Logs to audit trail (GDPR Art. 30)
    """

    def __init__(self, registry_path: str | None = None, *, tenant_id: str = "_default"):
        # Tenant-scoped, under CORVIN_HOME (never ``~/.corvin`` — that root is
        # not the live one on this install; see core/paths/tenant.py).
        self.tenant_id = tenant_id
        if registry_path is None:
            from core.paths.tenant import tenant_home  # noqa: PLC0415

            registry_path = str(tenant_home(tenant_id) / "plugins" / "panel_registry.json")
        self.path = Path(registry_path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()
        self.data.setdefault("panels", [])
        self.data.setdefault("version", "1.0")

    def register_panel(self, plugin_id: str, panel_spec: Dict[str, Any]) -> str:
        """
        Register a plugin panel from manifest.yaml console.settings_panel.

        Args:
            plugin_id: The plugin providing this panel
            panel_spec: Dict with keys: id, label, route, icon, group

        Returns:
            panel_id on success

        Raises:
            ValueError: Invalid panel spec, missing required keys
            RuntimeError: Registry save failed
        """
        # Validate required fields
        required = {"id", "label", "route", "icon", "group"}
        missing = required - set(panel_spec.keys())
        if missing:
            raise ValueError(f"Panel spec missing required keys: {missing}")

        # Validate route safety (no path traversal)
        route = panel_spec["route"]
        if route.startswith("..") or "//" in route:
            raise ValueError(f"Unsafe panel route: {route}")

        panel_id = panel_spec["id"]

        # Check for duplicate panel_id (same plugin can't register twice)
        if self.get_panel(panel_id):
            raise ValueError(f"Panel already registered: {panel_id}")

        # Create entry
        entry = PanelEntry(
            panel_id=panel_id,
            plugin_id=plugin_id,
            label=panel_spec["label"],
            route=panel_spec["route"],
            icon=panel_spec["icon"],
            group=panel_spec["group"]
        )

        self.data["panels"].append(asdict(entry))
        self._save()

        # Audit log
        self._audit_log("panel_registered", {
            "panel_id": panel_id,
            "plugin_id": plugin_id,
            "label": panel_spec["label"]
        })

        logger.info(f"✓ Panel registered: {panel_id} (plugin: {plugin_id})")
        return panel_id

    def get_panel(self, panel_id: str) -> Optional[Dict[str, Any]]:
        """Get a panel by ID."""
        for p in self.data.get("panels", []):
            if p["panel_id"] == panel_id:
                return p
        return None

    def get_panels_by_plugin(self, plugin_id: str) -> List[Dict[str, Any]]:
        """Get all panels for a plugin."""
        return [p for p in self.data.get("panels", []) if p["plugin_id"] == plugin_id]

    def get_all_enabled_panels(self) -> List[Dict[str, Any]]:
        """Get all enabled panels (for Console registration)."""
        return [p for p in self.data.get("panels", []) if p.get("enabled", True)]

    def enable_panel(self, panel_id: str) -> None:
        """Enable a panel (was disabled)."""
        panel = self.get_panel(panel_id)
        if not panel:
            raise ValueError(f"Panel not found: {panel_id}")

        panel["enabled"] = True
        panel["enabled_at"] = int(time.time())
        panel["disabled_at"] = None
        self._save()

        self._audit_log("panel_enabled", {"panel_id": panel_id})
        logger.info(f"Panel enabled: {panel_id}")

    def disable_panel(self, panel_id: str) -> None:
        """Disable panel (hide from sidebar, but keep registry entry)."""
        panel = self.get_panel(panel_id)
        if not panel:
            raise ValueError(f"Panel not found: {panel_id}")

        panel["enabled"] = False
        panel["disabled_at"] = int(time.time())
        self._save()

        self._audit_log("panel_disabled", {"panel_id": panel_id})
        logger.info(f"Panel disabled: {panel_id}")

    def unregister_panel(self, panel_id: str) -> None:
        """Completely remove a panel (on plugin uninstall)."""
        self.data["panels"] = [p for p in self.data["panels"] if p["panel_id"] != panel_id]
        self._save()

        self._audit_log("panel_unregistered", {"panel_id": panel_id})
        logger.info(f"Panel unregistered: {panel_id}")

    def unregister_plugin_panels(self, plugin_id: str) -> int:
        """Remove all panels for a plugin (on uninstall). Returns count removed."""
        panels_to_remove = self.get_panels_by_plugin(plugin_id)
        count = len(panels_to_remove)

        self.data["panels"] = [
            p for p in self.data["panels"]
            if p["plugin_id"] != plugin_id
        ]
        self._save()

        for panel in panels_to_remove:
            self._audit_log("panel_unregistered_on_plugin_uninstall", {
                "panel_id": panel["panel_id"],
                "plugin_id": plugin_id
            })

        logger.info(f"Unregistered {count} panel(s) for plugin {plugin_id}")
        return count

    def _audit_log(self, event_type: str, data: Dict[str, Any]):
        """Write ``plugin.panel.<event>`` to the CORE hash chain (GDPR Art. 30).

        Until 2026-09-07 this imported a module that does not exist
        (``core.audit.audit_chain``) and therefore *always* logged "audit chain
        not available, skipping" — panel registrations were never on the
        chain. Now goes through the verified-commit core writer; a failed
        commit is logged at ERROR (never silently skipped).
        """
        try:
            from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

            _register_panel_audit_allowlists()
            details = {k: v for k, v in data.items() if k in _PANEL_AUDIT_KEYS}
            core_audit_event(
                f"plugin.panel.{event_type}",
                tenant_id=str(data.get("tenant_id") or self.tenant_id),
                details=details,
            )
        except Exception as e:  # noqa: BLE001 — never silent
            logger.error("plugin.panel.%s audit write FAILED: %s", event_type, e)

    def _load(self) -> Dict[str, Any]:
        """Load registry from disk."""
        if self.path.exists():
            try:
                with open(self.path) as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Registry load error: {e}, using empty")
                return {"version": "1.0", "panels": []}
        return {"version": "1.0", "panels": []}

    def _save(self):
        """Save registry to disk."""
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f, indent=2)
            logger.info(f"Panel registry saved: {len(self.data['panels'])} panels")
        except Exception as e:
            logger.error(f"Registry save error: {e}")
            raise


# Global singleton (thread-safe in production)
_panel_registry_instance: Optional[PluginPanelRegistry] = None


def get_panel_registry() -> PluginPanelRegistry:
    """Get or create the global panel registry singleton."""
    global _panel_registry_instance
    if _panel_registry_instance is None:
        _panel_registry_instance = PluginPanelRegistry()
    return _panel_registry_instance
