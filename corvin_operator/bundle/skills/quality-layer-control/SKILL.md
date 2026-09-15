---
name: quality-layer-control
type: domain
description: Toggle quality disciplines (ADR Gate, docs-as-definition-of-done, etc.) on or off globally
claim:
  references: []
---

# Quality Layer Control

Manage which quality disciplines are active in your CorvinOS sessions.

## Available Commands

**Check status:**
```bash
python3 -c "from operator.bridges.shared.quality_layers import get_status, list_layers; import json; print(json.dumps(get_status(), indent=2))"
```

Or simpler — to list all layers:
```bash
python3 -c "from operator.bridges.shared.quality_layers import list_layers; import json; print(json.dumps(list_layers(), indent=2))"
```

**Enable a specific layer:**
```bash
python3 -c "from operator.bridges.shared.quality_layers import enable_layer; enable_layer('adr_gate'); print('✓ adr_gate enabled')"
```

**Disable a specific layer:**
```bash
python3 -c "from operator.bridges.shared.quality_layers import disable_layer; disable_layer('adr_gate'); print('✓ adr_gate disabled')"
```

**Enable all layers:**
```bash
python3 -c "from operator.bridges.shared.quality_layers import enable_all; enable_all(); print('✓ All quality layers enabled')"
```

**Disable all layers:**
```bash
python3 -c "from operator.bridges.shared.quality_layers import disable_all; disable_all(); print('✓ All quality layers disabled')"
```

## Configuration File

Settings are stored in `~/.corvin/global/quality-layers.json`. You can also edit it directly:

```json
{
  "enabled": true,
  "layers": {
    "adr_gate": true,
    "docs_as_definition_of_done": true,
    "e2e_driven_iteration": true,
    "usability_first": false
  }
}
```

- `enabled: true` — quality layers are active globally
- `enabled: false` — all layers are suppressed (but config is preserved)
- Per-layer toggles: individual disciplines can be on or off

## Behavior

**Fail-safe defaults:**
- If `quality-layers.json` doesn't exist, all layers are ON (fail-open)
- If a new layer is not listed, it defaults to ON
- If globally disabled (`enabled: false`), all layers are OFF

**When does this take effect?**
- New Claude Code sessions load the config at startup
- Existing sessions may not reflect changes until restart
- Changes are immediately persistent (file is written)

## Typical Workflows

**Turn off ADR Gate (e.g., for quick prototyping):**
```bash
python3 -c "from operator.bridges.shared.quality_layers import disable_layer; disable_layer('adr_gate'); print('✓ adr_gate disabled')"
```

**Check which layers are on:**
```bash
python3 -c "from operator.bridges.shared.quality_layers import list_layers; import json; [print(f'{k}: {v}') for k,v in list_layers().items()]"
```

**Reset to all defaults:**
Delete `~/.corvin/global/quality-layers.json` — next session loads defaults.
