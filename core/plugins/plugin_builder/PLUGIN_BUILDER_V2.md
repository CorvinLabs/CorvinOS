# Plugin-Builder v2: Complete Implementation (ADR-0262)

**Status:** Production-Ready (2026-09-19)  
**Version:** 2.0.0

## Overview

Plugin-Builder v2 is a comprehensive framework for building Corvin plugins from idea to distribution. It provides:

1. **Enhanced Scaffolding** — Generate plugin skeletons with lifecycle hooks
2. **Testing Framework** — Pytest fixtures and test runners for plugin validation
3. **Build System** — Package plugins as distributable wheels
4. **Development Workflow** — Orchestrate the complete development lifecycle

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│          Plugin Development Workflow                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Scaffolding          2. Testing            3. Building  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ Create       │    │ Run Tests    │    │ Generate     │  │
│  │ - plugin.py │ ── │ - Unit tests │ ── │ - setup.py   │  │
│  │ - tests/    │    │ - Fixtures   │    │ - Wheel      │  │
│  │ - setup.py  │    │ - Validation │    │ - METADATA   │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. Scaffolding (`scaffolding/`)

Generates plugin skeletons with explicit lifecycle hooks.

**Key Classes:**
- `LifecycleHookTemplate` — Template for plugins with on_load/on_execute/on_unload
- `EnhancedScaffolder` — Creates complete plugin structure
- `bootstrap_scaffold()` — Convenience function for quick setup

**Features:**
- Generates plugin.py with lifecycle hooks
- Creates tests/ directory with conftest.py and test_plugin.py
- Generates setup.py and pyproject.toml automatically
- Creates README.md with documentation

**Example:**

```python
from plugin_builder.scaffolding import bootstrap_scaffold

files = bootstrap_scaffold(
    output_dir="/tmp/my-plugins",
    plugin_id="my.data_connector",
    plugin_name="My Data Connector",
    plugin_type="data_connector",
    description="Connects to external data sources",
    author="Your Name",
)
```

### 2. Testing Framework (`testing_framework/`)

Provides pytest fixtures and test infrastructure for plugins.

**Key Classes:**
- `PluginContext` — Execution context with audit trail
- `PluginRegistry` — Mock registry for testing
- `CorvinPluginTestCase` — Base test class with assertions
- `PluginTestRunner` — Orchestrates test execution

**Features:**
- Plugin context fixtures (tenant isolation, session tracking)
- Mock audit trail recording and assertion
- Plugin registry for testing registration
- Test structure validation
- pytest fixture auto-discovery

**Example:**

```python
class TestMyPlugin(CorvinPluginTestCase):
    def test_plugin_execution(self):
        plugin = MyPlugin()
        plugin.on_load(self.context)
        
        result = plugin.on_execute({"data": "test"}, self.context)
        
        self.assert_audit_event("plugin_executed")
        self.assertEqual(result["status"], "success")
```

### 3. Build System (`build_system/`)

Generates and manages plugin wheel distributions.

**Key Classes:**
- `PackageMetadata` — Plugin package information
- `PackageBuilder` — Orchestrates wheel building
- `BuildResult` — Build operation result

**Features:**
- Generates setup.py and pyproject.toml
- Builds wheels via setuptools
- Validates packages post-build
- Configurable build metadata

**Example:**

```python
from plugin_builder.build_system import PackageBuilder, PackageMetadata

metadata = PackageMetadata(
    name="corvin-my-plugin",
    version="1.0.0",
    description="My plugin",
    author="Your Name",
)

builder = PackageBuilder(plugin_dir, metadata)
result = builder.build()

if result.success:
    print(f"Wheel: {result.wheel_path}")
```

### 4. Integration (`v2_integration.py`)

Orchestrates the complete plugin development workflow.

**Key Classes:**
- `PluginDeveloper` — Workflow orchestration
- `PluginDevelopmentPlan` — Configuration for development
- `DevelopmentResult` — Complete workflow result

**Features:**
- Scaffold → Test → Build pipeline
- Customizable step execution
- Error handling and validation
- Elapsed time tracking

**Example:**

```python
from plugin_builder.v2_integration import develop_plugin

result = develop_plugin(
    plugin_id="my.plugin",
    plugin_name="My Plugin",
    output_dir="/tmp/plugins",
    steps=["scaffold", "test", "build"],
)

if result.success:
    print(f"Plugin ready at: {result.scaffold_dir}")
    print(f"Wheel: {result.build_result.wheel_path}")
else:
    print(f"Errors: {result.errors}")
```

## Lifecycle Hooks

Every plugin scaffold includes three lifecycle hooks:

### on_load(context)

Called when the plugin is loaded.

```python
def on_load(self, context: Any) -> None:
    """Initialize plugin resources."""
    self.logger.info(f"Loading {self.plugin_id}")
    self._initialized = True
```

**Use for:**
- Initializing database connections
- Loading configuration
- Setting up logging
- Verifying dependencies

### on_execute(input_data, context)

Called to execute the plugin's primary function.

```python
def on_execute(self, input_data: Any, context: Any) -> Any:
    """Execute plugin logic."""
    if not self._initialized:
        raise RuntimeError("Plugin not initialized")
    return process_data(input_data)
```

**Use for:**
- Processing input data
- Calling external APIs
- Transforming data
- Computing results

