# Portable Tenant Phase 1c: User-Installed Plugins Directory

**Status:** Complete (2026-07-27)  
**ADR:** ADR-0243 (boot layers)  
**Related:** Layer-Plugins (L4), Compliance Baseline

## Overview

Phase 1c centralizes user-installed plugins in a tenant-scoped directory, separate from the global registry and tenant configuration. Plugins are discovered from `~/.corvin/tenants/<tenant_id>/plugins/` and managed via CLI commands.

### Key Invariants

- **Persistence:** Plugins are stored in `~/.corvin/tenants/<id>/plugins/installed/<plugin-id>/`
- **Registry:** Metadata lives in `~/.corvin/tenants/<id>/plugins/registry.yaml`
- **Isolation:** Each tenant has its own plugin namespace
- **Lifecycle:** Install, uninstall, enable, disable — all atomic operations
- **Boot layer:** User-installed plugins default to `boot_layer=installed` (least privileged, fully disableable)

## Directory Structure

```
~/.corvin/tenants/_default/
├── global/
│   └── tenant.corvin.yaml        # Tenant config (backward compat)
└── plugins/                       # NEW: centralized plugin directory
    ├── registry.yaml              # Index of installed plugins
    └── installed/
        ├── my-plugin-1/
        │   ├── plugin.yaml        # Entry point metadata
        │   ├── plugin.py          # Implementation
        │   ├── requirements.txt
        │   └── ...
        └── my-plugin-2/
            └── ...
```

### registry.yaml Schema

```yaml
schema_version: "1.0"
tenant_id: _default
plugins:
  - plugin_id: my-plugin-1
    version: 0.2.0
    display_name: My Custom Plugin
    enabled: true
    installed_at: "2026-07-27T12:34:56Z"
    installed_by: corvin-cli
    boot_layer: installed
```

## CLI Commands

All commands default to the current tenant (`CORVIN_TENANT_ID` or `_default`).

### Install

```bash
corvin plugin install /path/to/plugin
corvin plugin install /path/to/plugin --tenant my-tenant
```

**Behavior:**
1. Validates `plugin.yaml` against registry rules
2. Copies the entire plugin directory into `installed/<plugin-id>/`
3. Adds entry to `registry.yaml`
4. Plugin is enabled by default

**Exit codes:**
- 0: Success
- 1: Validation failure
- 2: Not found or system error

### Uninstall

```bash
corvin plugin uninstall my-plugin-1
corvin plugin uninstall my-plugin-1 --tenant my-tenant
```

**Behavior:**
- Removes plugin directory
- Removes entry from `registry.yaml`
- If plugin is currently loaded, nothing happens to the runtime (you must restart the operator)

### List

```bash
corvin plugin list
corvin plugin list --json
corvin plugin list --tenant my-tenant
```

**Output:**
```
Installed plugins (tenant: _default):

  [✓] my-plugin-1@0.2.0
      My Custom Plugin
      installed: 2026-07-27T12:34:56Z
  [✗] my-plugin-2@1.0.0
      installed: 2026-07-25T08:00:00Z
```

### Enable

```bash
corvin plugin enable my-plugin-1
corvin plugin enable my-plugin-1 --tenant my-tenant
```

Flips `enabled: true` in the registry. The plugin is loaded on the next operator restart.

### Disable

```bash
corvin plugin disable my-plugin-1
corvin plugin disable my-plugin-1 --tenant my-tenant
```

Flips `enabled: false` in the registry. The plugin is unloaded on the next operator restart.

## Implementation

### Core Module: `core/plugins/corvin_plugins/tenant_plugins.py`

Public API:

```python
class TenantPluginRegistry:
    """Manage plugins for a tenant."""
    
    def register_plugin(
        self, 
        plugin_id: str, 
        plugin_path: Path, 
        metadata: Dict[str, Any],
        *, 
        installed_by: Optional[str] = None
    ) -> None:
        """Install plugin from directory."""
    
    def unregister_plugin(self, plugin_id: str) -> None:
        """Remove plugin."""
    
    def list_plugins(self) -> List[TenantPluginEntry]:
        """All registered plugins."""
    
    def get_plugin_path(self, plugin_id: str) -> Optional[Path]:
        """Installation directory of a plugin."""
    
    def enable_plugin(self, plugin_id: str) -> None:
        """Mark enabled in registry."""
    
    def disable_plugin(self, plugin_id: str) -> None:
        """Mark disabled in registry."""

def get_tenant_registry(tenant_id: Optional[str] = None) -> TenantPluginRegistry:
    """Factory for the current tenant."""
```

