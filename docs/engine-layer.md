# Engine layer

> The LLM-execution boundary. Backend-agnostic by design — Claude Code,
> Codex CLI, OpenCode (driving Ollama local or cloud), or any future
> engine plugged into the same `WorkerEngine` Protocol.

![Engine layer — five backends, one protocol, engine-agnostic OS guarantees](diagrams/21-engine-layer.svg)

## The mental model

The agent's "brain" — the thing that turns a prompt into a response — is
**not** part of Corvin. It's a **dependency**, the same way the Linux
kernel is a dependency of `bash`. Corvin sits one layer up: it owns
the per-chat persona, the runtime-generation substrate, the audit
chain, the memory loadout — but it lets you swap the engine itself
based on what the chat needs.

This means:
- Different chats can run different engines **in the same process**
- A persona that needs Claude's specific capabilities (hooks,
  mid-stream-inject, system-prompt slot) gets Claude
- A persona that wants local-first / privacy-first / cost-first work
  gets OpenCode → Ollama
- A persona that needs Codex's behavior gets Codex
- The engine swap is a one-line change in a JSON file

The cost of this design is one abstraction: `WorkerEngine` Protocol.
Five concrete engines ship today; the abstraction is intentionally
small enough that adding a sixth is ~300 LOC plus capability
declarations.

## The problem this solves

A single-engine framework forces every workflow into the engine's
shape. That's fine until:

- You want to test a feature with a different LLM and discover the
  framework hard-codes Claude-specific assumptions
- A subset of users want local-first inference and you have to
  fork the framework
- A regulator says "must use a model hosted in EU only" and the
  framework's engine assumption blocks the deployment
- A specific persona is so simple it should run on a 7B local model
  while the rest of the system runs on Claude — and you want to
  do this **per chat**, not per deployment

Corvin's answer: engine selection is a **per-call decision**. The
adapter consults `profile.default_engine` for each chat turn and
dispatches accordingly. Two chats sitting in the same Discord guild
can run completely different engines in the same bridge process.

## The `WorkerEngine` Protocol (Layer 22)

```python
class WorkerEngine(Protocol):
    capabilities: dict[str, bool]   # see table below

    def spawn(
        self, prompt: str, *, env: dict
    ) -> Iterator[StreamEvent]: ...

    def cancel(self) -> None: ...
```

That's the whole interface. Every engine implementation:
1. Implements `spawn`, returning an iterator of normalised
   `StreamEvent` objects
2. Implements `cancel` for SIGTERM-on-budget-exceeded
3. Declares its `capabilities` dict so the adapter can gate features

The capability keys are **identical across engines** — a CI test
asserts this so no engine can silently omit a key. Today's keys:

| Key | Meaning |
|---|---|
| `mid_stream_inject` | Does the engine support `/btw` mid-stream user-message injection? |
| `hooks` | Does the engine honor PreToolUse hooks (path-gate, etc.)? |
| `skills_tool` | Does the engine load skill files via plugin discovery? |
| `add_system_prompt` | Does the engine accept `--append-system-prompt`? |
| `mcp` | Does the engine load MCP server config? |
| `stream_json` | Does the engine emit stream-json output? |
| `permission_modes` | Curated list of supported permission modes |
| `command_manifest` | Does the engine declare an `EngineCommandManifest` (ECI)? Exposes `/btw` transport type and engine-native `/e:<cmd>` commands. |
| `teb_hooks` | Does the engine route tool calls through TEB (L10 path-gate + L16 audit + L33 artifacts via Forge MCP server)? True for all engines. |

## The five shipped engines

### `ClaudeCodeEngine` — the default

Spawns `claude -p --output-format stream-json --verbose` with full
feature flags. Ships with every capability enabled. This is what
most chats run on; it has the broadest feature set in production
and is what every persona that uses `forge_enabled` /
`skill_forge_enabled` / mid-stream-inject defaults to.

**File:** `bridges/shared/agents/claude_code.py`

### `CodexCliEngine` — opt-in

Spawns `codex exec --json --skip-git-repo-check --ephemeral`.
Capabilities: `mcp + stream_json + teb_hooks + command_manifest` — no
skills_tool, no *live* mid-stream-inject (no live ECI transport; the adapter
queues `/btw` notes to the next turn instead). L10
path-gate, L16 audit, L33 artifacts delivered via TEB (M1).
Useful when a persona genuinely needs Codex's
behavior (e.g. specific code-completion patterns, ChatGPT account
billing). Adapter opt-in via `engine_factory` injection at adapter
construction time.

