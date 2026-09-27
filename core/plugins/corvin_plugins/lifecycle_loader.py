"""Plugin lifecycle loader — reads tenant config and loads plugins.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
real tenant loader is ``corvin_plugins.bootstrap.bootstrap_tenant``. This
module called ``registry.list_plugin_ids()`` / ``registry.get_plugin()``, which
do not exist, so :func:`load_plugins_for_tenant` could never run; it now says
so instead of failing with an AttributeError, and :func:`register_lifecycle_hooks`
no longer reports hooks it never registered.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
import yaml

from forge import paths as forge_paths


logger = logging.getLogger(__name__)


def read_tenant_config(tenant_id: str) -> dict[str, Any]:
    """Read tenant.corvin.yaml for plugin enable/disable state."""
    # tenant_global_dir validates the id (no traversal via a crafted tenant)
    tenant_path = forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"

    if not tenant_path.exists():
        return {}

    try:
        content = yaml.safe_load(tenant_path.read_text("utf-8"))
        return content if isinstance(content, dict) else {}
    except Exception as e:
        logger.warning(f"Failed to read tenant config: {e}")
        return {}


def load_plugins_for_tenant(tenant_id: str) -> dict[str, bool]:
    """Not implemented — use ``corvin_plugins.bootstrap.bootstrap_tenant``.

    Raises NotImplementedError rather than reporting plugins as loaded: the
    registry API this was written against does not exist.
    """
    raise NotImplementedError(
        "lifecycle_loader.load_plugins_for_tenant has no registry to load from; "
        "the tenant loader is corvin_plugins.bootstrap.bootstrap_tenant"
    )


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
    """Boot tripwire: every plugin enabled for the tenant must have loaded.

    A plugin is "enabled" when the tenant config does not set
    ``enabled: false`` for it; the ids checked are those declared in the
    tenant config plus those present in ``results``. Fail-closed: a declared
    or attempted plugin that is enabled but not loaded raises RuntimeError.
    """
    config = read_tenant_config(tenant_id)
    plugins_config = config.get("plugins") or {}
    if not isinstance(plugins_config, dict):
        raise RuntimeError("plugin boot tripwire failed: tenant 'plugins' config is not a mapping")

    for plugin_id in sorted(set(plugins_config) | set(results)):
        entry = plugins_config.get(plugin_id) or {}
        enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
        if enabled and not results.get(plugin_id, False):
            error_msg = (
                f"plugin boot tripwire failed: plugin {plugin_id} declared enabled "
                f"but failed to load. Check logs for details."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    logger.info("plugin boot tripwire passed: all enabled plugins loaded (tenant=%s)", tenant_id)
    return True


def register_lifecycle_hooks(tenant_id: str = "_default") -> None:
    """Not implemented: the plugin registry has no hook interface for these
    emitters. Raises instead of logging a registration that never happened."""
    raise NotImplementedError(
        "plugin lifecycle hooks cannot be registered: the registry exposes no hook "
        "interface (the registry audits load/disable itself via PluginContext.audit_emit)"
    )