### CLI Module: `ops/launcher/corvin/plugin_runtime_cmd.py`

Wired into `ops/launcher/corvin/plugin_cmd.py` via `add_runtime_parser()`.

Exports:
- `cmd_install(args)` → int
- `cmd_uninstall(args)` → int
- `cmd_list(args)` → int
- `cmd_enable(args)` → int
- `cmd_disable(args)` → int
- `dispatch_runtime(args)` → int

## Boot Integration (Future)

Not yet wired into the bootstrap flow. Future phases will:

1. Call `load_tenant_plugins(tenant_id)` during tenant boot
2. Convert each `TenantPluginEntry` to a `PluginRecord`
3. Load via the standard `registry.register(plugin, ctx)` path

## Backward Compatibility

Phase 1c is **additive only:**

- Plugins in `spec.plugins.installed` (tenant.corvin.yaml) continue to work
- Plugins in `plugins/installed/` are **not** loaded automatically yet
- No migration is required
- An operator can use both sources side-by-side

## Testing

24 unit tests in `core/plugins/tests/test_tenant_plugins.py` cover:

- Registry CRUD (create, read, update, delete)
- Persistence (YAML serialization/deserialization)
- Enable/disable
- Multiple plugins
- Error cases (duplicate install, missing plugin, etc.)
- Edge cases (nonexistent paths, corrupted files)

Run:
```bash
uv run pytest core/plugins/tests/test_tenant_plugins.py -v
```

## Security Model

- **Isolation:** Each tenant's plugins are isolated in separate directories
- **Boot layer:** User-installed plugins are `boot_layer=installed` by default (fully disableable)
- **Validation:** Manifest is validated before installation
- **No privilege escalation:** A community-origin plugin cannot claim `boot_layer=compliance` or `boot_layer=core`
- **Audit:** Installation is logged (future: audit chain integration)

## Limitations

### Current

- Plugins in `plugins/` are **not** loaded at boot (future phase)
- No dependency resolution (plugins cannot declare dependencies on other plugins)
- No versioning conflict detection (two plugins of the same id overwrite)
- No signature verification (future phase)

### Planned

- Automatic loading at tenant boot (Phase 1d)
- Plugin marketplace integration (future ADR)
- Signature verification via `awpkg` (future, post-LIP)
- Dependency resolution (future)

## Examples

### Scaffold, Validate, Install

```bash
# Create plugin from template
corvin plugin new router_backend com.example.my-router

# Validate manifest
corvin plugin check ./com_example_my_router/

# Install to tenant
corvin plugin install ./com_example_my_router/

# List
corvin plugin list
```

### Multi-Tenant Setup

```bash
# Install to tenant 'prod'
corvin plugin install /path/to/plugin --tenant prod

# Install to tenant 'staging'
corvin plugin install /path/to/plugin --tenant staging

# List in prod
corvin plugin list --tenant prod
```

### Enable/Disable Without Uninstalling

```bash
# Disable but keep
corvin plugin disable my-plugin

# Later, re-enable
corvin plugin enable my-plugin

# Uninstall when done
corvin plugin uninstall my-plugin
```

## References

- **ADR-0243:** Boot layer axis (COMPLIANCE < CORE < BUNDLED < INSTALLED)
- **ADR-0244:** Plugin scaffolding tooling (offline-only `corvin plugin new/check/types`)
- **Layer 4 (L4):** Cowork and multi-persona hub
- **Compliance Baseline:** GDPR/EU AI Act structural guarantees
- **ADR-0007:** Multi-tenant model

## Future: Phase 1d

Boot-time loading of plugins from `plugins/installed/` into the runtime registry.

```python
def bootstrap_tenant_plugins(tenant_id: str, registry: PluginRegistry) -> None:
    """Load user-installed plugins at startup."""
    tenant_registry = get_tenant_registry(tenant_id)
    for entry in tenant_registry.list_plugins():
        if not entry.enabled:
            continue
        # Load plugin module from entry.path
        # Convert to PluginRecord
        # Call registry.register()
```
