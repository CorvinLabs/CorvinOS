# Plugin System End-to-End Integration Guide

**Status:** Production Ready (v0.8+)  
**Date:** 2026-08-29  
**Version:** 1.0

## Overview

The CorvinOS plugin system is a complete, production-ready infrastructure for discovering, loading, managing, and executing plugins with full audit trail integration, consent enforcement, and compliance layer protection.

This guide documents the complete end-to-end lifecycle and how all components integrate together.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Complete Plugin Lifecycle](#complete-plugin-lifecycle)
3. [Core Components](#core-components)
4. [Integration Patterns](#integration-patterns)
5. [API Reference](#api-reference)
6. [Error Handling](#error-handling)
7. [Compliance & Security](#compliance--security)
8. [Troubleshooting](#troubleshooting)

---

## System Architecture

### Four Integration Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Console / CLI / External Systems (HTTP / subprocess)        │
├─────────────────────────────────────────────────────────────┤
│ Registry Interface (PluginRegistry)                         │
│ - discover()                                                │
│ - plugins_by_boot_layer()                                   │
│ - plugins_by_type()                                         │
│ - plugins_with_filters()                                    │
├─────────────────────────────────────────────────────────────┤
│ Lifecycle Manager (PluginLifecycle)                         │
│ - enable() → calls on_load()                                │
│ - disable() → calls on_unload()                             │
│ - health_check()                                            │
├─────────────────────────────────────────────────────────────┤
│ Persistent Storage (TenantRegistry / registry.yaml)         │
│ - Plugin metadata & origin                                  │
│ - Boot layer, consent status, settings                      │
│ - Atomic writes + backup recovery                           │
├─────────────────────────────────────────────────────────────┤
│ Audit Trail (hash-chained audit.jsonl)                      │
│ - Every plugin mutation is logged                           │
│ - Immutable, append-only, cryptographically chained         │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User Action (Console / CLI)
         ↓
   Routes / Handlers
         ↓
  Lifecycle Manager (enable/disable/install)
         ↓
   Registry & Storage (persist state)
         ↓
   Audit Trail (hash-chain event)
         ↓
   Plugin Lifecycle Hooks (on_load/on_unload)
         ↓
   Completion (response to user)
```

---

## Complete Plugin Lifecycle

### Phase 1: Discovery

A plugin is discovered through one of three mechanisms:

#### 1a. From Tenant Configuration

```yaml
# tenant.corvin.yaml
spec:
  plugins:
    installed:
      - id: my-plugin
        class_path: mypackage.plugins:MyPlugin
      - id: another-plugin
        # No class_path; will be resolved via entry_points
```

#### 1b. From Entry Points

```toml
# setup.py or pyproject.toml
[project.entry-points."corvin.plugins"]
my_plugin = "mypackage.plugins:MyPlugin"
```

#### 1c. Runtime Registration

```python
from core.plugins.corvin_plugins import registry

plugin = MyPlugin()
ctx = PluginContext(
    plugin_id="my-plugin",
    tenant_id="tenant-1",
    operator_id="admin@example.com",
)

registry.register(plugin, ctx, boot_layer=BootLayer.BUNDLED)
```

### Phase 2: Registration

When a plugin is discovered and loaded:

1. **Instantiation**: Plugin class is instantiated (calls `__init__`)
2. **Context Creation**: `PluginContext` captures tenant, operator, and metadata
3. **Boot Layer Assignment**: Plugin is assigned to compliance/core/bundled/installed
4. **Audit Event Emission**: `plugin.installed` event is written to audit trail
5. **Registry Update**: Plugin is added to runtime registry
6. **Storage**: Plugin metadata is persisted to `registry.yaml`

### Phase 3: Enable/Load

When a plugin is enabled:

1. **Consent Check**: If `consent_required()`, operator must grant `consent_granted_by`
2. **Dependency Resolution**: Plugin dependencies are loaded first
3. **on_load Hook**: Plugin's `on_load()` async method is called
4. **Provider Registration**: Plugin's provider backends are registered (if applicable)
5. **Audit Event**: `plugin.enabled` event is recorded
6. **Health Check**: Immediate health check to verify successful load

### Phase 4: Execution

When the system uses a plugin:

1. **Hook Invocation**: Appropriate hook is called (e.g., `on_task_start`, `on_error`)
2. **Context Passing**: Plugin receives `ExecutionContext` with tenant/operator info
3. **Audit Trail**: Hook invocation is logged (optional)
4. **Response Handling**: Plugin response is validated and used
5. **Error Recovery**: Errors are caught, logged, and don't crash the core

### Phase 5: Disable/Unload

When a plugin is disabled:

1. **Check Privilege**: Compliance layer plugins cannot be disabled
2. **Check Dependents**: Plugins depending on this one are checked
3. **on_unload Hook**: Plugin's `on_unload()` method is called
4. **Provider Detach**: Plugin's provider backends are unregistered
5. **Audit Event**: `plugin.disabled` event is recorded
6. **Registry Update**: Plugin is marked disabled

### Phase 6: Uninstall

When a plugin is uninstalled:

1. **Must be disabled first** (disable → then uninstall)
2. **State cleanup**: Plugin's `instances/<plugin_id>/` directory is removed
3. **Registry removal**: Plugin record is deleted from `registry.yaml`
4. **Audit event**: `plugin.uninstalled` event is recorded

---

## Core Components

### 1. PluginRegistry

**File**: `core/plugins/corvin_plugins/registry.py`

**Purpose**: Runtime discovery and lifecycle of plugins

**Key Methods**:

```python
# Discovery
registry.discover() -> list[str]
    # Returns sorted list of all registered plugin IDs

# Filtering
registry.plugins_by_boot_layer(BootLayer.BUNDLED) -> list[CorvinPlugin]
    # Returns all plugins on a specific boot layer

registry.plugins_by_type("audit_backend") -> list[CorvinPlugin]
    # Returns all plugins of a specific type

registry.plugins_with_filters(
    boot_layer=BootLayer.BUNDLED,
    plugin_type="audit_backend"
) -> list[CorvinPlugin]
    # Combined filtering with AND logic

# Lifecycle
registry.register(plugin, ctx, boot_layer=BootLayer.BUNDLED)
    # Register a plugin instance

registry.unregister(plugin_id, operator_initiated=True)
    # Unregister/disable a plugin

registry.disable(plugin_id)
    # Operator-facing unload (refuses compliance layer)

registry.can_disable(plugin_id) -> bool
    # Check if plugin can be disabled

registry.boot_layer_of(plugin_id) -> BootLayer
    # Get the boot layer of a plugin

# Health
registry.health_check(plugin_id) -> HealthStatus
    # Get plugin health status (deadline: 2.0s)

registry.health_all() -> dict[str, HealthStatus]
    # Get health status of all plugins
```

### 2. TenantRegistry

**File**: `core/plugins/corvin_plugins/state.py`

**Purpose**: Persistent plugin metadata storage (on disk)

**Key Methods**:

```python
# Load/Save
registry = TenantRegistry.load(
    tenant_id="tenant-1",
    auto_recover=True,  # Recover from backup on corruption
)
registry.save()

# Query
registry.get(plugin_id) -> PluginRecord
    # Get plugin metadata by ID

registry.records_dict() -> dict[str, PluginRecord]
    # Get all records as dictionary

registry.by_boot_layer(BootLayer.BUNDLED) -> list[PluginRecord]
    # Filter by boot layer

# Mutations
registry.install(record: PluginRecord)
    # Add new plugin record

registry.enable(plugin_id, consent_granted_by=None)
    # Mark plugin as enabled

registry.disable(plugin_id)
    # Mark plugin as disabled

registry.uninstall(plugin_id)
    # Remove plugin record
```

### 3. PluginLifecycle

**File**: `core/plugins/corvin_plugins/state.py`

**Purpose**: Coordinating disk + runtime state transitions

**Key Methods**:

```python
lifecycle = PluginLifecycle(tenant_registry=registry)

# Lifecycle operations
lifecycle.install(
    plugin_id="my-plugin",
    version="1.0.0",
    origin=PluginOrigin.BUILTIN,
)

lifecycle.enable(
    plugin_id="my-plugin",
    consent_granted_by="operator@example.com",  # Required if consent_required
)

lifecycle.disable(plugin_id="my-plugin")

lifecycle.uninstall(plugin_id="my-plugin")

# Status
lifecycle.get_status(plugin_id) -> PluginRecord
    # Get current plugin status
```

### 4. Plugin Loader

**File**: `core/plugins/corvin_plugins/loader.py`

**Purpose**: Dynamic plugin discovery and instantiation

**Key Functions**:

```python
# Load by class path
cls = load_from_class_path("mypackage.plugins:MyPlugin")
cls = load_from_class_path("mypackage.plugins.MyPlugin")  # Dot syntax

# Load from entry points
classes = load_from_entry_points(
    group="corvin.plugins",
    names=["my_plugin", "another_plugin"],  # Optional: only these names
)

# Discover and load from tenant config
plugins = discover_and_load(
    tenant_config=tenant_yaml,
    corvin_home=Path.home() / ".corvin",
    on_error=lambda pid, reason, error_type: log.error(...)
)
```

---

## Integration Patterns

### Pattern 1: Discovering Plugins (Read-Only)

```python
from core.plugins import corvin_plugins

# List all plugins
all_plugins = corvin_plugins.registry.discover()
# ['plugin-1', 'plugin-2', 'audit-backend']

# List plugins on compliance layer
compliance_plugins = corvin_plugins.registry.plugins_by_boot_layer(
    BootLayer.COMPLIANCE
)

# List all audit backends
audit_backends = corvin_plugins.registry.plugins_by_type("audit_backend")

# List bundled audit backends
bundled_audits = corvin_plugins.registry.plugins_with_filters(
    boot_layer=BootLayer.BUNDLED,
    plugin_type="audit_backend"
)
```

### Pattern 2: Registering a Plugin

```python
from core.plugins import corvin_plugins
from core.plugins.corvin_plugins.manifest import BootLayer
from core.plugins.corvin_plugins.protocol import PluginContext

# Instantiate the plugin
plugin = MyAuditBackend()

# Create execution context
ctx = PluginContext(
    plugin_id="my-audit-backend",
    tenant_id="tenant-1",
    operator_id="admin@example.com",
)

# Register it
corvin_plugins.registry.register(
    plugin,
    ctx,
    boot_layer=BootLayer.BUNDLED
)

# Verify
assert "my-audit-backend" in corvin_plugins.registry.discover()
```

### Pattern 3: Enabling a Plugin with Consent

```python
# Get tenant registry
registry = TenantRegistry.load(tenant_id="tenant-1")
lifecycle = PluginLifecycle(tenant_registry=registry)

# Install first (if new)
lifecycle.install(
    plugin_id="my-plugin",
    version="1.0.0",
    origin=PluginOrigin.VETTED,
)

# Enable (with consent if required)
record = registry.get("my-plugin")
if record.consent_required():
    # Get operator consent
    consent_granted_by = "operator@example.com"
else:
    consent_granted_by = None

lifecycle.enable(
    "my-plugin",
    consent_granted_by=consent_granted_by
)

# Verify health
health = corvin_plugins.registry.health_check("my-plugin")
print(f"Plugin health: ok={health.ok}, message={health.message}")
```

### Pattern 4: Console Integration

```python
# In a Flask route handler
from core.plugins.corvin_plugins.state import TenantRegistry
from core.plugins import corvin_plugins

@app.route("/v1/vibe/plugins", methods=["GET"])
def list_plugins():
    tenant_id = session["tenant_id"]  # From authenticated session
    
    # Load disk registry for metadata
    disk_registry = TenantRegistry.load(tenant_id=tenant_id)
    
    # Get runtime state
    runtime_plugins = corvin_plugins.registry.discover()
    
    # Combine: disk metadata + runtime state
    result = []
    for record in disk_registry.records.values():
        is_enabled = record.plugin_id in runtime_plugins
        result.append({
            "id": record.plugin_id,
            "name": record.name,
            "version": record.version,
            "origin": record.origin.name,  # builtin/vetted/community
            "boot_layer": record.boot_layer.name,
            "enabled": is_enabled,
            "pii_risk": record.pii_risk.name,
            "requires_consent": record.consent_required(),
        })
    
    return jsonify(result)
```

### Pattern 5: Error Handling

```python
from core.plugins.corvin_plugins.protocol import (
    PluginNotFound,
    PluginDisableRefused,
    PluginAlreadyRegistered,
)

# Disable a plugin with error handling
try:
    corvin_plugins.registry.disable("my-plugin")
except PluginDisableRefused:
    # Cannot disable compliance layer plugins
    log.error("Cannot disable compliance layer plugin")
except PluginNotFound:
    # Plugin is not registered
    log.error("Plugin not found")

# Get safe boot layer
try:
    layer = corvin_plugins.registry.boot_layer_of("unknown-plugin")
except PluginNotFound:
    # Handle missing plugin
    layer = BootLayer.INSTALLED  # Default
```

---

## API Reference

### PluginRegistry Methods

```python
class PluginRegistry:
    """Thread-safe runtime plugin registry."""

    def register(
        self,
        plugin: CorvinPlugin,
        ctx: PluginContext,
        *,
        boot_layer: BootLayer | str | None = None,
    ) -> None:
        """Register a plugin instance."""

    def unregister(
        self,
        plugin_id: str,
        *,
        operator_initiated: bool = False,
    ) -> None:
        """Unregister a plugin."""

    def disable(self, plugin_id: str) -> None:
        """Operator-facing unload (refuses compliance layer)."""

    def can_disable(self, plugin_id: str) -> bool:
        """Check if plugin can be disabled."""

    def get(self, plugin_id: str) -> CorvinPlugin:
        """Get plugin instance by ID."""

    def discover(self) -> list[str]:
        """List all registered plugin IDs."""

    def plugins_by_boot_layer(
        self,
        boot_layer: BootLayer | str,
    ) -> list[CorvinPlugin]:
        """Filter plugins by boot layer."""

    def plugins_by_type(self, plugin_type: str) -> list[CorvinPlugin]:
        """Filter plugins by type."""

    def plugins_with_filters(
        self,
        boot_layer: BootLayer | str | None = None,
        plugin_type: str | None = None,
    ) -> list[CorvinPlugin]:
        """Filter plugins by multiple criteria (AND logic)."""

    def health_check(self, plugin_id: str) -> HealthStatus:
        """Check plugin health (deadline: 2.0s)."""

    def health_all(self) -> dict[str, HealthStatus]:
        """Get health status of all plugins."""

    def boot_layer_of(self, plugin_id: str) -> BootLayer:
        """Get boot layer of plugin."""

    def replace(
        self,
        plugin: CorvinPlugin,
        ctx: PluginContext,
        *,
        replaces: str,
        boot_layer: BootLayer | str | None = None,
    ) -> None:
        """Replace a core boot layer plugin."""
```

---

## Error Handling

### Exception Hierarchy

```python
# Base exception
PluginException

# Specific exceptions
├── PluginNotFound("plugin_id not registered")
├── PluginAlreadyRegistered("plugin_id already registered")
├── PluginDisableRefused("cannot disable compliance layer")
├── PluginReplacementRefused("old plugin not registered")
├── ConsentRequired("operator consent needed")
├── HealthCheckTimeout("plugin health check timed out")
├── RegistryCorrupt("registry.yaml unreadable")
└── PluginException (catch-all)
```

### Error Handling Strategies

```python
# 1. Graceful degradation
try:
    plugin = registry.get("optional-plugin")
except PluginNotFound:
    plugin = None  # Continue without it

# 2. Operator-facing errors
try:
    registry.disable("compliance-plugin")
except PluginDisableRefused as e:
    return {"error": "Cannot disable compliance layer plugin"}, 403

# 3. Audit trail
try:
    lifecycle.enable("plugin", consent_granted_by="admin@example.com")
except ConsentRequired as e:
    audit.log_event("plugin.enable_failed", {"reason": "consent_required"})
    raise

# 4. Recovery
registry = TenantRegistry.load(tenant_id="t", auto_recover=True)
# If registry.yaml is corrupt, auto-recovery from .bak is attempted
```

---

## Compliance & Security

### Boot Layer Hierarchy

```
COMPLIANCE (non-disableable, core audit trail)
    ↓
CORE (replaceable with alternatives)
    ↓
BUNDLED (shipped with CorvinOS, disableable)
    ↓
INSTALLED (user-installed, disableable)
```

### Consent Enforcement

Plugins can require operator consent before enable:

```python
record = registry.get("plugin-id")
if record.consent_required():
    # Console must show disclosure to operator
    # Operator grants consent explicitly
    lifecycle.enable(
        "plugin-id",
        consent_granted_by="operator@example.com"
    )
```

### Audit Trail

Every plugin mutation is recorded:

```
plugin.installed    → Emitted when plugin is added to registry
plugin.enabled      → Emitted when plugin is enabled
plugin.disabled     → Emitted when plugin is disabled
plugin.uninstalled  → Emitted when plugin is removed
plugin.health_alert → Emitted when health check fails
plugin.config_changed → Emitted when settings change
```

All events are:
- Hash-chained (append-only)
- Include tenant_id, operator_id, timestamp
- Cannot be edited or deleted
- Verified via `voice-audit verify`

### Thread Safety

The registry is thread-safe through multiple layers:

1. **Global lock**: Protects `_plugins`, `_boot_layers`, `_contexts` maps
2. **Per-plugin operation lock**: Prevents concurrent enable/disable for same plugin
3. **File-level lock**: `fcntl.flock()` protects `registry.yaml` from multi-process writes
4. **Atomic writes**: Tempfile → fsync → rename

Result: Safe concurrent access from multiple Console requests, CLI commands, and background tasks.

---

## Troubleshooting

### Plugin Not Appearing

1. Check discovery:
   ```python
   discovered = registry.discover()
   if "my-plugin" not in discovered:
       print("Plugin not registered")
   ```

2. Check disk registry:
   ```python
   disk_reg = TenantRegistry.load(tenant_id="tenant-1")
   if "my-plugin" not in disk_reg.records:
       print("Plugin not in registry.yaml")
   ```

3. Check boot layer:
   ```python
   layer = registry.boot_layer_of("my-plugin")
   print(f"Boot layer: {layer}")
   ```

### Plugin Won't Enable

1. Check consent requirement:
   ```python
   record = disk_reg.get("my-plugin")
   if record.consent_required():
       print("Needs consent_granted_by parameter")
   ```

2. Check dependencies:
   ```python
   record = disk_reg.get("my-plugin")
   print(f"Dependencies: {record.depends_on}")
   ```

3. Check health:
   ```python
   health = registry.health_check("my-plugin")
   print(f"Health: ok={health.ok}, msg={health.message}")
   ```

### Registry Corruption

Recovery is automatic if configured:

```python
registry = TenantRegistry.load(
    tenant_id="tenant-1",
    auto_recover=True,  # ← enabled by default
)
# If registry.yaml is corrupt, recovery from .bak is attempted
```

Manual recovery:

```bash
# Check for backups
ls -la ~/.corvin/tenants/default/plugins/registry.yaml*

# Restore from backup
cp ~/.corvin/tenants/default/plugins/registry.yaml.bak \
   ~/.corvin/tenants/default/plugins/registry.yaml
```

### Performance Issues

1. **Many plugins**: Filter before iterating
   ```python
   # SLOW: iterate all plugins
   for p in registry.discover():
       if p.plugin_type == "audit_backend":
           ...

   # FAST: filter first
   for p in registry.plugins_by_type("audit_backend"):
       ...
   ```

2. **Health check timeout**: Plugin's `health_check()` took >2.0s
   - Increase the deadline in the plugin
   - Or split health check into faster components

3. **Registry.yaml too large**: Too many plugins recorded
   - Consider archiving old plugin history
   - Or split into multiple tenants

---

## See Also

- [ADR-0030](../decisions/ADR-0030-plugin-system-design.md) — Plugin system design
- [ADR-0233](../decisions/ADR-0233-plugin-lifecycle-consolidation.md) — Lifecycle specification
- [ADR-0243](../decisions/ADR-0243-boot-layer-mechanism.md) — Boot layer mechanism
- [layer-plugins.md](../claude-ref/layer-plugins.md) — Full layer reference
- [PLUGIN_SYSTEM_E2E_INTEGRATION_TESTS.md](#) — Test suite documentation

---

**Last Updated:** 2026-08-29  
**Maintainer:** CorvinOS Team  
**Status:** Production Ready
