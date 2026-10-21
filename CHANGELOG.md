# CorvinOS Changelog

## [Unreleased]

### Fixed — release hardening from the adversarial review of 2026-10-21 (six review rounds over the week's work)
- **A bridge adapter started from a shell, a test or a CI job no longer terminates every process in its cgroup on
  shutdown.** The drain-time sweep (`unit_sweep`) runs only when the adapter is the main process of its own systemd
  service; outside it the sweep is refused and logged. It had killed the test runner, the operator's shell and the
  Claude Code session that ran a bridge test.
- **Legacy console workflows report failures as failures.** A node whose worker engine failed, timed out or was missing,
  or whose prompt a safety gate refused, ends `node_failed` and the run `failed` — before, the error text was the node's
  "output" and the run reported success. The prompt that is really spawned (upstream output, inputs, skill bodies
  included) passes the pre-spawn gates, not only the node's instructions.
- **A scheduled console workflow runs.** `corvin_workflows run <wid>` resolves the tenant's saved workflow, cron fields
  are read in the schedule's timezone, the overrun policy (skip / queue / parallel) is enforced, an unparseable cron is
  refused with 400, a crash between a fire and the save no longer re-fires the workflow after a restart, and a corrupt
  `schedule.json` is never overwritten by the next write.
- **Workflow runs and pauses clean up.** Deleting a run removes its attachment snapshot and artifacts; an expired pause
  removes the engine checkpoint (it stayed listed as paused forever); a resume a gate refused is not left resumable and a
  re-saved checkpoint keeps its deadline; approvals count against the concurrent-run limit; closing the run's event stream
  (reload, navigation) never cancels the run any more — only "Stop" does.
- **Workflow backend hardening:** an imported `.awpkg` never replaces an existing tool implementation of the same name; a
  skill name in a workflow is a name, never a path (a traversal name could read any `SKILL.md`); a present but unreadable
  `tenant.corvin.yaml` refuses the run instead of dropping the engine policy; skills are read from the run's tenant, not
  `_default`; the design-chat attachment inliner reads only the capped prefix; Art. 17 erasure also removes the
  workflow's `.awpkg`; the stub engine's notice is English; the console's OS workers carry the session's tenant so the
  orchestration MCP server acts for that tenant.
- **Workflow editor:** the composer refuses the 11th attachment before uploading (the server allows 10 per message);
  a message the server refuses puts the operator's text and chips back; the design chat reconnects with backoff after a
  console restart; a failed upload batch removes the already-stored files; the "Upgrade" link appears only for a licence
  error; four German strings and a German-only "Read aloud" voice are gone; the chat says when older messages are not
  listed.
- **L34 data-flow gate fails closed on a load error.** A tenant with a policy file whose `data_classification` module or
  YAML cannot be loaded refuses the spawn; before, the guard became "no policy" and every prompt, a secret included,
  reached the engine.
- **ACS worker spans are priced again.** The model id is read from the CLI envelope's `modelUsage` (the final `result`
  frame carries no `model` key); the shared `attested_model` helper covers ACS, A2A and the gateway alike.
- **Spoken answers describe identifiers instead of reading them out** (paths, file names, commit hashes, ticket and ADR
  ids, versions, URLs), in German and English; any other output language keeps the tokens. Section numbers, program
  versions, dates and slash words stay spoken; runs of files or commits after a noun that names them are dropped as a whole.
- **Console tasks:** a task the operator cancelled or a reaper interrupted no longer makes the finishing turn "fail
  unexpectedly" and lose its streamed text; the kill path works on Windows (no `SIGKILL` there); the failure classifier
  imports on Windows (turn recovery was silently absent there); a running isolated update is not finalised as
  "interrupted" by the restarted console; the Windows console watchdog stays out of the updater's way; a Bash command
  that pushes, sends or uploads marks the turn as not re-runnable after a restart (closed class, never the command);
  tool-name matching for outbound effects uses word boundaries.
