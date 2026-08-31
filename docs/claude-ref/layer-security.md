# Security Hardening Reference (Layer 10 + Layer 16)

> Load when working on security hardening, path-gate, auth, consent, or observer-transcript code.
> Quick summary in CLAUDE.md § Layer 16 and § Path-Gate Hook.

## Layer 16 — Security hardening (Phase 1 + 2 + 3 + 4 + Roadmap F13/J/K/L)

Hardenings derived from a dialectical security review of layers 1–14.
Phase 1 covers four no-functional-impact changes (C, G, H, F). Phase 2
adds the `read_only` role split (capability gate), `path-gate v2`
(extended Bash detection), and `slot-mirror scope-gate` (engine
visibility limited to project+ scope). Phase 3 adds `loopback-deny`
(sitecustomize shim refusing 127.0.0.0/8 / ::1 / IMDS even with
`--share-net`) and `PIN-elevation` (`/auth-up <pin>` for time-bounded
elevation). Roadmap items now landed: F13 (path-gate boot self-test),
J (dialectic rate-limit), K (TTS-key XDG migration), L (daily-verify
systemd timer with bridge-notification on chain break). F12 (shared
test fixtures) and I (MCP-server netns isolation) remain deferred —
non-critical, separate session.

### Read-only role (Phase 2 — capability split)

The historical whitelist is a **binary** capability gate: a uid is
either an owner with full bot privileges or silently dropped. In
group chats with multiple human readers (family, team, workshop
audience) that conflates two orthogonal axes — *who may sit in the
chat* vs. *who may drive the bot*. The `read_only` list separates
them.

Per channel `bridges/<channel>/settings.json`:

```jsonc
{
  "whitelist":  ["+49xxx-OWNER"],     // full access
  "read_only":  ["+49yyy-MITLESER"],  // may be in chat, must NOT trigger
  // anyone else: silent drop, unchanged
}
```

**Resolution order** (`shared/js/auth.js::classify`):

1. Empty whitelist → DEV mode, everyone classifies as `owner`
   (mirrors legacy `authOk` fail-open).
2. uid on whitelist → `owner`.
3. `audience='all'` on the chat profile → `owner` (chat-open
   trumps read_only on that single chat).
4. uid on `read_only` → `read_only`.
5. otherwise → `unknown` (legacy whitelist_deny path still applies).

Whitelist beats `read_only` on collisions — an operator who
typo's the same uid into both lists keeps owner privileges. The
adapter's `_inbox_sender_authorized` mirrors the same precedence
for TOCTOU consistency.

**Daemon flow** (all four bridges, before `authOk`):

```
const ro = readOnlyOk(uid, text, chatKey);
if (ro.isReadOnly) {
  if (ro.firstDrop) await reply('🔒 read-only ACK ...');
  return;  // never writes to inbox
}
```

`firstDrop` is in-memory tracked per `(chatKey, uid)`. The first drop
sends the polite ACK; subsequent drops are silent. Daemon restarts
re-arm the ACK — acceptable, restarts are rare and the ACK is
harmless.

**Audit** — every drop emits `bridge.read_only_drop` (severity
`WARNING`) into the unified hash chain with `details.first_drop`,
`details.snippet` (200-char cap, mirrors `/btw` forensic-cap), and
`details.truncated`. A read-only user repeatedly attempting to drive
the bot shows up as a pattern under `voice-audit verify`.

**Adapter TOCTOU** — `_inbox_sender_authorized` returns
`(False, "read-only-drift")` when the sender is on `read_only` but
not on `whitelist`. This catches the case where the operator demoted
a user between daemon-write and adapter-read; the envelope is dropped
exactly like a whitelist drift, and the daemon-side gate stops future
messages.

**Per-subtask E2E:**
- `shared/js/test_auth_read_only.js` — 28 cases over `classify` /
  `readOnlyOk`, including first-drop tracking, second-drop silent ACK,
  per-(chat,uid) reset across chats, whitelist-beats-read_only
  collision, audience='all' bypass, and chain integrity via
  `voice-audit verify`.
- `shared/test_adapter_security_hardening.py::C2/1+C2/2` — adapter
  drift-drop returns `read-only-drift`, collision returns
  `whitelisted`. Wired into `run-all-tests.sh` as the `Node:
  read-only gate` suite.

**What you, as Claude Code, must NOT do:**

- Don't reverse the precedence to "read_only beats whitelist". The
  current rule prevents an operator from accidentally locking
  themselves out by listing the same uid twice; reversing it turns
  every typo into a self-DOS.
- Don't make the first-drop ACK persistent across daemon restarts.
  In-memory tracking is intentional — the cost of a polite ACK on a
  restart is zero, the cost of carrying a stale "already acked" set
  across deploys is operational confusion when the operator
  re-onboards a read-only user.
- Don't widen the snippet cap above 200 chars, mirror the `/btw`
  forensic-cap exactly. The audit chain is for recognising patterns,
  not for storing chat history.
- Don't move the `readOnlyOk` gate AFTER `authOk`. authOk would
  emit a `bridge.whitelist_deny` for a read_only sender — the
  capability classification belongs first, and the audit-event
  type carries the role intent.
- Don't add a third "auditor" or "guest" role without redoing the
  resolution table here, the adapter TOCTOU handling, and the test
  matrix. Adding silent classifications without test coverage is the
  fast path to a defense-in-depth that nobody can reason about.

### Observer-Transcript (Phase 2 — visibility-vs-capability)

The `read_only` role separates *trigger authority* from *chat membership*.
By default it goes one step further: a read-only sender is invisible to
the LLM — their text is dropped before the inbox. For Familien- /
Workshop-Settings where the LLM should *see* what the observers say
without letting them *trigger* the bot, set the per-chat profile flag:

```jsonc
{
  "chat_profiles": {
    "<chat-id>": {
      "observer_visibility": "transcript"   // default: "off"
    }
  }
}
```

**Flow** (`shared/js/in_chat_commands.js::getObserverVisibility` →
daemon → adapter):

1. Daemon's `readOnlyOk` returns `isReadOnly: true`.
2. Daemon checks `getObserverVisibility(chatKey)`. If `"transcript"`,
   it writes a side-channel envelope `{_observer: true, text, from, ts}`
   into the inbox **instead of** dropping. Silent on the daemon side
   (no ACK message sent to the observer).
