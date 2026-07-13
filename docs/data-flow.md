# data-flow.md — a message from phone to reply

## Mental model

A message that arrives at a bridge does **not** go through the layers in stack order. It goes through them in **dispatch order**: bridge → adapter → cowork (persona resolution) → router (if no persona pinned) → `claude` subprocess → tool calls (possibly through forge) → audit append → outbox → daemon → user. The layers are infrastructure; the dispatch is the path.

The picture in one sentence: **one Python adapter holds the loop, one `claude` subprocess does the thinking, every other moving piece is a daemon or an on-disk file**.

<p align="center">
  <img src="diagrams/02-data-flow.svg" alt="One message from phone to reply, eight stages with hot-reload boundaries annotated." width="1000" />
</p>

## End-to-end walkthrough

We trace one concrete message: *"find me the cheapest train Berlin → Munich for tomorrow"* sent as plain text from a whitelisted WhatsApp number.

### Stage 1 — daemon receives

The daemon holds a long-lived WhatsApp Web socket via Baileys. When a new message arrives:

1. `currentSettings()` reads `bridges/whatsapp/settings.json` fresh (mtime cache). Whitelist, rate limit, `enabled_chats` apply *as of right now* — even if you edited the file ten seconds ago.
2. The whitelist check rejects unknown senders silently.
3. `is_bot` / `fromMe` filters reject echoes and bot replies.
4. The message is normalized into an **inbox envelope** — JSON with `chat_key`, `text`, `audio_path` (if voice note), `attachments[]`, `timestamp`.
5. The envelope is written to `/tmp/corvin-voice-bridge/whatsapp/inbox/<id>.json`.

The daemon now waits for an outbox file with the matching id. It does not call Claude.

### Stage 2 — adapter picks up

A single adapter process watches every channel's inbox directory at once. On a new file:

1. `_load_channel_settings("whatsapp")` reads the daemon's settings fresh — same mtime-cache pattern.
2. **Voice-note transcription** — if the envelope has `audio_path`, the audio is transcribed via Whisper and the result replaces `text`. The original audio is kept for the audit entry.
3. **Profile resolution** — `_resolve_chat_profile(chat_key)` looks up `chat_profiles[chat_key]` first, then JID-normalised forms (WhatsApp), then `"default"`, then the empty profile (legacy max-open). The result is a **base profile** — `permission_mode`, `allowed_tools`, `disallowed_tools`, `append_system`, `add_dirs`, `mcp_servers`, optional `persona`.
4. **Cowork merge (if installed)** — if `cowork` is importable and the profile has `persona: <name>`, `cowork.resolver.resolve()` loads the persona JSON and merges its fields into the profile: lists union, scalars → profile wins, `mcp_servers` shallow-merged, `append_system` concatenated.

### Stage 3 — auto-routing (only if no persona pinned)

If the profile has no explicit `persona`, `_apply_auto_routing()` calls `bridges/shared/router.py`:

1. **Heuristic pass.** `_HEURISTIC_PATTERNS` matches `text.lower()` against per-persona regexes. "find me the cheapest train" hits the `browser` persona's pattern (transactional verb + travel noun). Confidence high → return `{persona: "browser", confidence: 0.9, why: "heuristic:travel"}`. Done in <1 ms.
2. **Embedding pass** (only if heuristic returned nothing or low confidence). The text is embedded via `text-embedding-3-small`; cosine-similarity against each persona's `routing_anchors` (cached on disk) picks the best.
3. **Fallback.** Below threshold → `assistant` persona. The router never returns `None`; the fallback is part of the contract.

The picked persona is merged into the profile (its tools, MCP servers, `append_system`, possibly its `FORGE_ALLOWED_TOOLS`). A `[browser]` prefix is queued for the eventual reply.

### Stage 4 — `claude` subprocess

`_build_claude_args()` materialises the resolved profile into command-line flags:

- `--permission-mode default|plan|acceptEdits|bypassPermissions`
- `--allowedTools` / `--disallowedTools`
- `--mcp-config <tempfile.json>` if `mcp_servers` is non-empty
- `--add-dir <path>` for each entry in `add_dirs`
- `--append-system-prompt <text>`
- The user message (transcribed text + attachment hints) as the prompt

Two environment variables travel with the subprocess:
- `FORGE_ALLOWED_TOOLS` — the per-persona allowlist (or unset for max-open)
- `VOICE_HOOK_RECURSION` — set to `1` if this `claude` invocation is *itself* a hook (e.g. the summarizer calling `claude -p`), to prevent the Stop hook firing on its own output.

### Stage 5 — Claude Code runs

This is the only stage we do not own. Claude Code:

- Reads its own MCP config (including the `forge` server, if it is registered) → `tools/list` exposes the forged tools the persona may call.
- Runs the agent loop: tool calls, sub-agent invocations, file edits, reads.
- Tool calls hit either standard Claude Code tools (Bash, Read, etc.) or MCP tools, including `mcp__forge__<name>` for forged tools.

If the agent decides it wants a *new* tool that does not exist yet, it calls `mcp__forge__forge_tool({...})`. See [forge.md](forge.md) §Lifecycle for the inner loop here. The relevant bit for data flow: the new tool surfaces via `tools/list_changed`, the agent waits one tick, then calls `mcp__forge__<name>` with the input it wants. Forge runs the tool in `bwrap`, writes a `runs/<id>/run_manifest.json`, and returns the JSON envelope to Claude.