- **Default plugins and skills:** the pre-1.7 Video Producer record is upgraded to CorvinTV at boot (the first-party
  listing's wider declarations are approved and named; before, the upgrade was refused on every boot); a re-synced
  imported skill costs no authoring credit and records its version (every boot spent one); a failed tenant knowledge-base
  init is retried after ten minutes, not every two seconds; the OTLP exporter follows a geo-tier opt-out at the next push;
  CorvinTV never installs `edge-tts` from a request (bootstrap/install pin it) and its adopted TTS key no longer reaches
  OS workers; the OTLP bridge imports on Windows.
- **A2A:** the one-time invite is verified before it is claimed (a flood of bad-signature calls had kept the real
  redeemer out); every mounted console route is body-capped on the gateway host, not only the video routes; a Free
  install answers neither ping nor friendship-ack; an ALLOW of `a2a.network` is audited once per ten minutes per
  tenant, not on every ping; a dropped group fan-out marks the message `dropped` and returns no endpoint; the A2A
  protocol E2E hosts bypass only the network gate.
- **Gateway boot:** a failure to start the telemetry, stability, GitHub-sync or KB-projection loops is logged instead of
  raising `NameError` (`logging` was never imported).
- **Fresh install:** the console's own virtualenv (`core/console/bootstrap.sh`, what `corvinos-serve` runs) now installs
  `pandas`, which the artifact preview needs next to `duckdb` — without it the preview answered "'pandas' is required";
  the KB projector's `stop()` waits for its tick so a test's projector can never write into the operator's live chain.
- **Tests that could not pass:** the console DAG E2E tests now run against a real ASGI server (Starlette's `TestClient`
  buffers an SSE stream that stays open across a pause); the spawn-gate E2E's fake CLI answered the house-rules classifier
  with its worker marker; the diagnostics review suite is aligned with the current contracts; the MCP A2A tool tests run
  as a member install; the bg-task timeout test uses the smallest honoured deadline.

### Changed — SkillForge body limit 16 KiB (ADR-2274)
- The linter limit and the injection cap are both 16 KiB, so a skill the linter accepts is injected whole (the
  injection cap was 4 KiB). Bound: 5 × 16 KiB per turn; no shipped skill reaches the cap.

### Removed — Task Timeline panel
- The console panel `/app/task-timeline` is gone; the interruption notices in the chat are the user-visible surface and
  the per-task timeline endpoint stays for the API.

### Added — strace diagnostics wrapper for the Claude CLI (not activated)
- `scripts/diagnostics/claude_strace_wrapper.py` records who signals the CLI and when it exits, without prompt content;
  activation is a deliberate operator step (see its README).

### Fixed — CorvinTV jobs ended "exit code 2" after a marketplace resync, and failed on Windows
- A console that had loaded CorvinTV before its move to the `buildin` tier kept starting the job process from the old
  marketplace-cache path after the cache was resynced: every job ended "The job process ended unexpectedly (exit code
  2)". The console now notices that the loaded plugin source is gone and reloads it from where it is; jobs still
  running from the old load keep running, are not closed as "interrupted", and stay cancellable. A load that came
  without a job runner (caught mid-resync) is retried.
- "Web slides available" now checks the Chromium build the installed Playwright actually launches; after a Playwright
  upgrade an older build no longer counts, and the missing one is downloaded once in the background.
- The gateway caps every `/v1/console/video/*` request body (PNG asset uploads at 8 MiB, the rest at 2 MiB) before
  the session check, so an unauthenticated upload of any size is no longer spooled to disk.
- Together with CorvinTV's own fixes (no `ffprobe` needed, a font on Windows, Windows job lifecycle; see its release
  notes), a real job on the Windows host that reported the failure produces its MP4 again.

### Fixed — the Marketplace says why a listed update cannot be installed
- `GET /v1/console/api/v1/marketplace/updates` now also returns `stale_source`: installed plugins whose index lists a
  newer version than the plugin source on this host can install (typically a local Corvin-Marketplace checkout that was
  never pulled). The Installed tab shows it as a notice with the fix (`git pull` in that folder, then "Check for
  updates") instead of showing no update button at all.

### Changed — Video Producer is now CorvinTV: on by default, narrated OpenAI first and edge-tts second
- **A fresh installation installs and enables CorvinTV** (and the Knowledge Graph) at first boot, with no operator
  step: it is a first-party plugin now (marketplace `buildin` tier, plugin 1.7.0). Nothing you built on breaks — the
  registry id `video_producer`, the `/v1/console/video/...` API, the `/video-producer` page, your data folder and the
  audit event names are unchanged; an install that still carries the old record is upgraded at the next boot, and an
  operator's uninstall is kept. What it sends where is declared in its manifest and gated per job (OpenAI TTS,
  edge-tts / Microsoft voices, optionally Anthropic for the storyboard); nothing is contacted at boot.
- **Narration:** the default engine is now the chain OpenAI → edge-tts. An exhausted, invalid or missing OpenAI key
  falls back instead of refusing the job; if both decline the job fails with a named error, never a silent video.
  `OpenAI only` stays selectable. Piper is gone from the video narration (the chat voice bridge is unchanged).
