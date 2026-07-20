# Browser Automation (ADR-0182)

CorvinOS can drive a real browser — open pages, navigate, fill fields, click
buttons, read results — while **you watch live** and can pause or take over.

**From the console chat**, this is a native tool (`corvin-browser`, ADR-0193)
the model calls directly as part of its own turn — the same way it calls
`Bash` or `Edit` — no special command, no separate process, no pre-turn
classifier deciding whether your message "counts" as browsing. See
`docs/browser-native-tool-integration.md` for the design. The rest of this
doc describes the underlying action surface, safety model, and the separate
Browser-page UI, which apply regardless of which caller drives them.

## How it works

- **Perception — Set-of-Marks.** Each `observe` returns a numbered list of the
  interactive elements on the page (`[0] textbox: Email`, `[1] button: Sign in`,
  …). The agent acts by index (`click(1)`), not by pixel — robust to layout
  changes and usable by any engine (Claude or the local Hermes).
- **Action.** A Playwright-managed browser runs per session (isolated profile,
  sandboxed). **Engine selection is Chrome-primary, Chromium-fallback:** a
  launched session tries your real **Google Chrome** first (best real-site
  compatibility and the real-browser feel), and transparently falls back to the
  bundled **Chromium** — the build `playwright install chromium` guarantees —
  when Chrome isn't installed or won't start. The working engine is cached for
  the process, so a host without Chrome doesn't retry it every session. Override
  with `CORVIN_BROWSER_CHANNEL` (`auto` default · `chrome` · `chromium`). The
  full tool surface is
  `browser.navigate/observe/click/fill/fill_secret/key/select_option/hover/drag/
  upload_file/read/scroll/back/tabs/switch_tab/extract_table/extract_form_schema/
  screenshot`. The autonomous agent, the REST endpoints, and the `browser.*` tool
  schema all expose the same set (kept in sync by a drift test).
- **Submit + navigate the way a person would.** The agent can press **Enter**
  (`key`) to submit a search or form — filling a field never submits it on its
  own — pick native `select` dropdown options, follow a link that opens in a
  **new tab** (it switches automatically), go **back**, and pull a table out as
  structured rows (`extract_table`). `fill_secret` types a vault-resolved value
  into a *non-password* field (their typed value is still never read back into
  perception) — since ADR-0189, neither `fill` nor `fill_secret` can target a
  live password field at all (the login-moment pause below takes over before
  either action reaches one; a TOCTOU backstop refuses it even if a field
  flips to `type="password"` after the last `observe()`).
- **Live view.** The console **Browser** page streams the driven browser as a
  live image, shows every action in real time, prompts you to approve sensitive
  actions, and has **Pause / Take over**.

## Safety (load-bearing)

- **Egress allowlist, enforced at the network layer** — every request the page
  makes (top-level navigation **and** subresource `fetch`/XHR/image/beacon/
  WebSocket) is validated against the tenant policy and aborted if disallowed,
  not just the address bar. This closes the classic indirect-prompt-injection
  exfil path (an allowlisted page `fetch()`-ing your data to an attacker host).
  Redirects and any click/Enter/select that navigates are re-checked. Fail-closed.
- **SSRF metadata guard** — cloud instance-metadata endpoints (169.254.169.254 &
  the link-local range, `metadata.google.internal`, Alibaba, the IPv6 IMDS) are
  blocked unconditionally — including obfuscated encodings (decimal, hex, octal,
  trailing-dot, IPv4-mapped IPv6) — even for a subresource request and even if a
  tenant allowlist names one.
- **Metadata-only audit** — every action logs host + action + element role; the
  audit trail and action log never contain typed values, passwords, or page text
  (a cross-host confirm shows only the host, never a URL that could carry a token).
- **Never echoes field values** — perception uses element *labels*, never a
  field's current value, so a typed secret/PII can't leak back into the model.
  Accessible names are length-capped and the planner's untrusted-content fence
  uses a per-request nonce, so page text can't break out and inject instructions.
- **Secret vault** — `fill_secret(index, vault_key)` types a secret from the
  vault; the value never enters the model context or any log.
- **Human-in-the-loop** — buy / send / delete / login clicks **and a committing
  Enter/Space or a `select`/`drag` on a payment/credential form** require your
  explicit confirmation in the live view. No confirm channel → the action is
  blocked (fail-closed).
