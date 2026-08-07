# CorvinOS Package Manager (ADR-0268)

ZIP-based skill/plugin/hook distribution system for marketplace-compatible packages.

## Overview

The PackageManager enables operators to install, manage, and verify skill packages distributed as ZIP archives. Packages follow the ADR-0268 manifest schema (with backward compatibility for Skill 2.0 format) and include skills, hooks, plugins, configuration, tests, and documentation.

## Storage Layout

```
~/.corvin/tenants/{tenant_id}/packages/
├── package_registry.json          # Persistent registry of installed packages
└── {package_id}/                  # Extracted package content
    ├── manifest.json              # Package metadata + capability declarations
    ├── README.md                  # Package documentation
    ├── SKILL.md                   # Skill documentation (entry point)
    ├── skills/                    # YAML skill definitions
    │   └── {skill_id}.yaml
    ├── hooks/                     # Python hook handlers
    │   ├── pre_execute.py
    │   ├── post_execute.py
    │   └── on_error.py
    ├── config/                    # YAML configuration templates
    │   └── *.yaml
    ├── docs/                      # Additional documentation
    └── tests/                     # Test suite
```

## Key Classes

### PackageManager

Main lifecycle manager for packages.

```python
from core.package_manager import PackageManager

pm = PackageManager(tenant_id="_default")

# Load a package from ZIP
pkg = pm.load_from_zip("/path/to/package.zip")

# List installed packages
packages = pm.list_packages()

# Get package metadata
pkg_info = pm.get_package("package_id")

# Verify package wiring (smoke test)
result = pm.verify_wiring("package_id")

# Enable/disable packages
pm.enable_package("package_id")
pm.disable_package("package_id")

# Get detailed status
status = pm.get_package_status("package_id")

# Uninstall package
pm.unload_package("package_id")
```

### PackageRegistry

Persistent registry of installed packages stored in `package_registry.json`.

```python
from core.package_manager import PackageRegistry

reg = PackageRegistry(tenant_id="_default")

# Check if package installed
if reg.has_package("package_id"):
    pkg = reg.get_package("package_id")
    print(f"{pkg.name} v{pkg.version}")

# List all packages
all_packages = reg.get_all_packages()

# Check installed versions for dependency resolution
versions = reg.get_installed_versions()  # {"pkg_id": "1.0.0", ...}
```

### PackageValidator

Static validation utilities for ZIP archives and manifests.

```python
from core.package_manager import PackageValidator
from core.package_manager.validators import ValidationError

try:
    # Extract and parse manifest from ZIP
    manifest = PackageValidator.validate_zip_integrity("/path/to/pkg.zip")
    
    # Validate manifest schema (ADR-0268 + Skill 2.0 compatible)
    PackageValidator.validate_manifest_schema(manifest)
    
    # Check dependencies
    installed = reg.get_installed_versions()
    PackageValidator.validate_dependencies(manifest, installed)
    
    # Extract permissions list
    perms = PackageValidator.validate_permissions(manifest)
    
except ValidationError as e:
    print(f"Validation failed: {e.message}")
    if e.field:
        print(f"  Field: {e.field}")
```

## Manifest Schema (ADR-0268)

### Minimal Example

```json
{
  "id": "com.example.my-skill",
  "version": "1.0.0",
  "name": "My Skill",
  "author": "Example Inc",
  "license": "MIT"
}
```

### Full Example

```json
{
  "id": "com.example.advanced-skill",
  "version": "2.1.0",
  "name": "Advanced Skill Package",
  "display_name": "Advanced Skill",
  "author": "Example Inc",
  "license": "Apache-2.0",
  "permissions": ["audit:write", "storage:read"],
  "dependencies": [
    {"id": "com.corvinlabs.core", "version": ">=1.0.0"}
  ],
  "capabilities": ["skill_loading", "hook_execution", "plugin_registration"],
  "supported_models": ["claude-3-sonnet", "claude-3-haiku"],
  "corvinOS": {
    "min_version": "0.10.110"
  },
  "contents": {
    "skills": [
      {"id": "my_skill", "file": "skills/my_skill.yaml"}
    ],
    "hooks": [
      {
        "id": "pre_execute",
        "file": "hooks/pre_execute.py",
        "trigger": "preprocessing",
        "priority": 100,
        "function": "setup_context"
      }
    ],
    "plugins": [],
    "routes": []
  },
  "configuration": {
    "required": ["api_key"],
    "optional": ["timeout_ms", "debug_mode"]
  },
  "metadata": {
    "category": "data-processing",
    "tags": ["ml", "analytics"]
  }
}
```

## Load Lifecycle

When `load_from_zip()` is called:

1. **Validate ZIP integrity** - Extract and parse `manifest.json`
2. **Validate manifest schema** - Ensure required fields + format (ADR-0268 or Skill 2.0)
3. **Check dependencies** - Verify all declared dependencies are installed with compatible versions
4. **Extract permissions** - List required permissions for operator approval (Phase 2)
5. **Extract to disk** - Copy ZIP content to `~/.corvin/tenants/{tenant}/packages/{package_id}/`
6. **Verify wiring** - Smoke test all skills (YAML), hooks (Python), config (JSON/YAML)
7. **Register in registry** - Add to `package_registry.json` with metadata + install timestamp
8. **Return metadata** - Return `InstalledPackage` object

Failures at any step trigger rollback (directory deleted if created).

## Unload Lifecycle

When `unload_package()` is called:

1. **Lookup package** - Verify package is registered
2. **Unregister from registry** - Remove from `package_registry.json` (Phase 2: unregister hooks/skills/plugins)
3. **Delete directory** - Remove `~/.corvin/tenants/{tenant}/packages/{package_id}/`

## Wiring Verification

`verify_wiring()` performs smoke tests on all package components:

### Skills (YAML)
- File exists
- Valid YAML syntax
- Required fields present (id, name, etc.)

### Hooks (Python)
- File exists
- Valid Python syntax (compile test)
- Function callable

### Configuration (JSON/YAML)
- Files parseable
- No schema violations (if schema provided)

### All Components
- All files referenced in manifest exist
- No missing dependencies
- Permissions are syntactically valid

## Error Handling

All validation failures raise `ValidationError` with:
- `message` - Human-readable description
- `field` - Which field failed (if applicable)
- `details` - Dict with error context (path, schema, etc.)

```python
from core.package_manager.validators import ValidationError

try:
    pkg = pm.load_from_zip("bad-package.zip")
except ValidationError as e:
    print(f"Installation failed: {e.message}")
    if e.field:
        print(f"  Problem in: {e.field}")
    if e.details:
        print(f"  Details: {e.details}")
```

## Phase 2 (Future)

Current implementation (Phase 1) covers:
- ✅ ZIP extraction and validation
- ✅ Manifest parsing and schema validation
- ✅ Dependency checking
- ✅ Persistent registry
- ✅ Wiring verification (syntax only)

Phase 2 will add:
- Skills registration in SkillForge
- Hooks registration in HookRegistry
- Plugins registration in PluginRegistry
- Permission approval flow
- Operator console UI
- Package enable/disable lifecycle hooks

## Testing

Run the test suite:

```bash
# Unit tests
uv run pytest tests/core/package_manager/test_package_manager.py -v

# Validators
uv run pytest tests/core/package_manager/test_validators.py -v

# E2E with real adscale-ldd package
uv run pytest tests/core/package_manager/test_e2e_adscale_ldd.py -v

# All tests
uv run pytest tests/core/package_manager/ -v
```

45 tests covering:
- ZIP integrity validation
- Manifest schema validation
- Dependency resolution
- Package extraction
- Registry persistence
- Wiring verification

## Examples

### Load a Package

```python
from core.package_manager import PackageManager

pm = PackageManager()
try:
    pkg = pm.load_from_zip("my-skill-1.0.0.zip")
    print(f"Installed: {pkg.manifest['name']} v{pkg.version}")
except ValidationError as e:
    print(f"Installation failed: {e.message}")
```

### List All Packages

```python
pm = PackageManager()
for pkg_id, pkg in pm.list_packages().items():
    status = "enabled" if pkg.enabled else "disabled"
    print(f"{pkg.manifest['name']} ({status}): {pkg.version}")
```

### Verify Package Wiring

```python
try:
    result = pm.verify_wiring("com.example.skill")
    print(f"Verification: {result['status']}")
except ValidationError as e:
    print(f"Wiring check failed: {e.message}")
```

### Check Package Status

```python
status = pm.get_package_status("com.example.skill")
print(f"Size: {status['size_bytes']} bytes")
print(f"Installed: {status['installed_at']}")
print(f"Enabled: {status['enabled']}")
```

## Related

- **ADR-0268** - Skill Package System (marketplace-compatible ZIP distribution)
- **SkillForge** - Runtime skill generation and registration (Phase 2 integration)
- **HookRegistry** - Hook lifecycle management (Phase 2 integration)
- **PluginRegistry** - Plugin discovery and loading

## References

- Skill 2.0 format: Used by existing skills packages (backward compatible)
- ADR-0268 manifest format: New standardized schema for packages
- ZIP structure: Follows marketplace conventions (skills/, hooks/, config/, docs/, tests/)
