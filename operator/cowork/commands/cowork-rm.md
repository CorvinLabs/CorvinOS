---
description: Delete a user persona (bundle persona stays untouched)
argument-hint: "<persona>"
---

Removes only the user variant under `~/.config/claude-cowork/personas/`.
The bundle persona inside the plugin remains available.

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/cowork" rm "$1"
```

Reproduce the result verbatim.
