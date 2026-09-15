---
description: Run claude locally in a persona (standalone — no bridge/voice)
argument-hint: "<persona> [-p <prompt>] [--dry-run] [-- <claude-args>...]"
---

Starts `claude` with the chosen persona configuration (MCP servers,
tool allowlist, append-system-prompt, add-dirs, permission-mode). Either
interactively or once via `-p "<prompt>"`. `--dry-run` only prints the
final argument list without invoking `claude`.

Anything after `--` is forwarded verbatim to `claude` (e.g. `-- --resume`).

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/cowork" run "$@"
```
