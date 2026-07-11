# Browser Automation — Task-Scoped Navigation & Voice-Guided Login (Concept)

Companion to [browser-automation.md](browser-automation.md) (ADR-0182/0183/0187).
Formalized in `Corvin-ADR/decisions/0189-browser-task-scoped-navigation-and-voice-guided-login.md`.

**Status: implemented (2026-07-11).** All four phases in §7 shipped. Two
implementation deltas versus the plan below, both simplifications that keep
the same external behavior:

- §4.2 planned splitting the collapsed "form is sensitive" boolean into
  `form_has_password_field` / `form_has_payment_field`. Shipped instead: the
  agent loop checks the already-existing per-mark `role == "password"` tag
  from `marks.py` directly, before ever calling the planner — same pause
  behavior, no change to the payment-form sensitive-commit gate at all
  (smaller, lower-risk diff on a well-tested security-critical path).
- §4.4's "genehmigen/ja" and "ablehnen/nein" were **already implemented**
  before this concept (the pending-confirm mic button in `browser.tsx`); only
  "weiter/continue" (resuming a `needs_login`/`needs_approval` pause) was new
  and is now wired to a new `POST /browser/{sid}/agent/continue` endpoint.

## 1. Problem

Two usability complaints from real use, both traced to the same root architecture:

1. **"Every navigation asks for approval."** With no `spec.browser.allowed_hosts`
   configured for a tenant (the common case — nothing writes this file today, see
   §3 below), `BrowserSession.navigate()` requires a live human confirm for *every*
   cross-host hop the agent makes, even to the exact site the user's own task named
   ("open https://example.com and read X"). The user's own instruction already is
   informed consent for that specific site; re-asking for it is friction, not
   safety.
2. **"I can't see where to log in, and I don't know I'm supposed to act."** When a
   task requires a login, there is no dedicated pause: the agent may attempt to
   fill password fields itself (nothing currently blocks a plain `fill()` on a
   password-role mark — see `browser/marks.py:127-129`), and even when a cross-host
   confirm *does* fire, the only feedback is a chat text line — there is no voice
   cue at all, and (until the separate `browser-handoff` fix) the live view often
   pointed at the wrong, disconnected session.

## 2. Non-negotiable: this is not a request to remove the security gate

The cross-host confirm exists because an LLM-driven browser agent is exposed to
**indirect prompt injection**: content on a page it reads can instruct it to
navigate elsewhere, submit a form, or exfiltrate data to an attacker host — with
no human ever having asked for that. ADR-0187 (shipped one day before this
concept) hardened exactly this class of finding at the network layer. Any change
here must **preserve or strengthen** that guarantee for anything the user did not
explicitly ask for, per this repo's own compliance test (CLAUDE.md: *"does this
weaken a structural compliance guarantee?"*).

The synthesis below removes friction for **in-scope** navigation while leaving
the fail-closed default untouched for **out-of-scope** navigation — it does not
touch `check_egress`, the network-layer route, or the forbidden-host guard at all.

## 3. Current state (verified in code, 2026-07-11)

| Question | Answer | Where |
|---|---|---|
| Confirm fires when | `confirm_cross_host=True` **and** `self._allowlist is None` **and** destination host ≠ current host | `browser/session.py::navigate` |
| Allowlist vs. confirm | **Mutually exclusive**, not layered. Allowlist configured → confirm *never* fires for navigate; only silent egress allow/abort. No allowlist → confirm fires on *every* cross-host hop, forever (no "already approved this session" memory). | same |
| Allowlist source | `spec.browser.allowed_hosts` / `forbidden_hosts` in `tenant.corvin.yaml` — manually edited YAML, no UI, no API, nothing writes it today | `routes/browser.py::_allowlist_resolver` |
| Task → allowlist link | **None.** The task string is never parsed for a URL to pre-approve. | `routes/chat.py::_handle_browser_command`, `browser/agent.py` |
| Egress-abort vs. confirm-decline | Different code paths, different messages, and — because the agent loop's `"cross-host" in str(e).lower()` check only matches the confirm-decline message — **different agent behavior**: a confirm-decline stops the loop and asks the human (`status: needs_approval`); an egress-abort does not stop the loop, the agent just keeps re-planning against a wall. | `browser/agent.py::run` |
| Login-field detection | Exists partially: `marks.py` tags `role: "password"`; `_FORM_SENSITIVE_JS` flags a form as "sensitive" if it has a password input **or** matches a card-number regex — collapsed into one boolean, so login and payment forms are indistinguishable today. Only gates the eventual *submit* click via the generic confirm; does not pause the loop. | `browser/marks.py`, `browser/session.py::_form_sensitive_hint` |
| Password autofill | Nothing *blocks* a plain `fill()` on a password field; the planner is only prompt-nudged ("use fill_secret, never fill") — not enforced. | `browser/agent.py:54` (prompt text) |

