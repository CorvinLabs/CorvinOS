# TIER 1 REPOSITORY STRUCTURE
## Marketplace & Core Repository Design

**Document Version:** 1.0  
**Status:** DESIGN SPECIFICATION  
**Last Updated:** 2026-08-29  

---

## EXECUTIVE SUMMARY

This document defines the directory layout and structure for both the **Corvin-Marketplace** (new) and **CorvinOS core** (modified) repositories after Tier 1 plugin extraction.

### Key Principles

1. **Self-contained plugins:** Each plugin directory has all code, tests, docs, and dependencies
2. **Zero core imports from plugins:** Plugins import from CorvinOS, not vice versa
3. **Clear separation:** Compliance layer stays in core; bundled plugins move to marketplace
4. **Operator workflow:** `corvin plugin install <name>` downloads from marketplace
5. **Backwards compatible:** Existing operator `.corvin/` directories work unchanged

---

## PART I: CORVIN-MARKETPLACE REPOSITORY STRUCTURE

### 1.1 Repository Root Layout

```
Corvin-Marketplace/
├── plugins/                              # Tier 1, 2, 3 plugins
│   ├── slack-notifier/
│   ├── data-transform-json-csv/
│   ├── audit-backend-json/
│   ├── notification-backend-discord/
│   ├── recall-backend-sqlite/
│   ├── stt-provider-openai-whisper/
│   └── router-backend-default/
│
├── _corvin_plugins/                      # Vendored base classes (re-exports)
│   ├── __init__.py                       # Re-exports PluginInterface, etc.
│   ├── plugin_interface.py               # Copy from CorvinOS (read-only)
│   ├── protocol.py                       # Copy from CorvinOS (read-only)
│   └── __version__.py                    # Pinned version (must match CorvinOS)
│
├── docs/
│   ├── README.md                         # Marketplace overview
│   ├── INSTALLATION_GUIDE.md             # How to install Tier 1 plugins
│   ├── PLUGIN_DEVELOPMENT_GUIDE.md       # For future Tier 2/3 authors
│   ├── TIER_1_MIGRATION.md               # High-level migration summary
│   ├── API_REFERENCE.md                  # Plugin interface API (for developers)
│   └── TROUBLESHOOTING.md                # Common issues + fixes
│
├── scripts/
│   ├── generate_registry.py              # Auto-generate registry.json
│   ├── install_plugin.sh                 # Helper to install from CLI
│   ├── verify_plugin.py                  # Validate plugin manifest + structure
│   ├── test_plugins.sh                   # Run all plugin tests
│   └── publish_registry.sh               # Update registry on GitHub
│
├── tests/
│   ├── e2e/
│   │   ├── test_operator_workflow.py     # Simulate: list → install → enable → test
│   │   └── conftest.py
│   ├── unit/
│   │   ├── test_plugin_registry.py       # registry.json validation
│   │   └── test_plugin_discovery.py      # Plugin discovery logic
│   └── fixtures/
│       ├── mock_plugin/
│       └── sample_manifests/
│
├── registry.json                         # Auto-generated (DO NOT EDIT)
├── registry.json.sig                     # Ed25519 signature (future: ADR-0249)
│
├── .github/
│   ├── workflows/
│   │   ├── validate_plugins.yml          # CI: validate all manifests
│   │   ├── test_plugins.yml              # CI: run plugin tests
│   │   └── publish_registry.yml          # CI: auto-publish registry
│   └── ISSUE_TEMPLATE/
│       └── bug_report.md
│
├── .gitignore
├── LICENSE                               # Apache-2.0 (same as CorvinOS)
├── CONTRIBUTING.md                       # Plugin contribution guidelines
├── README.md                             # Repo overview + quick start
└── pyproject.toml                        # Marketplace metadata (not for packaging)
```

### 1.2 Individual Plugin Structure

Each Tier 1 plugin follows this layout:

```
plugins/slack-notifier/
├── manifest.yaml                         # Plugin declaration (immutable)
├── plugin.py                             # Main plugin class
├── config_schema.json                    # JSON Schema for settings (optional)
│
├── requirements.txt                      # External Python dependencies ONLY
│                                         # (no corvin_plugins, no CorvinOS core)
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                       # Pytest fixtures
│   ├── test_plugin.py                    # Unit tests for plugin.py
│   ├── test_integration.py               # Integration tests with CorvinOS
│   ├── test_manifest.py                  # Validate manifest schema
│   └── fixtures/
│       ├── mock_slack_responses.py
│       └── sample_configs.yaml
│
├── README.md                             # Plugin overview + usage
├── CHANGELOG.md                          # Version history
├── LICENSE                               # Apache-2.0
│
├── docs/                                 # Optional: detailed documentation
│   ├── CONFIGURATION.md                  # How to configure the plugin
│   ├── EXAMPLES.md                       # Real-world use cases
│   └── TROUBLESHOOTING.md                # Common issues
│
└── .gitignore
```

### 1.3 Manifest Structure (manifest.yaml)

```yaml
# Plugin identity
id: slack-notifier
version: "1.0.0"
name: "Slack Notifier"

# Categorization
category: "notification"
boot_layer: "bundled"           # Or "installed" for user-installed plugins
origin: "builtin"               # Tier 1 = builtin in marketplace

# Authorship
author: "Corvin Labs"
email: "support@corvin-labs.com"
homepage_url: "https://github.com/corvin-labs/Corvin-Marketplace"
license: "Apache-2.0"

# Description
description: "Forward audit events to Slack via webhook"
long_description: |
  The Slack Notifier plugin integrates CorvinOS with Slack,
  enabling real-time notifications of audit events, errors,
  and status changes. Configure with a Slack webhook URL
  and select which events to forward.

# Entry point (required)
entry_point: "plugin.py::SlackNotifierPlugin"
plugin_type: "custom"

# Dependencies
dependencies: []                # Tier 1 plugins have zero plugin dependencies
conflicts_with: []

min_corvin_version: "0.8.0"     # Require Tier 1 pilot version
max_corvin_version: null        # No upper bound

# Permissions (for operator visibility + future enforcement)
permissions:
  network_egress: "allowed"    # Needs Slack webhook
  pii_risk: "medium"            # May contain user context in messages
  data_locality: "external"     # Data sent to Slack

# Health check
health_check:
  enabled: true
  timeout_seconds: 5
  failure_mode: "disable_plugin"

# Settings schema (JSON Schema)
settings_schema:
  type: "object"
  properties:
    webhook_url:
      type: "string"
      description: "Slack webhook URL"
      required: true
      pattern: "^https://hooks\\.slack\\.com/.*"
    
    notification_events:
      type: "array"
      items: { enum: ["audit.event", "error.critical", "plugin.enabled"] }
      default: ["audit.event"]
      description: "Events to forward to Slack"
    
    batch_size:
      type: "integer"
      minimum: 1
      maximum: 100
      default: 10
    
    timeout_seconds:
      type: "integer"
      minimum: 5
      maximum: 300
      default: 30
  
  required: ["webhook_url"]

settings_schema_version: "1.0"

# Metadata
tags: ["notification", "integration", "audit"]
keywords: ["slack", "webhook", "notifications", "events"]

# Timestamps (auto-filled)
created_at: "2026-08-29T00:00:00Z"
updated_at: "2026-08-29T00:00:00Z"
```

### 1.4 Registry Structure (registry.json)