**File:** `bridges/shared/agents/codex_cli.py`

### `OpenCodeEngine` — the provider-agnostic option

Spawns `opencode run --format json [--model <provider/model>]`.
OpenCode (anomalyco/opencode) is itself provider-agnostic: a single
CLI talks to Claude / OpenAI / Google / **Ollama (local or cloud)**
via opencode's openai-compatible provider config. Capabilities
match Codex's subset.

The big use-case: **local-first / privacy-first** chats via
Ollama. Opt-in per chat via `chat_profiles[<chat>].default_engine = "opencode"`
or the `/engine opencode` command.

**File:** `bridges/shared/agents/opencode_cli.py`

#### Local Ollama setup

One-time operator step:

1. `ollama serve` running on `http://localhost:11434`
2. At least one model pulled (`ollama pull qwen3:1.7b` or similar)
3. `~/.config/opencode/opencode.json` declares the provider:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": { "qwen3:8b": {}, "qwen3:1.7b": {} }
    }
  }
}
```

4. Pick the model per spawn: `engine.spawn(prompt,
   model="ollama/qwen3:1.7b")` (or via the persona's `"model"` field)

#### Cloud Ollama (no daemon needed)

Hosted Ollama exposes an OpenAI-compatible endpoint at
`https://ollama.com/v1` with bearer auth. Add a second provider
entry; opencode's `{env:VAR}` substitution keeps the key out of the
config file:

```jsonc
"ollama-cloud": {
  "npm": "@ai-sdk/openai-compatible",
  "options": {
    "baseURL": "https://ollama.com/v1",
    "apiKey": "{env:OLLAMA_API_KEY}"
  },
  "models": { "qwen3-coder:480b": {}, "minimax-m2.7": {} }
}
```

The `OLLAMA_API_KEY` is sourced from
`~/.config/corvin-voice/service.env` (same place every other
operator-managed secret lives — no inline secrets in the opencode
config).

Wall-clock benchmarks (rough, varies by hardware): cloud
`minimax-m2.7` ≈ 5 s warm / 20 s cold. Local CPU-bound `qwen3:1.7b`
≈ 80 s. The cloud option is the realistic local-first-with-good-
latency path for low-end hardware.

### `HermesEngine` — the zero-egress local engine

Drives the Ollama HTTP streaming API (`POST localhost:11434/api/chat`) directly
with Python stdlib `urllib` — no subprocess, no new runtime dependency.
Capabilities: `stream_json=True`; `mcp=FCB-bridged` (tool-use loop via
`teb/fcb.py`); `hooks=TEB` (L10/L16/L33 via Forge MCP server);
`mid_stream_inject=buffered` (ECI). No skills_tool, no session-pinning.

The key property: `locality=local, network_egress=none` in the L34
data-classification matrix. It is the **only engine that qualifies for
CONFIDENTIAL task classes** under the EU_PRODUCTION preset without a
compliance-zone exception. Every other engine makes an outbound API call.

**Model aliases** (override per spawn via the `model` field):

| Alias | Ollama tag | Use-case |
|---|---|---|
| `hermes-fast` | `nous-hermes-2:7b-mistral-q4_K_M` | Quick summaries, classification |
| `hermes-balanced` | `nous-hermes-2:13b-q4_K_M` | General delegation (default) |
| `hermes-capable` | `nous-hermes-3:8b-llama3.1-q5_K_M` | Structured output (M2 tool-calling) |
| `hermes-large` | `hermes-3:70b-llama3.1-q4_K_M` | Complex reasoning (GPU needed) |

Any Ollama-compatible model works — the alias table is a convenience layer on top.

**Setup** (one-time): `ollama serve` on `http://localhost:11434` and at least one
model pulled (`ollama pull qwen3:1.7b` or similar). Override the URL via
`CORVIN_OLLAMA_BASE_URL` or `OLLAMA_HOST` for Docker-sidecar scenarios.

**Persona stub** — `operator/cowork/personas/` does not ship a dedicated
hermes persona; route via `default_engine: "hermes"` in `chat_profiles` or
the `/engine hermes` in-chat command. (A `hermes-worker.json` persona was
removed in v1.2; use the engine-pin mechanism instead.)

