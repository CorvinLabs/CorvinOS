---
name: cowork
description: Multi-persona layer for Claude Code. Per chat (or per CLI call) a different agent role: coder (default), browser, inbox, research, ... Each persona has its own tools, MCP servers, system prompt and workspace. Optional, on top of voice or standalone. Trigger when the user mentions cowork, persona, agent role, multi-agent, bind chat, /cowork-*.
---

# cowork — personas for Claude Code

cowork is an **optional plugin on top of Claude Code** (and integrates
automatically with voice/bridges if both are installed). It turns a single
coding agent into a pool of roles that switch depending on the chat or CLI
call.

## Mental model

```
chat_profiles[<chat>].persona = "browser"
        │
        ▼
cowork.resolver.resolve("browser", overrides=chat_profile)
        │
        ▼
{cwd, mcp_servers, tools, append_system, permission_mode}
        │
        ▼
claude --mcp-config ... --append-system-prompt ... --add-dir ...
```

## What you, as Claude, need to know

- **Personas live in JSON files** under `operator/cowork/personas/` (bundle)
  or `~/.config/claude-cowork/personas/` (user; shadows the bundle).
- **Schema:** `name`, `description`, `permission_mode`, `allowed_tools`,
  `disallowed_tools`, `append_system`, `model`, `mcp_servers` (dict in
  Claude Code MCP config format), `add_dirs` (list), `needs_keys`,
  `needs_oauth`, `zero_config` (bool).
- **Voice integration is additive:** `chat_profiles[<chat>].persona = "..."`
  is a new optional field next to `permission_mode` / `allowed_tools` / etc.
  Profile fields merge on top of the persona (lists union, scalars
  overwrite, `append_system` concatenates).
- **Hot-reload remains:** the persona file is read fresh per inbox message,
  no restart needed.
- **Standalone:** `cowork run <persona> -p "..."` runs `claude` locally
  without involving voice/bridges.

## When the user asks ...

- "which personas do I have" → `/cowork-list`
- "what can persona X do" → `/cowork-show X`
- "make this chat the browser persona" → `/cowork-bind <channel> <chat> browser`
- "undo that" → `/cowork-unbind <channel> <chat>`
- "I want to give inbox its own MCP servers" → `/cowork-add inbox` (copies
  it into the user dir, then edit)
- "test browser persona without the bridge" → `/cowork-run browser -p "..."`

## What NOT to do

- **Never** set `chat_profiles.default.persona` without an explicit request —
  that would reroute every chat (same footgun rule as for
  `chat_profiles.default` in the voice plugin).
- **Never** claim a persona "is running" when `mcp_servers` is empty and
  `needs_oauth` / `needs_keys` are not satisfied. Give a setup hint instead.