Auto-generated by `scripts/generate_registry.py`. DO NOT EDIT manually.

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-29T15:30:00Z",
  "marketplace_version": "1.0.0",
  
  "plugins": [
    {
      "id": "slack-notifier",
      "version": "1.0.0",
      "name": "Slack Notifier",
      "description": "Forward audit events to Slack",
      "author": "Corvin Labs",
      "category": "notification",
      "boot_layer": "bundled",
      "origin": "builtin",
      "listed": true,
      "path": "plugins/slack-notifier",
      "entry_point": "plugin.py::SlackNotifierPlugin",
      "min_corvin_version": "0.8.0",
      "max_corvin_version": null,
      "tags": ["notification", "integration", "audit"],
      "manifest_sha256": "abc123...",
      "created_at": "2026-08-29T00:00:00Z"
    },
    {
      "id": "data-transform-json-csv",
      "version": "1.0.0",
      "name": "JSON ↔ CSV Data Transformer",
      "description": "Convert between JSON and CSV formats",
      "author": "Claude Code",
      "category": "utility",
      "boot_layer": "bundled",
      "origin": "builtin",
      "listed": true,
      "path": "plugins/data-transform-json-csv",
      "entry_point": "plugin.py::DataTransformPlugin",
      "min_corvin_version": "0.8.0",
      "max_corvin_version": null,
      "tags": ["data", "transformation", "utility"],
      "manifest_sha256": "def456...",
      "created_at": "2026-08-29T00:00:00Z"
    }
  ],
  
  "statistics": {
    "total_plugins": 7,
    "tier_1_plugins": 7,
    "tier_2_plugins": 0,
    "tier_3_plugins": 0,
    "categories": {
      "notification": 1,
      "utility": 1,
      "backend": 5
    }
  }
}
```

---

## PART II: CORVINOSOS CORE REPOSITORY STRUCTURE (AFTER EXTRACTION)

### 2.1 Changes to CorvinOS Plugin Directory

**Before extraction:**
```
core/plugins/
├── api_v2.py
├── marketplace.py
├── plugin_registry.py
├── corvin_plugins/
│   ├── plugin_interface.py      # Base classes
│   ├── protocol.py              # Protocol definitions
│   ├── registry.py              # Registry implementation
│   ├── bootstrap.py             # Boot tripwire
│   ├── providers/
│   │   ├── audit_backend.py     # Compliance
│   │   ├── notification_backend.py  # TO BE REMOVED
│   │   ├── user_backend.py
│   │   └── ...
│   └── ...
├── examples/                     # REMOVED ENTIRELY
│   ├── slack-notifier/
│   ├── data-transform-json-csv/
│   └── ...
└── templates/
    ├── slack_notifier_plugin.py  # REMOVED
    └── ...
```

**After extraction:**
```
core/plugins/
├── api_v2.py
├── marketplace.py                # Now imports from Corvin-Marketplace
├── plugin_registry.py
├── corvin_plugins/               # CORE ONLY - NO TIER 1 PLUGINS
│   ├── __init__.py               # Export base classes to Corvin-Marketplace
│   ├── plugin_interface.py       # Kept (vendored to marketplace)
│   ├── protocol.py               # Kept (vendored to marketplace)
│   ├── registry.py               # Kept
│   ├── bootstrap.py              # Kept (boot tripwire)
│   ├── manifest.py               # Kept
│   ├── loader.py                 # Kept
│   ├── validation.py             # Kept
│   ├── healing.py                # Kept
│   ├── health_check_tree.py       # Kept
│   ├── providers/
│   │   ├── audit_backend.py      # KEPT (compliance)
│   │   ├── user_backend.py       # Kept (if needed)
│   │   └── __init__.py
│   ├── sandbox/                  # Kept (plugin isolation)
│   └── ...
├── examples/                     # REMOVED
├── templates/                    # REMOVED
└── tests/
    ├── test_boot_platform_call_site.py  # KEPT (tripwire verification)
    ├── test_plugin_system.py            # KEPT (registry tests)
    └── ...
```

### 2.2 Core `__init__.py` Changes

**Before extraction:**
```python
from .corvin_plugins import (
    PluginInterface,
    PluginRegistry,
    bootstrap_global,  # Loads compliance + core + bundled plugins
    register_global_plugin,
)

# List of bundled plugins loaded at boot
DEFAULT_PLUGINS = [
    "slack-notifier",
    "data-transform-json-csv",
    "audit-backend-json",
    # ... 4 more Tier 1 plugins
]
```

**After extraction:**
```python
from .corvin_plugins import (
    PluginInterface,
    PluginRegistry,
    bootstrap_global,  # Now returns [] — compliance/core are empty
    register_global_plugin,
)

# Bundled plugins are now in Corvin-Marketplace
# Operator installs via: corvin plugin install <plugin-id>
DEFAULT_PLUGINS = []  # Empty — no bundled plugins in core
```

### 2.3 Core Provider Exports

**Location:** `core/plugins/corvin_plugins/__init__.py`

This file re-exports the base classes so Corvin-Marketplace plugins can import them:

```python
"""
CorvinOS Plugin System - Core Infrastructure

Base classes and protocols are exported here for use by both
the core plugin system and marketplace plugins.
"""

# Re-export for marketplace consumption (via vendored _corvin_plugins)
from .plugin_interface import PluginInterface
from .protocol import (
    HealthStatus,
    NotificationBackend,
    AuditBackend,
    UserBackend,
    DataConnector,
    RecallBackend,
    RouterBackend,
    SttProvider,
    SummaryProvider,
    PluginContext,
)
from .manifest import PluginManifest
from .registry import PluginRegistry

