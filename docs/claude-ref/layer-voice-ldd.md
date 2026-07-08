# Voice, LDD Toggle & Dialectic Reference (Layers 11–14, 23)

> Load when working on dialectic decisions, voice profiles, /btw injection, LDD layers, or STT.
> Quick summary in CLAUDE.md § Layer 11 / Layer 14.

## Layer 11 — Native dialectic decision-points (LDD-integrated)

The LDD `dialectical-reasoning` discipline is now wired into a small
curated set of high-consequence decision sites natively, not as a
prompt-injected skill alone.

### Where it fires (and where it absolutely does not)

| Site | Default mode | Threshold | When it triggers |
|---|---|---|---|
| `skill_promotion`  | `skill` | 0.5 | session→project→user transitions; heat from reach + grade-count |
| `forge_creation`   | `skill` | 0.5 | new tool name collides or shares namespace with an existing entry |
| `auto_routing`     | `fast`  | 0.5 | router-confidence < 0.6 AND high-stake content |
| `path_gate`        | `fast`  | 0.6 | unparseable Bash with hint-strings (deny stays fail-closed; dialectic only records) |
| `session_reset`    | `cli`   | 0.5 | session has 3+ promoted skills or 10+ tools |
| `voice_summary`    | `off`   | 0.5 | (opt-in) faithfulness check on the read-aloud summary before delivery; default-off because cli mode adds 5-15 s latency on every voice reply, flip on with `/dialectic-set voice_summary cli` |

NEVER fires on inbox-read, audit-write, TodoWrite, skill-inject
selection, regular forge tool-runs, or daemon poll loops.
Hot-paths are zero-overhead. Voice-TTS used to be on this NEVER
list; the `voice_summary` site is the explicit opt-in carve-out
introduced for users who accept the latency cost in exchange for
LLM-judged faithfulness on every voice summary.

### Cost contract

`operator/bridges/shared/dialectic.py` MUST NOT
`import anthropic`. Test `test_dialectic_lib.py` enforces this via
AST walk. Modes:
- `off` / `fast` — pure Python, 0 ms
- `skill` — local Claude builds the synthesis in its existing turn
  (kostenneutral, no extra spawn)
- `cli` — `claude -p --max-turns 1 --no-tools` subprocess
  (~5–8 s, authenticated via the user's Claude login → Max-Abo)

### Toggle

Five slash-commands persist into
`<scope_root>/global/dialectic.json` with mtime hot-reload:

```
/dialectic-on        # default state
/dialectic-off       # global kill-switch
/dialectic-status    # show modes + thresholds + counters
/dialectic-set <site> <fast|skill|cli|off>
/dialectic-show [on|off]   # reply-footer toggle
```

Per-chat opt-out: `chat_profile.dialectic_enabled = false`.

### Heat-Score formula

```
heat = 0.4 * consequence + 0.3 * uncertainty + 0.3 * (scope / 5)
```

- consequence: 0.1 reversible/local · 0.5 session-bound · 1.0 cross-session/irreversible
- uncertainty: 1 - confidence (or 1 - (top - second) for categorical picks)
- scope: 1 (one decision) · 3 (session-bound) · 5 (user-scope, all chats)

Pre-calibration ran 13 fictive tasks through the formula; all 13
match the expected trigger / no-trigger outcome (see
`test_dialectic_lib.py::case_calibration_table`).

### Compliance rationale by site (ADR-0073 G-014)

Each site's default mode is a deliberate design choice. This table documents why
each mode meets the EU AI Act Art. 14 (human oversight) requirement for that site,
so operators can justify deviations in their own DPIA.

| Site | Default mode | Oversight adequacy rationale | Regulatory note |
|---|---|---|---|
| `path_gate` | `fast` | Gate is **fail-closed regardless of dialectic outcome** — the dialectic adds transparency, not the safety boundary itself. Fast mode is justified: zero latency on a hot path; dialectic never weakens the deny decision. | EU AI Act Art. 14(4): override path exists (admin can whitelist specific paths). |
| `session_reset` | `cli` | Session reset is **user-initiated and irreversible** (deletes skills + tools). `cli` mode invokes an adversarial subprocess that must affirmatively confirm before the reset proceeds. Highest-quality oversight for an irreversible action. For `high_risk` tenants this default is load-bearing. | EU AI Act Art. 14(1): human must be able to "interrupt the AI system" — the cli confirmation dialog is that interrupt. |
| `skill_promotion` | `skill` | Skill promotion changes future LLM behavior across sessions. `skill` mode uses the injected skill itself as the reasoning source — appropriate for a skill-quality self-assessment. Low blast radius (promotion can be reversed by purging the skill). | No direct regulatory obligation; best-practice oversight for self-modifying system behavior. |
| `forge_creation` | `skill` | Tool creation is sandboxed by bwrap — the true safety boundary is the sandbox, not the dialectic. `skill` mode provides structured reasoning on namespace collisions at low cost. | EU AI Act Art. 15: robustness through sandboxing (structural), dialectic is transparency layer. |
| `auto_routing` | `fast` | Routing selects a persona, not a decision with legal effect. Fast mode is a keyword classifier (0 ms). The actual safety boundary is the persona's `permission_mode: bypassPermissions` gate (operator-controlled). | No Art. 22 GDPR trigger (no significant effect on the data subject from persona selection alone). |
| `voice_summary` | `off` | Voice summaries are a presentation layer (TTS output). The structural faithfulness defense is the two-layer voice-summary system baked into the summarizer's LLM call. Dialectic would add latency with no incremental safety benefit. | EU AI Act Art. 13 (transparency): fulfilled by the underlying summarizer's instruction-following. |

**For `high_risk` tenants** (`spec.risk_classification: high_risk`): `session_reset` must stay
at `cli` mode (already the default). Operators should additionally consider upgrading
`auto_routing` to `skill` mode to add a reasoning record for routing decisions.

### What you, as Claude Code, must NOT do

- Don't add `import anthropic` to `dialectic.py`. The CI lint rejects
  it via AST and the cost contract depends on it.
- Don't create a new site without registering it in `SITES` (mode +
  threshold). A site without a fast synthesizer falls back to thesis
  on `mode=fast` — that's by design (no-op site is better than a
  silent decision).
- Don't make the integration sites raise on dialectic failures. Every
  `decide()` call must be wrapped in `try/except` and silent on
  failure — dialectic is observability, never enforcement.
- Don't trigger dialectic on hot-paths (inbox-read, daemon loop,
  audit-write, TodoWrite). The Heat-Score gate alone won't save you
  if the call site itself fires 100x/sec.