## 4. Design

### 4.1 Task-scoped auto-approval (replaces per-hop confirm for named hosts only)

When `_handle_browser_command` starts a session, extract every URL/host
**explicitly present in the user's own task text** (reuse/extend the existing
`_URL_START_RE` intent-detector already in `chat.py`). Pass these as a new,
**ephemeral, per-session** `task_scoped_hosts` list into `BrowserSessionManager.create()`
— distinct from the persistent tenant YAML allowlist, and never written to disk.

Because "allowlist configured" currently disables confirm **entirely** (§3), a
naive "set `self._allowlist = task_hosts`" would silently *abort* — not ask a
human about — any off-task host the agent later tries to reach, which is a
regression versus today (today: at least a human gets asked; naively-scoped
allowlist: silent abort with the agent uselessly retrying forever, per the
egress-abort routing bug in §3's last row).

**Decision: decouple "pre-approved, no prompt" from "confirm still available as
fallback."** `navigate()` gains a small routing change:

```
if host in task_scoped_hosts (or same-registrable-domain redirect of one):
    allow silently (no confirm) — this is what the user asked for
elif tenant allowlist is configured:
    egress allow/abort exactly as today (unchanged)
else:
    confirm exactly as today (unchanged) — this is the actual injection defense
```

Same-registrable-domain redirects (e.g. `accounts.example.com` after starting
at `example.com`, or an OAuth provider redirect that returns to the origin site)
are treated as the same "task-scoped" entry — a *different* host entirely
(the classic injection payoff: a page instructs the agent to hop to
`attacker.evil`) still hits the existing confirm/egress path unchanged.

This is presented back to the user once, at task start, as part of the existing
chat status line: *"Approved for this task: example.com. Anything else will ask
you first."* — not a new confirm dialog, just an explicit statement of scope so
the user knows what was auto-approved and why.

### 4.2 Login-moment pause (new, additive control — not a replacement for §4.1's gate)

Split the existing collapsed "form is sensitive" boolean into two independently
observable signals (both already computable from existing JS — `marks.py`'s
password-role tagging and the card-number regex are already separate checks
internally, just OR'd together):

- `form_has_password_field` (login signal)
- `form_has_payment_field` (existing payment signal, unchanged behavior)

When the agent's `observe()` reports the active/focused context has
`form_has_password_field = true` **and** the agent has not already been told to
proceed past it this session:

1. The agent loop pauses (new `status: "needs_login"`, symmetric to the existing
   `needs_approval`) — it does **not** attempt to fill or submit the field itself.
   This makes the existing prompt-only nudge ("use fill_secret, never fill") an
   enforced pause instead of an unenforced hope.
2. Text status to chat (existing mechanism, already fixed to deep-link the live
   `sid` — see the `browser-handoff` fix): *"🔐 Login required on `<host>` — open
   the live view, log in, then say 'weiter' / '/browser continue'."*
3. **New:** a spoken voice notification via the wiring in §4.3, using the same
   text, through the Discord bridge — this is the concrete fix for "der Nutzer
   soll auch gesagt bekommen, was er zu tun hat."
4. The human completes the login manually, watching the (now-correct) live view.
   No credential ever passes through the LLM's context for this v1 — BYOK-vault
   `fill_secret` autofill on password fields is an explicit **non-goal** of this
   phase (see §6); a human typing their own password into a browser they can see
   is the safest possible v1.
5. Resume: an explicit human signal — `/browser continue <sid>` in chat (same
   pattern as the existing `/browser confirm <sid> <yes|no>`), or (§4.4) a narrow
   voice command — un-pauses the loop.

Payment-form submits keep today's behavior unchanged (generic sensitive-confirm
on the commit click) — §4.2 only changes login-shaped forms.

### 4.3 Voice notifications (register → mark_done → deliver_ready, no new plumbing)

Both `needs_approval` (existing) and `needs_login` (new) events, plus a short
voice **summary** at the end of every agent step, reuse the existing durable
cross-process notification queue already used for background-task completion:

- At session start (only when the originating chat turn came from a channel with
  known routing context — Discord `chat_id`/`channel`), call
  `completion_notify.register(task_id, channel="discord", chat_id=..., tenant_id=...)`.
- On `needs_approval` / `needs_login` / step-summary, call an equivalent
  "notify-in-place" hook (new — today's `mark_done` closes the task; a pausing
  notification must not) that writes an outbox envelope carrying `voice_path`
  (synthesized via the same `synthesize_voice_note()` the normal end-of-turn
  reply already uses) — Discord already renders any envelope with `voice_path`
  as an attached voice note (`daemon.js`), so no bridge-side change is needed
  once the console side sets that field.
- The existing pollers (`adapter.py`'s main loop, `bg_monitor.py`) already
  deliver whatever lands in the queue — reused unchanged.

This is purely additive (new information reaching the user, no new capability
granted to the agent) and is the safe part of this concept to build first,
independent of §4.1/§4.2.

### 4.4 Voice **control** — narrow scope, not open-ended

The user asked for "der Nutzer soll frei über Sprache steuern können." Read
literally (arbitrary spoken commands driving arbitrary browser actions), this
opens a new, unreviewed attack surface: STT is not adversarial-safe (background
audio, another voice in a Discord channel, a mis-transcription) driving actions
on a live, possibly-already-authenticated browser session is a materially
different risk than a voice *notification*.

**Scope for this phase: voice control of the existing confirm/pause primitives
only**, not of arbitrary browser actions:

- "weiter" / "continue" → resolves the current `needs_login` pause (§4.2)
- "genehmigen" / "ja" → approves the current `needs_approval` cross-host confirm
- "ablehnen" / "nein" / "abbrechen" → declines / stops the agent

Each of these already has a safe, idempotent, side-effect-bounded handler in the
system today (`/browser confirm`, a new `/browser continue`) — voice control here
means transcribing the utterance and routing it to that **existing, narrow**
endpoint, not synthesizing a new browser action from free text. Open-ended
"navigate to X" / "click the Y button" by voice is an explicit **non-goal**,
flagged for a separate, later design with its own threat model if wanted.

## 5. What this does NOT change

- `check_egress`, the network-layer request route, the SSRF metadata guard, and
  the forbidden-host list are untouched.
- Off-task, off-allowlist navigation still requires a human confirm (no
  allowlist) or is silently blocked (allowlist configured) exactly as today.
- Payment-form / other non-login sensitive-commit confirms are unchanged.
- No credential ever gets typed by the LLM in this phase.

## 6. Explicit non-goals (this phase)

- Autofilling login forms via BYOK vault credentials (`fill_secret` on a
  password mark) — the human types their own password, always, for now.
- 2FA/OTP-field detection — out of scope; the human handles the whole login
  flow, 2FA included, in the live view.
- Open-ended voice-driven browser actions beyond the fixed confirm/pause/decline
  vocabulary in §4.4.
- Converting the `"cross-host" in str(e).lower()` string-match routing (§3, last
  row) into typed exceptions — noted as a pre-existing fragility worth fixing,
  but orthogonal to this concept; tracked separately.

## 7. Phased delivery

1. **Voice notifications (§4.3)** — safe, additive, no security-relevant change.
   Ship first, independently.
2. **Task-scoped auto-approval (§4.1)** — a real security-relevant change (see
   ADR-0189); ship behind the same review bar as ADR-0187.
3. **Login-moment pause (§4.2)** — depends on (1) for the voice cue; independent
   of (2).
4. **Narrow voice control (§4.4)** — depends on (1)+(2 or 3) having a pausable
   state to resolve.
