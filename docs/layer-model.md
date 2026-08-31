# layer-model.md — the layered runtime
>
> Originally five layers stacked on vanilla Claude Code; the runtime
> grew. Layers 6-10 below cover the additions that landed alongside
> forge: the runtime tool factory (6), runtime skill creation (7), the
> session-bound lifecycle that resets all of those when a chat closes
> (8), the per-persona capability gate (9) and the structural path-write
> gate that makes "every persona may generate" safe (10). Each addition
> is a thin layer with one job, wired in via the same plugin / hook /
> MCP-server seams as the original five.

## Mental model

Corvin is **five layers stacked on top of vanilla Claude Code**. Each layer has a single responsibility, a clear contract with the layer below, and an explicit list of what it does *not* own. Layers are independently disable-able: removing the plugin or disabling the daemon for a layer leaves the layers below working unchanged.

<p align="center">
  <img src="diagrams/01-layer-stack.svg" alt="The five layers of Corvin, stacked on top of vanilla Claude Code." width="900" />
</p>

The numbering goes "outermost = highest", but every layer interacts with layer 0 (Claude Code) directly through its own seam. They do not chain through each other; each is wired in via the official Claude Code plugin / hook / MCP-server mechanism.

---

## Layer 0 — Claude Code

The vanilla agent runtime. Unchanged by this repo.

**Owns:** the conversation, the tool dispatcher, the LLM client, sub-agents, the slash-command parser, the standard tool set.

**Surfaces Corvin plugs into:**
- Stop hooks (read-aloud trigger)
- Notification hooks (relay to phone)
- MCP servers (for the forge runtime)

Corvin never patches Claude Code. If a layer needs a new affordance, it goes through the official surfaces above. This is the single rule that keeps an upgrade of Claude Code from breaking the OS.

---

## Layer 1 — voice

**Plugin:** `operator/voice/`
**Owns:** speech I/O at the desk and inside any bridged chat that supports voice notes.

### Responsibilities

- **Read-aloud at the desk.** A Stop hook intercepts the assistant's reply, optionally summarises it, sends the text to a TTS engine (OpenAI, ElevenLabs, or local `espeak-ng`), and plays the audio.
- **Voice-note transcription.** Inbound voice notes from any bridge are transcribed via Whisper before being handed to the agent.
- **Summarisation policy.** A length-aware policy (`voice_mode = auto | full | summary`) decides whether a long answer is read verbatim or condensed first. Per-utterance phrases ("lies das vollständig vor" / "fass zusammen") override the global policy for one turn.

### Contract with the layer below

