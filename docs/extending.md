# CorvinOS — Extensibility Hub

CorvinOS is designed as a platform, not a product. You extend it by dropping files
into a configuration tree — no forking, no patching core code, no restart in most
cases. The system reads your additions on the next message, grades them against real
usage, and promotes the ones that work.

This document is the entry point for five extension surfaces:

| Surface | What it is | Where it lives | Hot-reload |
|---|---|---|---|
| **Personas** | AI identity, system prompt, tool set, engine choice | `operator/cowork/personas/<name>.json` | Yes — next message |
| **Forge Tools** | Sandboxed, bwrap-isolated, MCP-callable Python tools | Chat request or JSON in forge workspace | Yes — MCP hot-register |
| **Skills** | Markdown instruction files injected into future turns | Chat request or `mcp__skill_forge__skill_create` | Yes — injected per turn |
| **Bridge Adapters** | New messaging channels (Teams, Matrix, Signal, custom) | `operator/bridges/<channel>/` | Restart needed |
| **Workflow Packages** | Installable bundles of personas + tools + skills | `corvin-pkg install <package.corvin-pkg>` | N/A — one-time install |

Every extension event — a new persona loaded, a forge tool created, a skill promoted,
a package installed — is written to the hash-chained audit log. Nothing happens silently.

---

## The hot-reload boundary

Most of CorvinOS hot-reloads without a restart. The rule is simple:

| Change | Reload mechanism | Restart needed? |
|---|---|---|
| Persona JSON — any field | Re-read on next message dispatch | No |
| `chat_profiles` in settings.json | Re-read on next message dispatch | No |
| `whitelist`, `rate_limit_per_hour` | Re-read on every message via `currentSettings()` | No |
| Forge tool created or promoted | MCP server hot-registers the new tool | No |
| Skill created, graded, or promoted | Injected into the next turn automatically | No |
| Workflow package installed | Takes effect immediately (personas/tools/skills land in place) | No |
| New bridge adapter daemon code | Daemon must be started | Yes — `bridge.sh restart` |
| Bot token or HTTP port change | Structural config, read at startup | Yes — `bridge.sh restart` |
| Core adapter code (`adapter.py`) | Python process reloads | Yes — `bridge.sh restart` |

---

## 1. Personas

A persona is a JSON file that defines how the AI behaves in a specific chat context:
its identity, its system prompt additions, its tool access, and its engine choice.
Drop a file in the right place and it is live on the next message — no restart, no
service interruption.

### Where persona files go

**User override** (your deployment only, gitignored, survives `git pull`):

```
~/.corvin/cowork/personas/<name>.json
```

**Bundle personas** (committed to the repo, visible to all deployments):

```
operator/cowork/personas/<name>.json
```

User override personas take priority over bundle personas with the same name.

### Minimal example

```json
{
  "name": "customer-support",
  "description": "First-line support agent for Acme Corp.",
  "append_system": "You are a customer support agent for Acme Corp. Always be polite and patient. If the user asks about billing, refunds, or account deletion, say 'I will connect you with our billing team' and stop. Never invent policy details you are not certain of.",
  "permission_mode": "bypassPermissions",
  "forge_enabled": false,
  "skill_forge_enabled": false,
  "memory_recall_enabled": true,
  "ldd_preset": "off"
}
```

### Full field reference

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | (required) | Unique identifier. Used in `/pin <name>` and in routing config. |
| `description` | string | `""` | One-line description. Shown in the console and `/personas` output. |
| `append_system` | string | `""` | Text appended to the system prompt after the base persona block and before the user context block. |
| `permission_mode` | enum | `"bypassPermissions"` | `bypassPermissions` / `acceptEdits` / `plan` / `default`. All bundle personas use `bypassPermissions` — the path-gate hook is the structural security boundary, not this field. |
| `mcp_servers` | object | `{}` | Additional MCP servers to start for this persona (same schema as `--mcp-config`). Shallow-merged with global MCP config. |
| `forge_enabled` | boolean | `true` | Whether forge tools are available in this persona's sessions. |
| `skill_forge_enabled` | boolean | `false` | Whether SkillForge (runtime skill creation) is enabled. Must be explicitly opted in. |
| `memory_recall_enabled` | boolean | `false` | Whether recall indexing and user model injection are active. |
| `working_dir` | string | repo root | The `--add-dir` working directory passed to the underlying engine. |
| `ldd_preset` | enum | `"off"` | LDD preset: `"off"` / `"light"` / `"full"`. |
| `add_dirs` | array | `[]` | Additional `--add-dir` paths passed to the engine for this persona. |
| `delegate_enabled` | boolean | `false` | Enables the orchestrator delegation tools (`delegate_claude_code`, `delegate_codex`, etc.). |
| `delegate_inject_skills` | boolean | `false` | Whether active skills are visible to delegated workers. |
| `delegate_forge_enabled` | boolean | `false` | Whether forge/skill-forge MCP tools are passed to delegated workers. |