__all__ = [
    "PluginInterface",
    "HealthStatus",
    "NotificationBackend",
    "AuditBackend",
    "UserBackend",
    "DataConnector",
    "RecallBackend",
    "RouterBackend",
    "SttProvider",
    "SummaryProvider",
    "PluginContext",
    "PluginManifest",
    "PluginRegistry",
]

__version__ = "0.8.0"  # Must match Corvin-Marketplace vendoring
```

---

## PART III: OPERATOR INSTALLATION PATHS

### 3.1 Plugin Installation Locations

When operator runs `corvin plugin install slack-notifier`:

```bash
# 1. Download from Corvin-Marketplace GitHub (or local filesystem)
# Command: corvin plugin install slack-notifier
#   or: corvin plugin install ./local/path/slack-notifier.tar.gz

# 2. Extract to operator's ~/.corvin/plugins/installed/<plugin-id>/
~/.corvin/
├── global/
│   ├── config.yaml
│   ├── plugin_trust_anchors.txt      # Future: ADR-0249
│   └── ...
├── plugins/
│   ├── installed/
│   │   ├── slack-notifier/           # ← Installed here
│   │   │   ├── manifest.yaml
│   │   │   ├── plugin.py
│   │   │   ├── config.yaml           # Operator fills this in
│   │   │   └── requirements.txt
│   │   ├── data-transform-json-csv/
│   │   │   ├── manifest.yaml
│   │   │   ├── plugin.py
│   │   │   └── config.yaml
│   │   └── [operator-installed plugins]
│   ├── bundled/                      # Empty (Tier 1 moved to marketplace)
│   └── registry.json                 # Local cache of marketplace registry
├── audit.jsonl                       # Audit trail
└── ...
```

### 3.2 Plugin Loading at Startup

**Sequence:**

1. **Bootstrap core** (CorvinOS startup)
   - Load compliance layer (none deployed today)
   - Load core layer (none deployed today)
   - Load bundled layer (none deployed today)
   - Result: `bootstrap_global()` returns `[]`

2. **Load operator-installed plugins**
   - Scan `~/.corvin/plugins/installed/*/manifest.yaml`
   - For each enabled plugin:
     - Import `plugin.py`
     - Instantiate plugin class
     - Call `on_load(context)`
   - Audit-log: `plugin.loaded` event

3. **Register with extension points**
   - If plugin implements `NotificationBackend`:
     - Register in notification system
   - If plugin implements `AuditBackend`:
     - Register in audit system
   - Etc.

**Code location:** `core/plugins/corvin_plugins/loader.py` (unchanged)

---

## PART IV: VERSION COORDINATION

### 4.1 Version Pinning

**Corvin-Marketplace** must pin the version of CorvinOS base classes it vendors:

```
_corvin_plugins/__version__.py:
  VENDORED_CORVINODOS_VERSION = "0.8.0"
  VENDORED_PLUGIN_INTERFACE_SHA256 = "abc123def456..."
```

When operator runs `corvin plugin install slack-notifier`:
1. Check operator's CorvinOS version (e.g., 0.8.0)
2. Check plugin's `min_corvin_version` (e.g., 0.8.0)
3. Check plugin's `max_corvin_version` (e.g., null = no upper bound)
4. **Verify:** 0.8.0 >= 0.8.0 and (0.8.0 <= null or null) ✅
5. Proceed with installation

### 4.2 Base Class Stability

**Promise:** Base classes in `core/plugins/corvin_plugins/` are stable.

- `PluginInterface`: Breaking changes require new protocol version (e.g., `PluginInterfaceV2`)
- `NotificationBackend`: Same
- `AuditBackend`: Same

When CorvinOS ships a breaking change to base classes:
1. Create new protocol version (e.g., `PluginContextV2`)
2. Mark old version as deprecated (but don't remove)
3. All marketplace plugins get a grace period to migrate
4. After grace period, old version is no longer loaded

---

## PART V: CI/CD INTEGRATION

### 5.1 GitHub Workflows (Corvin-Marketplace)

**`.github/workflows/validate_plugins.yml`**

Runs on every commit to validate all plugins:

```yaml
name: Validate Plugins

on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Validate manifests
        run: |
          for dir in plugins/*/; do
            python3 scripts/verify_plugin.py "$dir"
          done
      
      - name: Validate registry.json
        run: |
          python3 scripts/generate_registry.py --verify
      
      - name: Run plugin tests
        run: bash scripts/test_plugins.sh
