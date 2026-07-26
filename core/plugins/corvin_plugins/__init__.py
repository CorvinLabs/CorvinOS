"""corvin_plugins — unified plugin system for Corvin (ADR-0030)."""
from __future__ import annotations

from .manifest import (
    BreakingChange,
    CircularDependencyError,
    DependencyConflictError,
    DependencyResolver,
    PIIRisk,
    PluginDependency,
    PluginError,
    PluginManifest,
    PluginOrigin,
    PluginRecord,
    SettingsValidator,
    UnknownPluginType,
    UpdatePolicy,
    ValidationError,
    plan_settings_migration,
)
from .protocol import (
    KNOWN_PLUGIN_TYPES,
    CorvinPlugin,
    HealthStatus,
    PluginAlreadyRegistered,
    PluginContext,
    PluginNotFound,
)
from .registry import (
    PluginRegistry,
    discover,
    get,
    get_registry,
    health_check_all,
    register,
    unregister,
)

__all__ = [
    # protocol — the lifecycle contract (ADR-0030)
    "CorvinPlugin",
    "HealthStatus",
    "KNOWN_PLUGIN_TYPES",
    "PluginAlreadyRegistered",
    "PluginContext",
    "PluginNotFound",
    # registry — runtime registration
    "PluginRegistry",
    "register",
    "unregister",
    "get",
    "health_check_all",
    "discover",
    "get_registry",
    # manifest — registry records, dependency order, settings (ADR-0233)
    "BreakingChange",
    "CircularDependencyError",
    "DependencyConflictError",
    "DependencyResolver",
    "PIIRisk",
    "PluginDependency",
    "PluginError",
    "PluginManifest",
    "PluginOrigin",
    "PluginRecord",
    "SettingsValidator",
    "UnknownPluginType",
    "UpdatePolicy",
    "ValidationError",
    "plan_settings_migration",
]