- **Sandbox** — the Chromium renderer sandbox is ON by default (it loads
  untrusted pages). Only disable it on a sandbox-incapable host via
  `CORVIN_BROWSER_NO_SANDBOX=1`.
- **Bounds** — max 8 concurrent browser sessions per tenant; the profile
  (cookies/localStorage) is wiped when the session closes.

## Task-scoped navigation + voice-guided login (ADR-0189)

Two usability additions layered on top of the safety model above — neither
weakens the fail-closed default for anything the user didn't explicitly ask
for:

- **Task-scoped auto-approval.** The host named in a session-creating
  `browser_navigate` call (native chat tool, ADR-0193) no longer pauses for a
  confirm the first time — the user already approved it by naming it. Any
  OTHER cross-host hop the agent tries mid-task (something it discovered on
  the page, not something the user asked for) still requires the normal
  confirm. This only ever *narrows* what needs approval; it never disables
  the confirm/egress-allowlist machinery itself. (The Browser page's own task
  field never wired this extraction through its `POST /browser/session` call
  — a pre-existing gap unrelated to and unchanged by ADR-0193.)
- **Login pause (`needs_login`).** The agent loop checks for a visible
  password field *before* asking the planner what to do next. If one is
  present, the loop stops immediately — the agent can never decide to
  `fill()`/`fill_secret()` a password itself — and reports `needs_login`. The
  session stays open (it does **not** auto-close) so the live view keeps
  showing the page for the human to log in manually.
- **Approval pause (`needs_approval`).** A cross-host confirm that's declined
  or times out ends the run cleanly with `needs_approval` instead of retrying
  the same hop every step until `max_steps`. The session likewise stays open.
- **Resuming a pause.** The **Weiter** button (or saying "weiter") in the
  Browser page re-runs the agent loop on the *same* session — same browser,
  same cookies/page state — with a short note telling the planner not to
  repeat the step it paused for. (This resumes the Browser page's own
  agent-loop task, a separate mechanism from the native chat tool's granular
  actions — see the note after "Known limitations" below.)
- **Voice notification on pause.** If the tenant has notify routing
  configured (see `spec.browser.notify_channel`/`notify_chat_id` below), a
  proactive voice-capable notification is pushed the moment the agent pauses
  — not just the in-chat text delta — reusing the existing completion-notify
  outbox/TTS pipeline. No routing configured → silently skipped; the in-chat
  message and the live view remain the primary UX either way.
- **Narrow voice vocabulary in the Browser page.** The existing mic button
  now also listens for "weiter"/"continue" (resumes a pause) alongside the
  pre-existing "ja"/"nein" confirm-answer vocabulary. Recognized phrases route
  to the existing narrow confirm/continue endpoints only — there is no
  open-ended "do X" voice command that synthesizes new agent actions.

See `docs/browser-voice-guided-navigation.md` for the full design rationale
and `Corvin-ADR/decisions/0189-browser-task-scoped-navigation-and-voice-guided-login.md`
for the formal decision record.

## Enable it

**The installer does this for you.** `corvin-install` (which `install.sh` /
`install.ps1` both invoke) runs a `Browser automation` step that installs
Playwright and downloads its Chromium binary, so agent browsing works out of the
box like voice and image generation. The step is **fail-soft** (a failed ~150 MB
download never aborts the install) and **idempotent** (a re-run skips the
download when Chromium is already present). Chromium is the **guaranteed
fallback**; if you already have **Google Chrome** installed, launched sessions
use it automatically (see engine selection above) — no extra install needed.

**Auto-update self-heals it.** After a successful `uv tool upgrade` (the
auto-update path in `corvin-serve`), the backend re-checks the browser stack in
the background: a `[browser]` extra lost in the venv rebuild is restored into
the uv receipt, and a missing Chromium binary is downloaded — fail-soft, never
blocking server start. This closes the gap for early installs whose playwright
was pip-injected (wiped by upgrades) and for 0.10.45–0.10.47 installs that
never got Chromium.

To provision it by hand — e.g. on an environment set up without the wizard, or
to finish after a failed download:

```bash
corvin-install --browser           # installs Playwright + the Chromium binary
```

Note that on the canonical install (`curl | sh` → `uv tool install
'corvinos[browser]'`) only corvinos' own commands land on your PATH — bare
`playwright` and `pip` do **not** exist there. If the playwright *package*
itself is missing (the `[browser]` extra was never installed or got wiped),
restore it into the uv receipt first so upgrades keep it:

```bash
uv tool install --force 'corvinos[browser]'   # then: corvin-install --browser
```

On minimal Linux images Chromium additionally needs system libraries that only
root can install — this is the one step that must target the tool venv's own
interpreter explicitly:

```bash
sudo "$(uv tool dir)/corvinos/bin/python" -m playwright install-deps chromium
```

(Windows equivalent of the venv interpreter, should you ever need `-m
playwright` by hand: `& "$(uv tool dir)\corvinos\Scripts\python.exe" -m
playwright install chromium`.)

Playwright is imported lazily, so the console runs fine without it — the feature
simply activates once the package + browser are present. Open the console →
**Browser** to use it.

## Egress config (optional)

Restrict which hosts the agent may reach in `tenant.corvin.yaml`:

```yaml
spec:
  browser:
    allowed_hosts: ["example.com", "internal.corp"]   # only these (deny-by-default)
    forbidden_hosts: ["ads.example.net"]              # always blocked
```

No `allowed_hosts` → all hosts allowed (still audited). `forbidden_hosts` always
wins.

## Configuration reference

| Setting | Effect |
|---|---|
| `spec.browser.allowed_hosts` | Egress allowlist (deny-by-default when set) |
| `spec.browser.forbidden_hosts` | Always-blocked hosts |
| `spec.browser.notify_channel` / `spec.browser.notify_chat_id` | ADR-0189: opt-in routing (e.g. `discord` + a chat ID) for proactive voice notifications when the agent pauses. No console UI/API yet — manual YAML edit only, same pattern as the allowlist above. Absent → no proactive notification (in-chat text + live view still apply). |
| env `CORVIN_BROWSER_CHANNEL` | Launch engine: `auto` (default — Google Chrome, else bundled Chromium) · `chrome` (Chrome only, no fallback) · `chromium` (bundled only). Also accepts `chrome-beta`/`chrome-dev`/`msedge`. |
| env `CORVIN_BROWSER_HEADLESS` | Force `1`=headless / `0`=visible. Default: visible when a desktop display exists (incl. Windows/macOS), headless on a display-less server — the live view shows every action either way. |
| env `CORVIN_BROWSER_NO_SANDBOX=1` | Disable the renderer sandbox (constrained hosts only) |

## Native chat tool vs. the Browser page's own agent loop

Two distinct ways to drive the browser exist side by side, per ADR-0193:

- **Native chat tool (`corvin-browser`, ADR-0193).** The model calls granular
  actions (`browser_navigate`, `browser_click`, `browser_read`, …) directly
  as part of its own turn. A sensitive action it hits parks a confirm
  resolvable only from the live view's Approve/Decline buttons (or the
  decoupled `POST /browser/{sid}/confirm` endpoint) — there is no chat-text
  command for this anymore.
- **The Browser page's own "Aufgabe für den Browser" task field** still
  starts the older, autonomous agent loop (`POST /browser/{sid}/agent`) that
  runs a whole natural-language task to completion in the background, with
  its own Weiter button / voice vocabulary for `needs_login`/`needs_approval`
  pauses — unrelated to and unaffected by the chat-tool path above.

**Where it works — console only, for now.** The native browser tools are wired
only on the **console chat** turn, which mints the per-turn bearer token they
authenticate with. A **bridge** (Discord / WhatsApp / Teams) is a separate
process that cannot mint that token, so the browser tools are **not offered** on
bridge turns (previously they were offered but every call dead-ended in a
"no token — retry" loop). Ask from the console to drive a browser. Giving bridges
a governed browser path is a tracked follow-up (a real cross-process token
endpoint), not a silent capability.

## Known limitations

- A confirm from the native chat tool's granular actions can be approved
  **only** from the live view (Approve/Decline buttons, or the decoupled
  `POST /browser/{sid}/confirm` endpoint) — there is no chat-text command.