3. `_peek_side_channel` recognises `_observer` as a per-chat-lock
   bypass alongside `/btw` and `/cancel`.
4. `process_one` validates the sender is *currently* still on
   `read_only` (TOCTOU: a sender who was promoted to whitelist or
   removed from read_only between daemon-write and adapter-read is
   dropped with `bridge.inbox_whitelist_drift` reason
   `"observer-not-read-only"`). Otherwise the line is appended to
   `<voice_session_dir>/observers.jsonl` via
   `_append_observer_message`. Audit: `bridge.observer_appended`.
5. On the next OWNER turn (any normal text inbox), `process_one`
   atomically reads-and-clears the buffer via
   `_consume_observer_buffer` and prepends a clearly framed block via
   `_format_observer_block` to the prompt before claude is called.
   Audit: `bridge.observer_transcript_consumed` (entries count).

**Buffer caps** (defaults, override via env):

- `ADAPTER_OBSERVER_BUFFER_MAX_LINES` = 20
- `ADAPTER_OBSERVER_BUFFER_MAX_BYTES` = 4096
- `ADAPTER_OBSERVER_LINE_MAX_CHARS` = 500 (per-line truncation)

Oldest entries fall out first.

**Framing block** — the only structural barrier between an observer's
text and the LLM treating it as instruction. Hard-coded in
`_format_observer_block`:

```
---BEGIN-OBSERVER-<session_token>---
  HH:MM <sender>: <text>  ← newlines in text replaced with " ↵ "
  HH:MM <sender>: <text>
---END-OBSERVER-<session_token>---

<actual owner message>
```

The `session_token` is a random 16-char hex string generated once at adapter startup (`secrets.token_hex(8)`). Because it is unknown to observers who join mid-session, they cannot forge the framing delimiter. Observer text has newlines (`\n`, `\r`) replaced with the visible substitute ` ↵ ` before insertion — this prevents a crafted message from injecting content outside the framing block (V-005 in ADR-0072).

Prompt-injection through embedded "ignore previous instructions"
patterns is not made impossible by the framing — LLMs can still be
fooled — but the structural marker gives the model a clean signal
that the lines are observation, not directive. The forensic audit
event keeps a 200-char snippet per observer line for after-the-fact
review.

**Per-subtask E2E** in
`shared/test_adapter_security_hardening.py::C3/{1,2,3,4}`:

- C3/1: observer envelope → buffered + audit emitted, no outbox reply.
- C3/2: drift drop when sender no longer on `read_only`.
- C3/3: buffer cap (line count + byte count, oldest dropped first).
- C3/4: owner turn consumes buffer, prepends framed block in correct
  order, clears buffer, emits `observer_transcript_consumed`.

JS contract test: `shared/js/test_observer_visibility.js` (7 cases,
including default-profile fallback and invalid-value fail-open).

**What you, as Claude Code, must NOT do:**

- Don't make `observer_visibility = "transcript"` the global default.
  The legacy contract is "read-only = invisible"; flipping that
  silently turns every Familien-Chat into one where every observer
  message lands in the LLM context. Opt-in per chat is the only
  safe activation path.
- Don't drop the framing block or the UUID session token delimiter.
  The session-token-keyed delimiters (`---BEGIN-OBSERVER-<tok>---` /
  `---END-OBSERVER-<tok>---`) prevent an observer from forging the
  delimiter boundaries. The static text variant was replaced by
  ADR-0072 V-005 to close the newline injection vector.
- Don't make the buffer multi-shot. `_consume_observer_buffer`
  *atomically* clears the buffer file (unlink-after-read). A
  multi-shot buffer that prepended the same lines on every turn
  would compound the prompt-injection risk *and* burn tokens.
- Don't widen the per-line char cap above 500. The whole point of
  the transcript is that it's *ambient* — long pastes aren't
  ambient, they're directives in disguise.
