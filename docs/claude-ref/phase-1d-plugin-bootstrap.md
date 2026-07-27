# Phase 1d: Plugin Bootstrap Loading

## Overview

Phase 1d loads user-installed plugins from `~/.corvin/tenants/<id>/plugins/installed/`
during CorvinOS bootstrap, making them available as extension points and provider backends.

This phase completes the plugin lifecycle: Phase 1c (TenantPluginRegistry) provides install/uninstall/enable/disable, and Phase 1d integrates those plugins into the boot sequence.

## Architecture

### Plugin Loading Path

The boot sequence now has THREE load paths (in precedence order):

1. **Global plugins** (bundled) — `compliance` then `core` boot layers
2. **Declarative plugins** — `spec.plugins.installed` from `tenant.corvin.yaml`
3. **Runtime registry plugins** — TenantPluginRegistry (Phase 1d) ← NEW

Each path is independent and a plugin present in multiple paths loads only once (the earlier path wins).

### Boot Sequence

```
bootstrap_all()
├─ bootstrap_global()
│  └─ Load compliance + core layer global plugins
│
├─ bootstrap_declared()
│  └─ Load spec.plugins.installed from tenant.corvin.yaml
│
└─ bootstrap_tenant()  ← Phase 1d
   └─ Load tenant-installed plugins from TenantPluginRegistry
      ├─ Read registry.yaml at ~/.corvin/tenants/<id>/plugins/registry.yaml
      ├─ For each enabled plugin entry:
      │  ├─ Locate plugin directory in plugins/installed/<plugin-id>/
      │  ├─ Load plugin.py from disk
      │  ├─ Call Plugin.__init__() or setup() hook
      │  ├─ Register plugin in global PluginRegistry
      │  └─ Audit success/failure
      └─ Continue loading other plugins on any failure (fail-closed per-plugin)
```

### Plugin Discovery

A tenant plugin must live at:
```
~/.corvin/tenants/<id>/plugins/installed/<plugin-id>/
├── plugin.py           # Required: plugin implementation
├── manifest.json       # Metadata (created by CLI)
├── requirements.txt    # Optional: pip dependencies
└── ... other files
```

### Plugin Interfaces

#### Class-based Plugin (preferred)

```python
# plugin.py

class Plugin:
    """A tenant plugin."""
    plugin_id = "my-plugin"
    plugin_type = "notification_backend"  # or other provider type
    
    def on_load(self, context):
        """Called when the plugin loads during bootstrap.
        
        context is a PluginContext with registry handles for all provider types.
        """
        context.notification_registry.register(self)
```

#### Hook-based Plugin

```python
# plugin.py

def setup(context):
    """Called during bootstrap to set up the plugin.
    
    context is a PluginContext with registry handles for all provider types.
    """
    # Register backends, set up state, etc.
    context.notification_registry.register(my_notification_backend)
```

## Feature Flag

Phase 1d is behind the `plugin_runtime_lifecycle` feature flag, shipped dark (default off):

- **Default:** `off` — plugins are NOT loaded at bootstrap
- **Operator opt-in:** Set in Console Settings → Features → Runtime Plugin Lifecycle
- **Config:** `spec.features.plugin_runtime_lifecycle: true` in tenant.corvin.yaml
- **Env:** NO override via env var (respect the flag setting)

When the flag is OFF:
- `bootstrap_tenant()` returns `[]` without consulting the registry
- Tenant plugins never load
- Existing installs unchanged (no surprise plugin loading)

When the flag is ON:
- `bootstrap_tenant()` loads every `enabled: true` plugin from the registry
- Disabled plugins are skipped
- Load failures are audited but non-fatal

## CLI Integration

The Console already provides plugin management (Phase 1c):

```bash
# Install plugin from a directory
corvin plugin install /path/to/my-plugin

# List installed plugins
corvin plugin list

# Enable/disable
corvin plugin enable my-plugin
corvin plugin disable my-plugin

# Uninstall
corvin plugin uninstall my-plugin
```

Phase 1d loads whatever the CLI has installed and enabled.

## Security Model

### Boot Layer Enforcement

Tenant-installed plugins are constrained to the `installed` boot layer:
- They can **never** claim `compliance` or `core` layers
- Manifest gate (ADR-0250) rejects privileged claims
- Boot layer from registry downgraded to `installed` if misconfigured

### Provider Slot Gate (ADR-0250)

Plugins of certain types (e.g., `audit_backend`, `user_backend`) can take
"provider slots" — process-wide singletons. The provider-slot gate in
`_tenant_scope_permits()` enforces:
- **One audit backend per tenant** (multi-tenant isolation boundary)
- **Community-origin (untrusted) plugins** cannot take slots serving data
- **Refusal is audited** under `plugin.provider_slot_refused`

### Trust and Provenance (ADR-0249)

If `plugin_trust_enforcement` is enabled:
- Plugins with `origin=vetted` must carry a valid signature
- Plugins with `origin=community` are allowed but logged
- Trust failures are audited and non-fatal (plugin is skipped, boot continues)

## Audit Trail

Every significant event is recorded in the audit chain:

```jsonl
{"event_type": "plugin.loaded", "details": {"plugin_id": "my-plugin", ...}}
{"event_type": "plugin.load_failed", "details": {"plugin_id": "bad", "reason": "instantiate_failed", ...}}
{"event_type": "plugin.provider_slot_refused", "details": {"plugin_id": "...", "reason": "..."}}
{"event_type": "plugin.registry_unusable", "details": {"error_type": "YAMLError"}}
```

All audit events carry:
- `tenant_id` — which tenant's registry
- `plugin_id` — which plugin (if known)
- `reason` — why (if a failure)
- `error_type` — Python exception class (if applicable)

## Testing

All plugins tested via:
```bash
pytest core/plugins/tests/test_bootstrap_tenant_plugins.py -v
```

Test coverage:
- ✓ Bootstrap skips when flag is off
- ✓ Bootstrap loads enabled plugins
- ✓ Bootstrap skips disabled plugins
- ✓ One failed plugin doesn't block others
- ✓ Setup hook pattern works
- ✓ Missing plugin.py handled gracefully
- ✓ Corrupted registry handled gracefully
- ✓ Multiple plugins load in order

## Degradation

Phase 1d is designed to be resilient:

| Failure Mode | Behavior |
|---|---|
| Registry file missing | No plugins load (graceful default) |
| Registry corrupted (invalid YAML) | Logged, audited, no plugins load |
| Plugin directory missing | That plugin skipped, others load |
| plugin.py missing | That plugin skipped, others load |
| Plugin instantiation fails | That plugin skipped, others load |
| Provider slot refused | That plugin skipped, others load |

**One failing plugin never prevents others from loading.**

The only fatal failures are in the compliance layer (Phase 1a), which are separate.

## Dependency Resolution

Phase 1d does NOT currently implement dependency ordering between plugins.
All plugins load in the order they appear in `registry.yaml`.

If plugin A depends on plugin B, the operator must:
1. Ensure both are installed and enabled
2. Order them in `registry.yaml` (B before A)
3. Handle the case where B is disabled (graceful degradation)

Future phases (ADR-0244) may add explicit dependency declarations.

## Relationship to Declarative Path

The declarative path (`spec.plugins.installed` in tenant.corvin.yaml) and
the runtime path (TenantPluginRegistry) are **independent**:

- Declarative plugins are version-controlled and always load (when boot runs)
- Runtime plugins can be installed/uninstalled dynamically via the Console
- A plugin in BOTH paths loads from the declarative path (it "wins")
- The runtime path never overwrites the declarative path

**Example:** An operator can:
1. Pin a production plugin in `tenant.corvin.yaml` (declarative)
2. Install a test plugin via Console (runtime)
3. Boot loads both, in precedence order

## Integration Points

### boot_platform() sequence

1. `assert_compliance()` — tripwires (fail-closed)
2. `bootstrap_all()` → calls `bootstrap_tenant()`
3. `assert_post_boot()` — compliance layer verification

Phase 1d participates in step 2.

### PluginContext Provisioning

When `bootstrap_tenant()` loads a plugin, it calls `build_context()` which
provisions the PluginContext with:
- All provider registry handles (audit, notification, recall, etc.)
- `audit_emit` callable for hash-chained audit trail
- `corvin_home` and tenant metadata
- Compute/engine/channel registries (if provided by caller)

A plugin's `on_load(context)` receives this context as the bridge to
self-register as a provider.

## Future Phases

| Phase | Goal |
|---|---|
| 1e | Hot-reload of plugins (enable/disable without restart) |
| 1f | Plugin lifecycle hooks (on_enable, on_disable, on_shutdown) |
| 1g | Dependency ordering (plugin A requires plugin B) |
| 1h | Plugin marketplace integration |

---

## Implementation Details

### Module: corvin_plugins.bootstrap

**New functions:**
- `_load_tenant_plugin()` — Load one plugin from disk and register it

**Modified functions:**
- `bootstrap_tenant()` — Now uses TenantPluginRegistry instead of state.TenantRegistry

**Unchanged:**
- `bootstrap_global()` — Still loads bundled global plugins
- `bootstrap_declared()` — Still loads spec.plugins.installed
- `bootstrap_all()` — Still orchestrates all three paths

### Module: corvin_plugins.tenant_plugins

**Used by Phase 1d:**
- `TenantPluginRegistry` — Reads/writes registry.yaml and manages installed/ directory
- `TenantPluginEntry` — One plugin's registration metadata (enabled, version, etc.)

### Module: corvin_plugins.state

**Not used by Phase 1d** — This is for a future unified registry architecture.
Currently, the declarative path (bootstrap_declared) and state.py are separate;
Phase 1d uses tenant_plugins instead.

---

## Compliance Notes

- **Audit trail:** All load events recorded in hash-chained audit trail
- **Multi-tenant isolation:** Each tenant has independent registry and plugin directory
- **Consent:** Some plugin types require user consent (checked by provider slot gate)
- **Erasure:** Uninstalling a plugin does NOT delete its audit records (immutable)
- **Transparency:** Operator can inspect registry.yaml to see what plugins will load
