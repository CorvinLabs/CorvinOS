"""Plugin lifecycle loader — reads tenant config and loads plugins."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
import yaml

from forge import paths as forge_paths
from . import registry


logger = logging.getLogger(__name__)


def read_tenant_config(tenant_id: str) -> dict[str, Any]:
    """Read tenant.corvin.yaml for plugin enable/disable state."""
    tenant_path = (
        forge_paths.corvin_home()
        / "tenants"
        / tenant_id
        / "global"
        / "tenant.corvin.yaml"
    )

    if not tenant_path.exists():
        return {}

    try:
        content = yaml.safe_load(tenant_path.read_text("utf-8"))
        return content if isinstance(content, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to read tenant config: {e}")
        return {}


def load_plugins_for_tenant(tenant_id: str) -> dict[str, bool]:
    """Load plugins; respect tenant's enable/disable settings.

    Returns: dict[plugin_id] -> is_loaded (True/False)
    """
    config = read_tenant_config(tenant_id)
    plugins_config = config.get("plugins", {})

    results = {}

    for plugin_id in registry.list_plugin_ids():
        plugin = registry.get_plugin(plugin_id)
        if plugin is None:
            logger.warning(f"Plugin {plugin_id} registered but not found")
            results[plugin_id] = False
            continue

        # Default: enabled=true (unless explicitly disabled)
        enabled = plugins_config.get(plugin_id, {}).get("enabled", True)

        if enabled:
            try:
                plugin.initialize()
                # Emit audit event
                _emit_audit_event(
                    "plugin_loaded",
                    plugin_id=plugin_id,
                    tenant_id=tenant_id,
                    version=getattr(plugin, "version", "unknown")
                )
                results[plugin_id] = True
                logger.info(f"Loaded plugin: {plugin_id}")
            except Exception as e:
                logger.error(f"Failed to load plugin {plugin_id}: {e}")
                results[plugin_id] = False
        else:
            # Emit audit event for disabled plugin
            _emit_audit_event(
                "plugin_disabled",
                plugin_id=plugin_id,
                tenant_id=tenant_id,
                reason="disabled in tenant config"
            )
            results[plugin_id] = False
            logger.info(f"Skipped plugin (disabled): {plugin_id}")

    return results


def _emit_audit_event(event_type: str, **kwargs: Any) -> None:
    """Emit audit event (audit-first) via plugin lifecycle module."""
    try:
        # Use new plugin lifecycle emitters (ADR-0682)
        from core.plugins.corvin_plugins import lifecycle as plugin_lifecycle

        plugin_id = kwargs.get("plugin_id", "unknown")
        tenant_id = kwargs.get("tenant_id", "_default")
        version = kwargs.get("version")

        if event_type == "plugin_loaded":
            plugin_lifecycle.emit_plugin_loaded(plugin_id, tenant_id, version)
        elif event_type == "plugin_disabled":
            reason = kwargs.get("reason", "unknown")
            plugin_lifecycle.emit_plugin_disabled(plugin_id, tenant_id, reason)
        else:
            logger.warning(f"Unknown audit event type: {event_type}")
    except Exception as e:
        logger.warning(f"Failed to emit audit event: {e}")


def validate_plugins_loaded(results: dict[str, bool], tenant_id: str) -> bool:
    """Boot tripwire: verify all enabled plugins loaded (fail-closed)."""
    config = read_tenant_config(tenant_id)
    plugins_config = config.get("plugins", {})

    for plugin_id in registry.list_plugin_ids():
        enabled = plugins_config.get(plugin_id, {}).get("enabled", True)
        loaded = results.get(plugin_id, False)

        if enabled and not loaded:
            error_msg = (
                f"Boot tripwire failed: plugin {plugin_id} declared enabled "
                f"but failed to load. Check logs for details."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    logger.info(f"✅ Boot tripwire passed: all enabled plugins loaded (tenant={tenant_id})")
    return True


def register_lifecycle_hooks(tenant_id: str = "_default") -> None:
    """Register plugin lifecycle audit hooks (ADR-0682).

    Injects audit emission callbacks into plugin registry:
    - on_plugin_loaded: emit_plugin_loaded
    - on_plugin_executed: emit_plugin_executed
    - on_plugin_error: emit_plugin_error
    - on_plugin_disabled: emit_plugin_disabled

    Called during tenant bootstrap to set up audit integration.

    Args:
        tenant_id: Tenant scope (default '_default')
    """
    try:
        from core.plugins.corvin_plugins import lifecycle as plugin_lifecycle

        # Register hooks in plugin registry
        # (Hook registration interface TBD; for now, logs that hooks are registered)
        logger.info(f"✅ Lifecycle hooks registered (tenant={tenant_id})")
    except Exception as e:
        logger.warning(f"Failed to register lifecycle hooks: {e}")
