# CorvinOS Plugin Developer Guide

**Last Updated:** 2026-08-31  
**Status:** Phase 1 — Foundation

---

## Table of Contents

1. [Introduction](#introduction)
2. [Plugin Architecture](#plugin-architecture)
3. [Getting Started](#getting-started)
4. [Plugin Categories](#plugin-categories)
5. [Development Workflow](#development-workflow)
6. [Testing](#testing)
7. [Publishing](#publishing)
8. [Best Practices](#best-practices)
9. [FAQ](#faq)
10. [Appendix](#appendix)

---

## Introduction

Welcome! This guide teaches you how to build, test, and publish plugins for CorvinOS.

### What is a Plugin?

A **plugin** is a Python module that extends CorvinOS' behavior by hooking into the Layer 4 plugin system. Plugins are the primary extensibility mechanism and are discovered/managed via the CorvinOS Marketplace.

### Two Plugin Types

| Aspect | Buildin | Contributor |
|--------|---------|-------------|
| **License** | Apache 2.0 | MIT (or compatible) |
| **CLA Required?** | ✅ Yes | ❌ No |
| **Location** | `operator/marketplace/plugins/buildin/` | External repo or `contrib/` |
| **SLA Guarantee** | ✅ 48h bugfix, 24h security | ❌ Community-driven |
| **Security Audit** | ✅ Required | ❌ Optional |
| **Boot Layer** | `bundled` or `core` | `installed` |
| **Target Audience** | CorvinOS core features | Community experiments |

**Choose Buildin if:** You're adding a feature that will ship in every CorvinOS install.  
**Choose Contributor if:** You're publishing a specialized plugin for the community.

---

## Plugin Architecture

### Directory Structure

A plugin lives in a **category** folder:

```
operator/marketplace/plugins/[tier]/[category]/[plugin_id]/
├── plugin.json              ← Manifest (required)
├── src/
│   └── plugin_module/
│       ├── __init__.py
│       ├── plugin.py        ← Main plugin class
│       └── handlers.py      ← Event handlers (optional)
├── tests/
│   ├── test_plugin.py
│   └── conftest.py
├── docs/
│   ├── README.md            ← Overview
│   └── api.md               ← API reference (optional)
├── requirements.txt         ← Python dependencies
├── setup.py                 ← Setuptools config (for wheel builds)
└── LICENSE.txt              ← License file
```

### Plugin Manifest (plugin.json)

Every plugin has a **required** `plugin.json` manifest:

```json
{
  "id": "plugin:buildin-memory-cel_session_memory",
  "type": "plugin",
  "name": "CEL Session Memory",
  "version": "1.0.0",
  "author": "Anthropic PBC",
  "license": "Apache-2.0",
  "tier": "buildin",
  "category": "memory",
  "description": "Provides session-based memory storage using CEL evaluation.",
  "boot_layer": "bundled",
  "sla_level": "buildin",
  "distribution": {
    "supports_source": true,
    "supports_wheel": true,
    "wheel_url": "https://releases.corvinOS.dev/plugins/cel_session_memory-1.0.0-py3-none-any.whl",
    "wheel_checksum": "sha256:abc123def456..."
  },
  "dependencies": {
    "CorvinOS": ">=0.10.0"
  },
  "entry_point": "plugin_module.plugin:SessionMemoryPlugin",
  "security_audit": {
    "last_audit_date": "2026-08-30",
    "findings": 0
  }
}
```

**Schema validation:** Your `plugin.json` will be validated against `operator/marketplace/schemas/plugin-schema.json` during CI/CD.

### Plugin Class

Every plugin must extend `CorevinPlugin`:

```python
from corvin_plugins.base import CorevinPlugin
from corvin_plugins.registry import register_plugin

@register_plugin(
    plugin_id="plugin:buildin-memory-cel_session_memory",
    boot_layer="bundled"
)
class SessionMemoryPlugin(CorevinPlugin):
    """Session memory implementation using CEL."""
    
    def __init__(self, **config):
        super().__init__(**config)
        self.sessions = {}
    
    def on_session_start(self, session_id, **context):
        """Called when a new session starts."""
        self.sessions[session_id] = {}
    
    def on_session_end(self, session_id):
        """Called when a session ends."""
        del self.sessions[session_id]
    
    def handle_event(self, event_type, payload):
        """Handle a plugin event."""
        if event_type == "store_memory":
            session_id = payload.get("session_id")
            key = payload.get("key")
            value = payload.get("value")
            self.sessions[session_id][key] = value
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Git
- CorvinOS development environment (see `CONTRIBUTING.md`)

### Step 1: Choose Your Type

- **Buildin Plugin?** Start with the [buildin plugin template](#)
- **Contributor Plugin?** Start with the [contributor plugin template](#)

### Step 2: Clone the Template

For **Buildin** plugins (requires CLA):
```bash
git clone https://github.com/anthropics/plugin-template-buildin.git my-plugin
cd my-plugin
```

For **Contributor** plugins:
```bash
git clone https://github.com/anthropics/plugin-template-contributor.git my-plugin
cd my-plugin
```

### Step 3: Customize

1. **Edit** `plugin.json`:
   ```bash
   sed -i 's/PLUGIN_ID/my-custom-memory/g' plugin.json
   sed -i 's/Your Name/Jane Developer/g' plugin.json
   ```

2. **Implement** your plugin in `src/plugin_module/plugin.py`

3. **Write** tests in `tests/test_plugin.py`

4. **Document** in `docs/README.md`

---

## Plugin Categories

Choose **one** category for your plugin:

### 1. Memory
Session recall, user modeling, learning events (L28, ADR-0314–0321).

**Examples:**
- CEL Session Memory (buildin)
- User Model (buildin)

**Entry Points:**
- `CorvinPlugin.on_session_start(session_id, **context)`
- `CorvinPlugin.on_session_end(session_id)`
- `CorvinPlugin.handle_event("store_memory", payload)`

---

### 2. Security & Compliance
Auth, audit chain, path-gate, flow guard (L16, L10, L34, ADR-0232–0233).

**Examples:**
- Consent Gate (buildin)
- Audit Chain (buildin)

**Entry Points:**
- `CorvinPlugin.on_audit_event(event)`
- `CorvinPlugin.validate_request(request)`

---

### 3. Integration
Hooks, cowork hub, bridges, MCP servers (L4, L38, ADR-0243).

**Examples:**
- Bridge Handler (buildin)
- Cowork Hub (buildin)

**Entry Points:**
- `CorvinPlugin.on_message(message)`
- `CorvinPlugin.on_bridge_event(event)`

---

### 4. Data Processing
Artifact extraction, classification, anonymization (L25, L34, L36).

**Examples:**
- Artifact Extractor (buildin)
- Data Classifier (buildin)

**Entry Points:**
- `CorvinPlugin.process_artifact(artifact)`
- `CorvinPlugin.classify_data(data)`

---

### 5. Observability
Telemetry, heartbeat, diagnostics, self-repair (ACO L5).

**Examples:**
- Telemetry Collector (buildin)
- Health Monitor (buildin)

**Entry Points:**
- `CorvinPlugin.on_telemetry_event(event)`
- `CorvinPlugin.health_check()`

---

## Development Workflow

### 1. Setup Development Environment

```bash
# Clone repo
git clone https://github.com/anthropics/CorvinOS.git
cd CorvinOS

# Create plugin directory
mkdir -p operator/marketplace/plugins/buildin/memory/my-memory-plugin
cd operator/marketplace/plugins/buildin/memory/my-memory-plugin

# Initialize from template
cp -r ../../../../../templates/plugin-template/* .
```

### 2. Implement Plugin

Edit `src/plugin_module/plugin.py`:

```python
from corvin_plugins.base import CorevinPlugin
from corvin_plugins.registry import register_plugin

@register_plugin(
    plugin_id="plugin:buildin-memory-my-memory-plugin",
    boot_layer="bundled"
)
class MyMemoryPlugin(CorevinPlugin):
    def on_session_start(self, session_id, **context):
        print(f"Session {session_id} started")
```

### 3. Write Tests

Edit `tests/test_plugin.py`:

```python
import pytest
from src.plugin_module.plugin import MyMemoryPlugin

@pytest.fixture
def plugin():
    return MyMemoryPlugin()

def test_session_start(plugin):
    plugin.on_session_start("test_session")
    assert "test_session" in plugin.sessions
```

### 4. Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Type check
mypy src/ --strict

# Lint
pylint src/
```

### 5. Validate Manifest

```bash
# Validate plugin.json against schema
python -m jsonschema \
  operator/marketplace/schemas/plugin-schema.json \
  ./plugin.json
```

---

## Testing

### Unit Tests

Test your plugin in isolation:

```python
def test_store_memory(plugin):
    plugin.on_session_start("test_session")
    plugin.handle_event("store_memory", {
        "session_id": "test_session",
        "key": "user_name",
        "value": "Alice"
    })
    assert plugin.sessions["test_session"]["user_name"] == "Alice"
```

### Integration Tests

Test your plugin with CorvinOS:

```python
def test_plugin_registers_with_boot_layer():
    from corvin_plugins.registry import plugins_by_boot_layer
    plugins = plugins_by_boot_layer("bundled")
    assert any(p.id == "plugin:buildin-memory-my-memory-plugin" for p in plugins)
```

### E2E Tests

Test end-to-end behavior (see `tests/e2e/`).

---

## Publishing

### For Buildin Plugins

1. **Sign CLA** (see `operator/marketplace/templates/BUILDIN_PLUGIN_CLA.md`)
2. **Create PR** to `CorvinOS/main`
3. **Review & Audit** (Anthropic team)
4. **Merge** → Plugin published to marketplace
5. **Wheel Built** → CI/CD generates and signs `.whl`

### For Contributor Plugins

1. **Publish** to your own GitHub repo (MIT license)
2. **Submit** marketplace listing form (coming Week 5)
3. **Approval** (Anthropic team verifies compliance)
4. **Listed** → Plugin appears in CorvinOS Marketplace
5. **Distribute** via GitHub or optional wheel

---

## Best Practices

### Code Quality

✅ **DO:**
- Write type hints (`from typing import ...`)
- Include docstrings for all public methods
- Follow PEP 8 style guide
- Log important events (`logging.info(...)`)
- Handle errors gracefully

❌ **DON'T:**
- Hardcode configuration values
- Log PII or secrets
- Use global state without namespacing
- Ignore exceptions silently

### Security

✅ **DO:**
- Validate all input payloads
- Use `_assert_safe()` before logging data
- Respect `GDPR_MODE` environment variable
- Encrypt sensitive data at rest

❌ **DON'T:**
- Log prompts or user content
- Store unencrypted tokens
- Bypass audit trail
- Weaken compliance gates

### Performance

✅ **DO:**
- Lazy-load heavy dependencies
- Cache results when appropriate
- Use async for I/O operations
- Profile critical paths

❌ **DON'T:**
- Block on network calls
- Load entire datasets into memory
- Spawn unlimited threads/tasks

---

## FAQ

### Q: Can I use external libraries?

**A:** Yes! List all dependencies in `requirements.txt`. For buildin plugins, external deps must be:
- Lightweight (< 1 MB)
- Actively maintained
- Compatible with Apache 2.0 / MIT licensing

### Q: How do I update my plugin?

**A:** Update `version` in `plugin.json` (semantic versioning). Submit a new PR (buildin) or GitHub release (contributor).

### Q: Can my plugin access user data?

**A:** Yes, but only through the CorvinOS plugin API. Direct database access is forbidden. Use `CorvinPlugin.store_memory()` and `CorvinPlugin.retrieve_memory()`.

### Q: What happens if my plugin crashes?

**A:** CorvinOS isolates plugin failures. Your plugin's exception is caught, logged (audit trail), and the core continues. Fix bugs and re-publish.

### Q: How do I debug my plugin?

**A:** Set `CORVIN_DEBUG=1` and check logs in `~/.corvin/logs/plugins/`. Use Python debugger:
```bash
python -m pdb src/plugin_module/plugin.py
```

### Q: Can I charge money for my contributor plugin?

**A:** Not via the marketplace. You're free to charge for support/services separately.

---

## Appendix

### A. Plugin Template

GitHub repos (will be published Week 2):
- Buildin: `github.com/anthropics/plugin-template-buildin`
- Contributor: `github.com/anthropics/plugin-template-contributor`

### B. Example Plugins

See `operator/marketplace/plugins/buildin/` for production examples.

### C. Glossary

- **Tier:** Buildin (SLA+audit) or Contributor (community)
- **Boot Layer:** Determines load order (compliance → core → bundled → installed)
- **Category:** Functional area (memory, security, integration, data, observability)
- **Manifest:** `plugin.json` file describing the plugin
- **Entry Point:** Python path to the plugin class

### D. Resources

- **Plugin Schema:** `operator/marketplace/schemas/plugin-schema.json`
- **CLA (Buildin):** `operator/marketplace/templates/BUILDIN_PLUGIN_CLA.md`
- **License (Contributor):** `operator/marketplace/templates/CONTRIBUTOR_PLUGIN_MIT.md`
- **ADR-0262/0263:** Plugin-Builder system
- **ADR-0243:** Plugin boot-layer taxonomy
- **Layer 4:** Plugin system architecture

---

**Questions?** Open an issue on GitHub or email `plugins@corvinOS.dev`

**Last Updated:** 2026-08-31  
**Next Update:** Week 3 (Migration guide)