**Boot self-test:** `_check_hermes_ollama()` in `self_test.py` probes
`GET /api/tags` with a 2 s timeout. Result is WARNING (never CRITICAL) —
Ollama is optional; the adapter starts normally without it.

**Adapter dispatch:** `call_claude_streaming()` in `adapter.py` routes
`profile.default_engine == "hermes"` to `_call_hermes_streaming_via_engine()`,
the same pattern as the `"opencode"` branch. A daemon thread drives
`HermesEngine.spawn()`, events arrive via a `queue.Queue`, and the same
`ADAPTER_STREAM_IDLE_TIMEOUT` idle watchdog applies. No subprocess — no
`_register_subproc` needed. `/cancel` still calls `engine.cancel()` which
closes the HTTP response.

**Engine dispatch resolution order (M2.4):**
```
1. per-chat profile.default_engine (persona JSON or /engine command)
2. tenant.corvin.yaml::spec.default_engine  (console setting)
3. ClaudeCodeEngine  (hardcoded fallback)
```

**Switching in-chat:** `/engine hermes` (or model aliases `hermes-fast`,
`hermes-balanced`, `hermes-capable`, `hermes-large`) pins the current chat's
orchestrator delegation preference to `delegate_hermes`. Capability-degradation
note injected by the orchestrator's `append_system`.

**Console selector (M2.4):**
`GET/PUT /v1/console/settings/engine` + `GET /v1/console/settings/engine/health`.
Writes `spec.default_engine` (and optionally `spec.hermes_model`) to the tenant
YAML. Takes effect on the next turn — no adapter restart.

**Compliance gates (M2.1):**
`_run_pre_dispatch_gates()` runs L30.1b engine-trust, L34 data-classification,
and L35 egress gates before `HermesEngine.spawn()`. Trust manifest at
`agents/trust/hermes.yaml` (tier=low). L34 matrix entry: `locality=local,
network_egress=none` in `data_classification.py::DEFAULT_ENGINE_COMPLIANCE`.

**Audit events (M2.2):**
`hermes.turn_start`, `hermes.turn_end`, `hermes.turn_error`,
`hermes.stream_timeout`, `hermes.ollama_unavailable` — all registered in
`security_events.py` and emitted from the streaming function.

**Metrics (M2.5):**
`engine_metrics.py` — lazy Prometheus `corvin_bridge_hermes_turns_total`
Counter and `corvin_bridge_hermes_turn_duration_seconds` Histogram.

**File:** `bridges/shared/agents/hermes_engine.py`

### `CopilotCliEngine` — GitHub Copilot CLI worker (worker-only)

Wraps `copilot -p "<prompt>"` (github/copilot-cli v1.0.56+, standalone binary).
**Worker-only:** cannot serve as the OS engine — it lacks `/btw` live injection,
hooks, and skills_tool. Use via delegation: `/engine copilot` or
`mcp__corvin_delegate__delegate_copilot`.

**Task-type steering** via the `model` field: `shell`, `git`, `gh` inject a
context prefix; omit for general chat. Zero incremental cost for Copilot
Business/Enterprise subscribers.

**Auth:** `~/.copilot/config.json` or `GH_TOKEN` env var.

**L34 matrix:** `locality=us_cloud` — eligible for PUBLIC/INTERNAL/CONFIDENTIAL
under the permissive default (residency restriction is opt-in), but excluded
once an operator tightens the matrix or applies the EU_PRODUCTION preset.

**Self-test:** `_check_copilot_cli()` at INFO severity (binary is optional).

**Delegation persona:** `operator/cowork/personas/copilot-worker.json` — sets
`default_engine: "copilot"` with a shell/git task-type preset.

**File:** `bridges/shared/agents/copilot_cli.py`

---

## How a chat picks an engine

Three layers, evaluated in order:

```
1. profile.default_engine (from persona JSON or chat_profile)
2. CORVIN_USE_ENGINE_LAYER env (operator emergency rollback)
3. ClaudeCodeEngine (default)
```

The persona JSON is where most engine pinning happens:

```jsonc
// Example: an inline chat_profile that pins to OpenCode + Ollama
// (No dedicated bundle persona ships — wire inline or via /engine opencode)
{
  "default_engine": "opencode",
  "model": "ollama-cloud/qwen3-coder",
  "inject_skills":      false,   // OpenCode has no system slot
  "forge_enabled":      false,   // forge MCP needs Claude's mcp wiring
  "skill_forge_enabled": false,
  "append_system": "You are a local-first coding helper. …"
}
```

Activation per chat:
- bridge-side: set `chat_profiles.<chat>.default_engine = "opencode"` in `bridges/<channel>/settings.json`
- in-chat: send `/engine opencode`

## Capability-gated features (graceful degradation, not crashes)

The adapter consults `engine.capabilities[k]` per feature site. The
load-bearing pattern:

```python
if engine.capabilities.get("mid_stream_inject", False):
    engine.inject(text)          # live delivery (ClaudeCode stdin_json)
else:
    return False                 # inject_btw: no LIVE delivery on this engine
```

The `/btw` handler in `process_one` treats a `False` from `inject_btw` as
"no live delivery", **not** "no task running". It then checks the
engine-agnostic active-turn marker (`_turn_active(chat_key)`, set for every
engine in `call_claude_streaming`):

- **live delivered** → `📝 Note delivered to the running task.`
- **turn active but no live inject** (Hermes / OpenCode / Codex) → the note is
  **queued** into the `/btw` buffer and drained into the *next* spawn by
  `drain_btw_buffer()`; ack: `📝 Got your note … queued … added on the next turn.`
- **no turn active** → `No task is running right now …` (the true idle case).

This closes a long-standing false-negative: on Discord the stripped-PATH →
Hermes auto-downgrade (ADR-0159 M1) meant every `/btw` reported the misleading
"No task is running" and **dropped** the note, even mid-turn. The buffer drain
now runs in the Hermes/OpenCode/Codex spawn paths too — previously it lived only
on the Claude path, so the "buffered" mode this table advertised never actually
delivered.

Effects of running a non-Claude engine in an existing chat:

| Feature | Claude | Codex | OpenCode | Hermes |
|---|---|---|---|---|
| `/btw` mid-stream injection | ✓ live (stdin_json) | ○ queued → next turn | ○ queued → next turn | ○ queued → next turn |
| PreToolUse path-gate | ✓ native hooks | ✓ via TEB | ✓ via TEB | ✓ via TEB |
| L16 audit + L33 artifacts | ✓ native | ✓ via TEB | ✓ via TEB | ✓ via TEB |
| skill_inject (system prompt) | ✓ via `--append-system-prompt` | `<SYSTEM>` block (SkillCompiler) | `<SYSTEM>` block (SkillCompiler) | `<SYSTEM>` block (SkillCompiler) |
| Forge / Skill-Forge MCP | ✓ via `--mcp-config` | ✓ via `--mcp-config` | config-based (`opencode mcp`) | ✓ via FCB (tool-use loop) |
| TodoWrite / ExitPlanMode status events | ✓ | partial | ✗ | ✗ |
| permission modes | default · acceptEdits · bypassPermissions · plan | (limited) | default · bypassPermissions only | read_only (HTTP API) |
| Zero network egress | ✗ | ✗ | ✗ | ✓ (localhost only) |
| L34 CONFIDENTIAL-capable | ✗ | ✗ | ✗ | ✓ |

The **load-bearing rule**: missing a capability → degrade gracefully,
never crash. The regression tests in
`bridges/shared/test_adapter_engine_switch.py` enforce this for the
`mid_stream_inject` and `add_system_prompt` axes.

## Stream-event normalisation

Every engine emits the same five `StreamEvent` types regardless of the
underlying CLI's wire format:

| Event | Claude Code | Codex CLI | OpenCode | Hermes |
|---|---|---|---|---|
| `session_started` | `system.init` | `thread.started` | first `step_start` | HTTP connect OK → `raw["model"]` |
| `text_delta` | `assistant.message.content[].text` | `item.completed` (agent_message) | `text` with `part.text` | NDJSON chunk `message.content` |
| `tool_call` | `assistant.message.content[].tool_use` | _(not yet)_ | `tool_use` (terminal only) | _(M1: not emitted)_ |
| `turn_completed` | `result` (subtype=success) | `turn.completed` | synthesised on stdout EOF | NDJSON `done=true` + usage |
| `error` | `result` (is_error=true) | `turn.failed` or stderr | `error` (drills nested envelope) | URLError / HTTPError / Ollama `{"error":...}` |