- Reads the assistant's last message via the Stop-hook payload — no scraping of the terminal.
- Writes the audio to the local sound device (or the bridge's outbox, if the message arrived from a bridge).
- Never answers itself; never modifies the conversation; never decides which tool to call.

### What voice does not own

- It does not pick the persona (cowork does).
- It does not decide whether a chat receives a voice reply at all (the bridge's `voice_summary_mode` does).
- It does not transcode existing audio files; only fresh microphone capture and bridge-attached voice notes go through the transcription pipeline.

---

## Layer 2 — bridges

**Plugins:** `operator/bridges/{whatsapp,telegram,discord,slack,email}/`
**Owns:** ferrying messages between an external messenger and the agent.

### Responsibilities

- One **daemon** per channel (Node.js, except for the Python email bridge), holding the network connection — Baileys for WhatsApp, `node-telegram-bot-api` for Telegram, etc.
- A shared **Python adapter** (`bridges/shared/adapter.py`) that consumes inbox events from every daemon, runs them through a single `claude` subprocess, and writes the reply back to the daemon's outbox.
- **Whitelist enforcement** — every daemon has a per-channel whitelist; only matching `chat_key`s reach the adapter at all.
- **Inbox / outbox protocol** — JSON files in `/tmp/corvin-voice-bridge/<channel>/{inbox,outbox}/`, picked up by the daemon (outbox) or the adapter (inbox).

### Contract with the layer below

- Bridges call `claude` as a subprocess with the appropriate flags. They do not import or wrap the agent; they shell out to it. This is what lets a single agent serve five channels without conflict.
- Voice integrates downstream: when a bridge writes an outbox entry that includes audio, the daemon attaches the voice file and sends it.

### What bridges do not own

- They do not pick the persona — that is the adapter's job, in cooperation with cowork and the auto-router.
- They do not summarise; they pass the full reply text to the daemon, which forwards it untouched.
- They do not log to the audit chain themselves — the bridge audit wrapper (`bridges/shared/audit.py`) does, in a layer-5-aware way that no-ops when forge is missing.

### Hot-reload boundary

`whitelist`, `pin`, `rate_limit_per_hour`, `enabled_chats`, `chat_profiles` and the cross-channel `voice_summary_mode` reload on the next message via `currentSettings()` (Node) or `_load_channel_settings()` (Python). Tokens and HTTP ports do not — they are bound at boot.

---

## Layer 3 — cowork (personas)

**Plugin:** `operator/cowork/`
**Owns:** per-chat *role* of the agent — which tools it sees, which system prompt it gets, which directories it may touch.

### Responsibilities

- A **persona** is a JSON file under `operator/cowork/personas/<name>.json` (bundle) or `~/.corvin/cowork/personas/<name>.json` (user-specific override; `<repo>/.corvin/cowork/personas/` inside a repo). It declares: `tools` (allowlist / blocklist), `mcp_servers`, `add_dirs`, `append_system`, `permission_mode`, optional `routing_anchors` (for layer 4).
- A **resolver** (`operator/cowork/lib/resolver.py`) merges the persona declared on a `chat_profile` with the chat's other settings into the final argument list passed to `claude`.
- Bundled personas: `assistant`, `coder`, `browser`, `research`, `inbox`, `homeassistant`. Custom personas drop into the user override directory and are picked up via hot-reload.

### Contract with the layer below

- Cowork is **invoked from the adapter** (`_resolve_chat_profile`) — it does not run a daemon of its own.
- Voice does not import cowork; the adapter guards every cowork call with `_cowork is not None` so voice keeps working with cowork uninstalled.
- The bundle persona `coder` codifies the legacy max-open default: when a chat opts into `persona: "coder"`, the adapter sets `--dangerously-skip-permissions` exactly like the pre-cowork days.

### What cowork does not own

- It does not pick which persona handles a message; that is layer 4 (auto-routing) or an explicit `/persona <name>` pin.
- It does not declare per-persona forge ACLs in its own data — those live as a `FORGE_ALLOWED_TOOLS` value on the persona, but enforcement is layer 5's responsibility.

---

## Layer 4 — auto-routing

**Module:** `operator/bridges/shared/router.py`
**Owns:** picking *which persona* should handle the next message, when no persona is pinned for that chat.

### Responsibilities

- Three modes: `off`, `heuristic`, `embedding` (auto). Default is `heuristic` (zero-cost), with `embedding` as opt-in for chats where the heuristic is too brittle.
- **Heuristic** — a regex matcher in `_HEURISTIC_PATTERNS` keyed by persona. Two-token rule: ambiguous verbs ("open", "find") only fire a persona if paired with a strong noun ("URL", "page").
- **Embedding** — a `text-embedding-3-small` request against each persona's `routing_anchors`. Cached by anchor hash on disk so anchors only embed once.
- **Confidence threshold** — anything below threshold lands at the fallback persona (`assistant` by default), which has full tool access anyway and picks the right tool itself.

### Contract with the layer below

- The router is called from the adapter, not the bridge daemon. The router result is merged into the resolved profile *before* `_build_claude_args`.
- The `assistant` bundle persona is marked `routing_exclude: true` so the router does not "route" to itself when used as fallback.

### What auto-routing does not own

- It does not run any tool. It only labels.
- It does not change the agent's mind mid-message — once a persona is picked, the message goes through that persona's full system prompt and tool set.
- It does not alter the audit log; the persona pick appears in the audit entry written by the bridge wrapper, not by the router itself.

---

## Layer 5 — forge (runtime tool factory + policy + audit)

**Plugin:** `operator/forge/`
**Owns:** registration, execution, and audit of tools that did not exist when the session started.

This is the layer that makes Corvin more than a "phone-first frontend". A working agent that can *write a new tool, register it, run it, and audit-log everything it does* is qualitatively different from one that only chains pre-defined tools.

### Responsibilities

- **Registry** (`forge.registry`) — the on-disk table of forged tools, indexed by name. New tools are added via `mcp__forge__forge_tool`; the server then emits `notifications/tools/list_changed` so Claude Code re-issues `tools/list` on the next iteration.
- **Runner** (`forge.runner`) — invokes a forged tool inside a `bwrap` sandbox with a minimal mount set determined by the tool's `input_schema` (`x-bind: ro|rw`).
- **Per-run manifest** — `runs/<ts>_<id>/run_manifest.json` records inputs (with `x-redact: true` fields replaced by `<redacted>`), exit code, stdout, sandbox config.
- **Policy** (`forge.policy`) — `policy.json` controls breaker thresholds and forbidden-name patterns. Hot-reloaded via mtime cache; forbidden names are re-checked at *call* time, not just at *forge* time, so a tool registered before the rule existed cannot outlive it.
- **Per-persona ACL** — when `FORGE_ALLOWED_TOOLS` is set on the persona, the registry refuses calls to tools not on the list and emits an `acl.persona_denied` audit event.
- **Audit log** — `forge.security_events` writes to a single SHA-chained file. Bridge events use `bridges/shared/audit.py`, which calls into forge if it is installed and silently no-ops if not.

### Contract with the layer below

- Forge runs as its own MCP server, registered on Claude Code via `claude --mcp-config`. Bridges and cowork pass through unmodified — they neither know nor care about forge.
- The bridge audit wrapper imports from forge and falls back to a no-op when the import fails. This is a *one-way* dependency: voice depends on forge for audit, never the reverse.

### What forge does not own

- It does not pick which persona may call which tool — the persona declares `FORGE_ALLOWED_TOOLS`. Forge enforces, cowork declares.
- It does not rate-limit the agent overall; only forged-tool calls. Other tools (Bash, Read, MCP servers from cowork) are bound only by Claude Code's own permission system.
- It does not network-isolate by default. The sandbox unshares the network namespace; the operator opens it for specific personas via `policy.persona_sandbox_overrides`.

---

## Layer 6 — forge revisited (workspace scoping + namespace gate)

The forge plugin grew two cross-cutting fields in `policy.json` that don't fit the original "one tool, one sandbox" picture:

- **`persona_namespaces`** — registration prefix per persona (`coder` may only register `code.*`, `inbox` only `inbox.*`). Cross-persona name collisions are rejected at forge time and at call time.
- **`persona_sandbox_overrides`** — per-persona axes like `network: allow` for browser/research. The strict default (no network, no subprocess) stays for every persona without an entry; the operator opens up exactly what a role needs.

The MCP server also exposes a third meta-tool besides `forge_tool` and `forge_promote`: **`forge_list`**, which the persona is told to call before forging a duplicate. Discovery instead of blind creation.

Truncation behaviour changed too — when a forged tool's stdout exceeds the 4 MiB cap, the runner now writes the full bytes to `runs/<id>/artifacts/full_stdout.bin` and surfaces `meta.stdout_truncated`, `meta.stdout_total_bytes`, `meta.stdout_full_artifact` in the envelope. Silent data loss is out of the path.

---

## Layer 7 — skill-forge (runtime skill creation)

**Plugin:** `operator/skill-forge/`
**Owns:** generation, grading, promotion and pruning of **skills** — markdown bodies that get prompt-injected into future bridge turns.

Where forge generates *executable* artifacts (sandboxed tools), skill-forge generates *knowledge*: instruction-shaped markdown that the bridge adapter merges into the next claude subprocess' `--append-system-prompt`. The two plugins share the four-scope mechanic and the unified hash-chain audit log; the same `voice-audit verify` covers both.

Lifecycle:
- **Create** — `skill_create` runs the linter (prompt-injection, secrets, persona-boundary, length). Linter rejection blocks the write entirely.
- **Grade** — `skill_grade(name, run_id, score)` appends to the skill's grade history.
- **Promote** — `skill_promote(name, to=…)` walks task → session → project → user. Each step is gated by a grade threshold; project → user is operator-only.
- **Auto-grade after bridge turn** — when a bridge reply mentions an active skill (name variant or body snippet) without a negation in the immediate context, the adapter records a positive grade automatically. Skills that were genuinely useful keep climbing; skills that never fired fall away via the ungraded-TTL purge.

Visibility lives on three parallel paths: canonical workspace, plugin-slot mirror (engine reads at next subprocess boot), and adapter-injection (active immediately on the next bridge turn).

### What skill-forge does not own

- It does not execute anything. Skills are markdown.
- It does not have its own persona file — the historic `skill-forge` name resolves through `_PERSONA_ALIASES` to the unified `forge` persona, which carries both forge and skill-forge capabilities.

---

## Layer 8 — session-bound lifecycle (`/new` `/clear` `/reset` + 7-day timeout sweep)

**Module:** `operator/voice/scripts/session_reset.py` + `session_timeout_sweep.py`
**Owns:** wiping the chat-bound footprint when the user starts over.

A bridge chat owns four cleanup layers — session-scope skills, session-scope forge tools, the session forge workspace dir, and the voice conversation state. Layer 8 unifies them: a single user trigger (`/new` / `/clear` / `/reset`) writes one `session.reset` audit event into the unified hash chain, then rmtrees the four locations atomically. Daily timer sweeps anything older than 7 days the same way. Project- and user-scope are never touched.

---

## Layer 9 — capability gate (every persona forges its own tools/skills)

**Module:** `operator/cowork/lib/resolver.py` (`_inject_forge_capability` + `_inject_skill_forge_capability`)
**Owns:** turning a persona's `forge_enabled` / `skill_forge_enabled` flag into the actual MCP wiring.

Every persona that opts in via the corresponding flag receives the `mcp__forge__*` and/or `mcp__skill_forge__*` tools in its resolved `allowed_tools`, plus the matching MCP server in `mcp_servers`. The resolver also appends a runtime-built **capability brief** to the persona's `append_system` — telling it (a) which namespace prefix it owns, (b) whether its sandbox shares the host network, (c) where to discover existing artifacts before creating new ones. The brief is read fresh from `policy.json` each resolve, so it never lies about what the runtime actually permits.

Forge and skill-forge are no longer specialist personas with load-bearing safety isolation — they are opinionated specialists, with the structural safety living one layer below.

---

## Layer 10 — path-gate hook (structural write protection)

**Module:** `operator/voice/hooks/path_gate.py` (PreToolUse hook)
**Owns:** blocking direct `Write` / `Edit` / `MultiEdit` / `NotebookEdit` / `Bash` / `WebFetch` operations that target the forge / skill-forge workspaces, regardless of which persona made the call.

The hook fires before every matching tool call from any persona — `bypassPermissions` included. Protected paths: `<corvin_home>/**/forge/**`, `<corvin_home>/**/skill-forge/**`, the unified `audit.jsonl`, all `policy.json` files, and the engine-facing slot mirror under `operator/skill-forge/skills/dyn/**`. Bash is parsed for `>` / `>>` / `tee` / `mv` / `cp` / `sed -i` / `dd of=` / `python -c "open('…','w')"` / `rsync` etc.; `eval` / `exec` / command-substitution that mention a protected hint fail closed. Every block writes a `path_gate.denied` event into the hash chain.

This is the layer that makes "every persona may forge" safe. The MCP server stays the only writable path into the generation workspaces — not because the persona promised to be careful, but because the hook makes any other path return exit 2.

---

## Layer 11 — native dialectic decision-points

**Module:** `bridges/shared/dialectic.py`
**Owns:** thesis/antithesis/synthesis at five high-consequence sites — `skill_promotion`, `forge_creation`, `auto_routing`, `path_gate`, `session_reset`.

The library is wired into the integration sites natively, not as prompt-only injection. A Heat-Score gate (consequence × uncertainty × scope) filters trivial decisions out so hot paths stay zero-overhead. Modes: `off` / `fast` (local, < 1 ms) / `skill` (caller's Claude builds the synthesis in its existing turn — kostenneutral) / `cli` (`claude -p --max-turns 1` subprocess, Max-Abo). A `dialectic.json` toggle file with mtime hot-reload + five slash-commands (`/dialectic-on/off/status/set/show`) lets the operator scope the discipline. The library MUST NOT import the Anthropic SDK — a CI lint enforces this.

---

## Layer 12 — voice listener-profile

**Module:** `bridges/shared/profile.py` (`for_tts_audience`) + `scripts/summarize.py`
**Owns:** how the read-aloud is shaped for the listener's comprehension level, jargon tolerance, style preference, background, and analogy permission.

Six canonical fields (`voice_audience_level`, `…_jargon`, `…_style`, `…_background`, `…_metaphors`, `…_domains`) live in `~/.config/corvin-voice/profile.json`. The renderer composes a HÖRER-PROFIL / AUDIENCE block, appended only to TTS prompts (not chat), that re-affirms faithfulness while permitting low-jargon listeners (≤ 1) to translate code identifiers and CLI commands into spoken language. Six slash-commands (`/voice-user-show/set/clear/preview/help`) own the field mutations. Persona tone (Layer 9) and audience profile stack orthogonally in `summarize._system_for`.

---

## Layer 13 — `/btw <text>` mid-stream injection

**Modules:** all four daemons + `bridges/shared/adapter.py`
**Owns:** pushing a follow-up note into a *currently streaming* claude subprocess instead of queuing it as a new turn.

The daemon writes a `{_btw: true, text}` side-channel envelope; the adapter's main loop calls `_peek_side_channel()` per inbox file and bypasses the per-chat lock for `_btw` / `_cancel` envelopes. `process_one` looks up the live stdin in `_running_stdins[chat_key]` and writes one stream-json `user` message line. Adapter spawn shape changed for this — claude is now invoked with `--input-format stream-json` and a stdin pipe; the first `result` event closes the stdin so EOF is clean. Anything sent after that falls back to the normal queue path.

---

## Layer 14 — LDD-toggle system

**Module:** `bridges/shared/ldd.py` + `cowork/lib/resolver.py::_resolve_ldd_section`
**Owns:** which LDD disciplines are active at all, globally and per chat, with persona-level defaults and a hard-cascade gate for sinnlos child/parent combinations.

Twelve canonical layer IDs (loop_driven_engineering, e2e_driven_iteration, dialectical_reasoning, dialectical_cot, root_cause_by_layer, docs_as_dod, reproducibility_first, loss_backprop_lens, method_evolution, drift_detection, iterative_refinement, per_subtask_e2e). Storage in `<scope_root>/global/ldd.json` with mtime-cache; resolution is direct-state (profile per-layer > profile master > cfg master > cfg per-layer > default-on) followed by a hard-cascade gate (`dialectical_cot → dialectical_reasoning`, `per_subtask_e2e → e2e_driven_iteration`, `drift_detection → docs_as_dod`). Native integration sites today: `skill_inject.collect_active_skills` (filter), `skill_inject.auto_grade_from_output` (same filter), `dialectic.resolve_mode` (couples Layer 11 to the master). Slash-commands `/ldd-on/off/status/set/preset` plus four named presets (`default`, `strict`, `quick`, `off`). Per-persona profiles in each `personas/<name>.json` via `ldd_preset` + `ldd_layers` + `ldd_enabled` — chat_profile overrides win, with a special "kill-should-actually-kill" rule that drops persona-injected layers when the chat sets `ldd_enabled: false`. The library MUST NOT import the Anthropic SDK (CI lint).

---

## Layer 15 — outcome-grounded skill grading

**Module:** `bridges/shared/skill_inject.py` + `cowork/lib/resolver.py`
**Owns:** turning user reactions (approval / rejection / rephrase) into structural grades on the skills active in the previous turn.

Auto-grade after a turn (Layer 7) detects mention/paraphrase and records score 0.7. Layer 15 extends this: the adapter snapshots `_last_turn_skills[chat_key] = {run_id, skills, user_text, ts}` after each turn; the *next* user turn pops that snapshot one-shot (TTL 30 min) and runs `skill_inject.grade_from_user_followup(...)` BEFORE invoking claude. Detection uses curated phrase lists (German + English) for approval/rejection plus `difflib.SequenceMatcher.ratio() ≥ 0.6` for rephrase. Precedence: rejection (0.1) > approval (0.9) > rephrase (0.3). Each detected signal writes one grade per prev-turn skill into `meta.json` and emits a `skill.outcome_graded` event. Snapshot hygiene via `/reset`, `/cancel`, and a periodic `_cleanup_last_turn_skills` sweep. See [skills.md](skills.md) §3 Grade for the full path.

---

## Layer 16 — security hardening across surfaces

**Modules:** `bridges/shared/auth_elevation.py`, `bridges/shared/auth.js`, `hooks/path_gate.py` (extended), `skill_forge/linter.py` (extended), `forge/sandbox_helpers/sitecustomize.py`
**Owns:** strengthening the existing five enforcement surfaces with read-only role + observer transcript (Surface 1), NFKC + confusable normalization in the linter (Surface 3), loopback-deny via sitecustomize shim (Surface 4), path-gate v2 vectors `eval`/`exec`/`$()`/heredoc fail-closed (Surface 5), and PIN-elevation for `forge_promote` / `skill_promote` (new Surface 6).

Plus Roadmap items: F13 (path-gate boot self-test), J (dialectic rate-limit), K (TTS-key migration into XDG), L (daily systemd audit-verify timer with bridge notification on chain break). Full breakdown in [security.md](security.md) §"Layer 16 — hardening across the surfaces".

---

## Layer 17 — process model (Phase 1+3 landed)

**Modules:** `bridges/shared/process_table.py` (registry) + `bridges/shared/adapter.py` integration + `/ps` slash command via `phase3_cli.py`
**Status:** Phase 1 (registry, 17 E2E cases) + Phase 3 (adapter hooks: register on subprocess spawn, update on each tool_use event, deregister in finally; `/ps`/`/ps -a` slash command live). Signals (PLAN/SUMMARIZE/CONTEXT_DROP/QUIET) and `/kill`/`/nice` are Phase 4 (need daemon-side signal-routing).
**Owns:** making session lifecycle visible and controllable from the messenger via `/ps`, `/kill`, `/nice`, and custom signals (PLAN, SUMMARIZE, CONTEXT_DROP, QUIET).

Today the adapter's per-chat lock + `_in_flight` tracker form an invisible scheduler. Layer 17 surfaces it as first-class processes via `<corvin_home>/run/sessions.jsonl` — one record per session with status, persona, tokens, parent, nice value, current in-flight tool, exit reason. Writes serialised through `fcntl.flock` on a sidecar lock file; reads mtime-cached for cheap polling. Atomic via tmp+rename. The MVP is structurally independent of `adapter.py`; integration is the next slice. See [concept-os-completion.md](concept-os-completion.md) §Layer 17 for the full design (process tree, signals, /top, signal envelopes).

---

## Layer 18 — inter-session pipes (Phase 1 + 2 landed)

**Modules:** `bridges/shared/pipe_registry.py` (registry) + `core/pipe/mcp_server.py` (MCP surface)
**Status:** Phase 1 + 2 + 3 done. Registry MVP (17 E2E cases) + MCP server exposing 9 pipe tools over JSON-RPC stdio (10 E2E cases) + Phase 3 `/pipe <list|create|write|read|rm|meta>` slash commands via `phase3_cli.py` (31-case JS dispatcher test green).
**Owns:** sessions composing — output of one persona feeds the input of another. Three pipe modes: named FIFO (multi-write/multi-read, persistent), anonymous (single-read auto-removes the pipe; the `cmd1 | cmd2` analogue), broadcast (per-subscriber cursor, fan-out).

Storage in `<corvin_home>/run/pipes/` as JSONL data file plus sidecar meta + subscribers JSON. Per-pipe `fcntl.flock` serialises writes. Cursor advance per subscriber on broadcast read; late subscribers only see writes after subscribe. Validates pipe names against path traversal. Skips corrupt JSONL lines. The MCP server (`core/pipe/`) is the production-facing interface that personas opt into via `pipes_enabled: true` in their JSON. Domain errors surface as MCP `isError=true` tool-result envelopes (per MCP spec), JSON-RPC errors only for protocol violations.

---

## Layer 19 — service manager (Phase 1 + 2 landed)

**Modules:** `core/init/init.py` (supervisor) + 7 `*.service.yaml` files across the existing plugins
**Status:** Phase 1 (supervisor + 15 E2E cases) + Phase 2 (7 service manifests across plugins, smoke-tested via discover_services) + Phase 3 read-only `/svc list` + `/svc deps <name>` slash commands. `/svc start/stop/restart/journal` and bridge.sh migration to call init.py for actual supervision are Phase 4.
**Owns:** dependency-graph init system replacing `bridge.sh`. Supervises children (restart on failure with exponential / linear / none backoff, max_restarts cap), captures journal-style logs per service, sends hot-reload signals.

Tiny YAML subset parser (no PyYAML dep) handles scalars, lists, comments. ServiceDef + ServiceState dataclasses. `discover_services` walks plugin dirs for `*.service.yaml`. `topological_order` respects `requires` (hard) + `wants` (soft), detects cycles, fails on missing required deps. Supervisor uses `start_new_session=True` for clean SIGHUP delivery (operators must use `exec` prefix in `exec_start` when `hot_reload` is set, so the signal reaches the actual binary not the `/bin/sh` wrapper). See [concept-os-completion.md](concept-os-completion.md) §Layer 19.

---

## Layer 20 — context memory manager (Phase 1 + 2 landed)

**Modules:** `bridges/shared/context_budget.py` (budget) + `bridges/shared/context_cold_storage.py` (cold storage)
**Status:** Phase 1 (budget + 18 E2E cases) + Phase 2 (cold-storage tier with pluggable EmbeddingProvider; 20 E2E cases) + Phase 3 read-only `/budget show` + `/budget policy` slash commands. Active pre-flight budget gate (auto-evict/compress before each turn) is Phase 4.
**Owns:** treating the LLM context window as a managed resource. Per-session quotas, working-set tracker, three OOM policies (evict / compress / reject), and a cold-storage tier where evicted turns are vector-embedded for cosine-similarity retrieval. Pure bookkeeping for the budget; embedding is provider-injected.

Action ladder: `used/quota < 0.9` → `ok`; `0.9 ≤ u/q ≤ 1.0` → `warn`; `u/q > 1.0` → configured `oom_policy`. Eviction drops oldest turns; production deployment can pair `evict()` with `ColdStorage.page_out()` so evicted turns survive in compressed form. Cross-provider safety: pages embedded with provider A are skipped when querying with provider B (incompatible vector spaces). See [concept-os-completion.md](concept-os-completion.md) §Layer 20.

---

## Layer 21 — federation (NOT IMPLEMENTED, intentional)

Federation requires real WireGuard tunnels, mTLS certificates, and cross-host audit-chain double-signing. The threat model and operational complexity are non-trivial and the testing requirements (real network namespaces, real cross-host process spawning) cannot be safely exercised in a chat or single-host CI. Deferred until multi-host demand is established. The concept is fully designed in [concept-os-completion.md](concept-os-completion.md) §Layer 21; implementation is intentionally future work.

---

## Layer dependencies

The arrows that matter:
- **voice → forge**: through `bridges/shared/audit.py`, with an `is None` guard. If forge is uninstalled, audit becomes a no-op.
- **forge → nothing in this repo.** Forge is splittable on day one; it imports nothing from voice or cowork.
- **bridges ↔ cowork**: bidirectional through the adapter. Cowork is optional; voice guards every call.
- **router → cowork**: the router emits a persona name; cowork resolves it. Router does not depend on any specific persona being present — it falls back to `assistant`.

This shape is what lets each layer be removed (or, in the case of forge, *extracted into its own repo*) without breaking the others.

---

## Cross-cutting: hot-reload, audit, whitelist

Three concerns cut across all layers and are documented per-layer above. Their cross-cutting summary:

- **Hot-reload.** Layers 2, 3, 4, and forge's `policy.json` all use mtime-based reload. Layer 1's TTS engine choice and Layer 0 are bound at boot.
- **Audit.** Forge events are first-class audit. Bridge events are audit *if forge is installed*. Cowork persona switches and routing decisions surface in the bridge entries. Voice itself does not append to the audit chain — its actions are leaves of bridge events that are already being audited.
- **Whitelist.** Layer 2 owns it. Personas (3) and policy (5) sit *inside* the whitelist trust boundary; they restrict the agent's behaviour for the trusted user, never gate access for new users.

---

## Next

- [data-flow.md](data-flow.md) — see the layers in motion as a single message goes phone → bridge → adapter → cowork → router → claude → forge → audit → reply.
- [security.md](security.md) — the four enforcement surfaces (ACL, policy, sandbox, audit) and what each one is for.
