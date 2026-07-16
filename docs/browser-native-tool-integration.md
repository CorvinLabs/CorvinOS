# Browser Control as a Native Chat Tool (Concept)

**Status: ADR filed, not yet implemented.** See `Corvin-ADR/decisions/0193-browser-native-chat-tool-integration.md`
for the accepted decision record — this doc is the fuller design behind it.

## 1. Problem — why "build me a web UI" launches a live browser

Diagnosed in a separate session (see chat history): the web console's chat has its own,
console-specific auto-browse mechanism, unrelated to the L5 cowork persona router:

1. `core/console/corvin_console/routes/chat.py`'s `_BROWSE_SIGNAL_RE` — a broad regex —
   pre-gates every incoming message for words like `website`, `login`/`einlogg`, `checkout`,
   `click`, or a bare domain suffix (`.com`/`.io`/…).
2. If it matches, `_classify_browser_intent()` spawns a **second, separate `claude -p`
   subprocess** whose only job is a binary "BROWSE or NO" classification.
3. If it says BROWSE, the **entire chat turn is redirected** to `_handle_browser_command()`,
   which hands the task to `BrowserAgent` — its own loop that spawns **yet another `claude -p`
   subprocess per planning step** (`browser/agent.py::_spawn_claude`).

A request like *"baue mir eine Web-UI mit Login-Formular"* trips the regex on `login` and gets
classified as a browsing task, even though the user wants code written. Once triggered, the
user is now in a **different process, with none of the main conversation's context**, watching
a live browser attempt to "do" something that was never meant to be interactive.

This is the concrete pain point the user asked to solve: not just "the classifier is
imprecise" (a regex/prompt tuning fix), but structurally — browser control lives in a
side-channel, sealed off from the conversation that's supposed to be driving it.

## 2. Design goal

Make browser control a **tool the same Claude Code turn can call directly** — like it already
calls `Bash`, `Edit`, or (per `assistant.json`) generic Playwright — instead of a classifier
deciding up front whether to hijack the whole turn into a separate process. The model reasons
about the actual task, in the actual conversation, with full context, and reaches for a
browser action only when it decides one is actually needed — exactly the same way it already
decides when to open a file vs. run a shell command.

## 3. What already exists that makes this buildable, not speculative

Verified directly in the repo — this is not a green-field design:

- **`core/console/corvin_console/browser/tools.py`** already defines `BROWSER_TOOLS`: a
  complete, MCP-shaped tool schema (`name`, `description`, `inputSchema`) for every granular
  browser action — `browser.navigate`, `.observe`, `.click`, `.fill`, `.fill_secret`, `.read`,
  `.scroll`, `.back`, `.screenshot`, `.hover`, `.key`, `.select_option`, `.upload_file`,
  `.drag`, `.tabs`, `.switch_tab`, `.extract_table`, `.extract_form_schema`. Its own docstring
  says: *"an MCP stdio bridge … can register the same list verbatim."* **Nothing consumes it
  today** — confirmed via repo-wide grep. This concept is that bridge.
- **Every security gate already lives below the agent-loop layer**, in `browser/session.py`
  and `browser/compliance.py`, and is enforced **regardless of caller** — REST route, the
  current agent loop, or a future direct MCP tool call all go through the same
  `BrowserSession` methods:
  - Egress/SSRF (`compliance.check_egress`, incl. cloud-metadata and DNS-rebind blocking)
  - Per-subresource-fetch route guard (`session._route_egress`, closes indirect-injection exfil)
  - New-tab / popup-flood guard
  - Cross-host confirm (`_confirm_cross_host_or_park`), skippable only for ADR-0189
    task-scoped hosts the user's own task text named
  - Live password-field refusal (`_refuse_if_live_password`), independent of the agent
    loop's own `needs_login` pre-check
  - Sensitive-action confirm (`_confirm_sensitive_or_raise`) for checkout/payment/delete-shaped
    clicks, fail-closed with no confirm broker wired
  - Metadata-only audit (`audit_action`, value-bearing keys scrubbed defensively)

  **None of this needs to change.** The redesign only changes *what decides to call these
  functions* — not what happens once they're called.
- **The pause/resume result shape is already tool-result-shaped.** `BrowserAgent.run()`
  returns `{"status": "needs_login"|"needs_approval"|"done"|"error"|"max_steps", ...}` today.
  The frontend already derives its "paused" banner purely by scanning for this shape in the
  action log — nothing needs reshaping for a tool call to return the same dict.
- **The live-view page is already caller-agnostic.** `browser.tsx` attaches to a session via a
  `?sid=` deep link, independent of whatever started it (this was a deliberate prior fix). The
  chat route already emits a `[open Browser to watch live](/console/app/browser?sid=…)`
  markdown link in its reply today — a tool-call-based design reuses this verbatim.
- **The registration convention for a compliance-sensitive custom tool is already
  established** by `imagegen-zero-config`: a catalog entry (`mcp_manager/catalog.py`) plus an
  idempotent `seed_builtin.py`-style function, *not* a persona-hardcoded `mcp_servers` JSON
  entry — specifically so L34 (data classification) / L35 (network egress) gates apply
  structurally at spawn time, the same way they already gate every other MCP tool.

## 4. Proposed architecture

**A new `corvin-browser` MCP server**, following the `imagegen-zero-config` pattern:

1. `operator/mcp_manager/servers/corvin-browser/main.py` — a `FastMCP` server whose
   `@mcp.tool()` functions are thin wrappers translating `BROWSER_TOOLS`' schema into calls
   against the *existing* `BrowserSessionManager`/`BrowserSession` API — no new browser-control
   logic, just a new entry point onto the one that exists.
2. Registered via catalog + `seed_builtin.py`-style idempotent seeding (not persona JSON), so
   L34/L35 compliance gates apply exactly like every other catalog tool, and operators can
   deactivate it the same way they can deactivate any other tool.
3. **The L44 acceptable-use gate and ADR-0189 task-scoped-host extraction move from
   `routes/chat.py` into the tool's own entry function** (`browser_navigate`/`browser_start`),
   mirroring how `imagegen-zero-config/main.py` already calls its own compliance check rather
   than depending on an outer route to have done it. This is a **strengthening**, not a
   loosening: every call path through the tool is gated at the point of use, not once at
   classification time.
4. **`_BROWSE_SIGNAL_RE` and `_classify_browser_intent()` are retired.** No pre-turn
   classification. The model decides, per its own tool-use reasoning, exactly like it already
   does for Playwright in the `assistant` persona today.
5. **The agent loop's per-step nested `claude -p` planner subprocess goes away for the chat
   path.** The calling Claude Code turn *is* the planner now — one process, one context,
   the whole conversation. (`browser/agent.py` and its REST `POST /browser/{sid}/agent`
   endpoint can stay as-is for non-interactive callers — see Non-goals.)
6. **Pause/resume becomes ordinary conversation.** A `browser_click` call that lands on a
   sensitive action, or a `browser_fill` call refused because the field is a live password
   input, returns a normal tool result (`{"status": "needs_login", ...}` / a refusal message).
   The model sees this in the same turn and tells the user in plain text what's needed. The
   user's next message is a **normal chat reply** — no `/browser continue <sid>` slash command
   — and the model's next tool call, informed by the user's own words in the same
   conversation, proceeds. This is a genuine UX simplification, not just a wiring change.
7. **Live-view is unchanged.** The tool's first navigate/start call returns a `session_id`;
   the model includes the existing deep-link markdown in its reply, exactly as `routes/chat.py`
   does today. No new WS plumbing.

## 5. Dialectical pass: does an always-available browser tool increase risk?

**Thesis:** giving the model direct, always-on access to `browser_navigate`/`click`/`fill`
(rather than gating it behind a classifier) increases the attack surface for indirect prompt
injection — a malicious instruction embedded in earlier conversation text or a fetched page
could try to get the model to browse somewhere it shouldn't.

**Antithesis:** every security-relevant check (egress/SSRF, cross-host confirm, sensitive-action
confirm, live-password refusal, audit) is enforced **inside `session.py`/`compliance.py`,
independent of who calls it** — a direct MCP tool call hits the exact same gates a
classifier-routed call hits today. The tool being "always available" changes *who decides to
call it*, not *what happens when it's called*. This is the same trust model the codebase
already applies to `Bash` (far more powerful, always available, no classifier gate) and to the
generic Playwright MCP tool already wired into the `assistant` persona today.

**Synthesis:** the *current* design is arguably higher-risk, not lower: once the classifier
says BROWSE, `BrowserAgent` runs **autonomously for up to `max_steps`** in a background task,
one planning subprocess per step, with the human only watching (not steering) via the live
view. A native-tool design has the human's own conversation turn interleaved between every
tool call — the model proposes one action, the result (including any pause) comes back into
the *same* turn the human is actively part of. Removing the classifier does not remove a
safety gate; it removes a routing heuristic sitting in front of gates that were never the
classifier's job to enforce in the first place.

## 6. Non-goals

- **Not rewriting `session.py` or `compliance.py`.** Every gate described in §3 stays exactly
  as it is; this concept only changes the entry point.
- **Not removing the existing REST API or the standalone agent loop.** `POST
  /browser/{sid}/agent` (task → autonomous run) stays for non-interactive callers — e.g. an
  AWP/Task-Engine workflow node (ADR-0192) that genuinely wants "run this to completion in the
  background," which is a different, legitimate use case from "the user and the model are
  having a conversation about a live task right now."
- **Not a new voice-notification mechanism.** `browser/notify.py`'s pause notification stays
  for cases where a browser task genuinely does need to run unattended (the REST/agent-loop
  path); the chat-native path's "pause" is just a normal turn ending with a question, no
  separate notification needed since the user is already in the conversation.
- **Not touching the L5 cowork persona router** (`operator/bridges/shared/router.py`) — it was
  investigated and confirmed unrelated to this bug; out of scope here.

## 7. Phased delivery (proposed)

1. `corvin-browser` MCP server (`main.py` wrapping `BrowserSessionManager`/`BrowserTOOLS`'
   schema) + catalog/seed_builtin registration, with the L44 gate and task-scoped-host
   extraction moved into the tool's own entry function.
2. Retire `_BROWSE_SIGNAL_RE` / `_classify_browser_intent()` / `_handle_browser_command()` from
   `routes/chat.py`'s per-message path.
3. Regression coverage: a test suite proving "build me a website" / "implement a login form"
   style coding prompts no longer route to any browser mechanism, alongside the existing
   `test_browser_automation.py` security-gate suite re-run unchanged (since none of those gates
   moved).
4. Docs sync: `docs/claude-ref/layer-*.md` (browser-automation section), `docs/browser-voice-guided-navigation.md`, an ADR for the routing-shape change.