- A `needs_login`/`needs_approval` pause from the Browser page's own agent
  loop is resumed from the live view (Weiter button or saying "weiter")
  only — the `/browser continue <sid>` chat-text command that used to be an
  alternative path was retired in ADR-0193 Phase 2.
- Sensitivity detection is heuristic (element name + URL path + form context); an
  icon-only commit button on a plain-looking page may not be auto-flagged (the
  network-layer egress guard, the audit trail, and the live view are the
  backstops). A committing Enter/Space and a `select`/`drag` on a
  password/card-bearing form *are* now gated.
- DNS rebinding (an allowlisted hostname whose DNS later resolves to a
  metadata/loopback IP) is not caught by the lexical host check; the network
  route validates the request URL, not the post-resolution IP. Pin hosts you
  care about by IP where this matters.
- The screencast live view renders the real screen, so a secret typed into a
  *non-password* field (e.g. an API-key box) is visible to the operator watching
  it — the live view is owner-only and the value still never reaches the model
  context or any log.

## Stability + lifecycle

- **The browser going away is handled cleanly.** If the launched Chromium
  crashes, or (attach mode) you quit your real Chrome, the session is marked
  disconnected: the next action returns an actionable "the browser was closed —
  start a new one" instead of an opaque 500, the live view stops instead of
  freezing on the last frame, and — in attach mode — the screencast stops
  capturing your real Chrome the moment consent lapses or the session is paused.
- **A slow page never wedges a whole task.** A single click/navigate timeout or
  a stale element is reported and the agent re-observes and continues, rather
  than aborting the run; the REST/tool surface translates raw browser timeouts
  and target-closed errors into actionable 409s.
- **No session-cap wedge.** Up to 8 concurrent sessions per tenant; genuinely
  idle sessions (no running agent, no pending confirm, idle > 30 min) are
  reaped when a new one is created, so forgotten sessions never permanently
  block new ones. `GET /browser/sessions` lists your live sessions (metadata
  only) so you can see and close them.

## Attach to your real Chrome (ADR-0200)

Besides the managed empty Chromium above, CorvinOS can drive **your own,
logged-in Chrome** — your cookies, sessions, 2FA — so authenticated tasks work
without a fresh login each time. Console → **Browser → „Mit meinem echten Chrome
verbinden…"**.

Flow (all in the panel):
1. **Grant consent.** A dedicated, TTL-capped (default 1h), revocable
   `real-chrome` consent. Attaching without it is refused (HTTP 403).
2. **Start Chrome yourself.** Copy the shown per-OS command — it starts Chrome
   with `--remote-debugging-port` and a **dedicated automation profile**
   (Chrome 136+ refuses the debug port on the default profile; the separate
   profile is also the visible trust boundary). The command is ready to run:
   it points at your **actual** Chrome/Chromium install (probed per-OS,
   including per-user Windows installs), fills in a real default profile path
   (`~/.corvin/chrome-automation-profile`, no placeholder to edit), and on
   Windows is emitted in PowerShell-correct form (`& "…\chrome.exe" …`).
   CorvinOS never launches it for you. Log into the accounts you want it to use.
3. **Paste the `ws://…` CDP endpoint** Chrome prints, and attach.
4. **Confirm-mode** toggle: *confirm-each* (default — every sensitive action
   asks) or *watch-mode* ("act freely" with a hard, non-extendable TTL, max
   30 min, then it reverts).

Security (this is the point — your real logins are in reach):
- **Every gate still runs:** egress allowlist + SSRF/metadata block on every
  request, L44 house-rules, metadata-only audit, sensitive-action confirm — the
  attach only changes how the browser context is obtained.
- **Always visible** — an attached session is forced non-headless; you see it act.
- **Detach never touches your browser:** closing the session disconnects the CDP
  link only; it never closes your tabs and never wipes a profile.
- **Audit-distinguishable:** every attached action is tagged `attach=real-chrome`
  in the hash-chained log (metadata only, never page text).
- **Watch-mode suppresses only the prompt** — audit and egress still run, and it
  only applies to attached sessions (never the empty launched one).

- The browser-extension mode (a CorvinOS-authored extension, deeper than CDP
  attach) is not built; CDP attach (ADR-0200) is the supported real-browser path.
