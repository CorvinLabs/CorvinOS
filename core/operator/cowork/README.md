# cowork — multi-persona layer for Claude Code

`cowork` is an **optional plugin** in the same marketplace as `voice`. It turns
Claude Code into a multi-role agent: per chat (or per CLI invocation) a
different persona — coder, research, inbox, orchestrator, ... Each one with its own
tools, MCP servers, system prompt and workspace.

> **Voice works completely without cowork.** cowork sits on top and can be
> added or removed at any time.

## Mental model

```
chat_profiles[<chat>].persona = "research"
        │
        ▼  (voice adapter, only if cowork is installed)
cowork.resolver.resolve("research", overrides=chat_profile)
        │
        ▼
{permission_mode, allowed_tools, mcp_servers, add_dirs, append_system, ...}
        │
        ▼
claude --mcp-config ... --append-system-prompt ... --add-dir ... --allowedTools ...
```

## Three ways to use cowork

### 1. Per chat (with voice + bridge)

```bash
# bind once:
cowork bind whatsapp "+49170...@s.whatsapp.net" research
# or via slash command:
/cowork-bind whatsapp +49170...@s.whatsapp.net research
```

From now on, that one chat answers as the research agent (Playwright MCP loaded,
WebFetch / WebSearch allowed). Other chats stay unchanged. Hot-reload — no restart needed.

### 2. Standalone (without voice / bridge)

```bash
# one-shot request:
cowork run research -p "find me the cheapest train tickets to Munich"

# interactive claude session with the persona:
cowork run research

# pass arguments through to claude (everything after `--`):
cowork run coder -p "review my changes" -- --resume
```

### 3. Edit the persona file yourself

For personas with their own MCPs (e.g. `inbox` with Gmail), copy the bundle
into the user dir and add the `mcp_servers`:

```bash
cowork add inbox
$EDITOR ~/.config/claude-cowork/personas/inbox.json
```

User personas shadow bundle personas. `cowork rm inbox` removes only the user
variant, the bundle stays.

## Bundled personas

| Persona | zero-config | Tools / MCP | Generation capability | What for |
|---|---|---|---|---|
| `assistant` | ✓ | bypass-permissions, all tools | forge tools + skills | Generalist — auto-routing fallback when the router has no clear pick |
| `coder` | ✓ | bypass-permissions, all tools | forge tools + skills (`code.*` namespace) | Like `assistant` but explicit for coding chats |
| `research` | ✓ | Playwright MCP, WebSearch, WebFetch | forge tools (with shared network) + skills | Web research + browser automation; notes under `~/cowork/research/` |
| `inbox` | ✗ | Gmail MCP + Calendar MCP | forge tools + skills | Email triage, calendar suggestions |
| `homeassistant` | ✗ | HASS MCP (opt-in) | None by default — opt-in via `forge_enabled` / `skill_forge_enabled` | Smart-home control |
| `orchestrator` | ✓ | five delegate MCP tools | forge tools + skills | Routes sub-tasks to worker engines (Codex, OpenCode, Hermes, Copilot CLI) |
| `forge` | ✓ | `mcp__forge__*` + `mcp__skill_forge__*`, no Bash/Edit/Write | Native (the persona itself is the generator). Historic name `skill-forge` resolves here via alias | Focused runtime-generation specialist for explicit "let's build something" sessions |

`research` loads Playwright on first call via `npx -y @playwright/mcp` (one-time
~150 MB). If that's too much, replace `mcp_servers` in a user variant with a
lighter web MCP.

### Capability flags

`forge_enabled: true` and `skill_forge_enabled: true` are the per-persona
opt-ins for runtime generation. The resolver injects the MCP servers, adds
`mcp__forge__forge_tool` / `mcp__forge__forge_promote` / `mcp__forge__forge_list`
(or the seven `mcp__skill_forge__*` tools) to `allowed_tools`, and appends a
runtime-built **capability brief** to `append_system`. The brief is read fresh
from the bundle `policy.json` per resolve, so it knows which namespace prefix
the persona owns and whether its sandbox shares the host network. The path-gate
hook (`operator/voice/hooks/path_gate.py`, layer 10) keeps the generation
workspaces writable only via the MCP servers, regardless of the persona's
`permission_mode` — including `bypassPermissions`.

## Persona schema

```jsonc
{
  "name": "research",
  "description": "...",
  "permission_mode": "bypassPermissions",  // default | plan | acceptEdits | bypassPermissions
  "allowed_tools":   [],                   // empty with bypassPermissions = allow all
  "disallowed_tools":[],
  "append_system":   "You are a research agent. ...",
  "model":           "claude-sonnet-4-6",            // optional
  "mcp_servers": {                                   // claude-code MCP format
    "playwright": { "command": "npx", "args": [...] }
  },
  "add_dirs":      ["~/cowork/research"],
  "needs_keys":    [],                               // env vars required
  "needs_oauth":   [],                               // OAuth providers
  "zero_config":   true,                             // → wizard auto-installs

  // Generation opt-in (the resolver injects the MCP server + tools + brief):
  "forge_enabled":        true,
  "skill_forge_enabled":  true,
  "tool_namespace":       "research",   // registration prefix in policy.persona_namespaces
  "forge_default_scope":  "session",    // task | session | project | user
  "allowed_forged_tools": [],           // optional ACL allowlist (empty = wildcard)
  "inject_skills":        true,         // false on generator personas (forge) so skill bodies don't pollute the prompt
  "routing_anchors":      [...],
  "routing_exclude":      false
}
```

## Slash commands

| Command | Effect |
|---|---|
| `/cowork-list` | Show all known personas (bundle + user) |
| `/cowork-show <persona>` | Print a persona as JSON |
| `/cowork-bind <channel> <chat> <persona>` | Bind a chat to a persona |
| `/cowork-unbind <channel> <chat>` | Remove a binding |
| `/cowork-add <persona>` | Copy a bundle persona into the user dir |
| `/cowork-rm <persona>` | Delete a user persona (bundle stays) |
| `/cowork-run <persona>` | Standalone invocation (no bridge) |

## Merge order in `chat_profiles`

When a `chat_profile` contains *both* a `persona` field and its own custom
fields, the two merge:

- **Scalars** (`permission_mode`, `model`): profile override wins.
- **Lists** (`allowed_tools`, `disallowed_tools`, `add_dirs`): union — persona
  first, then profile. No duplicates.
- **Dicts** (`mcp_servers`): shallow merged, profile keys win.
- **`append_system`**: concatenated — persona prompt + chat-specific hint.

## What stays unchanged in voice

- All daemons, hot-reload, whitelist, PIN auth, notification relay.
- The `chat_profiles` schema in the voice plugin — `persona` is just one new
  optional field next to the existing ones.
- If cowork is not installed, voice ignores the `persona` field entirely and
  behaves exactly like v0.x.

## Bundled tools

- **`bin/gmail-helper`** — local helper that lets every persona compose Gmail
  with **real MIME attachments** (Drafts via OAuth, or SMTP send via App
  password). Symlink it into `$PATH` once (e.g. `ln -s
  $(pwd)/bin/gmail-helper ~/.local/bin/gmail-helper`). First-time setup is
  guided: `gmail-helper wizard`. Full docs: [`bin/gmail-helper.md`](bin/gmail-helper.md).

## Tests

```bash
python3 operator/cowork/test/test_resolver.py                    # Unit
python3 operator/bridges/shared/test_adapter_cowork.py     # Integration
```

Both are also part of the central `bash operator/bridges/run-all-tests.sh`.
