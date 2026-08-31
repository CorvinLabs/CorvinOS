---
description: Copy a bundle persona into the user dir (~/.config/claude-cowork/personas/) for editing
argument-hint: "<persona> [--force]"
---

Copies the bundle persona to `~/.config/claude-cowork/personas/<name>.json`.
User personas shadow bundle personas. Useful for filling in your own MCP
server configs for `inbox` (Gmail/Calendar) etc.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/cowork" add "$@"
```

Reproduce the result verbatim.