Adapter consumers (`/btw`, status callbacks, retry-on-corrupted-session,
budget accounting) work against this normalised vocabulary, so
adding a fifth engine is "implement spawn() that yields these five
event types" — no consumer-side changes.

## How the engine layer integrates with the rest of Corvin

| Adjacent layer | What it gets / gives |
|---|---|
| **Personas** | `default_engine` + `model` fields on the persona JSON; resolver passes them through to the adapter unchanged |
| **Adapter** | `_resolve_spawn_inputs` returns engine-agnostic kwargs; `call_claude_streaming` dispatches to `_call_claude_streaming_via_engine` (Claude/Codex), `_call_opencode_streaming_via_engine` (OpenCode), or `_call_hermes_streaming_via_engine` (Hermes). Resolution order: per-chat `profile.default_engine` → `tenant.corvin.yaml::spec.default_engine` → Claude fallback. |
| **Skill-Forge** | Skills land via `--append-system-prompt` (Claude) or `<SYSTEM>` prefix (OpenCode). The skill workspace is engine-agnostic |
| **Forge MCP** | Tools advertise via the engine's MCP wiring; the actual tool execution (bwrap + audit) is engine-independent |
| **Audit chain** | Every spawn emits the same envelope; engine identity lands in `details.engine` for forensic correlation |
| **Voice (L23)** | STT runs *before* engine spawn; engines never see audio |

## Why Codex / OpenCode / Hermes aren't the default

The default stays Claude Code because:
- Most personas in production rely on at least one capability that
  only Claude has (mid-stream inject, hooks, skill_inject system slot)
- Codex is a billing / behavior alternative, not a feature superset
- OpenCode's local-first story is opt-in by user intent — making
  it default would silently break every chat that depends on capability X
- Hermes has no live mid-stream inject, no session pinning — it's
  the zero-egress / CONFIDENTIAL-class specialist (L10/L16/L33 via TEB,
  MCP via FCB bridge, /btw buffered via ECI)

The opt-in-per-persona pattern lets each chat choose the right
trade-off. Use `default_engine: "opencode"` in a persona for OpenCode;
`default_engine: "hermes"` for zero-egress local inference via Hermes.

## EAOS — Engine-Agnostic OS Shell

![EAOS architecture — five engines, ECI, TEB, FCB, SkillCompiler](diagrams/26-eaos-architecture.svg)

Non-ClaudeCode engines (Codex, OpenCode, Hermes) previously ran without
L10 path-gate, L16 audit coverage, or L33 artifact registration. EAOS
closes this gap with four subsystems wired into the Forge MCP server:

### Tool Execution Broker (TEB)

Every tool call from any engine flows through `teb/broker.py` inside the Forge
MCP server. TEB enforces L10 path-gate (fail-closed, same `path_gate.py` rules),
L16 audit chain, and L33 artifact auto-registration for all five engines. The
compliance guarantees that were previously ClaudeCode-only are now universal.

### Engine Command Interface (ECI)

Each engine declares an `EngineCommandManifest` (`eci/manifest.py`) with two
fields:
- **`btw_transport`**: how `/btw` is delivered — `stdin_json` (ClaudeCode, live),
  `buffered` (Hermes, injected at next prompt boundary), or `None` (Codex /
  OpenCode — no live ECI transport). For any non-`stdin_json` engine the adapter
  never drops the note: while a turn is active it queues the text into the `/btw`
  buffer and `drain_btw_buffer()` prepends it to the next spawn's system prompt
  (wired into the Hermes/OpenCode/Codex spawn paths, not just ClaudeCode).
- **`native_commands`**: engine-specific commands exposed under the `/e:` namespace
  (e.g. `/e:model-info` for Hermes). Routed by the adapter dispatcher to
  `engine.handle_command(cmd, args)`.

### Function-Call Bridge (FCB)

`teb/fcb.py` translates between MCP tool-call format and OpenAI function-calling
format. `HermesEngine` now runs a full tool-use loop: Hermes emits
`tool_calls` in OpenAI format → FCB translates to MCP → TEB executes with
L10/L16/L33 enforcement → FCB translates results back. The `mcp` capability
is now `True` for Hermes.