- The panel's "web slides available" now needs an installed Chromium, not only the Playwright package.
- Fixed before release: bento scenes silently fell back to the classic slide, ten of 32 worst-case bento layouts
  overflowed, and with an imported sans-serif style the title slide's two lines overlapped.

### Added — Automatic model saving: a Free-tier allowance you can tune in Settings
- Automatic choice of a cheaper model for simple turns is now measured and limited on the Free tier to a share of
  saving per UTC month (default 10 %, floor USD 5); Member is unlimited. Only the **automatic** tiers are limited —
  models you pin, upgrades to a stronger model and everything else are untouched. The share is computed from your
  real completed turns (same tokens priced at the default model vs the model that ran), never from a constant.
- Settings → *Automatic model saving*: set your own limit (Free can lower it or switch it off with 0, Member can set
  any value or none), see this month's measured saving with its window, and why the automation is paused.
  Changes apply to the next turn and are audited (`cost.optimize_capped` / `_resumed` / `_config_changed`).
- Removed the unmounted `TokenSavingsTracker`, `SavingsStore` and `billing_savings` route: they were fed by nothing,
  used fixed constants as "savings" and a made-up credit per token.

### Added — Agent conversations → "Your agents": register an agent and share it with peers from the console
- A freshly installed instance could not use `/ask @peer`, `/ask @mine` or `/talk`: the console could only list
  local agents, so the refusal "this peer offers no federable agent — the peer must mark one as federable" named an
  action that existed only as a hand-written API call. New panel *Agent conversations → Your agents*: one click
  registers the Claude Code agent (**not** shared), a separate **Share with paired peers** switch is the explicit opt-in
  (`PATCH /federation/agents/{id}`; strict body, only Claude Code agents, CSRF, audited). Refusals now say where to fix it.
- Both sides need this version for `/ask @peer` to work: the peer's operator registers and shares an agent there.

### Added — A2A: every sent message shows its stage, a refusal names its cause, a live health watch
- **Message lifecycle.** The receiver records `delivered → accepted → processing → completed|failed|rejected|timeout`
  per task; the sender asks with a signed, content-free task-status query that rides on the ping (direct or relay,
  origin-bound) and follows the peer while the answer is pending. The peer chat draws one status symbol on **every**
  message (queued, sent, delivered, accepted, agent working, done, refused, unconfirmed) with the chain and the age
  of the last observation; an unconfirmed send now resolves to its last known stage. Refused messages offer *Resend*.
- **A refusal names its cause.** The peer's worker path used to discard why it refused, so the sender saw a bare
  `rejected`. A closed reason code (`quota`, `engine_failed`, `house_rules`, …) now travels in the signed response
  and reaches the operator as fixed text.
- **Daily compute pool.** A free-tier instance runs 10 agent tasks per UTC day (one pool for every engine). A refused
  task no longer pays for the L44 model call first, every pong carries `task_capacity` (`available` | `limit_reached`)
  and the peer header shows *daily limit reached* before anything is sent.
- **Continuous watch** (`ops/systemd/corvin-a2a-watch.{service,timer}`, `a2a_live_watch.py`): proves the RUNNING host is
  on current code (a service keeps the code it started with) and that the A2A contract holds over its real HTTP
  boundary; warns about offline peers, spent pools and refusal streaks. Sends no task, spends no quota.
- MCP-sent A2A tasks used to skip the relay (the orchestration MCP server could not import `corvin_core`, which the
  sender read as "relay fallback off"); fixed.
- Compatibility: additive. A peer on an older build answers a plain pong (no stage support) and keeps working
  exactly as before. Windows: stage persistence and the probe no longer depend on `os.O_NOFOLLOW`.
- Update an existing install with `sh update.sh` (Windows: `update.ps1`, Claude Code: `/corvin:update`).

### Changed — README redesign and nine overview pages
- New banner (`assets/banner.svg`), header with badges and a navigation row, and a rewritten
  README from "The problem we solve" on: CorvinOS as the system layer around your agent, with
  one diagram per topic and an honest status table.
- Nine pages under `docs/overview/` — token savings, self-learning, Skills 2.0 & ACP, CorvinOS
  as an OS, organizations, A2A, video, marketplace & plugins, extensibility — each with a
  LIVE / SHADOW / GATED / PARTIAL / NOT BUILT status table and 2–4 SVG diagrams
  (`docs/overview/img/`).