### Stage 6 — assistant reply

The adapter parses Claude's JSONL output stream. The final assistant message is captured. Two side-effects in parallel:

1. **Stop hook fires** in Claude Code itself. If running at the desk, the voice plugin's Stop hook intercepts and starts the read-aloud pipeline (summarize → TTS → audio output). This pipeline does *not* go through the adapter; it uses the same hook system Claude Code provides for any plugin.
2. **Adapter writes the outbox.** A JSON envelope with `text`, optional `attachments[]` (files Claude wrote), optional `audio_path` (if `voice_summary_mode` says to attach a voice note) is written to `/tmp/corvin-voice-bridge/whatsapp/outbox/<id>.json`. While the turn is still running, the adapter also writes interim `_progress`/`_heartbeat` envelopes (same `msg_id`) so the daemon can show a live status — see `claude-ref/adapter-runtime.md` §Sticky progress messages for how every daemon renders those as one edited-in-place message instead of a flood.

### Stage 7 — audit append

For every inbox-to-outbox cycle the bridge audit wrapper appends one entry. Fields include `chat_key`, `persona`, `routing.why`, `tool_calls[]`, `forge_calls[]`, `outbox_id`, `prev_sha`, `sha`. The chain field links the new entry's hash to the previous entry's hash; a tampered or reordered file fails `voice-audit verify`.

If forge is uninstalled, the wrapper silently no-ops here. The conversation works the same; you simply lose audit until forge is back.

### Stage 8 — daemon delivers

The daemon picks up the outbox file, attaches files / voice note, and sends to the chat. It deletes the outbox file on success. The user sees the reply prefixed with `[browser]` so the persona pick is visible.

---

## Hot-reload boundaries

Several files in this dispatch path are read on every message:

| File | Read by | Effect when edited |
|---|---|---|
| `bridges/<channel>/settings.json` | daemon (`currentSettings()`) + adapter (`_load_channel_settings`) | Whitelist, rate limits, `chat_profiles`, audience toggle apply on next message |
| `bridges/shared/settings.json` | adapter | `voice_summary_mode`, `routing.mode`, `progress_updates` apply on next message |
| `operator/cowork/personas/<name>.json` | resolver, when persona is invoked | Persona's tools, MCP servers, system prompt apply on next message that uses that persona |
| `operator/forge/policy.json` | forge (mtime cache) | Breaker thresholds and forbidden-name patterns apply on next forge call |

Files that do **not** hot-reload — bound at daemon/adapter boot:

| File | Why |
|---|---|
| `~/.config/corvin-voice/service.env` | API keys read by every daemon at startup; rotating a key needs `bridge.sh restart` |
| daemon code itself | Structural changes (new envelope fields, new hook logic) — the running daemon does not pick up code edits |
| HTTP ports | Bound to the listening socket at boot |

This split is documented in `CLAUDE.md` §Hot-reload convention as the rule for editing the codebase.

---

## What can go wrong, where

A short failure-mode atlas, in dispatch order:

| Symptom | Likely stage | Where to look |
|---|---|---|
| Bot does not reply at all | Stage 1 (whitelist / rate limit) | Daemon log: `whitelist rejected` / `rate-limit hit` |
| Bot replies but ignores recent setting change | Stage 2 (settings cache) | Adapter log: `settings.json reloaded (mtime=...)` should appear right after the file edit |
| Bot picks the wrong persona | Stage 3 (router) | Adapter log: `routing decision: <persona> via <heuristic\|embedding> conf=...` |
| Persona's MCP tool not visible | Stage 4 (`--mcp-config`) | Adapter log: temp MCP file path + `claude --mcp-config <path>` invocation |
| Forged tool not available right after registration | Stage 5 (`tools/list_changed` race) | Need a one-tick wait between `forge_tool` and the first call (see [forge.md](forge.md) §Race) |
| Forged tool blocked unexpectedly | Stage 5 (policy hot-reload) | `voice-audit tail` should show `policy.reloaded` and the blocking entry |
| Reply text arrives but no voice note in chat | Stage 6 (`voice_summary_mode`) | `bridges/shared/settings.json` value vs. expected |
| Audit entry missing | Stage 7 (forge install) | `voice-audit verify` will say "forge not installed" if so |

---

## Why one adapter, one subprocess

The adapter holds a per-`chat_key` lock so two messages in the same chat run sequentially. Cross-chat parallelism is allowed: chat A's message 1 can run concurrently with chat B's message 1, but A's message 2 waits for A's message 1 to finish.

The single `claude` subprocess per message is a deliberate choice over keeping a long-running one:

- A fresh subprocess per message means *no state leaks between chats*. Whatever the agent did in chat A is invisible to chat B, period.
- Per-chat memory is restored explicitly via the `--append-system-prompt` glue from cowork + the persona, plus the bridge-wide memory tiers (`/profile`, `/memory`, `/vault`). Nothing escapes that surface.
- The price is a few hundred milliseconds of `claude` startup per turn. We pay it.

---

## Next

- [forge.md](forge.md) — the inner loop of stage 5 when a forged tool is involved.
- [security.md](security.md) — the four enforcement surfaces (ACL, policy, sandbox, audit) and where each one trips.
- [agent-behavior.md](agent-behavior.md) — what stages 4-7 look like *from inside Claude*, including how I (the agent) see hot-reload.