### Adding a custom MCP server

The `mcp_servers` field lets you attach any MCP-compatible server to a persona
without touching the global config:

```json
{
  "name": "research",
  "mcp_servers": {
    "acme-kb": {
      "command": "{{PYTHON}}",
      "args": ["/opt/acme/kb_server.py"]
    },
    "acme-cloud": {
      "command": "npx",
      "args": ["-y", "@acme/mcp-server-example"],
      "env": {
        "ACME_API_KEY": "${ACME_API_KEY}"
      }
    }
  }
}
```

Template variables are expanded in `command`/`args`/`env` values before the
config is materialized:

- `{{PYTHON}}` — `sys.executable`, the interpreter that can import CorvinOS's
  own packages. Use this instead of a bare `python3`, which does not exist on
  Windows PATH and resolves via the *spawning* process's PATH on POSIX
  (usually a system interpreter without your dependencies).
- `{{REPO_ROOT}}` — the directory holding `operator/` (the repo checkout, or
  the vendored tree inside a wheel install).
- `{{CORE_ROOT}}` — the directory holding the `core/` package tree (equals
  `{{REPO_ROOT}}` in a checkout; `site-packages` in a wheel install).
- `{{HOME}}` — the running user's home directory.

Note: image generation no longer needs a persona-level `mcp_servers` entry —
the built-in `imagegen-zero-config` catalog tool (ADR-0191) is seeded and
active on every installation.

### Pinning a persona to a chat

From within the chat:

```
/pin customer-support
```

To remove the pin and return to auto-routing:

```
/unpin
```

### Assigning a persona to a chat profile

For permanent assignment without requiring users to type `/pin`, add a `persona`
field to the chat profile in `operator/bridges/<channel>/settings.json`:

```json
{
  "chat_profiles": {
    "123456789": {
      "persona": "customer-support"
    }
  }
}
```

This takes effect on the next message to that chat (hot-reload applies).

### Audit trail

When a persona is loaded for the first time in a session, a `persona.loaded` event
is written to the audit chain with the persona name, channel, and chat key. No
system prompt content is written — metadata only.

---

## 2. Forge Tools

Forge tools are schema-bound, sandboxed tools that the AI can create at runtime and
call as MCP tools. They persist across sessions (at `project` or `user` scope) and
can do anything a subprocess can do — file I/O, HTTP calls, running scripts — within
the sandbox policy. No code changes to CorvinOS required.

### Creating a tool in-chat

The easiest path is to ask:

```
Create a forge tool called "fetch_weather" that takes a city name and returns
the current weather by calling the Open-Meteo API (no API key needed). Store at
project scope.
```

The AI calls the forge MCP tool with the schema and implementation. The tool is
immediately available as `mcp__forge__fetch_weather` in the same session.

### Creating a tool manually

Tools are JSON files. Minimal structure:

```json
{
  "name": "fetch_weather",
  "description": "Fetch current weather for a city using the Open-Meteo API.",
  "input_schema": {
    "type": "object",
    "properties": {
      "city": {
        "type": "string",
        "description": "City name, e.g. Berlin"
      }
    },
    "required": ["city"]
  },
  "implementation": {
    "type": "python",
    "code": "import urllib.request, json\nurl = f'https://geocoding-api.open-meteo.com/v1/search?name={inputs[\"city\"]}&count=1'\nwith urllib.request.urlopen(url) as r:\n    geo = json.load(r)['results'][0]\nwurl = f'https://api.open-meteo.com/v1/forecast?latitude={geo[\"latitude\"]}&longitude={geo[\"longitude\"]}&current=temperature_2m,wind_speed_10m'\nwith urllib.request.urlopen(wurl) as r:\n    data = json.load(r)\nprint(json.dumps(data['current']))"
  },
  "meta": {
    "scope": "project",
    "secrets": [],
    "network": "allow"
  }
}
```

Place the file in the forge workspace, then register it:

```
mcp__forge__artifact_register path=<tool-file.json>
```

For the full schema reference, see [forge.md](forge.md).

### Scopes

| Scope | Lifetime | Shared across |
|---|---|---|
| `task` | Current turn only | This turn, this session |
| `session` | Until `/new` or `/clear` | All turns in the current chat session |
| `project` | Permanent | All sessions in the current project |
| `user` | Permanent | All projects for this user |

To promote a tool to a wider scope:

```
mcp__forge__forge_promote name=fetch_weather target_scope=user
```

### Sandbox policy

By default, forge tools run in a `bwrap` sandbox:

- No network access (unless `meta.network: "allow"` is set and the persona permits it)
- Fresh `/tmp` per invocation
- Read-only `/usr` and `/etc`
- No access to `~/.corvin/`, audit chains, or policy files — enforced by the path-gate hook

The persona determines whether `network: allow` is respected. The `browser` and
`research` personas grant network access; all others deny it by default.

### Secret injection

If a tool needs an API key or password:

1. Store the secret in the vault: `/vault set MY_API_KEY sk-...`
2. Declare the secret name in the tool's `meta.secrets`: `["MY_API_KEY"]`
3. At execution time, the runner injects it as an environment variable inside the sandbox.

The secret value never appears in the tool definition, the AI context, or any log.

### Audit trail

Every forge tool creation, promotion, and invocation writes an event to the audit
chain: tool name, scope, persona, channel, and chat key. No implementation code or
output content is written.

---

## 3. Skills (SkillForge)

Skills are Markdown documents injected into future conversation turns as additional
system prompt context. Unlike forge tools (which *do* things), skills *shape how the
AI thinks and responds* — they encode workflows, checklists, domain-specific reasoning
patterns, or project conventions.

Skills are self-improving: the system grades each skill after every turn based on
whether the AI applied it, and automatically promotes skills that earn consistent
positive grades to wider scopes.

### Enabling SkillForge on a persona

SkillForge must be explicitly opted in:

```json
{
  "skill_forge_enabled": true
}
```

The `assistant` and most bundle personas do not enable this by default.

### Creating a skill in-chat

```
Create a skill called "code-review-checklist" that always checks for:
1. Missing error handling
2. Hardcoded secrets
3. Missing tests
Save at session scope.
```

The AI calls `mcp__skill_forge__skill_create`. The skill is immediately injected into
subsequent turns in the same session.

### Creating a skill via MCP directly

```
mcp__skill_forge__skill_create(
  name="code-review-checklist",
  body="When reviewing code, always check:\n1. Missing error handling...",
  scope="session"
)
```

### Linter

All skills pass through a linter before being saved. The linter rejects:

- Prompt-injection patterns (instructions to ignore previous instructions, roleplay
  as a different AI, reveal system prompts)
- Embedded secrets (API key shapes, private key PEM blocks)
- Persona-boundary phrases (instructions that would override the persona definition)
- Oversized bodies (default limit: 8 KB)

A rejected skill returns the linter's rejection reason. Fix the content and retry.

### Grading and promotion

Skills are promoted through a scope ladder based on usage grades:

| Promotion | Requirement |
|---|---|
| task → session | ≥ 1 positive grade |
| session → project | ≥ 3 grades with mean ≥ 0.5 |
| project → user | Explicit `force=True` in `mcp__skill_forge__skill_promote` |

Grades are assigned automatically after each turn:

- **Mention/paraphrase grade (0.7)**: the adapter checks whether the active skill
  was mentioned or applied in the AI response.
- **User approval (0.9)**: the user explicitly approves the behavior guided by the skill.
- **User rejection (0.1)**: the user explicitly rejects the behavior.
- **Rephrase (0.3)**: the user rephrases or corrects the behavior.

You can manually grade a skill:

```
mcp__skill_forge__skill_grade name=code-review-checklist score=0.9
```

### Managing skills

```
mcp__skill_forge__skill_list              # list all skills at all scopes
mcp__skill_forge__skill_get name=...      # read the full skill body
mcp__skill_forge__skill_diff name=...     # diff between scope levels
mcp__skill_forge__skill_purge name=...    # delete a skill
```

### Audit trail

Skill creation, promotion, grading, and purge all emit audit events with skill name,
scope, grade score, and channel/chat context. No skill body content is written to
the audit chain.

---

## 4. Bridge Adapters

A bridge is a pair: a **Node.js daemon** that speaks the channel's protocol, and a
**settings.json** that the shared adapter reads to configure routing. Adding a new
channel means writing one daemon file and one settings file — no changes to the
shared adapter or any other part of CorvinOS.

### Minimum requirements

A bridge daemon must:

1. Accept messages from the channel and write them to the shared inbox.
2. Accept reply envelopes from the inbox and send them back to the channel.
3. Expose a `GET /status` endpoint on its assigned port that returns `{"status": "ok"}`.
4. Have a `settings.json` with at least `whitelist` (array), a token field, and
   optionally `chat_profiles` and `rate_limit_per_hour`.
5. Have an `install.sh` that runs `npm install`.
6. Have an entry in `operator/bridges/bridge.sh` so `bridge.sh up/down/status/tail`
   work uniformly.

### Reference implementation

The simplest complete bridge is the Telegram daemon:

```
operator/bridges/telegram/daemon.js
```

It is approximately 400 lines and covers: bot initialization, whitelist checking,
rate limiting, the inbox protocol, media handling, command passthrough, and graceful
shutdown. Use it as the template for a new channel.