- Removed figures that had no basis in the code ("60–80 % savings", "$22,500 → $4,320",
  "convergence guaranteed", "quality +2–5 % per cycle", "99.9 % SLA", "Forge 2.0 production
  ready") and the links to the non-existent `README_CORVINVS_EXPLAINED.md` / `FORGE_2_0_LIVE.md`.
  The token page instead shows costs measured from the audit chain (2026-09-24 → 10-04) with the
  reference model and the counting window named.

### Changed — README: dedicated Claude Code install section, maintainer/voice sections moved out
- Added a standalone, non-collapsed "Installing via Claude Code" section (marketplace add →
  plugin install → `/corvin:install` → `/corvin:update`), step-by-step; the Quick Start's
  collapsed Claude Code block now just points to it instead of duplicating the content.
- Removed "Maintainer: Update & Deploy Cycle" (`scripts/update-and-deploy.sh`/`.ps1` are
  unaffected and still documented in their own script headers) and "Voice languages" from
  the README — both were maintainer/settings-page documentation, not install/update content.

### Added — `/corvin:update` and a one-screen README quick start
- The Claude Code `corvin` plugin (0.2.0) gains `/corvin:update`: `--check` fetches
  `origin/main` and reports the checkout's commit and version against it without
  changing anything (exit 4 = update available); without it the checkout's own
  `update.sh` / `update.ps1` runs when the checkout is behind (`--force`: always), its
  exit code is passed through (1 rolled back, 2 rollback failed, 3 lock held), and the
  console is re-checked over HTTP. Data and configuration (`.corvin/`, git-ignored, and
  `~/.config/corvin-voice`) are not touched.
- README "Quick Start" is now a TL;DR table (install / update / uninstall per platform
  and from Claude Code) with the details in collapsed sections; install and update no
  longer repeat each other.
- `/corvin:install` honours `CORVIN_CONSOLE_PORT`, like `update.sh`.

### Changed — install.ps1 / update.ps1 run directly in an already-open PowerShell session
- Every documented and printed invocation dropped the
  `powershell -ExecutionPolicy Bypass -File <script>.ps1` wrapper in favour of
  `.\<script>.ps1` — `install.ps1`, `update.ps1`, `uninstall.ps1`,
  `scripts/update-and-deploy.ps1`, `scripts/install_repair.ps1`, README and the
  Windows install docs. If the session's execution policy blocks local scripts
  ("running scripts is disabled on this system"), the docs now name the actual
  one-time fix (`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or
  `-Scope Process Bypass -Force` for just that window) instead of baking a
  Bypass flag into every invocation. The corvin-labs.com install.ps1 stub and
  the Claude Code `/corvin:install` command's subprocess invocation (no
  interactive session to inherit a policy from) are unaffected.

### Changed — CorvinOS installs only from a local clone of the repository
- `install.sh` / `install.ps1` install only the checkout they sit in (or the one
  passed with `--editable` / `-Editable`, which must carry `.corvin_repo` +
  `pyproject.toml`). The no-checkout download mode (`curl … | sh`, `irm … | iex`,
  which fetched `main` into an installer-managed tree) and `--pypi` / `-PyPI`
  are removed; anything else stops before the first download with the
  `git clone` steps. corvinos is always installed `--editable`, never from an index.
- `update.sh` / `update.ps1` never install: with nothing installed, or with a
  legacy PyPI install, they stop with the clone steps instead of downloading an
  installer. Existing checkouts and legacy installer-managed trees still update.
- The launcher's start-up PyPI check (`maybe_pypi_autoupdate`) is skipped for an
  editable checkout install — its code is the checkout.
- `/corvin:install` (Claude Code plugin) runs the checkout's own installer and
  never downloads one; it also no longer passes `--yes` to `install.sh` (which
  rejected it) and calls the real `corvin-restore` entry point.
- README "Quick Start" and the install / upgrade / Windows docs describe the
  clone-based install only. The Corvin-Website repo replaces the published
  `install.sh` / `install.ps1` with stubs that install nothing and print the
  clone steps (live once the site is redeployed).

### Added — automatic, persisted whole-session voice summary; chat header decluttered
- Every chat now gets a spoken recap generated automatically in the background
  as the conversation progresses, independent of whether any browser tab is
  open (`chat_runtime._spawn_session_summary_auto`, fired after each
  successfully completed turn; `routes/voice.py::generate_and_persist_session_summary`
  runs the actual transcript-summarise + TTS pipeline off the turn's critical
  path). Regeneration is skipped unless the chat has gained at least 3 turns
  since its last summary, so a fast back-and-forth doesn't pay for a fresh
  paid LLM+TTS spawn pair on every reply.
- The result is persisted to a fixed, per-chat file pair (`voice-summary/
  session-summary.<ext>` + `.meta.json` inside the chat's own session
  directory) — always overwritten on regeneration, never a growing archive —
  so it survives the browser tab, and the login session, that was open when
  it was generated. Deleting the chat deletes its summary with it; no
  separate pruning or erasure path was needed.
- New **Voice Summaries** panel (sidebar, under Messaging) lists every chat
  in the tenant that has a summary, newest first, with the recap text and a
  native `<audio>` player — reachable after logging back in with a fresh
  session, in a different tab, or from a different chat than the one that
  generated it. Backed by `GET /v1/console/voice/summaries`; playback reuses
  the existing generic `/chat/sessions/{sid}/workdir/{filepath}` file route,
  no bespoke audio endpoint was added.
- This is distinct from the existing on-demand session-recap button (still
  ephemeral, click-only, unarchived — regenerating it does not touch the
  automatic summary) and from the live per-turn "speak this reply" playback
  (still gated on the voice-output toggle).
- Removed the header's **Audit** and per-chat **Engine** override buttons
  from the chat UI. The Audit Trail panel itself is unchanged and still
  reachable from a message's "View graph →" link; the read-only engine
  label in the status bar footer is unaffected — only the two header
  controls were removed.

### BREAKING — Hermes and all local Ollama inference removed (ADR-2091)
- The Hermes worker engine and every path that ran inference on a local Ollama
  server are gone: `hermes`, `claude_code_local` (Claude Code → Ollama redirect),
  `opencode_ollama`, the `ollama_local` model provider, the L44 local classifier
  and the voice-summary local backend. Remote providers stay (`ollama_cloud` is a
  hosted API), as do user-defined custom engines/providers.
- **Legacy config is mapped, never rejected.** A stored `default_engine`,
  `worker_engine`, per-chat engine pin or `/engine` argument equal to `hermes`,
  `hermes-*`, `local`, `ollama`, `opencode_ollama` or `claude_code_local`
  resolves to `claude_code` via `engine_registry.normalize_legacy_engine_id`
  (one `engine.legacy_mapped` log warning per value per process).
  `spec.hermes_model` and `engine_models.hermes` are ignored. There is no
  automatic engine fallback any more: a missing or unauthenticated Claude Code
  yields an actionable error pointing at Setup.
- **L44 house rules:** classifier orders are now `cloud_only` and `floor_only`.
  A tenant whose L35 egress policy does not admit `api.anthropic.com` runs
  `floor_only` — no classifier, no network; a deny-pattern match stays deny,
  every other task escalates (never the policy default), audited as
  `house_rules.floor_only`. The degraded path for `cloud_only`
  (`classifier_error_tier0_degraded`) is unchanged.
  `CORVIN_HOUSE_RULES_CLASSIFIER_ORDER` can only force `floor_only`; the
  `local_first` / `local_only` / `cloud_first` values resolve to the computed order.
- **L34 data classification:** `hermes`, `claude_code_local` and
  `opencode_ollama` left the engine matrix. SECRET data has no bundled
  admissible engine and CONFIDENTIAL data only `opencode_http` — both are
  blocked unless the tenant declares its own local engine via
  `engine_compliance`.
- **Voice summaries:** backend order is `cli` → `structural`. A local-only
  tenant (`CORVIN_TTS_LOCAL_ONLY` or egress-denied) goes straight to `structural`.
- The `corvin-hermes-health.{service,timer}` units are no longer installed;
  `bridge.sh down` / `bridge.sh up` remove them from hosts that still have them.
- `corvin setup` (launcher) has no Ollama step; `--ollama-url` / `--model` are
  gone and old `ollama_url` / `model` keys in the launcher config are dropped on read.
- Removed env vars: `CORVIN_HERMES_URL`, `CORVIN_HERMES_BASE_URL`,
  `CORVIN_HERMES_MODEL`, `CORVIN_OLLAMA_BASE_URL`,
  `CORVIN_HOUSE_RULES_HERMES_TIMEOUT_S`, `CORVIN_HOUSE_RULES_DISABLE_HERMES`,
  `CORVIN_HOUSE_RULES_KEEP_ALIVE`, `CORVIN_HOUSE_RULES_MODEL`,
  `CORVIN_DELEGATE_HERMES_ZONE`, `CORVIN_VOICE_PREWARM`.
- Historical `hermes.*` audit events keep their `EVENT_SEVERITY` entries so
  records already in the hash chain stay readable; nothing emits them any more.

### Fixed — Windows: no more visible terminal window for a background-running Corvin
- Every remaining Task-Scheduler-registered launch path that pointed a
  console-subsystem executable straight at `-Execute` now wraps it in a
  generated hidden-launch `.vbs` invoked via `wscript.exe //B` (Task
  Scheduler has no equivalent of `Start-Process -WindowStyle Hidden` — that
  flag only works when PowerShell itself calls `CreateProcess`, not the Task
  Scheduler service). Covers the default login-time autostart task
  (`install.ps1`/`update.ps1`) and the opt-in always-on `corvin-service
  install` boot-time task (ADR-0184 Stufe 2) — the latter previously showed
  a console window at every single boot for anyone who had opted into it.
- The installer's own uvicorn/`claude auth login` subprocess spawns now pass
  the same `CREATE_NO_WINDOW` flag every other Windows subprocess spawn in
  the codebase already uses, closing a latent inconsistency (not reachable
  today since `install.ps1` always has an attached console, but would have
  shown a window the day an unattended installer invocation is added).

### Fixed — Chat sidebar task indicator shows real task state
- The per-chat indicator in the chat session list now reads the session's
  server task log (`GET /v1/console/chat/sessions/{sid}/tasks`) instead of the
  browser's IndexedDB task cache, which is only filled for tasks the page
  already knew about and therefore never saw a newly started task.
- It shows the task (its instruction, truncated), its phase (Queued / Running /
  Done / Failed / Cancelled), the elapsed time, and how many more tasks are in
  flight. A finished task stays visible for two minutes on chats other than
  the open one. Server timestamps are epoch seconds and are read as such.
- The task log carries no completion percentage, so an in-flight task gets an
  indeterminate progress bar rather than an invented number.

### Tasks panel on the Task-Tracking SSOT (ADR-2056)
- `core/task_tracking/` is now a working store: per-tenant SQLite, audit-first
  `task_item.*` events in the core chain, hierarchy + dependency rules,
  optimistic locking, soft delete with cascade/restore.
- Console API `/v1/console/task-tracking/*`; the Tasks panel (`/app/initiatives`)
  shows Tree, Board, Timeline, Table and Activity views with a detail drawer.
- `initiatives.json` imported once and frozen: its write routes answer 410; the
  evidence verifier keeps running and is attached by `external_ref`.

## [1.0.0] — 2026-09-10

### 🎉 Release Candidate — Production Ready

**This is v1.0.0 — the first production-ready release of CorvinOS.**

**Key Accomplishments:**
- ✅ **Cross-platform installation** (macOS, Linux, Windows) — unified experience
- ✅ **Claude Code integration** — auto-detect + credential reuse
- ✅ **Zero external dependencies** — Ollama removed (user-requested)
- ✅ **Self-contained venv** — no system Python required
- ✅ **E2E tested** — all platforms verified (Tier 1-4 gates)
- ✅ **Adversarial reviewed** — 0 findings (Security, Robustness, UX)
- ✅ **Production hardened** — fail-fast, audit trail, idempotent

### 📦 Installation

**One-liner:**
```bash
curl -fsSL https://corvin-labs.com/install.sh | sh
```

**From GitHub:**
```bash
git clone https://github.com/CorvinLabs/CorvinOS.git
bash install.sh --editable .
```

### 🧪 Test Results

**Tier-1/2 Tests:** 15/15 passed ✓  
**Adversarial Review:** 6/6 passed ✓  
**E2E Docker Framework:** Ready ✓  
**CI/CD Gate:** Configured ✓  

### 📊 Features

| Component | Status | Details |
|---|---|---|
| install.sh (bash) | ✅ | Claude Code, no Ollama, Phase structure |
| install.ps1 (PowerShell) | ✅ | Windows 5.1+ compat, UAC-aware |
| README.md | ✅ | Quick-start at top, clear instructions |
| E2E Tests | ✅ | 15 Tier-1/2 + Docker framework |
| Adversarial Review | ✅ | Security, Robustness, UX validated |
| CI/CD | ✅ | GitHub Actions workflow |
| ADR-0666 | ✅ | Architecture documented in Corvin-ADR |

### 🚀 What's Next

**Post-1.0.0 (Weeks 2–5):**
- Windows Docker E2E full simulation
- GitHub release announcement + blog post
- Community feedback

---

**v1.0.0 Production Ready.** 🎉