### `voice_summary` site — two-layer faithfulness defence

The site delivers the truthfulness check on every voice summary in two
stacked layers. Layer one is **always-on, structurally**, baked into
the summarizer's own LLM call — zero extra latency. Layer two is the
**optional CLI judge**, a separate `claude -p` round, opt-in globally
via `/dialectic-set voice_summary cli` and per-persona via the
`_PERSONA_VOICE_SUMMARY_MODE` map in `summarize.py`.

#### Layer one — inline SELF-CHECK in the summarizer prompt

The four base prompts (`SYSTEM` and `SYSTEM_WITH_TASK`, German + English)
each carry a final `SELBST-PRÜFUNG` / `SELF-CHECK` block, appended in
`_system_for(...)` AFTER the persona-tone addendum and the audience
block. The block instructs the LLM to walk the candidate one final time
and revise on faithfulness defect, completeness gap, listener-angle
slip, or meta-question drift.

**Order is load-bearing.** The self-check MUST be the most-recent
instruction in the system prompt, AFTER persona and audience. The
persona-cycle E2E on 2026-05-09 first placed the self-check inside the
base prompt (before the persona addendum) and three personas — coder,
homeassistant, os — drifted into invented context (git-status
fabrication, Vault/Policy/Path-Gate hallucination, Path-Gate-Hook
projection). After moving `SELF_CHECK_BLOCK` into a separate constant
appended last, those three drifts disappeared. Most-recent instruction
wins; the self-check is now structurally pinned on top.

The block is plain prompt text, not a separate LLM call — zero extra
latency, no subprocess, no audit-chain entry. The faithfulness loop
runs as part of the same `claude -p` round that produces the summary.
This is the Option-B design from the design discussion: structural
always-on, baked into how summaries are produced.

#### Layer two — CLI judge (`dialectic.judge_summary`)

`dialectic.judge_summary(source, candidate, lang=…, persona=…, mode=…)` is
the integration point for the second-model verification pass. Called
unconditionally from `summarize.summarize()` after the candidate is
produced. Site mode default is `off` → zero-cost no-op (no subprocess,
no audit) for personas without an override.

`summarize.py` carries a per-persona override map
(`_PERSONA_VOICE_SUMMARY_MODE`) that forces `mode="cli"` for the three
personas the inline self-check leaves with residual drift — `research`
(background-knowledge leakage), `forge` (operational add-ons), `orchestrator`
(delegation-path verbosity drift). For other personas the global site mode
applies; the operator can still flip `/dialectic-set voice_summary cli`
to force the CLI judge for every persona.

When fired, the CLI judge runs a separate
`claude -p --max-turns 1 --no-tools` call with a faithfulness-specific
prompt:

```
SOURCE: <<< original assistant reply >>>
CANDIDATE SUMMARY: <<< current voice summary >>>
Reply EXACTLY ONE LINE:
  FAITHFUL  | <one-sentence why>
  CORRECTED | <revised summary on the same line, no newlines>
```

Verdicts:

- `FAITHFUL` → candidate ships unchanged. Audit logs verdict, source
  text is not rewritten.
- `CORRECTED` → revised text replaces the candidate. The corrected
  text MUST keep the same length budget and speaking-style as the
  original; the judge is instructed accordingly.
- Any other / unparseable / empty / timeout → ship the candidate
  (safe default: a confused judge never silently mangles user-
  visible output).

Source-text cap: 4000 chars (head 2/3 + tail 1/3 with explicit
truncation marker) to keep the CLI round-trip within the 20 s
timeout budget. Long sources lose middle-faithfulness signal — the
trade-off is documented; for very long replies the structural
summary in `summarize.py` is the primary defense, the judge is the
secondary.

`skill` mode is registered but degrades to `skipped` because the
voice summarizer's own `claude -p` call has a fixed prompt shape
that can't carry the dialectic markup without distorting the
output. Future work could rewrite the summarizer to fold the
faithfulness check into a single LLM turn (zero-latency); for now
`cli` is the only active mode.

Audit chain receives a `decision.dialectical` event with
`site="voice_summary"`, `mode="cli"`, `choice="faithful"` or
`"corrected"`, plus the verdict line in `synthesis`.

Per-subtask E2E in `shared/test_dialectic_voice_summary.py`
covers seven cases: mode=off no-op (no spawn), cli FAITHFUL,
cli CORRECTED, five unparseable shapes default to faithful,
timeout / missing-cli default to ship, summarize.summarize()
integration both off (no spawn) and cli (corrects through to
the final return value).

#### What you, as Claude Code, must NOT do (voice_summary)

- Don't make the judge fail-closed (drop the candidate on judge
  error). Voice replies are user-facing; an unreachable judge must
  never silence the user's reply.
- Don't bypass the rate-limit / recursion gates by calling
  `_run_summary_judge` directly. The `judge_summary()` wrapper is
  the ONLY supported entry point; it's where mode resolution,
  rate-limiting, recursion-guard and audit emission live.
- Don't widen the source-text cap (`_SUMMARY_JUDGE_SRC_CAP`) above
  ~8 KB without re-thinking the timeout budget. The 20 s judge
  timeout is per-CLI-roundtrip; long inputs drag the wall-clock
  cost up linearly.
- Don't change the `mode="off"` default in SITES without operator
  consent. The default protects the latency contract for users who
  haven't opted in to global CLI verification;
  `/dialectic-set voice_summary cli` is the supported global activation
  path. Per-persona escalation lives in `_PERSONA_VOICE_SUMMARY_MODE`
  in `summarize.py` — that's the right place to register a persona
  for which the inline self-check leaves residual drift.
- Don't move the SELF-CHECK block back into the base prompts. Order is
  load-bearing; if the persona-tone addendum is appended after the
  self-check, persona-tone wins and the most-recent-instruction
  contract breaks. The 2026-05-09 E2E drift (coder git-status
  invention, homeassistant Vault hallucination, os Path-Gate-Hook
  projection) is the failure mode that motivates the always-last
  placement.
- Don't add a persona to `_PERSONA_VOICE_SUMMARY_MODE` without an
  E2E-derived reason. The cost is 5–15 s of extra latency per voice
  reply for every chat using that persona — an entry in this map is
  a deliberate latency-for-rigor trade. Document the observed drift
  in the same docstring (see the existing entries for research /
  forge / orchestrator).

## Layer 12 — Voice listener-profile (TTS audience tuning)