- Don't include `_observer` envelopes in the periodic
  session-timeout sweep without thinking about ordering. The buffer
  lives at `<session_dir>/observers.jsonl` and is implicitly cleared
  by the day-30 `session.timeout` reset (the whole session_dir gets
  rmtree'd). That's intended; an explicit hourly buffer-only
  cleanup would race with the consume-path.

### C — Inbox re-validation (TOCTOU defense)

`adapter._inbox_sender_authorized(channel, sender, chat_key)` is
called from `process_one()` BEFORE any other handling. It re-loads
`bridges/<channel>/settings.json` and re-checks the sender against
the current whitelist; on `audience: "all"` chat profiles the check
is bypassed (mirrors the daemon-side gate).

The daemon already filtered at write time, but the user may have
edited the whitelist between daemon-write and adapter-read. A drift
hit drops the inbox file with a `bridge.inbox_whitelist_drift`
WARNING audit event (msg_id + reason in details). Empty / missing
whitelist => fail-open (legacy behaviour preserved). When whitelist
is absent from channel settings, a WARNING is logged to the
`corvin.adapter` logger on every message:
`"channel X: no whitelist configured — all senders accepted"`. This
makes the fail-open legacy behavior observable to operators without
changing it (ADR-0072 V-012). The `bridge.sh doctor --strict` mode
also flags missing whitelists.

### G — `/btw` audit body snippet

The existing `bridge.btw_inject` audit event now carries a verbatim
`snippet` (200-char cap) and `truncated` flag in `details`, in
addition to `delivered` and `len`. Without the body, a hostile
mid-stream injection would only show up as a length number; the
snippet gives forensic recoverability without leaking the full body
when it's a long paste.

### H — Audit chain boot health check

`audit.audit_health_check()` runs once on adapter boot, calls
`security_events.verify_chain()`, and on integrity failure emits a
CRITICAL `audit.chain_gap_detected` event WITHOUT `hash_chain=True`.
The out-of-band write is deliberate: a broken chain would otherwise
prevent the very mechanism we use to record the corruption.

The event lists the first 20 problems (line + issue) and
`problem_count` so operators see the failure on the next
`voice-audit verify` run rather than on manual inspection only.
Daily systemd-timer with a Bridge-notification on chain break is
**Roadmap** (not in Phase 1).

### F — Linter NFKC + confusable normalisation

`skill_forge.linter.lint()` now NFKC-normalises the body and folds a
curated set of cyrillic confusables (а/е/о/р/с/у/х/і/ј and uppercase
variants) to their Latin look-alikes BEFORE substring / regex
matching. The original body is stored unchanged; only the match
input is transformed. Closes the homoglyph-bypass class for the
prompt-injection / persona-boundary detectors.

False-positive risk is essentially zero: NFKC is lossless for ASCII,
and the confusable map only collapses pre-existing look-alike pairs.

### Phase 2A — Path-Gate v2 (extended Bash detection)

`operator/voice/hooks/path_gate.py` extends the v1 deny-vectors with:

| Vector | What | Behaviour with protected hint |
|---|---|---|
| `eval` / `exec` | shell metaprogramming | fail-closed |
| `$(...)` / backticks | command substitution | fail-closed |
| `>(...)` | bash process substitution | fail-closed |
| `bash -c` / `sh -c` | recursive shell | fail-closed |
| `xargs` | deferred substitution | fail-closed |
| `awk -i inplace` | in-place rewrite | fail-closed |
| heredoc `<<EOF` | unparseable body | fail-closed |
| unbalanced quotes | shlex broken + hint | fail-closed |
| `mkfifo` + hint | named pipe creation proxying write | fail-closed |
| `exec N>` redirect | file-descriptor-based write to path | fail-closed (path extracted or hint present) |
| `>(...)` write side | process substitution write target | fail-closed |
| `$(...) \|` pipe position | command substitution in write-adjacent pipe | fail-closed |

Each vector is paired with a "benign use" allow-case in
`hooks/test_path_gate.py` so the gate doesn't deny legitimate
commands that happen to mention `forge` / `skill-forge` / `audit.jsonl`
/ `policy.json` outside a write context. False-positive denies on
heredocs / xargs that contain the literal word "forge" without any
write intent are accepted cost — the alternative (let the deny slip
on a recursive-shell trick) is structurally worse.

### Phase 2B — Slot-mirror scope-gate (cross-chat leak prevention)

`SkillRegistry.create()` only writes the engine-facing slot mirror
(`operator/skill-forge/skills/dyn/<sanitized>/SKILL.md`) when the
skill's scope is `project` or `user`. Task- and session-scope skills
stay reachable via adapter-injection in the originating chat, but
cannot leak across chats through the engine's plugin-skill loader —
which discovers slots at subprocess boot and feeds them to the next
agent run regardless of which chat owns them.

Promotion through `MultiSkillRegistry.promote()` lets a session-scope
skill cross the gate by re-creating it at the higher scope (slot
written there). The lower-scope source delete uses
`purge_slot=False` so the new authoritative copy keeps the slot.
Tests in `operator/skill-forge/tests/test_plugin_slot.py` cover the
promote-path AND the negative cases (task / session creates leave the
slot empty).

### Phase 2b — L16 Audit Chain Integration (Execution Context Events, ADR-0248)

Every OS turn emits an audit event with ExecutionContext metadata captured
throughout the turn's lifecycle. This data enables compliance traceability
(who used which engine/model), performance analytics, cost estimation, and
debugging. The event type is `console.execution_context`.

**Event structure** — all fields captured at turn completion:

```json
{
  "type": "console.execution_context",
  "timestamp": 1721899935.123,
  "turn_id": "ot_abc123xyz",
  "session_id": "web:sid_xyz",
  "tenant_id": "_default",
  "details": {
    "engine_id": "claude_code",
    "model_source": "claude",
    "model_name": "claude-opus-5",
    "delegation_mode": "native",
    "duration_ms": 1234,
    "exit_code": 0,
    "tokens_input": 150,
    "tokens_output": 50,
    "tool_calls_count": 0,
    "started_at": "2026-07-26T14:32:15Z",
    "completed_at": "2026-07-26T14:32:16.234Z",
    "turn_number": 5,
    "acs_run_id": null,
    "tde_router_decision": null
  }
}
```

**Metadata only** — no PII, no prompt text, no user content. Per GDPR Art. 5
data minimisation, the audit chain captures only:
- `engine_id`: Which engine executed the turn (claude_code | acs | tde | hermes)
- `model_source`: Model provider (claude | ollama | openrouter | hermes)
- `model_name`: Normalized model name (stripped of timestamps)
- `delegation_mode`: How the turn was routed (native | acs | tde | fallback)
- `acs_run_id`, `tde_router_decision`: Delegation correlation IDs (optional)
- Performance: `duration_ms`, `tokens_input`, `tokens_output`, `tool_calls_count`
- Result: `exit_code` (0 = success, non-zero = error)
- Traceability: `turn_id`, `session_id`, `tenant_id`, `turn_number`
- Timestamps: RFC 3339 UTC `started_at`, `completed_at`

**Compliance use cases:**

1. **Audit trail**: Correlate who used which engine/model (EU AI Act Art. 12/13).
2. **Performance analytics**: Average duration per engine, model choice patterns.
3. **Cost estimation**: Aggregate tokens × model pricing.
4. **Debugging**: Link failures to engine/exit_code; correlate with workflow runs
   via `acs_run_id` or `tde_router_decision`.

**Fail-closed design:**
- Emission is best-effort; audit failures never break the turn.
- `_emit_execution_context_event()` is wrapped in try/except (no re-raise).
- If the audit chain is unreachable, the turn completes normally.

**Queryable via REST API** (Phase 2b amendment, future):
- `GET /v1/console/audit?type=console.execution_context` — all execution events
- `GET /v1/console/audit?type=console.execution_context&engine_id=acs` — filter by engine
- `GET /v1/console/audit?type=console.execution_context&model_source=claude` — filter by model source
- `GET /v1/console/audit?type=console.execution_context&delegation_mode=tde` — filter by mode
- `GET /v1/console/audit?type=console.execution_context&turn_id=ot_...` — single turn

**Implementation notes:**
- `ExecutionContextBuilder` tracks the turn lifecycle (start → complete).
- Emit call placed at EACH turn completion path (6 paths in `stream_turn`).
- Event details extracted from `ExecutionContext.to_dict()` for JSON serialization.
- Audit event allowlist (`_ALLOWED_FIELDS["console.execution_context"]`) prevents PII leakage.

### Phase 3D — Loopback-deny (sitecustomize shim wired into bwrap)

Personas with `network: allow` (research) share the host
net namespace via `--share-net`. Loopback-deny narrows that exposure:
the runner binds `operator/forge/forge/sandbox_helpers/` read-only into
the bwrap and sets `PYTHONPATH=<helpers>` so Python's auto-import of
`sitecustomize.py` patches `socket.socket.connect` /  `connect_ex` to
refuse 127.0.0.0/8, `::1`, `localhost` (+ aliases) and 169.254.169.254
(cloud IMDS).

`Policy.deny_loopback_for_persona(persona)` is the gate:

- `network: allow` + no `loopback` field → **deny by default** (safe)
- `network: allow` + `loopback: allow` → opt-in (loopback reachable)
- `network: deny` (or persona missing from overrides) → False (no
  loopback to deny when the namespace is fully unshared anyway)

Wiring: `runner.py` pulls both `allow_network` and `deny_loopback`
from the effective policy and passes them to `build_bwrap_cmd`. The
audit chain reflects the policy intent — `tool.network_share` events
now carry `sandbox: bwrap+net-noloop` (default for research)
or `bwrap+net` (when the operator opted into loopback) plus a
`deny_loopback: bool` field.

Coverage in `operator/forge/tests/test_persona_sandbox.py`: spawns a
local 127.0.0.1 HTTP stub, runs a `urllib.request.urlopen` tool under
three configs (research default, research+loopback:allow, coder) and
asserts the inner result + audit-event sandbox label match the policy.

**Caveats** (intentional):
- The shim only patches Python sockets. A bash tool that runs
  `curl http://127.0.0.1` bypasses it. Coverage scales with
  Python tools (urllib / requests / httpx / aiohttp); the audit
  event records the policy intent so an operator can correlate.
- `socket.create_connection` with a pre-resolved address bypasses
  the patch (we hook the user-visible API). Real HTTP libraries all
  end up at `socket.connect` so coverage is effectively complete.

### Phase 3E — PIN-elevation (`/auth-up <pin>`)

`bridges/shared/auth_elevation.py` (+ `js/auth_elevation.js`) lets a
chat's PIN-authenticated owner temporarily elevate forge / skill-forge
**promote** privileges. The default is fail-open for non-bridge
callers (CLI, tests); the bridge-side `auth_elevation_gate.py`
PreToolUse hook denies `mcp__forge__forge_promote` /
`mcp__skill_forge__skill_promote` when no elevation grant is active
for the calling chat.

Storage: `<corvin_home>/global/auth/elevation.json` with TTL.
Audit: every `engage` / `revoke` writes into the unified hash chain.

**Rate-limiting (ADR-0072 V-006):** `grant()` tracks failed attempts per chat_key in an in-memory LRU counter (not persisted — restart resets, which is acceptable). Threshold: 5 failures within 60 seconds → 300-second lockout. During lockout, `grant()` returns `(False, "pin-lockout")` immediately without consulting the stored PIN, and emits `auth.elevation_lockout` (WARNING). On correct PIN, the counter is cleared. On threshold being hit, `auth.elevation_lockout_started` (WARNING) is emitted with `fail_count` and `lockout_s`.

### F13 — Path-Gate self-test on boot

`path_gate.path_gate_self_test()` runs at adapter boot, feeds a
curated set of "must-deny" payloads through `check()` and emits a
CRITICAL `path_gate.self_test_failed` audit event when any vector
falls through. Coverage spans direct write to forge / skill-forge /
audit.jsonl / policy.json / slot-mirror, plus Bash redirect / tee /
eval+hint / cmd-subst / sed -i / heredoc / python -c open vectors.
Fast smoke alarm — the comprehensive matrix lives in
`hooks/test_path_gate.py`. Self-test vectors also cover: exec
file-descriptor redirect to audit.jsonl, mkfifo with forge directory
hint, process substitution write side `>(...)` with forge hint,
command substitution in pipe position — all four raise `deny=True`
(ADR-0072 V-007 + V-013).

### J — Dialectic rate-limit (sliding-window per site)

`dialectic.decide()` now throttles `mode in (skill, cli)` calls
through a 60s sliding-window limit per site. Defaults conservative:
6/min for skill_promotion + forge_creation + session_reset, 12/min
for auto_routing, 30/min for path_gate. Operator override via
`cfg.rate_limits[<site>]` (calls per 60s int). When the window is
full, the call degrades to thesis-only / mode=off + emits a
`dialectic.rate_limited` audit event.

Side-effect: the bug where `_audit()` called `_audit_writer` without
the `path` argument is also fixed — `decision.dialectical` events
now actually land in the chain. The fix uses `_audit_chain_path()`
which resolves to `<corvin_home>/global/forge/audit.jsonl`.

### K — TTS-key migration aus corvinOS-Silo nach XDG

`voice_lib.sh::voice_migrate_legacy_silo_key()` is an idempotent
helper that copies `OPENAI_API_KEY` from
`<corvin_home>/voice/.env` (or `<corvin_home>/global/voice/.env`)
into the canonical XDG location `~/.config/corvin-voice/.env` when
the canonical file doesn't already carry the key. Non-destructive —
the silo file stays in place. Test rebuilds the scenario in a tempdir
sandbox + asserts (a) successful migration, (b) idempotency on second
run, (c) existing canonical key beats silo (no overwrite).

### L — Daily audit-chain verify timer + bridge notification

Two systemd user units in `operator/voice/scripts/systemd/`:

- `corvin-audit-verify.service` — oneshot calling
  `voice_audit.py verify --notify-bridge`.
- `corvin-audit-verify.timer` — `OnCalendar=*-*-* 04:30:00`,
  `Persistent=true`.

`bridge.sh up` enables both timers (session-timeout 03:30 + this one
04:30); `bridge.sh down` disables them. The `--notify-bridge` flag
reads `relay.json` (legacy single-target OR new `targets: [...]` list)
and writes one outbox envelope per target with
`_audit_chain_break: true` + a 5-line summary of the first chain
problems. Bridges then forward the warning to Telegram / Discord /
WhatsApp / Slack / Email.

Coverage in `operator/voice/scripts/test_audit_verify_notify.py`
(21 assertions): chain-break + relay → envelope written; clean chain
→ no envelope; no relay / disabled relay → exit 1 + no envelope; the
systemd unit templates ship with the plugin and bridge.sh wires them
into ALL_UNITS + cmd_up.

### v3 — Secret-Injection (capability-style)

Closes the gap that forced operators to either pass secrets as plain
arguments through the LLM (visible in chat history, audit chain, and
worse) or to bake them into the impl text at create-time (visible in
the manifest, the slot mirror, and the cache). The new pattern lets a
forged tool **declare** which env-var-style secrets it needs, while
the value lives only in an operator-owned vault and reaches the tool
exclusively via the bwrap subprocess env — never the LLM context.

**Files added/changed:**

- `operator/forge/forge/secret_vault.py` — vault load + key validation
  + best-effort literal redaction. Vault path:
  `~/.config/corvin-voice/secrets.json` (override via
  `CORVIN_SECRET_VAULT`), mode 0600 enforced.
- `operator/forge/forge/policy.py` — adds `persona_secret_allow` field
  + `secrets_for_persona()` + `secret_check()` (fail-closed, no entry
  = no secrets).
- `operator/forge/forge/registry.py::create()` — validates
  `meta.secrets` at create-time, audits declared refs by name.
- `operator/forge/forge/runner.py` — resolves vault keys, applies the
  persona ACL, merges values into the bwrap subprocess env, walks the
  parsed envelope to redact accidental value leaks, audits
  `tool.secrets_injected` (names only).
- `operator/voice/hooks/path_gate.py` — protects the vault file from
  direct Write/Edit/Bash writes (read via `cat` stays allowed; the
  threat is plant-a-rogue-key, not exfil-via-cat which the read-side
  is not the right layer for anyway).
- `operator/forge/tests/test_secret_injection.py` — 58-case
  per-subtask E2E (real bwrap, real vault, real audit chain).

**Tool-side contract.** A tool declares its secret needs in
`meta.secrets` as a list of POSIX-shaped env-var names. The runner
resolves them through the persona ACL + vault and exposes them in
the subprocess env under the same name. The tool reads them via
`os.environ["KEY_NAME"]` exactly like a normal env var:

```python
# tool body
import os
api_key = os.environ.get("OPENAI_API_KEY", "")
```

**Operator-side contract.** Operator writes
`~/.config/corvin-voice/secrets.json` (mode 0600) and adds explicit
allow-list entries to workspace `policy.json`:

```jsonc
{
  "persona_secret_allow": {
    "research": ["OPENAI_API_KEY"],
    "inbox":    ["GMAIL_CREDENTIALS"]
  }
}
```

**Failure modes (all fail-closed):**

| Case | Outcome | Audit event |
|---|---|---|
| Vault file mode > 0600 | `VaultError` at load | `secret.vault_malformed` |
| Vault has invalid key shape | `VaultError` | `secret.vault_malformed` |
| Vault missing the requested key | `SecretMissing` | `secret.vault_missing` |
| Persona without allow-entry | `SecretACLDenied` | `acl.persona_secret_denied` |
| Persona allow-list excludes key | `SecretACLDenied` | `acl.persona_secret_denied` |
| Tool prints secret accidentally | stdout/stderr literal-redacted | `tool.secrets_injected` (names) |

**Recursive envelope redaction.** After the tool's stdout JSON is
parsed, the runner walks the structure (dicts/lists/scalars, depth
cap 32) and replaces any string-leaf containing a literal secret
value with `<redacted>`. Keys / types are preserved so tool consumers
still see their expected shape; only the leaked plaintext goes away.
This is the defence against `print(json.dumps(dict(os.environ)))`
patterns without breaking the tool's intentional structured output.

**Cache safety.** Secret values never enter the cache key (the cache
key derives from the payload, and secrets are not in the payload —
they live in the vault and the env). A cache replay therefore returns
a previously redacted envelope without re-injecting the value, which
is the safe behaviour: the tool was not executed, the value did not
leave the runner.

**Why env, not stdin.** Env-var injection is the simplest way to
match how the rest of the world (Python `os.environ`, bash `$VAR`,
Node `process.env`) reads secrets, with zero per-tool API divergence.
The bwrap PID-namespace unshare prevents intra-sandbox `/proc/PID/environ`
reads from other processes, so the env-leakage class only re-emerges
if the tool itself prints it — which the redaction layer covers.
Stdin-injection stays available as a future swap-in for genuinely
hostile-tool scenarios; the current threat model is "LLM accidentally
writes a tool that emits its env," not "operator runs adversarial
code on purpose."

**What you, as Claude Code, must NOT do:**

- Don't put a secret value into the `payload`, the `input_schema`, or
  the impl source. The whole point of the capability split is that
  the tool spec on disk references the secret **by name**, never by
  value. A tool body that hardcodes a key defeats the entire vault.
- Don't add a `default` entry to `persona_secret_allow`. The
  fail-closed-on-missing-entry semantics is the safety invariant
  every persona relies on; a `default` would silently grant secrets
  to every new persona that lands in the cowork tree.
- Don't widen the env-var key pattern. POSIX-shape (`^[A-Z_][A-Z0-9_]*$`,
  64-char cap) rejects path-traversal sequences, dot-namespaces, and
  weird unicode-shaped collisions. A typo like `lower_case` or
  `BAD-KEY` should be a hard error at create-time, not a silent
  drop-and-keep.