### on_unload(context)

Called when the plugin is unloaded.

```python
def on_unload(self, context: Any) -> None:
    """Clean up resources."""
    self.logger.info(f"Unloading {self.plugin_id}")
    self._initialized = False
```

**Use for:**
- Closing database connections
- Cleaning up temporary files
- Saving state
- Freeing resources

## Testing

Each scaffold includes a test suite with:

1. **Lifecycle Tests** — Verify on_load/on_unload behavior
2. **Execution Tests** — Test on_execute with sample data
3. **Health Checks** — Verify plugin status reporting
4. **Audit Trail** — Ensure events are recorded

**Run tests:**

```bash
cd my_plugin_scaffold
pytest tests/ -v
```

**Example test:**

```python
def test_plugin_lifecycle(self, plugin_context):
    plugin = MyPlugin()
    
    # Load phase
    plugin.on_load(plugin_context)
    assert plugin._initialized is True
    
    # Execute phase
    result = plugin.on_execute({"test": "data"}, plugin_context)
    assert result is not None
    
    # Unload phase
    plugin.on_unload(plugin_context)
    assert plugin._initialized is False
```

## Build and Distribution

Scaffolds include ready-to-use build configuration:

**setup.py:**

```python
setup(
    name="corvin-my-plugin",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["corvin-plugins"],
    extras_require={
        "dev": ["pytest>=7.0", "pytest-cov>=3.0"],
    },
)
```

**pyproject.toml:**

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "corvin-my-plugin"
version = "0.1.0"
dependencies = ["corvin-plugins"]
```

**Build a wheel:**

```bash
cd my_plugin_scaffold
python -m build
# Generates: dist/corvin_my_plugin-0.1.0-py3-none-any.whl
```

## Compliance & Auditing

### Audit Trail

All plugin execution is recorded in the audit trail:

```python
context.log_audit_event(
    event_type="plugin_executed",
    payload={
        "status": "success",
        "duration_ms": 42,
        "output_size": 1024,
    }
)
```

### Tenant Isolation

All operations respect tenant boundaries:

```python
context = PluginContext(
    plugin_id="my.plugin",
    tenant_id="_default",  # Tenant scope
    session_id="session-123",
    user_id="user-456",
)
```

### Health Checks

Every plugin reports health status:

```python
health = plugin.health_check()
# {
#     "status": "healthy",
#     "plugin_id": "my.plugin",
#     "initialized": true,
# }
```

## Workflow Integration

Plugin-Builder v2 integrates with CorvinOS workflows:

1. **Interview → Classification** — Via existing `interview.py` and `classifier.py`
2. **Documentation** — ADRs, architecture docs via existing generators
3. **Scaffolding** — Enhanced with v2 lifecycle hooks
4. **Testing** — Built-in test framework
5. **Distribution** — Wheel building and validation

## File Structure

```
my_plugin_scaffold/
├── plugin.py                    # Plugin implementation
├── __init__.py                 # Package initialization
├── tests/
│   ├── conftest.py             # Pytest configuration
│   ├── test_plugin.py          # Plugin tests
│   └── __init__.py
├── setup.py                    # Build configuration
├── pyproject.toml              # Modern build config
└── README.md                   # Documentation
```

## Performance Characteristics

- **Scaffold Generation:** <100ms
- **Test Execution:** Depends on test suite (typically <1s)
- **Wheel Build:** 1-5s
- **Total Development:** 10-30s for a complete workflow

## Error Handling

### Build Failures

```python
result = developer.develop(plan, output_dir)

if not result.success:
    for error in result.errors:
        print(f"Error: {error}")
```

### Validation Errors

```python
structure = validate_plugin_structure(plugin_dir)

if not structure.is_valid():
    for issue in structure.issues:
        print(f"Issue: {issue}")
```

## Load-Bearing Invariants

1. **Emits-Never-Loads (ADR-0244):** Plugin-Builder emits artifacts but never loads them
2. **Tenant Isolation:** All operations filter by tenant_id
3. **Audit Trail:** Every operation is recorded (appendix-only)
4. **Lifecycle Hooks:** on_load/on_execute/on_unload are mandatory
5. **Health Checks:** Every plugin reports health status

## Integration with ADR-0262

This implementation fulfills ADR-0262 requirements:

- ✅ Idea-first interview (via existing `interview.py`)
- ✅ Checkpoint review (via existing `checkpoint.py`)
- ✅ Generated E2E tests (via existing `generators/e2e_tests.py`)
- ✅ **Enhanced scaffolding with lifecycle hooks** (NEW)
- ✅ **Testing framework with fixtures** (NEW)
- ✅ **Build system for wheel distribution** (NEW)

## Next Steps

1. **Install dependencies:** `pip install build pytest pytest-cov`
2. **Generate a scaffold:** Use `develop_plugin()` or `/plugin-builder` console command
3. **Implement plugin logic:** Edit `plugin.py`
4. **Write tests:** Add test cases to `tests/test_plugin.py`
5. **Build wheel:** Run `python -m build`
6. **Distribute:** Upload to plugin marketplace or registry

## References

- ADR-0262: Plugin-Builder V2
- ADR-0244: Plugin System Boundaries
- ADR-0245: Extension Surface Map
- ADR-0314: Learning Infrastructure
