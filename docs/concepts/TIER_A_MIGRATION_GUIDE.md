# Tier A Migration Guide — Convert Built-in Skills to Plugins

## Overview

This guide documents how to migrate a built-in Tier A skill to the Plugin System.

**Tier A Plugins** are built-in, always-on skills that ship with CorvinOS. They run without sandbox restrictions and are part of the core feature set.

## Migration Steps

### Step 1: Extract Skill Code

Copy your skill implementation to `core/orchestration/plugin_system/plugins/[skill-id]/`

Example: `code-review` skill → `plugins/code-review/`

```
plugins/code-review/
├── __init__.py              # Plugin class
├── main.py                  # Business logic
├── manifest.json            # Metadata
└── settings.json            # Default settings
```

### Step 2: Create Plugin Manifest

**File:** `plugins/[skill-id]/manifest.json`

```json
{
  "id": "code-review",
  "name": "Code Review",
  "version": "1.0.0",
  "type": "skill",
  "description": "Automated code review with Claude",
  "tier": "a",
  "min_corvin_version": "0.11.0",
  "settings_schema": {
    "type": "object",
    "properties": {
      "model": {
        "type": "string",
        "enum": ["haiku", "sonnet", "opus"],
        "default": "sonnet"
      }
    }
  }
}
```

### Step 3: Implement Plugin Class

**File:** `plugins/[skill-id]/__init__.py`

```python
from corvin_plugin import Plugin, PluginContext

class CodeReviewPlugin(Plugin):
    plugin_id = "code-review"
    plugin_type = "skill"
    version = "1.0.0"
    display_name = "Code Review"
    
    async def on_load(self, ctx: PluginContext) -> None:
        """Called when plugin is loaded."""
        pass
    
    async def on_unload(self) -> None:
        """Called when plugin is unloaded."""
        pass
    
    # Your business logic here
    async def review_code(self, code: str) -> dict:
        """The actual skill implementation."""
        # ... use ctx.config.settings["model"] for settings access
        return {"feedback": "..."}
```

### Step 4: Register in Registry

Add to `.corvin/tenants/_default/plugins/registry.yaml`:

```yaml
code-review:
  id: code-review
  version: 1.0.0
  name: Code Review
  type: skill
  tier: a
  enabled: true
  installed_at: 2026-07-26T00:00:00Z
  settings:
    model: sonnet
```

### Step 5: Wire MCP Endpoint (if needed)

If your skill needs a `/slash-command`, register as MCP server:

```python
# plugins/code-review/server.py
from fastapi import FastAPI

app = FastAPI()

@app.post("/code-review")
async def review(code: str):
    # Use plugin instance
    return await plugin_instance.review_code(code)
```

### Step 6: Test

```bash
# Unit tests
uv run pytest tests/test_code_review_plugin.py

# Integration test
uv run pytest tests/test_plugin_lifecycle.py -k code-review

# Manual: toggle in Console
# http://localhost:3000/plugins
# → Enable "Code Review" toggle
# → Verify `/code-review` command works
```

## Example: Code Review Migration

### Before (Built-in Skill)

```
core/
├── skills/
│   └── code_review.py       # Monolithic skill
```

### After (Tier A Plugin)

```
core/orchestration/plugin_system/plugins/
└── code-review/
    ├── __init__.py          # Plugin class
    ├── main.py              # Business logic (unchanged)
    ├── manifest.json        # Metadata
    └── settings.json        # Default settings
```

## Checklist

- [ ] Skill code extracted to `plugins/[id]/`
- [ ] `manifest.json` created with metadata
- [ ] Plugin class implements `Plugin` protocol
- [ ] Settings schema defined in manifest
- [ ] MCP endpoint wired (if needed)
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Registry YAML entry created
- [ ] Console toggle works
- [ ] Docs updated

## Phase 3: Automate

Eventually, create a migration script:

```bash
./scripts/migrate-skill-to-plugin.py core/skills/code_review.py \
  --name "Code Review" \
  --tier a \
  --version 1.0.0
```

This would automate steps 1-5 above.

## References

- [ADR-0XXX Plugin System](./ADR-0XXX-PLUGIN_SYSTEM.md)
- [Plugin Manifest Specification](../../operator/orchestration/plugin_system/models.py)