### SkillCompiler

`eci/skill_compiler.py` compiles active `SKILL.md` files into the correct
injection format per engine: `--append-system-prompt` flag for ClaudeCode,
structured `<SYSTEM>` block for Hermes / OpenCode / Codex. All engines now
receive skill context regardless of whether they have a native system-prompt slot.

### Console

New page at `/app/engine-control` shows the live capability matrix (one row
per engine, one column per capability key) and the registered `/e:<cmd>`
commands per engine. Read-only API: `GET /v1/console/settings/engine/capabilities`.

---

## What you, as operator, must NOT do

- **Don't promote Codex / OpenCode / Hermes to default.** Default stays
  Claude Code. The capability-degradation rule applies *per chat*; making
  a partial-capability engine the default silently breaks every
  chat that used a depending feature.
- **Don't add `default_engine: "opencode"` or `"hermes"` to existing
  feature-rich personas.** Personas like `coder`, `forge`, `research`,
  `orchestrator` depend on Claude-specific features (skills_tool, hooks,
  forge MCP). Engine-pinned chats must explicitly disable every feature
  that the target engine cannot honor (forge_enabled, skill_forge_enabled, etc.).
- **Don't widen `permission_modes` beyond what each engine actually
  honors.** Pretending OpenCode supports `acceptEdits` (just because
  the value parses) lets bridge callers request a mode the engine
  silently ignores.
- **Don't ship the engine binaries inside the repo.** The `claude` /
  `codex` / `opencode` CLIs are operator-installed. The engine
  resolver respects env-var overrides (`CLAUDE_CLI`, `OPENCODE_BIN`),
  then `~/.opencode/bin/opencode`, then PATH.
- **Don't write `OLLAMA_API_KEY` directly into `opencode.json`.** Use
  the `{env:OLLAMA_API_KEY}` substitution. The key lives in
  `~/.config/corvin-voice/service.env` (same place as
  `OPENAI_API_KEY` for STT). Inline secrets in opencode config
  defeats the central key-management discipline.
- **Don't add `import anthropic` to engines.** They authenticate via
  the operator's CLI subscription (Max-Abo for Claude, ChatGPT for
  Codex, Ollama Cloud for OpenCode). The CI lint rejects the import.
- **Don't bypass the WorkerEngine Protocol.** Direct engine API
  access from non-adapter code couples the consumer to one
  engine's wire format and breaks the swap promise.

## Concrete commands + files

| Action | Where |
|---|---|
| List active engine in chat | `/whoami` shows `engine=…` |
| Pin engine for a chat | `chat_profiles.<chat>.persona = "<persona-with-default_engine>"` |
| Emergency rollback to legacy spawn | `CORVIN_USE_ENGINE_LAYER=0 bash bridge.sh restart` |
| Add a new engine | implement `WorkerEngine` Protocol + capability dict + register in `engine_factory` |
| Test engines | `bridges/shared/agents/test_engines_e2e.py` (36 cases incl. live opt-in) |
| Test OpenCode | `bridges/shared/agents/test_opencode_cli.py` (30 cases incl. opt-in cloud test) |

## Where to look in the code

- `operator/bridges/shared/agents/__init__.py` — `WorkerEngine`
  Protocol + `StreamEvent` dataclass + tolerant JSONL parser
- `operator/bridges/shared/agents/claude_code.py` — Claude
  implementation (full feature set)
- `operator/bridges/shared/agents/codex_cli.py` — Codex
  implementation (mcp + stream_json subset)
- `operator/bridges/shared/agents/opencode_cli.py` — OpenCode
  implementation (provider-agnostic)
- `operator/bridges/shared/adapter.py::call_claude_streaming` —
  the dispatch site
- `operator/cowork/personas/copilot-worker.json` — delegation persona for CopilotCliEngine
- `bridges/shared/agents/copilot_cli.py` — CopilotCliEngine implementation

## Adjacent docs

- [Architecture](architecture.md) — engine as one of the five
  orthogonal axes
- [Runtime generation](runtime-generation.md) — how forge / skill-
  forge degrade across engines
- [Audit and compliance](audit-and-compliance.md) — engine identity
  in the chain
- Architecture: AWP as orchestration standard
- Phase 2 adapter-engine migration
- AWP standards-only (engine layer owns execution)
