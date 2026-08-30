# Plugin Marketplace Developer Guide

**Version:** 1.0.0  
**Date:** 2026-08-30  
**Status:** Production Ready

## Overview

This guide is for plugin developers who want to create and publish plugins on the CorvinOS Marketplace.

## Table of Contents

1. [Getting Started](#getting-started)
2. [Plugin Structure](#plugin-structure)
3. [Manifest Format](#manifest-format)
4. [Configuration Schema](#configuration-schema)
5. [Best Practices](#best-practices)
6. [Publishing](#publishing)
7. [Troubleshooting](#troubleshooting)

---

## Getting Started

### Prerequisites

- CorvinOS v0.7.0 or later
- A GitHub account (for hosting your plugin repository)
- Basic knowledge of Python or JavaScript (depending on plugin type)

### Quick Start

1. Use **Plugin-Builder** to scaffold a new plugin:
   ```bash
   corvin plugin:create --name "my-awesome-plugin" --category "Integration"
   ```

2. This generates a plugin template with:
   - `manifest.yaml` — Plugin metadata
   - `plugin.py` / `plugin.js` — Main plugin code
   - `config_schema.json` — Configuration template
   - Tests and examples

3. Develop your plugin locally
4. Test with `corvin plugin:test`
5. Publish to GitHub
6. Submit to CorvinOS Marketplace

---

## Plugin Structure

A typical plugin directory looks like:

```
my-awesome-plugin/
├── manifest.yaml                 # Metadata (required)
├── plugin.py                     # Main code (required)
├── config_schema.json            # Config validation (required)
├── README.md                     # User-facing docs (required)
├── LICENSE                       # License file (required)
├── requirements.txt              # Dependencies
├── tests/
│   └── test_plugin.py           # Unit tests
└── examples/
    └── config-example.yaml      # Config example
```

---

## Manifest Format

The `manifest.yaml` file contains plugin metadata. Here's a complete example:

```yaml
# Plugin Identity
id: github-integration
name: GitHub Integration
version: 1.2.3

# Author Information
author:
  name: Jane Doe
  email: jane@example.com
  url: https://example.com

# Licensing
license: Apache-2.0

# Description (user-facing)
description: Integrate GitHub repositories with CorvinOS
long_description: |
  This plugin allows you to:
  - Sync GitHub repository contents
  - Trigger workflows on push events
  - Display GitHub status in the dashboard
  
  Full documentation at https://docs.example.com

# Classification
category: Integration
keywords:
  - github
  - version-control
  - ci/cd
  - automation

# Repository
homepage: https://github.com/example/github-integration
repository:
  type: git
  url: https://github.com/example/github-integration

# Dependencies on other plugins
dependencies:
  webhook-manager: "2.0.0"    # Required version
  notification-hub: "*"       # Any version

# Plugins this conflicts with
conflicts_with:
  - gitlab-integration         # Can't run both

# CorvinOS version constraints
min_corvin_version: "0.7.0"
max_corvin_version: null       # null = no upper bound

# Permissions required (user must approve)
required_permissions:
  - storage.read              # Read local files
  - storage.write             # Write local files
  - network.https             # Make HTTPS requests
  - process.fork              # Start subprocesses

# Sandbox constraints
sandbox:
  cpu_limit_percent: 25       # Max 25% CPU usage
  memory_limit_mb: 512        # Max 512 MB RAM
  timeout_seconds: 300        # Max 5 minutes per operation
  isolation: process          # Run in separate process

# Main entry point
entrypoint: plugin.py

# Boot layer (controls load order and disableability)
boot_layer: installed         # installed | bundled | core | compliance

# Configuration schema validation
config_schema: ./config_schema.json
```

### Manifest Field Reference

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `id` | string | Yes | Lowercase with hyphens only, 3-64 characters |
| `name` | string | Yes | Display name, 3-128 characters |
| `version` | string | Yes | Semantic version (MAJOR.MINOR.PATCH) |
| `author` | object | Yes | Name, email, optional URL |
| `license` | string | Yes | SPDX identifier (e.g., "Apache-2.0", "MIT") |
| `description` | string | Yes | Short description, 10-200 characters |
| `long_description` | string | No | Markdown-formatted full description |
| `category` | string | Yes | One of: Authentication, Performance, Security, Database, Integration, UI, Analytics, Tooling |
| `homepage` | string | No | URL to plugin website |
| `repository` | object | No | Git repository information |
| `keywords` | array | No | Search tags (max 10) |
| `dependencies` | object | No | Map of plugin_id to version spec |
| `conflicts_with` | array | No | List of conflicting plugin IDs |
| `min_corvin_version` | string | No | Minimum CorvinOS version (default: "0.7.0") |
| `max_corvin_version` | string | No | Maximum CorvinOS version (null = no limit) |
| `required_permissions` | array | No | List of permissions user must grant |
| `sandbox` | object | No | Resource limits and isolation mode |
| `entrypoint` | string | No | Main executable or entry point file |
| `boot_layer` | string | No | Load order layer (default: "installed") |
| `config_schema` | string | No | Path to JSON Schema for config validation |

---

## Configuration Schema

The `config_schema.json` defines the structure of plugin configuration. It's a JSON Schema v7 document that users' configuration must match.

### Example

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "GitHub Integration Configuration",
  "type": "object",
  "properties": {
    "github_token": {
      "type": "string",
      "title": "GitHub Personal Access Token",
      "description": "Required for authentication. Generate at https://github.com/settings/tokens",
      "minLength": 20
    },
    "repositories": {
      "type": "array",
      "title": "Repositories to Monitor",
      "items": {
        "type": "object",
        "properties": {
          "owner": {
            "type": "string",
            "title": "Repository Owner",
            "examples": ["torvalds", "python"]
          },
          "repo": {
            "type": "string",
            "title": "Repository Name",
            "examples": ["linux", "cpython"]
          },
          "watch_events": {
            "type": "array",
            "title": "Events to Watch",
            "items": {
              "type": "string",
              "enum": ["push", "pull_request", "issues", "release"]
            }
          }
        },
        "required": ["owner", "repo"]
      },
      "minItems": 1,
      "maxItems": 50
    },
    "sync_interval_minutes": {
      "type": "integer",
      "title": "Sync Interval (minutes)",
      "minimum": 5,
      "maximum": 1440,
      "default": 60
    },
    "enable_notifications": {
      "type": "boolean",
      "title": "Enable Notifications",
      "default": true
    }
  },
  "required": ["github_token", "repositories"]
}
```

### Schema Best Practices

1. **Use Descriptive Titles & Descriptions** — Users see these, make them clear
2. **Provide Examples** — Show valid values
3. **Set Defaults** — Minimize required fields
4. **Validate Constraints** — Use `minimum`, `maximum`, `pattern`, `enum`
5. **Hide Secrets** — Mark API keys/tokens with `"type": "password"` format (UI will mask input)

---

## Best Practices

### 1. Depend on Stable APIs Only

- Don't depend on private/undocumented CorvinOS APIs
- Use the stable public plugin API
- Check CorvinOS version constraints in your manifest
- Document minimum version required for each feature

### 2. Request Only Needed Permissions

- List each permission your plugin actually needs
- Avoid over-requesting (e.g., don't ask for `network.http` if you only need `network.https`)
- Document why each permission is needed in your README

### 3. Handle Configuration Errors Gracefully

```python
# Good: validate config on startup, fail with helpful message
from jsonschema import validate, ValidationError

try:
    validate(instance=config, schema=CONFIG_SCHEMA)
except ValidationError as e:
    raise PluginConfigError(f"Invalid config: {e.message}")

# Bad: assume config is valid, crash later
github_token = config['github_token']  # KeyError if missing!
```

### 4. Resource Usage

- Respect sandbox limits (CPU, memory, timeout)
- Don't spawn unlimited background threads
- Clean up resources on shutdown (close files, connections)
- Monitor your plugin's resource usage during development

### 5. Error Messages

- Make error messages user-friendly
- Never log API keys or tokens (they'll be in audit logs!)
- Include troubleshooting steps

```python
# Good: user-friendly, actionable
raise PluginError(
    "Could not connect to GitHub. "
    "Verify your token is valid: https://github.com/settings/tokens"
)

# Bad: technical, leaks credentials
raise PluginError(f"API error: {response.json()}")  # Might include token!
```

### 6. Testing

```bash
# Run locally
corvin plugin:test

# Check manifest validity
corvin plugin:validate

# Profile resource usage
corvin plugin:profile

# Test with different config schemas
corvin plugin:test --config tests/fixtures/config-*.yaml
```

### 7. Documentation

Your plugin should include:
- **README.md** — What it does, installation, quick start
- **Configuration Example** — Sample `config-example.yaml`
- **Troubleshooting** — Common issues and solutions
- **Change Log** — What's new in each version

---

## Publishing

### Pre-Publish Checklist

- [ ] Manifest is valid (`corvin plugin:validate`)
- [ ] Tests pass (`corvin plugin:test`)
- [ ] README is complete and accurate
- [ ] Configuration example works
- [ ] No secrets in code or docs
- [ ] License file included
- [ ] Version bumped (semantic versioning)
- [ ] Change log updated

### Publishing Steps

1. **Push to GitHub** — Your plugin must be in a public GitHub repository
2. **Create GitHub Release** — Tag a version (e.g., `v1.2.3`)
3. **Submit to Marketplace** — Visit CorvinOS Marketplace → "Publish Plugin"
4. **Fill out submission form:**
   - Plugin repository URL
   - Brief description
   - Screenshots (optional)
   - Support email
5. **Security review** — We review for obvious security issues
6. **Publication** — Approved plugins appear in the marketplace within 24 hours

### After Publishing

- Monitor reviews and ratings
- Respond to user feedback and bug reports
- Release updates as needed
- Keep documentation up to date

---

## Troubleshooting

### "Manifest validation failed: field 'id' must be lowercase"

**Solution:** Plugin IDs must be lowercase with hyphens only. Change `MyPlugin` to `my-plugin`.

### "Dependency 'auth-plugin' version spec invalid"

**Solution:** Version specs must be semantic versions or `*` wildcard. Change `auth-plugin: "2"` to `auth-plugin: "2.0.0"` or `auth-plugin: "*"`.

### "Plugin requires permission 'storage.execute' which doesn't exist"

**Solution:** Check the list of valid permissions:
- `storage.read`, `storage.write`
- `network.http`, `network.https`
- `process.fork`, `process.exec`
- `filesystem.read`, `filesystem.write`

### "Community plugins cannot use boot_layer=core"

**Solution:** Community-contributed plugins must use `boot_layer: installed`. Only built-in and vetted plugins can use other layers.

### "Cannot publish: configuration schema is invalid"

**Solution:** Run `corvin plugin:validate` to check your `config_schema.json`. It must be valid JSON Schema v7.

---

## Support

- **Questions:** CorvinOS Plugin Developer Forum
- **Issues:** GitHub Issues in your plugin repository
- **Security Bugs:** security@corvin.io

---

**Learn More:**
- [User Guide](PLUGIN_MARKETPLACE_USER_GUIDE.md) — For end users
- [Operator Guide](PLUGIN_MARKETPLACE_OPERATOR_GUIDE.md) — For administrators
- [Plugin-Builder Reference](../plugin-builder/README.md) — Scaffolding tool