```

**`.github/workflows/publish_registry.yml`**

Runs on release tag to publish marketplace registry:

```yaml
name: Publish Registry

on:
  push:
    tags: ['v*']

jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Generate registry
        run: python3 scripts/generate_registry.py
      
      - name: Sign registry (future: ADR-0249)
        run: python3 scripts/sign_registry.py
      
      - name: Push to GitHub
        run: |
          git add registry.json registry.json.sig
          git commit -m "chore(registry): update for v${{ github.ref_name }}"
          git push
```

### 5.2 CorvinOS CI/CD

No changes needed to CorvinOS CI/CD. Tier 1 plugin tests are now in Corvin-Marketplace.

CorvinOS still runs:
- `pytest core/plugins/tests/test_boot_platform_call_site.py` ✅
- `pytest core/plugins/tests/test_plugin_system.py` ✅
- All compliance/core layer tests ✅

---

## PART VI: DOCUMENTATION STRUCTURE

### 6.1 Corvin-Marketplace Docs

**`docs/README.md`**
- What is the Corvin marketplace?
- How do I install a plugin?
- How do I create a plugin? (for Tier 2/3)
- FAQ

**`docs/INSTALLATION_GUIDE.md`**
- Install Tier 1 plugins
- Configure each plugin
- Enable/disable/uninstall
- Troubleshooting

**`docs/PLUGIN_DEVELOPMENT_GUIDE.md`**
- Plugin architecture
- Implement `PluginInterface`
- Create `manifest.yaml`
- Write tests
- Submit PR

**`docs/API_REFERENCE.md`**
- `PluginInterface` methods
- `NotificationBackend` protocol
- `AuditBackend` protocol
- Etc.

### 6.2 CorvinOS Docs

**New file: `docs/plugins/MARKETPLACE_OVERVIEW.md`**
- Why Tier 1 moved to marketplace
- How to install marketplace plugins
- Difference between core plugins and marketplace plugins

**Updated: `docs/plugins/PLUGIN_DEVELOPMENT.md`**
- Removed examples of Tier 1 plugins
- Added reference to Corvin-Marketplace documentation

---

## APPENDIX A: File Checklist

### Corvin-Marketplace Files to Create

```
✅ plugins/*/manifest.yaml (7 files)
✅ plugins/*/plugin.py (7 files)
✅ plugins/*/requirements.txt (7 files)
✅ plugins/*/tests/ (7 dirs)
✅ plugins/*/README.md (7 files)
✅ _corvin_plugins/__init__.py
✅ _corvin_plugins/plugin_interface.py (copied)
✅ _corvin_plugins/protocol.py (copied)
✅ scripts/generate_registry.py
✅ scripts/verify_plugin.py
✅ scripts/test_plugins.sh
✅ docs/*.md (5+ files)
✅ tests/e2e/test_operator_workflow.py
✅ .github/workflows/validate_plugins.yml
✅ .github/workflows/publish_registry.yml
✅ LICENSE
✅ CONTRIBUTING.md
✅ README.md
```

### CorvinOS Files to Delete

```
❌ core/plugins/examples/ (entire dir)
❌ core/plugins/templates/slack_notifier_plugin.py
❌ core/plugins/templates/bridge_channel_plugin.py
❌ core/plugins/templates/router_backend_plugin.py
❌ [other Tier 1 templates]
```

### CorvinOS Files to Keep

```
✅ core/plugins/corvin_plugins/plugin_interface.py
✅ core/plugins/corvin_plugins/protocol.py
✅ core/plugins/corvin_plugins/registry.py
✅ core/plugins/corvin_plugins/bootstrap.py
✅ core/plugins/corvin_plugins/manifest.py
✅ core/plugins/corvin_plugins/providers/audit_backend.py (compliance)
✅ core/plugins/tests/test_boot_platform_call_site.py
✅ core/plugins/tests/test_plugin_system.py
```

---

**Document owner:** Architecture Team  
**Status:** READY FOR REVIEW