Layer 9 (`PERSONA_STYLE`) modulates how the *speaker* sounds. Layer 12
modulates how the read-aloud is shaped for the *listener* — comprehension
level, jargon tolerance, background, style preference, analogy permission,
and which technical domains may stay untranslated. The two axes are
orthogonal and stack in `summarize._system_for`: base prompt → persona →
audience.

### Storage

Single bridge-wide JSON: `~/.config/corvin-voice/profile.json`, owned by
the existing `bridges/shared/profile.py` module. The TTS-audience fields
are siblings of `name` / `tone` / `timezone` under canonical keys:

| Key                          | Values                                  |
|------------------------------|-----------------------------------------|
| `voice_audience_level`       | `novice` / `intermediate` / `expert`    |
| `voice_audience_jargon`      | `0`–`5` int (0 = translate everything)  |
| `voice_audience_style`       | `concise` / `verbose` / `example-driven`|
| `voice_audience_background`  | free text, ≤200 chars                   |
| `voice_audience_metaphors`   | `on` / `off`                            |
| `voice_audience_domains`     | comma-list, max 8 domains               |
| `voice_audience_learning`    | `0`–`3` int (annex depth)               |

`_sanitize_voice_audience()` validates fail-open: malformed values are
silently dropped so a typo never breaks TTS.

### Render path

`profile.for_tts_audience(lang)` returns either an empty string (no fields
set → backward-compat, prompt is byte-identical to pre-layer-12) or a
HÖRER-PROFIL / AUDIENCE block. The block reaffirms the faithfulness rule
inline so the LLM cannot trade content for tone.

