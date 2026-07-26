"""Plugin Lifecycle Manager (ADR-0XXX Phase 1).

Handles: Install → Enable → Config Change → Disable → Uninstall
"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from core.orchestration.plugin_system.models import (
    AuditEvent,
    Plugin,
    PluginAlreadyExists,
    PluginNotFound,
    PluginQuota,
    PluginRegistry,
    PluginState,
    ValidationError,
)


class PluginLifecycleManager:
    """Orchestrates plugin lifecycle events."""

    def __init__(
        self,
        registry: PluginRegistry,
        audit_emit: Callable[[AuditEvent], None],
        base_state_path: Path = Path(".corvin/tenants/_default/plugins/state")
    ):
        """Initialize lifecycle manager.

        Args:
            registry: Plugin registry (handles persistence)
            audit_emit: Callback to emit audit events
            base_state_path: Base directory for plugin state
        """
        self.registry = registry
        self.audit_emit = audit_emit
        self.base_state_path = base_state_path

    def install(
        self,
        plugin: Plugin,
        user_id: Optional[str] = None,
        **hook_kwargs
    ) -> Plugin:
        """Install a plugin.

        1. Verify plugin doesn't already exist
        2. Set metadata (installed_at, installed_by)
        3. Call plugin.on_install() hook (if implementable)
        4. Add to registry
        5. Persist registry
        6. Emit audit event
        """
        # Check if already installed
        try:
            self.registry.get(plugin.id)
            raise PluginAlreadyExists(f"Plugin {plugin.id} already installed")
        except PluginNotFound:
            pass  # Good, not installed yet

        # Update plugin metadata
        plugin_installed = replace(
            plugin,
            installed_at=datetime.utcnow(),
            installed_by=user_id,
            quota=PluginQuota(plugin_id=plugin.id),
            state=PluginState(
                plugin_id=plugin.id,
                storage_path=self.base_state_path / plugin.id
            )
        )

        # Call on_install hook (if plugin is hookable)
        try:
            if hasattr(plugin, "on_install"):
                plugin.on_install(**hook_kwargs)
        except Exception as e:
            raise RuntimeError(f"Plugin {plugin.id} on_install() failed: {e}")

        # Add to registry and persist
        self.registry.add(plugin_installed)
        self.registry.save()

        # Emit audit event
        event = AuditEvent.plugin_installed(
            plugin_id=plugin.full_id(),
            tier=plugin_installed.tier.value,
            user_id=user_id or "system",
            source=(
                getattr(plugin.marketplace, "source", "direct")
                if plugin.marketplace
                else "direct"
            )
        )
        self.audit_emit(event)

        return plugin_installed

    def enable(
        self,
        plugin_id: str,
        user_id: Optional[str] = None,
        **hook_kwargs
    ) -> Plugin:
        """Enable a plugin.

        1. Get plugin from registry
        2. Set enabled=True, enabled_at=now
        3. Call plugin.on_enable() hook
        4. Persist registry
        5. Emit audit event
        """
        plugin = self.registry.get(plugin_id)

        if plugin.enabled:
            return plugin  # Already enabled

        # Call on_enable hook
        try:
            if hasattr(plugin, "on_enable"):
                plugin.on_enable(**hook_kwargs)
        except Exception as e:
            raise RuntimeError(f"Plugin {plugin_id} on_enable() failed: {e}")

        # Update plugin
        plugin_enabled = replace(
            plugin,
            enabled=True,
            enabled_at=datetime.utcnow()
        )

        self.registry.plugins[plugin_id] = plugin_enabled
        self.registry.save()

        # Emit audit event
        event = AuditEvent.plugin_enabled(
            plugin_id=plugin_enabled.full_id(),
            user_id=user_id or "system"
        )
        self.audit_emit(event)

        return plugin_enabled

    def config_change(
        self,
        plugin_id: str,
        new_settings: Dict[str, Any],
        user_id: Optional[str] = None,
        **hook_kwargs
    ) -> Plugin:
        """Change plugin configuration.

        1. Get plugin from registry
        2. Validate new settings against schema
        3. Call plugin.on_config_change() hook
        4. Update settings
        5. Persist registry
        6. Emit audit event
        """
        plugin = self.registry.get(plugin_id)
        old_settings = plugin.settings.copy()

        # Validate new settings (if schema exists)
        if plugin.settings_schema:
            from core.orchestration.plugin_system.models import SettingsValidator
            validator = SettingsValidator(plugin.settings_schema)
            try:
                validator.validate(new_settings)
            except ValidationError as e:
                raise ValidationError(f"Settings validation failed: {e}")

        # Call on_config_change hook
        try:
            if hasattr(plugin, "on_config_change"):
                plugin.on_config_change(old_settings, new_settings, **hook_kwargs)
        except Exception as e:
            raise RuntimeError(f"Plugin {plugin_id} on_config_change() failed: {e}")

        # Update plugin
        plugin_updated = replace(plugin, settings=new_settings)

        self.registry.plugins[plugin_id] = plugin_updated
        self.registry.save()

        # Emit audit event
        event = AuditEvent.plugin_config_changed(
            plugin_id=plugin_updated.full_id(),
            user_id=user_id or "system",
            old_config=old_settings,
            new_config=new_settings
        )
        self.audit_emit(event)

        return plugin_updated

    def disable(
        self,
        plugin_id: str,
        user_id: Optional[str] = None,
        **hook_kwargs
    ) -> Plugin:
        """Disable a plugin.

        1. Get plugin from registry
        2. Set enabled=False
        3. Call plugin.on_disable() hook
        4. Persist registry
        5. Emit audit event
        """
        plugin = self.registry.get(plugin_id)

        if not plugin.enabled:
            return plugin  # Already disabled

        # Call on_disable hook
        try:
            if hasattr(plugin, "on_disable"):
                plugin.on_disable(**hook_kwargs)
        except Exception as e:
            raise RuntimeError(f"Plugin {plugin_id} on_disable() failed: {e}")

        # Update plugin
        plugin_disabled = replace(plugin, enabled=False)

        self.registry.plugins[plugin_id] = plugin_disabled
        self.registry.save()

        # Emit audit event
        event = AuditEvent(
            timestamp=datetime.utcnow(),
            event_type="plugin_disabled",
            plugin_id=plugin_disabled.full_id(),
            user_id=user_id or "system"
        )
        self.audit_emit(event)

        return plugin_disabled

    def uninstall(
        self,
        plugin_id: str,
        user_id: Optional[str] = None,
        **hook_kwargs
    ) -> None:
        """Uninstall a plugin.

        1. Get plugin from registry
        2. Call plugin.on_uninstall() hook
        3. Remove from registry
        4. Delete state directory
        5. Persist registry
        6. Emit audit event
        """
        plugin = self.registry.get(plugin_id)

        # Call on_uninstall hook
        try:
            if hasattr(plugin, "on_uninstall"):
                plugin.on_uninstall(**hook_kwargs)
        except Exception as e:
            raise RuntimeError(f"Plugin {plugin_id} on_uninstall() failed: {e}")

        # Delete state directory
        if plugin.state and plugin.state.storage_path.exists():
            import shutil
            shutil.rmtree(plugin.state.storage_path)

        # Remove from registry
        self.registry.remove(plugin_id)
        self.registry.save()

        # Emit audit event
        event = AuditEvent(
            timestamp=datetime.utcnow(),
            event_type="plugin_uninstalled",
            plugin_id=plugin.full_id(),
            user_id=user_id or "system"
        )
        self.audit_emit(event)