### Inbox protocol

The shared adapter communicates with daemons via a JSON newline-delimited inbox.
Incoming message envelope (daemon → adapter):

```json
{
  "channel": "mybridge",
  "chat_key": "mybridge:12345",
  "user_id": "12345",
  "text": "Hello",
  "attachments": [],
  "ts": 1716288000.0
}
```

Outgoing reply envelope (adapter → daemon):

```json
{
  "chat_key": "mybridge:12345",
  "text": "Hello back",
  "audio_path": "/tmp/corvin/tts_12345.ogg",
  "is_voice": true
}
```

### Settings hot-reload in your daemon

The daemon must read `settings.json` on every message — not once at boot — by
calling `currentSettings()` from `operator/bridges/shared/js/settings.js`. This
gives you hot-reload for whitelist changes, pin changes, rate limit tuning, and
chat profile updates while the daemon is running.

```js
const { currentSettings } = require('../shared/js/settings');

// inside message handler:
const settings = currentSettings();
if (!settings.whitelist.includes(userId)) return;
```

### Registering in bridge.sh

Add a block to `operator/bridges/bridge.sh` following the existing pattern for
Telegram or Discord. The script handles start, stop, status, and log tail uniformly
if you follow the naming conventions (`<channel>/daemon.js`, port on
`BRIDGE_PORT_<CHANNEL>`).

### What needs a restart vs. what does not

| Change | Restart needed |
|---|---|
| `whitelist`, `rate_limit_per_hour`, `chat_profiles` in `settings.json` | No — hot-reloaded |
| Daemon JavaScript code | Yes — `bridge.sh restart <channel>` |
| Bot token or webhook URL | Yes — `bridge.sh restart <channel>` |
| Port number | Yes — `bridge.sh restart <channel>` |

---

## 5. Workflow Packages

Workflow packages (`.corvin-pkg` files) bundle personas, forge tools, skills, and
configuration into a single signed archive. They are the distribution unit for
CorvinOS extensions — share a package and the recipient gets a complete, coherent
capability without manual file placement.

### Installing a package

```bash
corvin-pkg install <name>.corvin-pkg
```

The installer:

1. Verifies the package signature against the publisher's trusted public key.
2. Extracts personas to `~/.corvin/cowork/personas/` (user override location).
3. Registers forge tools at the scope declared in the package manifest.
4. Installs skills at the declared scope.
5. Emits a `corvin_pkg.installed` event to the audit chain.

Preview what a package contains before installing:

```bash
corvin-pkg inspect <name>.corvin-pkg
```

### Creating a package

A package is a signed archive built from a `manifest.json`:

```json
{
  "name": "acme-support-workflow",
  "version": "1.0.0",
  "description": "Customer support workflow: persona, ticket tool, escalation skill.",
  "publisher": "Acme Corp",
  "min_corvin_version": "0.9.0",
  "contents": {
    "personas": ["personas/customer-support.json"],
    "forge_tools": ["tools/fetch_ticket.json"],
    "skills": ["skills/escalation-checklist.md"]
  }
}
```

Build and sign the package:

```bash
corvin-pkg build manifest.json --out acme-support-workflow.corvin-pkg
```

### Package security

Packages require a valid signature to install. The public key for each trusted
publisher is pinned in operator config. To add a publisher:

```bash
corvin-pkg trust add <publisher-name> <public-key-file.pem>
```

Unsigned packages are rejected. Packages whose declared `min_corvin_version`
exceeds your installed version are also rejected with an upgrade prompt.

Package contents land in the user override locations — they are never committed to
the repo automatically and will not be overwritten by `git pull`.

---

## Extension quick reference

| What you want | How to do it | Where it lives | Restart? |
|---|---|---|---|
| Custom agent behavior | Add or edit a persona JSON | `~/.corvin/cowork/personas/<name>.json` | No |
| Attach an MCP server | Add `mcp_servers` to a persona | Same persona JSON | No |
| Runtime automation tool | Create a forge tool | Chat request or JSON in forge workspace | No |
| Reusable reasoning pattern | Create a skill | Chat request or `mcp__skill_forge__skill_create` | No |
| New messaging channel | Write a Node.js bridge daemon | `operator/bridges/<channel>/` | Yes (daemon start) |
| Shareable bundle | Build a `.corvin-pkg` | `corvin-pkg build` | No (one-time install) |

---

## Going further

- [forge.md](forge.md) — full forge tool schema, policy fields, and sandbox options
- [awpkg.md](awpkg.md) — workflow package manifest format and signing
- [personas.md](personas.md) — auto-routing, LDD presets, and the full persona merge order
- [bridges.md](bridges.md) — the inbox protocol in detail, media handling, and bridge health checks