- Don't lower `REDACT_MIN_VALUE_LEN` below 8. Sub-8-char values get
  a high false-positive rate (random three-letter substrings of
  unrelated secrets clash with English words in tool output);
  realistic API keys are 20+ chars anyway.
- Don't catch `SecretACLDenied` / `SecretMissing` and retry with a
  fallback. Both are operator-action signals — either authorise the
  persona, add the key to the vault, or change the tool. Falling
  back to "run with empty env" is the silent-failure mode that
  produces the worst debugging experience.
- Don't write secret values into `meta.secrets`. The list is for
  **names** (env-var keys). A value-shaped string like `"sk-abc..."`
  would (a) fail validation but (b) — if it didn't — land in the
  manifest on disk, in the registry, and in the slot mirror. Hard
  fail at create-time is the right outcome.
- Don't bypass `path_gate` for the vault. Even though Bash `cat
  $VAULT` stays allowed (read is a different concern), `Write` /
  `Edit` / `Bash redirect/tee/sed -i` to the vault is structurally
  blocked because the LLM writing the vault is the
  plant-rogue-key vector. Operator writes happen outside the bridge.

### Phase 4 — Per-user observer-transcript consent gate

Phase 2 (above) introduced the chat-level observer-transcript flow:
when the owner sets `chat_profiles[<id>].observer_visibility =
"transcript"`, every read-only sender's text is buffered and prepended
to the next OWNER turn. That flow is **chat-scoped** — once flipped,
*every* observer's text flows in, without the observers ever
consenting.

Phase 4 closes that gap with a **per-uid consent gate**. Default is
*deny*: an observer's text never reaches the buffer until the observer
themselves grants consent via a slash-command. Three modes:

| Mode | Slash-command | Lifetime |
|---|---|---|
| `durable` | `/consent on` / `/consent yes` | Until revoked |
| `time_bounded` | `/consent 30s` / `/consent 1h` / `/consent 7d` | TTL clamped to [60s, 30d] |
| `per_message` | `/share <text>` | Single message admitted, no state stored |

#### Storage and audit

Single JSON file per (channel, chat) at
`<corvin_home>/global/consent/<safe_channel>__<safe_chat>.json`.
Concurrent writes are serialised with a `.lock` sidecar
(`fcntl.flock`). Expired `time_bounded` entries are pruned lazily on
every read.

Every grant / revoke / drop / expiry / consume-time-drift emits a
`consent.*` event into the unified hash chain at
`<corvin_home>/global/forge/audit.jsonl` — the same chain forge,
skill-forge, path-gate and auth-elevation already use. One
`voice-audit verify` covers all of it.

#### Identity binding

The slash-command carries the sender's platform uid (Telegram
`msg.from.id`, Discord `message.author.id`, WhatsApp
`m.key.participant`, Slack `event.user`). The owner cannot grant on
behalf of another user because the slash-command runs in the granter's
own message. Owner-side commands are limited to `list`,
`status <uid>`, `revoke <uid>`, `help` — no `grant <uid>`.

#### Adapter integration (TOCTOU re-validation)

Two integration sites in `bridges/shared/adapter.py` consult the gate:

1. **Write path** (observer envelope arrives) — `process_one()` calls
   `consent.is_granted(channel, chat_key, uid)`. On miss, the envelope
   is dropped and `consent.observer_dropped` is audited. `_share: true`
   envelopes bypass the gate as a one-shot admit and emit
   `consent.share_admitted` instead. Each buffered entry persists its
   `consent_reason` and `one_shot` flag for re-validation.

2. **Consume path** (owner turn folds the buffer in) — each entry is
   re-validated against the *current* consent store before the line
   reaches the prompt. Entries the granter revoked or whose TTL
   expired between buffer-write and owner-turn-consume are dropped
   with a `consent.consume_drift` event. `_share` one-shots always
   pass — they were admitted at write time and shouldn't be
   retroactively cancelled.

#### Daemon integration (read-only-side)

Each daemon's read-only branch calls
`inChatCmds.dispatchReadOnlyConsent(...)` BEFORE
`maybeForwardAsObserver`. The dispatcher returns:

- `null` — text is not `/consent` or `/share`; daemon falls through
  to the existing observer-forward path.
- `{ reply, kind }` — daemon sends `reply` and returns. Used for
  `/consent on/off/<ttl>/status/help`.
- `{ admitShare: true, sharePayload, reply, kind }` — daemon writes
  `_observer: true, _share: true, text: sharePayload` to the inbox
  AND sends `reply`.

`/share` is gated on `observer_visibility = "transcript"` for the
chat — without that flag the envelope would land in the buffer but
never get consumed. The dispatcher returns a friendly hint instead
of silently admitting.

Wired into all four daemons (telegram, discord, slack, whatsapp) and
the Discord interaction-create + WhatsApp mock-HTTP paths. The
`/share` JSON regex mirrors `consent.py::SHARE_PREFIX_RE` byte-for-
byte so the daemon-side parser can never disagree with the Python
module.

#### Owner-side commands (in `in_chat_commands.js::dispatch`)

```
/consent                  show owner-side help
/consent list             list active consents in this chat
/consent status <uid>     query a user's status
/consent revoke <uid>     force-revoke (e.g. observer kicked from chat)
```

The owner typing `/consent on` / `<duration>` / `off` gets a friendly
redirect — owners are not subject to the gate; the read-only-side
forms are for read-only senders only.

#### Per-subtask E2E (load-bearing)

`shared/test_consent_gate.py` covers nine cases — pure-function
`parse_ttl` (18 sub-cases), `parse_share_prefix` (7 sub-cases),
grant/revoke/status round-trip, lazy-prune of expired entries, the
write-path drop without consent, the write-path pass with durable
consent, the `_share: true` one-shot bypass, the consume-path drift
when revoke happens between buffer-write and owner-turn, and the CLI
subcommands round-trip.

`shared/js/test_consent_dispatcher.js` covers the JS-side dispatcher
in 27 sub-cases — non-`/consent`/`/share` text returns `null`, the
`/share` transcript-flag gate (off → hint, on → admit), case
insensitivity, bare `/share` usage hint, owner-side `/consent on`
durable + `/consent <duration>` + `/consent status` + `/consent off`
round-trips through the real CLI, garbage-duration error, and
missing-uid friendly error.

Both wired into `run-all-tests.sh`.

#### What you, as Claude Code, must NOT do (consent gate)

- Don't bypass `is_granted` from any other adapter path. The
  observer-buffer write **and** the owner-turn consume MUST both
  consult the gate — single-sided enforcement leaks an observer's
  text whenever the granter revokes between the two moments.
- Don't make `/share` work without `observer_visibility =
  "transcript"`. The whole point of the visibility flag is the
  owner's structural opt-in; letting `/share` push observer text
  into a chat the owner never opened up to transcript mode is a
  structural bypass of Phase 2.
- Don't widen the snippet cap above 200 chars or drop the
  `truncated` flag in `consent.observer_dropped` /
  `consent.share_admitted`. The forensic value is "we have enough to
  recognise an injection pattern", not "we log everything verbatim".
  Long observer pastes don't belong in the audit chain.
- Don't allow the owner to grant on another user's behalf. Identity
  binding is the *single* invariant that lets the gate be trusted —
  the sender's platform uid is set by the daemon from the bridge
  protocol; a `consent grant <uid>` owner-side command would let
  the owner grant on behalf of someone who never typed
  `/consent on`.
- Don't catch `consent.observer_dropped` and silently re-admit on
  retry. The drop is the load-bearing default; an "and-then-let-it-
  through-after-N-retries" shim defeats the gate.
- Don't move the consent store under `<scope_root>/forge/` or
  `<scope_root>/skill-forge/`. The path-gate hook
  (`operator/voice/hooks/path_gate.py`) protects those subtrees from
  direct Write/Edit/Bash; consent writes happen via the JS slash-
  command and the Python CLI, both of which are *off* the path-
  gate path. Sliding consent into one of the protected subtrees
  would either (a) require widening path-gate's allowlist or
  (b) wedge the CLI behind a permission prompt — both are wrong
  fixes.
- Don't add a "consent inheritance" feature where a previously-
  granted uid carries consent across chats. The gate is per-
  (channel, chat, uid) on purpose — the same person can be a
  chatty observer in one group and a silent read-only in another.

#### Naming legacy

The consent gate's source files (`consent.py`, `adapter.py`, the
`in_chat_commands.js` integration block) carry "Layer 17" labels in
their docstrings and inline comments. Those labels are pre-fold
artefacts: the gate originally shipped as a standalone "Layer 17"
section before being folded into Layer 16 Phase 4 (this section).
The Phase-3 process-model layer 17 (`/ps`, `/kill`, `phase3_cli.py`)
is the canonical "Layer 17" going forward; consent gate references
in code comments stay as historical anchors and don't need a
sweeping rename.

### Deferred (Roadmap, separate session)

- **F12** — shared `_test_fixtures.py` for adapter tests. Refactoring
  with broad blast radius; current per-test sandbox setup is verbose
  but works. Not blocking.
- **I** — MCP-server netns isolation. Needs slirp4netns + per-mcp-
  process namespace handling; non-trivial systems work. Today MCP
  servers run with the host network namespace; the path-gate +
  policy-gate cover the FS write side, MCP-side network egress is the
  remaining axis.

### What you, as Claude Code, must NOT do

- Don't make `_inbox_sender_authorized` fail-closed on missing
  settings or empty whitelist — that's the legacy fail-open contract.
  The drift-drop only fires when whitelist is **populated** and the
  sender is **explicitly absent** from it.
- Don't widen the `/btw` snippet cap above 200 chars or drop the
  `truncated` flag. The forensic value is "we have enough to
  recognise an injection pattern" not "we log everything verbatim";
  long pastes don't belong in the audit chain.
- Don't make `audit.chain_gap_detected` part of the hash chain. The
  whole point of the `hash_chain=False` flag is that a broken chain
  can still record its own gap. Linking the gap event to the broken
  predecessor would require the chain to be intact to log that the
  chain is broken.
- Don't drop the confusable map in favour of "NFKC alone". NFKC
  alone does NOT collapse cyrillic ↔ latin look-alikes — it only
  folds full-width / compatibility forms. Both passes are needed.
- Don't tie path-gate's Bash detection to a hard-coded list of
  "trusted commands". The fail-closed-on-hint rule is the entire
  reason the v2 vectors are safe; an allowlist trades blast radius
  for ergonomics — wrong direction.
- Don't add a workspace policy.json that flips a persona's
  `loopback` to `allow` without recording why. Loopback-allow
  is the "browse my local services" knob; legitimate uses
  (local-service tests, dev-loop traffic) need a clear comment in
  the override file so the security review can find them.
- Don't widen the dialectic rate-limit beyond what the operator
  set in `cfg.rate_limits`. The defaults are intentionally
  conservative; raising them per-call breaks the budget contract
  every operator-toggle relies on.
- Don't add a hook into `voice_audit.py verify` that silences the
  exit-1 path on chain break. The exit code is the only reliable
  signal for the systemd timer; suppressing it would hide a
  CRITICAL state behind a notification that may itself fail to
  deliver.
- Don't move `path_gate_self_test()` from boot-time into the
  hot-path. It's an O(12) check meant to fire once per process, not
  per tool call — putting it inside `main()` would multiply the cost
  per Bash command without security gain.
- Don't touch `<repo>/operator/skill-forge/skills/dyn/` from the
  registry without going through the slot-gate. The scope-check is
  load-bearing — bypassing it re-opens the cross-chat leak vector.
- Don't make the observer UUID session token discoverable before
  session start — it is generated at adapter boot, not at channel
  creation. A per-message token would allow observers to extract and
  reuse it within the same message pair. Per-boot is the correct
  granularity.
- Don't lower the PIN lockout threshold below 5 or the window below
  60 seconds — these are the minimum values that provide meaningful
  brute-force protection for a 4-digit PIN space without blocking
  legitimate elevation attempts on a momentary typo.

## Path-Gate Hook (layer 10) — direct-FS-write protection on forge / skill-forge workspaces

The layer-10 PreToolUse hook (`operator/voice/hooks/path_gate.py`) is the
structural enforcement that lets every `zero_config` persona carry
`forge_enabled` / `skill_forge_enabled` without giving up the sandbox.
It runs before every `Write` / `Edit` / `MultiEdit` / `NotebookEdit` /
`Bash` / `WebFetch` tool call from any persona — regardless of
`permission_mode` — and denies (exit 2 + stderr reason) when the call
targets a forge / skill-forge workspace. The MCP server stays the only
writable path.

**Protected paths** (any absolute resolved path that matches these):

| Pattern | What it protects |
|---|---|
| `<corvin_home>/**/forge/**` | Forge workspaces in any scope |
| `<corvin_home>/**/skill-forge/**` | SkillForge workspaces in any scope |
| `<corvin_home>/**/audit.jsonl` | Unified hash-chain audit log |
| `<corvin_home>/**/policy.json` | Per-scope policy override |
| `<repo>/operator/skill-forge/skills/dyn/**` | Engine-facing slot-mirror |
| `<repo>/operator/forge/forge/policy.json` | Bundled default policy |
| `<repo>/operator/forge/forge/policy.default.json` | Bundled default policy (alt name) |

**Bash detection** — write-target paths are extracted via:
- `>` / `>>` / `&>` / `>&` redirects
- `tee` (with optional flags), incl. `tee -a`
- `mv` / `cp` / `install` / `rsync` last-non-flag-arg
- `sed -i` last-non-flag-arg
- `dd of=<path>` (any arg position)
- `python -c "open('<path>', 'w'/'a'/'x')"` literal paths

**Fail-closed Bash rule** — if the command contains `eval` / `exec` /
`$(...)` / backticks AND any string from `("forge", "skill-forge",
"audit.jsonl", "policy.json")` appears in the command, the hook denies
even though it cannot enumerate the actual targets. Same for
unbalanced quotes that defeat `shlex.split` while referencing a
protected hint. Rationale: missing a write vector = silent
linter/policy bypass; a few false-positive denies on benign
commands that happen to mention "forge" are acceptable cost.

**Why no MCP-bypass token is needed** — the forge and skill-forge MCP
servers write via plain Python file-IO (`_atomic_write_text`,
`open(...).write(...)`) inside their own subprocesses. Those writes
never traverse Claude's PreToolUse tool-call gate, so the hook only
ever blocks Claude's own tool calls trying to skirt the MCP path. The
MCP path stays open by construction.

**Audit** — every block emits a `path_gate.denied` event into the
unified chain at `<corvin_home>/global/forge/audit.jsonl` via
`forge.security_events.write_event`. Fields: `tool_name`, `target`,
`command` (truncated to 200 chars for Bash), `persona`
(`CORVIN_CALLER_PERSONA`), `channel_id` (`CORVIN_CHANNEL_ID`),
`reason`. `voice-audit verify` covers these events because they go
through the same hash chain as `tool.created`, `skill.namespace_denied`,
`session.reset`, etc.

**What you, as Claude Code, must NOT do:**

- Don't make the hook fail-open. The whole sandbox of layers 6 and 7
  rests on it being fail-closed for ambiguous Bash commands that
  reference protected hints.
- Don't add a Bash bypass for "trusted personas" — the persona is
  determined by `CORVIN_CALLER_PERSONA` env, which is settable by
  the persona's own prompt-injected output. There is no trustworthy
  persona signal at this layer.
- Don't introduce a new write-tool (e.g. a future `WriteBatch`) without
  adding it to the matcher in `operator/voice/hooks/hooks.json` AND a
  case in `test_path_gate.py`. A missed matcher = a missed gate.
- Don't catch the audit-write failure silently in
  `path_gate._emit_audit` and elevate it to a deny — observability is
  best-effort here; the deny itself is enforced by the exit code, not
  by the audit write succeeding. Conflating the two layers turns a
  recoverable observability blip into a hard production failure.

---

## CLAG — Chain-Locked Adaptive Gating (ADR-0133)

Module: `operator/forge/forge/clag.py`

`gate(path, layer_id, *, dna_seed=None, ttl=None)` must be called before any
security-sensitive operation. It verifies the audit chain is intact and issues a
short-lived **Chain Integrity Token (CIT)** that proves the operation ran against
a verified chain at a specific point in time.

### gate() steps

| Step | Mechanism | Detects |
|---|---|---|
| 0 | Resolve `dna_seed` via `get_active_seed()` if not provided | Missing tier coupling |
| 0.5 | CIT self-verify: re-HMAC prior cached CIT with current seed | LSAD seed-tier drift attack |
| 1 | `last_dna_in_chain()` — read tail DNA + hash | — |
| 2 | Shadow hash check (`_shadow_hashes[layer_id]`) | External chain append/tamper between calls |
| 3 | `verify_last_k(path, k=VERIFY_K_DEFAULT)` | Hash-link breaks in last k events |
| 4–6 | Derive CIT key, build `ChainIntegrityToken`, verify self | Derivation integrity |
| 7 | Write `audit.cit_issued` event (audit-first) | — |
| 8 | Update shadow hash | — |
| 8.5 | Cache CIT in `_layer_cits` (bounded `OrderedDict`) | — |
| 9 | Epoch tick → `write_epoch_anchor()` at every `EPOCH_EVENTS` calls | Long-range tampering |

### Key parameters (current values)

| Constant | Value | Why |
|---|---|---|
| `DNA_PREFIX_LEN` | 32 | 128-bit entropy; birthday cost ~2^64 |
| `VERIFY_K_DEFAULT` | 50 | Equals `EPOCH_EVENTS` — covers one full epoch |
| `EPOCH_EVENTS` | 50 | Epoch anchor frequency |
| `CIT_TTL_SECONDS` | 300 | 5-minute freshness window |
| `_MAX_LAYER_CITS` | 256 | Bounded `OrderedDict`; FIFO eviction |

### CIT self-verification (step 0.5)

On every `gate()` call from a static layer (L10, L16, L19, L38, L29…), the prior
cached CIT is re-HMACed with the *current resolved seed*. A mismatch means the
LSAD seed was silently rotated between calls (tier downgrade attack) →
`ChainIntegrityFailure(reason_code="cit_tampered")`.

**OrderedDict eviction invariant:** `_layer_cits[layer_id] = cit` followed
immediately by `_layer_cits.move_to_end(layer_id)` ensures the just-cached CIT
is always at the tail (newest) before FIFO eviction. Without `move_to_end()`,
re-assigning an existing key leaves it at its original insertion position and
`popitem(last=False)` would evict it on the next new-layer insert.

### Non-JSON lines in verify window

`verify_last_k()` counts (and reports) any non-JSON line found in the last-k
tail window. A non-JSON line indicates possible chain corruption or a
deletion + re-chain attack where a legitimate event was replaced with an
unparseable line and the successor event's hash was rewritten to skip the gap.

### Fail-closed import logic

All four call sites (consent, disclosure, adapter, remote_trigger_receiver) use
the same three-condition forge-expected heuristic:
`_forge_inner.exists() OR _spec_known("clag") OR _spec_known("forge")`.
When any condition is true but the import fails → `ChainIntegrityFailureGateUnavailable`
(CRITICAL log + fail-closed). When all three are false → WARNING + fail-open
(forge genuinely absent, minimal deployment).

### Audit event fields (allow-list)

- `audit.cit_issued`: `layer_id`, `epoch`, `tail_hash_prefix` (16 hex), `dna_prefix` (16 hex), `cit_fp`, `ttl`
- `audit.epoch_anchor`: `epoch`, `tail_hash_prefix` (16 hex), `prev_epoch_tail_prefix` (16 hex)
- `chain.integrity_failed`: `layer_id`, `reason_code`

### Must NOT do

- Omit `move_to_end(layer_id)` after CIT cache insertion — without it, FIFO
  eviction can remove the just-updated CIT of a static security layer.
- Silently skip non-JSON lines in `verify_last_k()` — count and surface them.
- Call `_clag_gate(audit_p, ...)` when `audit_p` might be `None`; guard with
  `if audit_p is not None`.
- Check only `_spec_known("forge")` in fail-closed detection — also check
  `_spec_known("clag")` so a partial install triggers fail-closed uniformly.
- `import anthropic` from `clag.py` (CI AST lint enforces).