**Low-jargon translation permission.** When `voice_audience_jargon ≤ 1`,
the renderer appends an extra clause **before** the faithfulness
reaffirmation: explicit permission to render code identifiers, CLI
commands and API names in plain everyday language ("translation, not
invention"), with the instruction to *signal* the code-token rather
than speaking the literal string. The clause was added after a
2026-05-07 A/B run where Profile B (jargon=0, novice, "Tante kein
Tech") still kept code tokens like `voice_detect_engine` and
`OPENAI_API_KEY` in the spoken output — the LLM resolved the conflict
between jargon=0 and faithfulness toward faithfulness because
"translating" felt like "inventing." The clause is gated on
`jargon ≤ 1`; high-jargon listeners do not get it (saves tokens, avoids
over-translation). Order in the rendered block is permission-first,
faithfulness-second, so the new clause is *re-framing* faithfulness,
not *weakening* it. Tests in `test_profile.py::TtsAudienceTests`
guard both directions.

`stop_hook.sh` resolves the block once per turn after language detection
and passes it via `--audience` to `summarize.py`. The setsid subshell
inherits `AUDIENCE` as an env var so a `voice_kill_current_tts` doesn't
strand the value.

**Bridge-side integration** (`adapter.build_voice_summary`): the bridge
ALSO has its own voice-note path that does NOT go through `stop_hook.sh`
— every bridge subprocess is spawned with `VOICE_HOOK_RECURSION=1`, so
the stop-hook short-circuits for every Discord / WhatsApp / Telegram /
Slack reply. Without a second integration site the AUDIENCE block
would be dead code for every bridge chat. `build_voice_summary`
therefore imports `profile` as `_voice_profile` (optional, mirrors the
`_skill_inject` / `_cowork` patterns) and appends `--audience <block>`
to its `summarize.py` invocation when `for_tts_audience("de")` returns
non-empty. Backward-compat: when no audience fields are set, the argv
is byte-identical to the pre-layer-12 invocation. Per-subtask E2E
coverage in `test_adapter_voice_audience.py` (3 cases: learning=3 →
LERN-ZUGABE in argv; empty profile → no `--audience`; profile module
unavailable → graceful no-op).

**Pre-strip fallback logic** (M3.1 resilience): `build_voice_summary` applies
a two-stage pipeline: (1) `strip_for_tts.py --mode code-only` removes code
blocks and raw HTML so `summarize.py` sees clean prose, (2) `summarize.py`
produces the final summary. When stage 1 fails (timeout, exception) or returns
empty (e.g., input is 100% code blocks), stage 2 now receives the raw text
instead of empty input. Three fallback cases: (a) `strip_for_tts.py` timeout
(10s) → log warning, use raw text; (b) `strip_for_tts.py` exception (non-zero
exit) → log warning, use raw text; (c) `strip_for_tts.py` returns empty →
log warning, use raw text. When `summarize.py` itself returns empty, fall back
to `text[:max_chars]` (structural compression's first N chars). Logging is
explicit ("strip_for_tts returned empty", "summarize returned empty") so
operators can diagnose why a voice summary became generic. Per-subtask E2E
coverage in `test_adapter_voice_stripper_fallback.py` (3 cases: stripper empty,
stripper timeout, summarizer empty) and `test_voice_summary_e2e.py` (4 cases:
normal, code-heavy, empty input, short input).

**Learning-mode annex.** When `voice_audience_learning ≥ 1`, the renderer
inserts a structurally-marked LERN-ZUGABE / LEARNING ANNEX clause AFTER
the (optional) low-jargon clause and BEFORE the faithfulness
re-affirmation. The depth scales with the integer:

| Value | Effect on the read-aloud |
|-------|--------------------------|
| `0`   | off — no annex, byte-identical to pre-learning behaviour |
| `1`   | one half-sentence gloss on the first non-trivial term in the source |
| `2`   | one underlying concept introduced (1–2 sentences), picked from the source |
| `3`   | concept introduction (1–2 sentences) plus a one-sentence recap that pins the new vocabulary |

The clause is purely **additive**: it explicitly forbids trading source
content for didactics, reordering the summary, or shortening it to make
room. Faithfulness and completeness on the summary itself stay
load-bearing. The clause also includes the explicit instruction "must be
spoken aloud as part of the output, not silently appended" — that's the
load-bearing rule that keeps the annex in the TTS stream rather than
becoming chat-only metadata.

**Deterministic backfill (symmetry with the metaphor).** The `--audience`
instruction asks the summarizer LLM to append the annex itself, but LLMs miss
audience instructions for the learning annex just as often as for the metaphor.
`build_voice_summary` therefore backfills the LERN-ZUGABE deterministically in
EVERY path (override, short-text, main long-text, and the summarizer
missing/empty/failed fallbacks) via `_append_lern_zugabe`, gated on
`_has_lern_zugabe_suffix` so a marker the LLM already produced is never
duplicated — exactly mirroring `_append_metapher` / `_has_metapher_suffix`.
Order is load-bearing: the annex is backfilled BEFORE the metaphor (the metaphor
bridge "follows the learning annex"). Earlier only the metaphor was backfilled on
the main long-text path, so a substantive answer came back with the metaphor but
no learning annex ("Metaphern da, Learning fehlt"); the regression is covered by
`test_voice_appendix.py` L12.app/8 (main path backfills BOTH).

The depth-text branches off in `for_tts_audience()`'s `learning_clause`
(both `de` and `en` paths) by integer match; values outside `0..3` are
fail-open dropped via `_sanitize_voice_audience()`. The `learning=0`
case still emits the bullet `Lern-Modus 0/3` for transparency, but
suppresses the annex clause — `0` is the explicit "off" sentinel.

### User-facing commands (in `bridges/shared/js/in_chat_commands.js`)

| Command                          | Effect                                |
|----------------------------------|---------------------------------------|
| `/voice-user-show`               | filtered view of `voice_audience_*`   |
| `/voice-user-set <key>=<value>`  | short keys: `level`, `jargon`, `style`, `background`, `metaphors`, `domains`, `learning` — mapped onto `voice_audience_<key>` in profile.json |
| `/voice-user-clear`              | rm-only-if-set across all seven keys  |
| `/voice-user-preview [de\|en]`   | render the AUDIENCE block as it would land in the prompt |
| `/voice-user-help`               | field-by-field reference              |

The shared `/profile show` also lists the canonical keys (humanize() was
extended) so power users can manage them via either UI.

### What you, as Claude Code, must NOT do

- Don't put faithfulness or completeness rules into the AUDIENCE block.
  Same reasoning as Layer 9: those are global and live in `SYSTEM` /
  `SYSTEM_WITH_TASK`. The AUDIENCE block is allowed to *re-affirm* them
  (it does, on its last line), but never to override or weaken them.
- Don't widen the validator. The strict whitelist is the layer that
  keeps a malformed value from poisoning the prompt — every new value
  needs a corresponding entry in `_VOICE_AUDIENCE_*` constants.
- Don't move audience rendering into `for_system_prompt()`. That path
  fires on every reply (chat + TTS). Audience is TTS-only by design;
  paying the token cost on chat replies for content the user reads
  with their eyes is waste.
- Don't auto-save audience fields from LLM output. Profile changes
  only happen through slash-commands or explicit user confirmation —
  letting the model edit its own audience profile is a drift loop.
- Don't let the LEARNING ANNEX subtract from or reshape the summary.
  The clause is structurally **additive**: the annex appends after a
  faithful summary, never inside it. Removing the additivity guard
  (`ADDITIV` / `purely ADDITIVE`) or the faithfulness re-affirmation
  AFTER the annex would re-open the very faithfulness-vs-didactics
  drift the explicit ordering exists to prevent.
- Don't drop the "must be spoken aloud" instruction from the annex
  clause. Without it the LLM may emit the teaching content as
  chat-only metadata or markdown the TTS path strips — defeating the
  whole purpose of a *voice* learning mode.

## Layer 13 — `/btw <text>` mid-stream injection

Lets the user push a follow-up note into a *currently streaming* claude
subprocess instead of queuing it as a new turn. The classic case: "I forgot
to mention X — please also consider it" while Claude is still working on
the original request.

### How it flows

1. Daemon (`telegram/discord/slack/whatsapp/daemon.js`) recognises a
   message starting with `/btw ` (case-insensitive, body matched as
   `[\s\S]+` so multiline notes work) and writes a side-channel inbox
   envelope: `{from, chat_id, _btw: true, text, ts}`. WhatsApp gates on
   `m.key.fromMe`, the rest gate via the existing whitelist.
2. Adapter's main loop (`submit_inbox_item`) calls `_peek_side_channel()`
   on every inbox file. Side-channel envelopes (`_btw` and `_cancel`)
   bypass the per-chat lock — otherwise they would queue *behind* the
   very turn they want to talk to. Regular messages still acquire the
   lock as before.
3. `process_one` sees `_btw: true`, calls `inject_btw(chat_key, text)`.
4. `inject_btw` looks up the per-chat live stdin in
   `_running_stdins[chat_key]` and writes one stream-json `user`
   message line. ACK to outbox is one of: "📝 Notiz an den laufenden
   Task durchgereicht.", "Leere /btw — schreib z.B. …", or "Gerade
   läuft kein Task — schick deine Notiz als normale Nachricht …".

### Adapter changes for stream-json input

- `_build_claude_args(prompt_via_stdin=True)` returns `["claude", "-p"]`
  without the positional prompt arg.
- `call_claude_streaming` now spawns with `stdin=subprocess.PIPE` and
  `--input-format stream-json --output-format stream-json --verbose`,
  writes the initial prompt as one JSONL line, registers the stdin in
  `_running_stdins`, and registers the Popen in `_running_subprocs`
  (unchanged).
- On the first `result` event the adapter unregisters stdin and closes
  it so claude EOFs cleanly. A `/btw` racing past this point falls back
  to the normal queue path (and gets the "kein Task am Laufen" ACK).
- `_cancel_chat` also drops the stdin entry so an in-flight `/btw`
  doesn't write into a pipe whose owner is being SIGTERM'd.

### Per-subtask E2E

`shared/test_adapter_btw.py` covers the round-trip in 9 cases, including
the load-bearing E2E: a fake `claude` binary (Python script on a temp
PATH) that reads stream-json from stdin and emits assistant + result
events with a 1.5 s sleep between them. The test thread injects a `/btw`
during that window and asserts the second reply ends up as `final_text`.
Wired into `run-all-tests.sh`.

### What you, as Claude Code, must NOT do

- Don't try to inject after the first `result` event. The window is
  bounded by stdin-close on result; later `/btw` envelopes get the
  fallback ACK and that's the contract — the user can simply send the
  text as a normal message instead.
- Don't widen `_peek_side_channel` to `_reset` envelopes. `/reset` must
  serialize behind the running turn so the wipe doesn't race with a
  subprocess writing into the workdir.
- Don't add a "btw grace period after result". The simple "stdin closes
  on first result" rule is the entire reason the per-chat lock-bypass
  is safe — once result fires, there's nothing to inject into anymore.
- Don't bypass the `inject_btw` helper. The `_running_stdins_guard` lock
  is the only thing that prevents an inject racing with the streaming
  loop's stdin-close on result.

This change is **structural** — adapter spawn shape changed (stream-json
input, stdin pipe). After updating, `bash operator/bridges/bridge.sh
restart` is required for the running adapter to pick it up.

## Layer 13b — Discord-native slash-command registration

Discord's client-side slash-command picker blocks any `/<name>` input
with `"<name> isn't available in this environment"` when no application
command of that name is registered for the bot. The bridge's text-based
dispatcher in `daemon.js` never even sees the message.

`operator/bridges/discord/slash_commands.js` registers every
bridge command (`/btw`, `/stop`, `/reset`, `/voice-user-set`,
`/voice-on`, `/dialectic-*`, `/ldd-*`, `/profile`, `/memory`, `/vault`,
`/schedule`, …) as a CHAT_INPUT application command on `clientReady`.
Each command takes a single optional STRING option called `args`,
keeping the schema uniform across all 45 commands.

**Interaction → text round-trip.** `interactionToText(interaction)`
rebuilds the equivalent text payload (`/<cmd>` or `/<cmd> <args>`).
`daemon.js`'s new `interactionCreate` handler runs the same dispatch
chain as `messageCreate` — auth → rate → /stop|/cancel → /btw → shared
in-chat-cmds dispatcher → /debug → plain inbox. The user sees an
ephemeral "got it" in response to the interaction; adapter-routed
replies still arrive as normal channel messages via the outbox.

**Registration scope:**
- Default: global (`client.application.commands.set(COMMANDS)`) —
  covers DMs and every guild the bot is in. First-time propagation can
  take up to ~1 hour; subsequent updates are typically within a minute.
- Override: `DISCORD_GUILD_IDS=<id1>,<id2>` env var → per-guild
  registration (instant, useful during development).

**Idempotent.** `set(COMMANDS)` replaces the previous registration in
full — re-running on every boot never duplicates entries. Failures are
logged but never thrown: the text-path dispatcher still handles
copy-pasted `/<cmd>` messages when registration fails.

**What you, as Claude Code, must NOT do:**

- Don't move per-command logic into `slash_commands.js`. The whole point
  of the uniform `args`-string schema is that the existing
  `in_chat_commands.js` dispatcher stays the single source of truth for
  what each command does. Adding option-typed signatures (booleans,
  enums, sub-commands) per command would re-introduce divergence
  between the text-path and the interaction-path.
- Don't make `registerCommands` throw on failure. The
  `try/catch + log` swallow is intentional — a failed registration
  must not bring the daemon down, because the text-path still works
  for users who escape the picker (paste, leading non-`/` char, etc.).
- Don't switch the interaction-reply to `ephemeral: false`. The
  ephemeral ack keeps the channel clean; the real adapter response
  comes through the outbox as a regular channel message.

This change is **structural** — `slash_commands.js` is loaded at
daemon boot and `interactionCreate` is wired in. After updating, run
`bash operator/bridges/bridge.sh restart` so the live Discord
daemon picks up the registration. First-time global propagation may
take up to an hour; set `DISCORD_GUILD_IDS=<id>` for instant per-guild
registration in your test guild.

## Layer 14 — LDD-Toggle-System (per-layer enable/disable)

Layer 11 (`/dialectic-on/off`) toggles a single LDD discipline. Layer 14
generalises that pattern: every load-bearing LDD layer can be flipped on
or off independently — globally or per chat — and the effect is
**structural** (skill injection, dialectic gates, future native sites
all consult the same gate function), not just a documentation change.

### Layer set

Twelve canonical IDs in `bridges/shared/ldd.py::LAYERS`:

```
loop_driven_engineering, e2e_driven_iteration,
dialectical_reasoning, dialectical_cot,
root_cause_by_layer, docs_as_dod,
reproducibility_first, loss_backprop_lens,
method_evolution, drift_detection,
iterative_refinement, per_subtask_e2e
```

`using_ldd` is the bootstrap entry-point and intentionally NOT in the
toggle set — disabling it would break every other layer's dispatch.
Skill-name canonicalisation (lower-case, plugin-prefix strip,
hyphen→underscore, alias-map) is owned by `layer_for_skill_name()`;
the alias `docs_as_definition_of_done → docs_as_dod` is the only
non-mechanical mapping today.

### Storage

Single file `<scope_root>/global/ldd.json` with mtime-cache, written on
first call (best-effort — read-only FS falls back to ephemeral
defaults). Default = master ON, every layer ON. Same file convention as
`dialectic.json`, same load/save dance.

### Resolution order (`ldd.is_layer_active(layer, profile=…)`)

The function computes a *direct state* (the layer's own toggle resolution)
and then applies the *cascade gate* (does the parent permit it?).

Direct state — same chain as before cascade existed:

1. `profile.ldd_layers[layer]` per-chat override (bool).
2. `profile.ldd_enabled = False` per-chat master kill.
3. `cfg.enabled = False` global master kill.
4. `cfg.layers[layer]` global per-layer toggle.
5. Default `True` (fail-open for unknown layer IDs too — a typo on the
   command line never silently disables an integration site).

Cascade gate — applied AFTER the direct state, only when the layer has
a parent in `DEPENDS_ON`. If the parent is inactive (recursively), the
child is forced inactive even when the direct state would say on.

### Hard-cascade dependencies (`DEPENDS_ON`)

Three pairs are wired structurally — running the child without the
parent produces silent no-op work, not just suboptimal work:

| Child | Parent | Why |
|---|---|---|
| `dialectical_cot`  | `dialectical_reasoning` | CoT *is* SGD-on-thoughts with dialect steps — without dialectical_reasoning the step mechanism is undefined. |
| `per_subtask_e2e`  | `e2e_driven_iteration`  | Per-subtask-E2E is a hardening rule on the E2E loop; without the loop there is nothing to harden. |
| `drift_detection`  | `docs_as_dod`           | drift_detection scans docs as source-of-truth; without docs-as-DoD the doc base isn't reliable enough for the signal. |

Soft couplings (loop_driven_engineering ↔ e2e/root_cause/docs_as_dod,
method_evolution ↔ any-discipline, root_cause_by_layer ↔
reproducibility_first, …) are intentionally **not** in `DEPENDS_ON` —
they get a one-line warning at `/ldd-set` time but no structural
gate. The rule of thumb for adding a new entry: (1) the child is
conceptually a refinement of the parent, AND (2) running the child
without the parent does silent no-op work (not just suboptimal work).

### Cascade is read-path, not write-path

`set_layer(child, True)` while the parent is off **persists the
on-state** but the read-path resolves the child to inactive via the
cascade gate. Flipping the parent back to on auto-reactivates the
child without a second toggle. Rationale: the operator's intent stays
visible in `/ldd-status` and `ldd.json`, and there's no hidden state
to forget about. Same principle as the existing
`profile.dialectic_mode_<site>` ↔ master-toggle relationship — the
write captures intent, the read resolves precedence.

### Cascade beats explicit child override

`profile.ldd_layers[child] = True` cannot lift the cascade. The
operator must lift the parent (per profile or globally) to reactivate
the child. This is the one place where the explicit-beats-master
precedence is intentionally reversed — `dialectical_cot` without
`dialectical_reasoning` is sinnlos by definition, and an "explicit
override" of a sinnlos combination shouldn't silently succeed.

### `effective_state(layer, profile=…)` for diagnostics

Returns `(active, reason)` where reason is one of:
`"on"`, `"manual_off"`, `"profile_master_off"`, `"global_master_off"`,
`"cascade_off:<parent>"`. Used by `/ldd-status` to show the
most-specific reason a layer is off, and by `/ldd-set` to fire the
warning when a parent is currently down.

### Per-persona LDD profiles (cowork resolver integration)

Three optional fields per persona JSON tune the LDD discipline to the
persona's actual job profile:

```jsonc
{
  "name": "research",
  "ldd_preset":  "quick",                       // basis from PRESETS
  "ldd_layers":  {"reproducibility_first": true}, // delta on top
  "ldd_enabled": true                           // optional master override
}
```

Resolution order (in `cowork/lib/resolver.py::_resolve_ldd_section`):

1. `persona.ldd_preset` → expand via `ldd.PRESETS` into a layers dict.
2. `persona.ldd_layers` → shallow-merge on top (delta wins per layer).
3. `chat_profile.ldd_layers` → shallow-merge on top of (1+2).
4. Master flag: `chat_profile.ldd_enabled` > `persona.ldd_enabled` >
   preset-derived (`"off"` → false, anything else → true).
5. **Special rule for chat-master kill**: when
   `chat_profile.ldd_enabled is False`, persona-injected per-layer
   entries are **dropped** — only chat-explicit per-layer entries
   survive. Rationale: a user typing `/ldd-off` (or its per-chat
   equivalent) expects "kill" to actually kill, not be subverted by
   a persona-shipped `dialectical_reasoning: true` that would
   otherwise beat the master via the explicit-per-layer-precedence rule.

The merged `ldd_layers` / `ldd_enabled` land directly on the resolved
profile dict. The bridge adapter passes that profile to
`ldd.is_layer_active(profile=…)` unchanged, so the cascade gate from
the previous section applies on top.

### Bundle-persona LDD defaults

**Default-OFF policy (since 2026-05-11):** every bundle persona ships
with `ldd_preset: "off"`. The soft LDD discipline does NOT inject by
default. Hard structural enforcement (forge sandbox, skill-forge
linter, path-gate, audit hash-chain, promotion gates, MCP-server) lives
OUTSIDE the LDD toggle system and stays active regardless of these
defaults.

| Persona | Preset | Delta | Active layer count |
|---|---|---|---|
| `coder` | off | — | 0 / 12 |
| `forge` (Tools + Skills) | off | — | 0 / 12 |
| `research` | off | — | 0 / 12 |
| `assistant` | off | — | 0 / 12 |
| `inbox` | off | — | 0 / 12 |
| `homeassistant` | off | — | 0 / 12 |
| `os` | off | — | 0 / 12 |
| `orchestrator` | off | — | 0 / 12 |

Operators who want LDD-discipline active for a specific chat opt in via
`chat_profile.ldd_layers` (per-layer) or `chat_profile.ldd_enabled = True`
plus per-layer overrides in `bridges/<channel>/settings.json`. The
global default in `ldd.py::_default_config()` is also off — flipping
the global master on via `/ldd-on` or per-chat overrides is the only
path to re-enable.

**Why default-off:** soft LDD relies on prompt-injection of skill
bodies plus model self-discipline; the value it adds is real but
indirect (it catches verification-theatre, symptom-patches and
overfit-on-one-test, not erfundene API-calls or library-existence
claims). The hard structural gates already cover the load-bearing
safety surface; soft LDD on top is opt-in for code-with-tests
workflows where the discipline clearly pays off.

### Override-stack visibility

`/whoami` shows the persona's declared LDD config in a single line
(`LDD: preset=quick master=on +reproducibility_first`); `/ldd-status`
shows the effective state on the global config side. Per-chat
overrides via `chat_profile.ldd_layers` / `ldd_enabled` are not
surfaced by `/whoami` directly — operators inspect them via the
bridge `settings.json` or by combining the two outputs.

### What you, as Claude Code, must NOT do (persona layer)

- Don't invent a fourth merge axis. The two-source model
  (persona vs. chat_profile) is the contract — adding a third
  (e.g. "user-global LDD profile") breaks the
  "kill should actually kill" rule that motivates the chat-master
  special case.
- Don't move LDD fields into `_LIST_FIELDS` or `_DICT_FIELDS` of
  the resolver. They go through `_resolve_ldd_section`, which
  understands preset semantics. A naive shallow-merge on
  `ldd_layers` would lose the chat-master-kill drop-rule.
- Don't add a persona without picking an `ldd_preset`. The default
  (no field) means "every layer on" — fine for code-generation
  personas, surprising for action-personas. If a new persona doesn't
  do engineering work, give it `"ldd_preset": "off"` (or `"quick"`)
  explicitly so future readers see the intent.

### Native integration sites

| Site                                          | Effect when layer is OFF                                      |
|-----------------------------------------------|---------------------------------------------------------------|
| `skill_inject.collect_active_skills`          | Skills whose name maps to the OFF layer are filtered out      |
| `skill_inject.auto_grade_from_output`         | Same filter — no auto-grade for OFF layers                    |
| `dialectic.resolve_mode(...)` (Layer 11)      | Every site degrades to `mode=off` when `dialectical_reasoning` is OFF |

The dialectic-gate is layered AFTER explicit per-site profile overrides,
so `profile.dialectic_mode_<site>: "fast"` still wins over the LDD
master gate. This mirrors the existing `profile.dialectic_enabled =
False` / `cfg.enabled = False` precedence — explicit-opt-in beats
master-gate.

### Slash-commands (in `bridges/shared/js/in_chat_commands.js`)

```
/ldd-status                    # show master + per-layer state
/ldd-on                        # global default-on
/ldd-off                       # global kill-switch (every layer off)
/ldd-set <layer> <on|off>      # per-layer toggle, hyphen-form accepted
/ldd-preset <name>             # default | strict | quick | off
```

The `quick` preset keeps only the load-bearing gates that catch shipped
regressions (`e2e_driven_iteration`, `docs_as_dod`, `per_subtask_e2e`,
`dialectical_reasoning`) and turns the prompt-injection-heavy
disciplines off — useful for fast exploratory sessions where the full
discipline overhead is too much.

### Cost contract

`ldd.py` MUST NOT import the Anthropic SDK. Mirror of dialectic.py's
contract — every decision is local file-IO + dictionary lookups,
sub-millisecond. The CI lint in `test_ldd_lib.py::case_no_anthropic_sdk_import`
enforces it via AST walk.

### Hot-reload

Same mtime-cache pattern as `dialectic.json`: a slash-command write
flips the on-disk state, and the next inbox message picks it up
without restart. No daemon-side caching — `is_layer_active()` is
called fresh per skill-inject and per dialectic decision.

### What you, as Claude Code, must NOT do

- Don't flip the default in `ldd.py::_default_config()` back to
  `enabled: True` / layers-all-True without an explicit operator
  decision. The default-OFF policy (2026-05-11) is the published
  contract; flipping it silently re-enables prompt-injection for every
  fresh tenant + every bundle persona on the next boot. If an operator
  wants LDD on by default for their installation, they edit their
  `ldd.json` in `<corvin_home>/global/` or set per-chat overrides — the
  bundle default stays off.
- Don't set a bundle persona's `ldd_preset` to anything other than
  `"off"` without an explicit decision recorded in this section. The
  bundle is the curated baseline for every fresh tenant; bringing back
  "quick" or "default" per-persona re-introduces silent LDD activation
  on chats that didn't opt in.
- Don't add `import anthropic` to `ldd.py`. The CI lint rejects it via
  AST and the cost contract depends on it.
- Don't introduce a new layer ID without a matching entry in `LAYERS`
  AND a corresponding `PRESETS` decision for `quick` (the other
  presets default by definition: every layer in / every layer out). A
  layer that is in `LAYERS` but missing from `PRESETS["quick"]` will
  silently default-off when the user picks `quick`.
- Don't add a `DEPENDS_ON` entry for "soft" couplings — only the
  three sanctioned hard-cascade pairs belong there. Use the
  `/ldd-set` warning mechanism for soft hints if needed.
- Don't introduce a `DEPENDS_ON` cycle. The recursive resolver in
  `is_layer_active` would infinite-loop;
  `test_ldd_dependencies.py::case_no_cycles_in_dependency_graph`
  catches it via tortoise-walk if it ever lands.
- Don't fail-closed on unknown layer IDs in `is_layer_active()`. A
  typo'd integration-site name must never silently disable that
  site — it's safer for the gate to no-op than to silently mute a
  load-bearing discipline.
- Don't move the LDD gate ABOVE the explicit per-site/per-profile
  overrides in `dialectic.resolve_mode`. Operators who set a specific
  mode for a single site are saying "this one matters" and that
  signal must not be overridden by the master gate.
- Don't write LDD state from any path other than the `/ldd-*`
  slash-commands or the `ldd.set_*` API. The path-gate hook does NOT
  cover `ldd.json` (it's not a forge / skill-forge artifact); the
  contract here is "operator-only via slash-commands or API".

## Layer 23 — Speech-to-Text (engine-agnostic, pluggable providers)

STT is the **boundary layer** between bridge voice-notes and any
WorkerEngine (Layer 22). Engines never see audio — they see the
transcript. Multi-engine deployments (Claude Code / Codex CLI /
Gemini CLI / future engines) all share the same STT path.

**Where it lives:** `operator/voice/scripts/stt/` — a small package
with a Protocol + concrete providers + resolver. `transcribe.py` is
now a thin CLI wrapper that delegates to the package.

```
operator/voice/scripts/stt/
├── __init__.py
├── base.py            # STTProvider Protocol + TranscriptResult + STTError tree
├── openai_whisper.py  # OpenAI Whisper-1 (default, ~0.006 $/min)
├── local_whisper.py   # pywhispercpp/whisper.cpp (air-gap / EU-residency fallback;
│                       # ADR-0185 M1 — cross-platform, incl. Windows; faster-whisper
│                       # kept as an opt-in CORVIN_STT_LOCAL_ENGINE=faster-whisper path)
└── resolver.py        # chain selection + per-provider fallback
```

**Resolution order:**

1. `CORVIN_STT_PROVIDER=<name>` env → pin one provider. **No
   fallback** — fail-loud if unavailable. This is the operator-
   override path for policy enforcement (e.g. "this tenant must
   never use OpenAI").
2. Else `CORVIN_STT_CHAIN=<n1>,<n2>` → operator-supplied chain.
3. Else default chain: `local → openai`.

The chain falls through on `STTProviderUnavailable` and
`STTTranscriptionFailed`. It does **NOT** fall through on
`STTTimeout` — multiplying user wait time by retrying a slow
provider is the wrong default.

**Provider contract** (`STTProvider`):

| Method | Purpose |
|---|---|
| `name` | Identifier used in audit events + env overrides |
| `is_available() -> bool` | Cheap probe; no paid API call; checks env + import + local resources |
| `transcribe(audio_path, *, lang=None, timeout_s=None) -> TranscriptResult` | The real work |

`OpenAIWhisperProvider._resolve_api_key()` checks `CORVIN_STT_OPENAI_KEY`
/ `OPENAI_API_KEY` in `os.environ` first, then falls back to reading
`~/.config/corvin-voice/.env` or `service.env` directly (mirrors
`say.py`'s TTS key resolution). This matters because `bridge.sh` /
`voice_lib.sh` export the key into the shell before Python starts on
Linux/macOS, but `bridge.ps1` on Windows launches the console/daemon
directly with no equivalent `.env`-loading step — without the file
fallback, `os.environ` is empty there even when the key is configured,
and STT fails with `no STT provider available` (2026-07-06 Windows-10
incident).

`TranscriptResult` carries `text` (the only PII-bearing field),
`provider`, `lang`, `duration_s`, and a derived `chars`. The audit-
event emission uses only the metadata fields, **never** `text`.

**Audit-chain integration** — two new event types registered in
`forge/security_events.py::EVENT_SEVERITY`:

| Event | Severity | Details |
|---|---|---|
| `voice.transcribed` | INFO | `provider`, `lang`, `audio_s`, `wall_clock_s`, `chars` (NEVER `text`) |
| `voice.transcribe_failed` | WARNING | `reason` (`timeout` / `provider-error` / `package-unreachable`), `error` (200-char cap), `wall_clock_s` |

Both events ride through `_audit_event` in `adapter.py`, which is
already in the unified hash chain — `voice-audit verify` covers
them automatically. Cross-references with `bridge.message_received`
via `msg_id` so an operator can trace a voice-note end-to-end.

**Phase-6 metric families** (added to `audit_metrics.py`):

| Metric | Type | Labels |
|---|---|---|
| `corvin_voice_transcribed_total` | counter | `stt_provider` |
| `corvin_voice_transcribe_failed_total` | counter | `reason` |

`stt_provider` allow-list: `openai`, `local`. Reason allow-list
extended with `timeout`, `provider-error`, `package-unreachable`.

**Operator knobs:**

| Env var | Effect |
|---|---|
| `CORVIN_STT_PROVIDER` | Pin one provider (no fallback) |
| `CORVIN_STT_CHAIN` | Override default chain, e.g. `local,openai` |
| `CORVIN_STT_LOCAL_MODEL` | pywhispercpp GGML model name (`tiny-q5_1` / `base` / `base-q5_1` / `small` / `medium` / `large-v3`, ...); default `tiny-q5_1` (~31 MB, Q5_1-quantized) |
| `CORVIN_STT_LOCAL_ENGINE` | Opt-in: `faster-whisper` switches the `local` provider to the legacy CTranslate2 engine when it's installed (`corvinos[voice]` extra); never the default |
| `BRIDGE_TRANSCRIBE_TIMEOUT` | Per-provider budget in seconds (unchanged from pre-Layer-23) |

**Per-tenant policy hook** (future): `tenant.corvin.yaml` has the
`spec.stt.provider` slot reserved for "this tenant must use provider
X" enforcement, mirroring engine-policy (Phase 3.2) and zone-policy
(Phase 3.3). Phase-6.x will wire that gate; today the operator
expresses the same intent via the env var on the bridge process.

**Test surface** (`operator/voice/scripts/test_stt.py`, 35 cases):
- TranscriptResult chars math
- Real providers satisfy the Protocol
- Pinned provider used; pinned-but-unavailable raises (no fallback)
- Unknown pinned provider name raises
- Chain: first unavailable falls through; first broken falls through
- Chain: timeout does NOT fall through
- Chain: all unavailable raises with diagnostic
- Chain: unknown names silently skipped
- `available_providers()` lists only reachable
- CLI: exit 1 on no provider; exit 1 on missing file
- No-PII contract enforced via structural source check on
  `_emit_transcribe_ok` — fails the test if a future edit
  reintroduces `result.text` into the audit details
- **`LocalWhisperPywhispercppTests`** (ADR-0185 M1): a REAL, non-mocked
  round trip — downloads the `tiny-q5_1` GGML model (~31 MB, cached under
  a fixed OS-temp test dir) and transcribes a real speech fixture
  (`operator/voice/scripts/fixtures/stt_sample.wav`), asserting the
  actual recognized text, detected language, lang-hint honouring,
  missing-file error, and timeout behavior. Skipped only when
  `pywhispercpp` itself isn't importable — never mocked.
- `LocalWhisperMissingPackageTests` / `LocalWhisperEngineSelectionTests`:
  `STTProviderUnavailable` when `pywhispercpp` is unimportable (simulated
  via `sys.modules` patching); `CORVIN_STT_LOCAL_ENGINE` opt-in switch
  logic.

Installer step tests: `tests/test_installer_stt.py` (hermetic, mocked
network) — `ensure_stt()` package-presence branching + `_download_whisper_model()`'s
already-present / success / network-failure / empty-result paths.

### What you, as Claude Code, must NOT do (Layer 23)

- **Don't put the transcript content into any audit-event field.**
  The `test_audit_event_only_carries_metadata` case is the regression
  gate; it scans `_emit_transcribe_ok` for `result.text` and fails
  the suite if found. DSGVO baseline.
- **Don't fall back through the chain on `STTTimeout`.** The chain
  is for provider-side problems, not for "user waited 60 s and
  nothing came back" — retrying a second provider on top doubles
  the wait. The user gets a clean "konnte nicht transkribieren"
  reply, the audit chain records the timeout with reason, and the
  operator sees the cadence in `corvin_voice_transcribe_failed_total{reason="timeout"}`.
- **Don't add a provider that calls into the WorkerEngine.** The
  whole point of Layer 23 is that STT runs BEFORE engine spawn —
  an engine-mediated provider would re-introduce the engine
  asymmetry the layer exists to eliminate. Multimodal audio in
  the engine layer is a separate decision (out of scope for Layer
  23).
- **Don't widen the `stt_provider` Prometheus label.** Provider
  names are intentionally short and curated. Free-form provider
  names would let cardinality grow per-engine-version
  ("openai-1", "openai-2", ...) and saturate the metrics surface.
- **Don't make `is_available()` make a paid API call.** It runs on
  every resolver pass (every voice note). The probe is allowed to
  check env vars, import the SDK, look at a model file on disk —
  nothing that costs money or wakes a sleeping GPU.
- **Don't drop the `STTTimeout` distinction.** The three exception
  classes (`Unavailable`, `Timeout`, `TranscriptionFailed`) drive
  three different downstream behaviours; collapsing them into a
  single `STTError` removes the chain's ability to make the right
  fallback / surface decision.
- **Don't bypass the audit emission.** The
  `transcribe_audio()` wrapper in `adapter.py` is the only
  sanctioned callsite for the STT package from the bridge. Direct
  imports from elsewhere in the adapter would bypass the audit
  hook + the context-carrying `audit_context` kwarg. Future call
  sites add an `audit_context` argument or wrap through
  `transcribe_audio`.
- **Don't add `OPENAI_API_KEY` to the audit details on failure.**
  The `error` field is 200-char-capped; an SDK exception that
  embeds the key in its message would land in the chain. Two
  safeguards: the 200-char cap, and the curated `reason` field
  that operators look at first.
- **Don't drop the back-compat CLI shape of `transcribe.py`.**
  `listen.sh` (the mic-record-and-transcribe slash command) calls
  it as `transcribe.py <audio> --lang <hint>`. The new `--provider`
  / `--timeout-s` flags are additive; removing or renaming the
  pre-existing flags breaks the voice-listen path on the operator's
  desktop. The `test_cli_returns_1_when_no_provider` /
  `test_cli_returns_1_on_missing_file` cases pin the exit-code
  contract; rewire them in the same commit if you must change
  shapes.

**References:**
- `operator/voice/scripts/stt/` — package
- `operator/voice/scripts/transcribe.py` — CLI wrapper
- `operator/voice/scripts/test_stt.py` — 15-case E2E
- `operator/bridges/shared/adapter.py::transcribe_audio` —
  in-process integration site
- `core/gateway/corvin_gateway/audit_metrics.py` — two
  new metric families.

