# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.14] — 2026-07-06

### Added
- **Real automatic self-update on Windows** (was: manual command only, since
  0.10.12). `corvin-serve` now hands off to a detached PowerShell helper that
  waits for the current process to fully exit (unlocking its own interpreter
  / extension files), runs the upgrade, and relaunches `corvin-serve` with
  the same arguments — the update actually applies without the user running
  anything by hand. Falls back to the old manual-command message if the
  handoff itself can't be spawned. Logs every step to
  `%TEMP%\corvin-self-update.log` for diagnosability, since nothing is
  attached to a console by the time most of the script runs. The
  Task-Scheduler autostart path (`install.ps1`) is unchanged — it already
  upgrades before launching `corvin-serve` as a separate step.
  **Note:** the PID-wait/relaunch sequence could only be verified for
  correct script generation and Python-side control flow here (no Windows
  machine available) — needs a real-machine check.

## [0.10.13] — 2026-07-06

### Fixed
- **License reload throttle silently swallowed the "Apply Key" reload
  ("Key applied — tier: free" even with a valid, correctly-signed key).**
  `reload_from_disk()`'s 5s rate limiter throttled ALL calls uniformly, but
  `reload_from_disk()` is also invoked on every authenticated console session
  op (`auth.py::_compute_lic_proof`, "cheap to call per session op"). In real
  browser usage that per-request call fires moments before the user submits
  "Apply Key", so the apply endpoint's own reload almost always landed inside
  the cooldown window opened by that incidental prior call and got silently
  dropped — the key was written to disk correctly, but the reload no-op'd and
  the stale (free) tier is what got reported back, with no error anywhere.
  Verified end-to-end with an actual reported key: signature and claims
  validation both passed; the bug was purely in the throttle/reload
  interaction. Fix: track a hash of the last-loaded token content — the
  throttle now only applies to redundant re-reads of *unchanged* content; a
  reload that would pick up genuinely new on-disk content always goes
  through, regardless of timing. Also: `routes/license.py`'s apply-key
  endpoint now resolves `corvin_home` via the canonical `forge_paths.
  corvin_home()` (matches every other reader/writer) instead of an ad-hoc
  `Path.home()`-only computation that could diverge from where
  `reload_from_disk()` actually looks in a source-checkout run.

## [0.10.12] — 2026-07-06

### Fixed
- **Windows: `corvin-serve` auto-update always failed with no diagnostic
  ("auto-upgrade failed. Run manually: ...").** `maybe_pypi_autoupdate()` runs
  *inside* the already-running `corvin-serve` process and tried to overwrite
  that exact process's own interpreter/extension files in place — Windows
  keeps those files locked for the process's lifetime (unlike POSIX, where a
  running executable's inode can be replaced), so the upgrade subprocess
  reliably failed with an inscrutable, swallowed error. Auto-update now skips
  the doomed live attempt on Windows and shows a clear message + the exact
  manual command instead; on other platforms, upgrade failures now surface
  the actual subprocess stderr instead of a bare "failed". The Windows
  autostart (Task Scheduler) path is unaffected — `install.ps1`'s supervisor
  already runs the upgrade as a separate step *before* launching
  `corvin-serve`, so it never hits this lock.

## [0.10.11] — 2026-07-06

### Fixed
- **Windows: license keys silently fell back to Free tier ("mode too permissive"
  false positives, 7 files):** NTFS has no POSIX group/other permission bits, so
  `os.stat().st_mode` reports a permissive-looking value on Windows regardless
  of a file's real ACLs, and `os.chmod(0o600)` cannot narrow it — every
  "reject if group/other-readable" security check in the licence/identity
  stack therefore ALWAYS tripped on Windows. Worst offender:
  `operator/license/validator.py::_find_token()` / `_find_token_disk_only()`
  rejected `session.key` on sight and `return`ed before ever checking
  `global/license.key` — so a freshly pasted "Apply License Key" was silently
  ignored and the console reported `tier: free` with no error. Also broke
  `operator/forge/forge/secret_vault.py` (hard `VaultError`, not just a
  warning) and spammed false "too permissive" warnings every few minutes from
  `operator/bridges/shared/instance_identity.py` (`instance_id.json`,
  `instance_key.pem`). Added a `sys.platform.startswith("win")` guard to all
  10 call sites across `operator/license/{validator,sync,compute_quota,
  session_refresh,shard_verifier}.py`, `operator/bridges/shared/
  instance_identity.py`, and `operator/forge/forge/secret_vault.py`. POSIX
  behaviour is unchanged (298 + 24 existing tests still green).

### Changed
- **Normal engine delegation no longer shares the ACS daily compute quota
  (supersedes ADR-0150 LIC-DELEGATE-MCP-COMPUTE-01):** `delegate_claude_code` /
  `delegate_codex` / `delegate_opencode` / `delegate_hermes` / `delegate_copilot`
  (`core/delegate/corvin_delegate/delegation.py::run_delegate`) previously
  charged the same `compute_units_per_day` pool as ACS (Free tier: 1/day),
  so exhausting either one blocked both. Maintainer decision: plain
  engine-to-engine delegation is not a metered "big data / heavy compute"
  feature and should keep working once the ACS quota is spent — only ACS
  (`chat_runtime.py` web-chat branch + `acs_engine_adapter.run_acs_workflow`)
  remains quota-gated. `engines_allowed` still gates `run_delegate` unchanged.

## [0.10.10] — 2026-07-06

### Fixed
- **Web-chat delegation completely broken since 0.9.x (`max_depth` regression):**
  a prior "increase delegation budgets" commit (a47c6d3) blanket-scaled every
  `_DELEGATION_BUDGET_DEFAULTS` field by ~100×, including `max_depth` (2→200).
  Unlike the other fields (plain iteration/worker counters), `max_depth`
  bounds *recursive* worker delegation (M4) and is hard-capped at 10 by
  `acs_validator` R32 — so every delegated web-chat turn since that commit
  failed validation with `workflow validation failed: R32 ... exceeds ceiling
  of 10`. Reset `max_depth` to `4` (matching the ACS runtime's own built-in
  recursive-depth default) in `chat_runtime.py`; the R32 safety ceiling
  itself is unchanged. Also tightened the Settings UI's `max_depth` bound
  (`routes/settings.py`) from `max: 2000` to `max: 10` so a user can no
  longer configure a value that always fails validation.

## [0.9.48] — 2026-06-28

### Fixed
- **engine.span import ordering (ADR-0171):** `engine_span` was imported at
  module level in all four spawn sites (`acs_runtime.py`, `adapter.py`,
  `a2a_worker.py`, `awp_walker.py`) before `shared/` was added to `sys.path`.
  The import silently fell back to `_espan = None`, so zero universal
  `engine.span.start/end` events were ever emitted despite the ADR-0171
  infrastructure being in place. Moved `sys.path` setup before the import in
  all four files — every engine invocation now writes audit spans.
- **TTS 422 on long responses:** `TtsRequest.text` had `max_length=8000` which
  caused Pydantic to reject valid responses containing code or long prose.
  Raised the validation ceiling to 50 000 and added silent truncation at 4 000
  chars (OpenAI TTS-1 hard limit) inside the handler.

## [0.9.45] — 2026-06-28

Security fixes, console command center, and reliability improvements.

### Fixed
- **License revocation gap (CRITICAL):** Cancelled subscriptions' JWT tokens
  remained valid for up to 400 days because the local validator only checked a
  trust-manifest endpoint that returns 404. The validator now calls
  `GET /v1/licenses/revoked` on Corvin-Features on every license load, with a
  1 h disk cache for resilience against transient network outages.
- **`exp=None` bypass:** Tokens without an `exp` claim were accepted as
  eternally valid. A missing expiry claim is now rejected immediately
  (fail-closed).
- **Console "Connection lost" bubble** no longer persists on transient WebSocket
  reconnects — only shown after confirmed disconnect.
- **TTS degrades silently** when the TTS engine is unavailable instead of
  showing a red error banner.
- **GDPR audit fingerprinting:** Discord UIDs and email addresses are now
  SHA-256-fingerprinted (first 16 hex chars) in all bridge audit events instead
  of logged raw (Art. 4(1)).

### Added
- **Server-side slash-command dispatcher (ADR-0170 M6):** `/help`, `/clear`,
  `/license`, `/quota`, `/workers`, `/audit` and all other console slash-commands
  are now dispatched server-side via the ACS command-center pipeline — enabling
  voice, API, and bridge clients to call them without a web UI.
- **ACS worker artifacts inline (ADR-0170):** Files written by delegated ACS
  worker turns are automatically surfaced as inline attachments in the chat
  (image / audio / video / PDF / JSON / HTML / CSV / text / markdown).

## [0.9.44] — 2026-06-27

Delegation reliability — two independent causes of "delegated turns fail while
direct turns work", plus a console artifact-rendering fix.

### Fixed
- **L34 delegation engine-id drift (CRITICAL for web-chat delegation):**
  `chat_runtime` classifies a delegated turn under the ACS fan-out alias
  `engine_id="acs"`, but the L34 DataFlowGuard registry only held `"acs_worker"`,
  so the guard failed closed (`unknown_engine`) and silently blocked *every*
  delegated turn while direct turns kept working. Bound the alias to the registry
  via a `DELEGATION_ENGINE_ID` single-source-of-truth (shared by producer and
  registry) and locked the invariant with regression tests. Fail-closed L34
  guarantee preserved: PUBLIC delegated turns pass; INTERNAL/CONFIDENTIAL/SECRET
  stay gated to local engines.
- **Claude CLI false-negative under stripped PATH (ADR-0159 M1):** under
  systemd/`bridge.sh` the OS-engine auto-detect probed a bare
  `shutil.which("claude")` → `None` even when claude was installed (PATH lacks
  `~/.local/bin`), silently downgrading the turn to hermes → "hermes connect
  error: timed out" and tripping the `engine.claude_cli` self-test. Auto-detect
  and `acs_runtime` now use the hardened resolver (`CORVIN_CLAUDE_BIN` → PATH →
  known install locations); `bridge.sh` resolves and exports `CORVIN_CLAUDE_BIN`
  for all children.

### Changed
- **Console inline-artifact gate** now surfaces all renderable media/data types
  (image/audio/video prefixes + pdf/json/html/csv/plain/markdown + extension
  fallback) instead of a narrow image/pdf/csv/html allow-set; files Claude or a
  delegated ACS run writes now round-trip into the chat as the messenger bridges
  already do.

### Docs
- Condensed `CLAUDE.md` to a load-bearing summary (~1060→200 lines); detail lives
  in `docs/claude-ref/*`. Documented the L34 delegation alias invariant and the
  engine-binary resolver contract.

## [0.9.42] — 2026-06-27

Security & compliance hardening from an iterative integration review of the
last development cycle (ADR-0163 ULO, ADR-0164/65 ATO, ADR-0166 SPG, ADR-0167
L35, ADR-0168 CCC, ADR-0169 L-Gates, ACS wiring).

### Security
- **SPG (CRITICAL):** admitted guests were granted the full owner command set
  (incl. `/vault` BYOK-secret access) via a hard-coded `isOwner: true`. Owner
  status is now resolved per-user; `/vault` and mutating `/objective` commands
  are owner-gated.
- **ATO:** the M5 copilot-delegation path and M7 compute blueprint bypassed the
  pre-dispatch gate chain (L34/L35/L44/license/trust); gates now run before
  spawn/return.
- **MCP:** `check_egress` failed open when the L35 egress gate could not load
  for a plugin with declared hosts — now fails closed.
- **ULO:** objectives are tenant-scoped end-to-end (no cross-tenant bleed) and
  sanitized against prompt-injection before rendering.
- **ACS:** worker/manager engine resolution now uses the request tenant instead
  of the process env (ADR-0007), preventing cross-tenant config bleed.
- **Egress:** statically forbidden hosts now win over a ratchet-allow.

### Fixed
- **GDPR Art. 17:** RAG erasure handler read a camelCase manifest key the
  validator never emits, making every erasure a silent no-op; it now reads the
  canonical `erasure_handler` key.
- **Console:** CCC chat entity action-cards linked to non-existent `/console/*`
  routes (9/10 → 404); corrected to `/app/*`.
- Spurious CCC task entities created from the bare word "task"; gate self-test
  failures now audit at CRITICAL.

### Tests
- Repaired stale tests: pytest-collection crash in `test_rag_basic`, Hermes
  bootstrap/engine-detect updated to the 2-tier qwen3 model + async contract,
  and forge path/scope tests aligned to the `.corvinOS` on-disk hard cut.

## [0.9.0] — 2026-06-23

First public PyPI release (`pip install corvinos`). Version 0.9.0 marks the
public, pre-1.0 debut of the runtime that was developed internally through the
0.x series; the lower public number signals "release-candidate quality, API may
still move before 1.0." Install and run with `pip install corvinos` then
`corvinos-serve` (web console at `http://localhost:8765`).

### Hardened — pre-release review (10 iterative adversarial rounds)

- **EU AI Act Art. 5 (L44 acceptable-use) enforced at one chokepoint.** Added
  `check_l44` to the shared `spawn_gates` SSOT and wired it — fail-closed,
  audit-first — into every authenticated engine-spawn path (bridge adapter,
  console chat, workflow nodes, ACS, gateway, A2A worker, console assistant,
  workflow-explain, task pool). Previously L44 was enforced per-surface and
  several spawn paths bypassed it.
- **No-API-key / zero-egress path works end-to-end.** The console web chat now
  drives Hermes (local Ollama) via the WorkerEngine layer; the L44 classifier
  uses a local-Ollama primary path (engine-aware ordering) so a Hermes tenant
  never reaches a cloud API; the console self-hosts its fonts (no external CDN
  beacon on load).
- **Fresh `pip install` works.** Fixed an import-order bug that left
  `forge_paths` `None` on a wheel install (10+ console pages returned 500); the
  wheel now vendors the SHA-anchored L44 policy and the engine/EU config
  templates, and ships only the built SPA (not the frontend source or CI
  artifacts). Verified by a real fresh-venv boot.
- **Audit/compliance.** L36 erasure events now carry controlled reason codes
  (no filesystem paths or exception text in the tamper-evident chain), guarded
  by a fail-closed `_emit`-boundary scrubber; RAG query audit joins the L16
  hash chain; console license gates fail closed to free-tier; DSI "Test
  connection" no longer always-reports-OK; path-gate closes `>|` / space-less
  redirect bypasses.
- **Onboarding.** `corvinos` / `corvinos-serve` entry points; correct default
  port (8765); honest install guidance; removed user-facing ADR references from
  the console UI; ACS runs triggered from chat now surface under Agentic
  Compute. Dead/legacy code removed (a dev demo that self-granted a Pro
  license, orphaned scripts, removed-subsystem references).

### Added — CopilotCliEngine: fifth WorkerEngine (ADR-0071) (2026-05-31)

- **`CopilotCliEngine`** (`operator/bridges/shared/agents/copilot_cli.py`):
  fifth `WorkerEngine` implementation. Wraps `copilot -p` (github/copilot-cli
  v1.0.56+, standalone binary). Worker-only — cannot serve as the OS engine
  (lacks `/btw` live injection, hooks, and skills_tool). Zero incremental cost
  for GitHub Copilot Business/Enterprise subscribers.

- **`delegate_copilot` MCP tool**: fifth tool on the `corvin_delegate` server.
  `model` field steers task type: `shell`, `git`, `gh` (prompt-prefix), or omit
  for general chat.

- **`copilot-worker` persona** (`operator/cowork/personas/copilot-worker.json`):
  delegation persona for CopilotCliEngine. Sets `default_engine: copilot`.

- **Console integration**: Engines page shows CopilotCliEngine as a worker-only
  card with binary-detection status.

### Changed — A2A Friendship Token (ADR-0070) (2026-05-30)

- **Friendship Token pairing** (`operator/bridges/shared/a2a_friendship.py`):
  new URL-optional pairing flow. Both peers run import-token; connection starts
  PENDING and upgrades to ACTIVE once the peer URL is known. Token format:
  `corvin-a2a:ft1:<payload>.<sig>`.

- **Console UI**: `/remote-trigger/pair/friendship/` routes for create, import,
  set-url, revoke, and list. Auto-URL detection on `GET /pair/my-url`.

- **CLI**: `corvin-a2a create-token`, `import-token`, `set-url`, `my-url`,
  `revoke-token` subcommands.

### Changed — BYOK tag filter (2026-05-30)

- Vault items must be tagged `"byok"` to appear in the BYOK UI. Prevents
  internal vault entries (provision tokens, friendship keys) from appearing on
  the API-Keys page.

### Removed — Bundle persona cleanup (2026-05-30)

- **Deleted personas**: `browser`, `jarvis`, `local-coder`, `orchestrator-haiku`,
  `hermes-worker` removed from `operator/bundle/personas/` and
  `operator/cowork/personas/`. Bundle count: 12 → 8.
  - `browser` → merged into `research` (Playwright MCP now in research)
  - `orchestrator-haiku` → superseded by Layer-29.5 Phase-3 adaptive OS-turn
    model selection (Haiku ≤60K chars, Sonnet above)
  - `local-coder` → use `/engine opencode` or `chat_profiles.default_engine`
  - `hermes-worker` → use `/engine hermes` or `chat_profiles.default_engine`
  - `jarvis` → removed; no replacement (briefing-style UX moved to assistant)

- **`research` persona updated**: now includes Playwright MCP (`mcp_servers.playwright`)
  and extended routing anchors for interactive web tasks.

### Added — HermesEngine: fourth WorkerEngine (ADR-0066 M1) (2026-05-29)

- **`HermesEngine`** (`operator/bridges/shared/agents/hermes_engine.py`):
  fourth `WorkerEngine` implementation. Drives Ollama's HTTP streaming API
  (`POST /api/chat`) via Python stdlib `urllib` — no subprocess, no new
  runtime dependency. 21/21 tests green (12 protocol-contract unit tests +
  7 live tests against local Ollama `qwen3:1.7b`).

- **L34 CONFIDENTIAL class unlocked for delegation:** `HermesEngine` maps
  to `locality=local, network_egress=none` — the only engine that qualifies
  for CONFIDENTIAL task classes under the EU_PRODUCTION preset without a
  compliance-zone exception.

- **`delegate_hermes` MCP tool** (`core/delegate/corvin_delegate/mcp_server.py`):
  fourth tool on the `corvin_delegate` server. Same input schema as the
  other three (`delegate_claude_code`, `delegate_codex`, `delegate_opencode`).

- **`hermes-worker` persona** (historical — removed in v1.2):
  was a bundled cowork persona pinning `default_engine: hermes`. Replaced by
  `/engine hermes` in-chat command or `chat_profiles.default_engine = "hermes"`.

- **Boot self-test** (`operator/bridges/shared/self_test.py`):
  `_check_hermes_ollama()` probes `GET /api/tags` (2 s timeout). WARNING if
  Ollama is unreachable — never CRITICAL; adapter starts normally without it.

- **`resolver.py` delegation brief** updated to mention four engines;
  `_inject_delegate_capability` adds `delegate_hermes` to `allowed_tools`.

- **`code.hermes_delegation` project-scope skill** registered in skill-forge
  with routing guidance, model alias table, and L34 CONFIDENTIAL note.

- **Adapter direct-dispatch path** (`operator/bridges/shared/adapter.py`):
  `_call_hermes_streaming_via_engine()` + dispatch branch for
  `profile.default_engine == "hermes"`. Closes the gap where `hermes-worker`
  persona silently fell through to Claude Code. No subprocess management —
  identical queue/idle-watchdog pattern to the OpenCode path.

### Added — HermesEngine Production Parity (ADR-0067 M2.1–M2.5) (2026-05-29)

- **M2.1 Compliance gates:** `_run_pre_dispatch_gates()` helper runs L30.1b
  engine-trust, L34 data-classification, and L35 egress gates before every
  Hermes and OpenCode OS-turn spawn — closing the GDPR Art. 30 audit gap.
  `agents/trust/hermes.yaml` trust manifest (tier=low) added.
  `data_classification.py::DEFAULT_ENGINE_COMPLIANCE` now includes
  `hermes: {locality=local, network_egress=none}`.

- **M2.2 Audit events:** 10 new event types registered in `security_events.py`:
  `hermes.turn_start/end/error/stream_timeout/ollama_unavailable` and
  `opencode.turn_start/end/error/stream_timeout`. All emitted from the
  respective streaming functions; `console.engine_setting_updated` also added.

- **M2.3 `/engine hermes` switcher:** `engine_switch.py` — hermes and aliases
  (`hermes-fast/balanced/capable/large`, `local-hermes`) added to
  `ENGINE_ALIASES`, `VALID_ENGINES`, and `supported_aliases()`.

- **M2.4 Console engine selector:** New `routes/engine.py` in
  `core/console/corvin_console/` — `GET/PUT /settings/engine` reads/writes
  `tenant.corvin.yaml::spec.default_engine`; `GET /settings/engine/health`
  probes Ollama (base_url_hash only, never full URL). Adapter reads
  `spec.default_engine` as tenant-level default in the engine dispatch
  resolution order.

- **M2.5 Prometheus metrics:** `operator/bridges/shared/engine_metrics.py` —
  lazy `prometheus_client` Counters + Histograms for Hermes and OpenCode
  OS-turns. Called from both streaming functions (best-effort, never blocks).

- **E2E verification:** `test_hermes_e2e_full.py` T07 adds 9 checks for
  M2.1–M2.5; full suite 30/31 green (1 skipped: fastapi absent in unit env).

---

## [0.19.0] — 2026-05-26

### Added — EU AI Act Certification Package Complete (2026-05-26)

**All structural EU AI Act certification gaps closed. Corvin is now
self-assessment-ready for EU AI Act 2026 (Limited Risk, Art. 50 + Art. 73)
and multi-framework aligned (ISO 42001 + NIST AI RMF).**

- **Content Marking (Art. 50 §4):** Every final outbound message carries a
  machine-readable `provenance` block (`ai_generated`, `generator_id`,
  `persona`, `session_id`, `timestamp_utc`). Injected in `adapter.py
  _envelope()` on `_final=True`; omitted from progress/heartbeat envelopes.
  13 unit tests covering all bridge types and edge cases.
  (`ADR-0057 M1`, `test_content_marking.py`)

- **L40 Incident Tracker (Art. 73):** `incident_tracker.py` — structured
  incident records for the 6 serious-incident categories (chain integrity,
  consent bypass, engine policy violation, PII in audit chain, secret
  exposure, disclosure failure). `IncidentAutoDetector` hooks into CRITICAL
  audit events. `notify-draft` generates Art. 73 §2 BSI/ENISA notification
  draft. 17 unit tests. (`ADR-0057 M6`, `test_incident_tracker.py`)

- **Operator Declaration Gate (Art. 28–30):** `operator_declaration.py` —
  boot-time CRITICAL probe in `eu_production`/`eu_production_ollama`
  profiles; blocks adapter start if `dpia_completed: false` or declaration
  absent. Audit event `operator.declaration_verified` (PII-stripped).
  10 unit tests. (`ADR-0057 M7`, `test_operator_declaration.py`)

- **Annex IV Generator (Art. 43):** `corvin_annex_iv.py` — reproducible
  Annex IV Technical Documentation assembled from manifests + ADRs.
  Subcommands: `generate`, `validate`, `cross-reference`, `export-package`.
  `export-package` bundles all 4 framework YAMLs + compliance report + test
  summary + signed SHA-256 manifest for Notified Body delivery.
  13 smoke tests. (`ADR-0057 M8`, `test_corvin_annex_iv.py`)

- **Multi-Framework Compliance Manifest (ADR-0060):** Machine-readable
  `compliance/iso-42001.yaml` (22 clauses) + `compliance/nist-ai-rmf.yaml`
  (22 rules across GOVERN/MAP/MEASURE/MANAGE). GPG-signed together with
  existing `eu-ai-act.yaml` + `gdpr.yaml`. `corvin-compliance-check
  --all-frameworks` now evaluates 60 rules: **60 passed, 0 warnings**.
  Cross-reference table links every NIST/ISO clause to its EU AI Act article.

- **Compliance CI Gate:** `compliance/ci_review.py` extended with L37/L38/L39
  layer-pattern map so PRs touching `audit_sealer`, `remote_trigger`, `a2a_*`,
  and `incident_tracker` trigger the Haiku compliance review automatically.

- **sign.sh:** Non-interactive re-signing (`--pinentry-mode loopback`) works
  without a TTY — enables CI and Discord-bridge signing sessions.

- **ADR Status Updates:** ADR-0056, ADR-0057, ADR-0061 promoted from Draft
  to Accepted.

- **Test Suite:** 4 new ADR-0057 test suites added to `run-all-tests.sh`
  (total: +53 tests across 4 files).

### Added — Phase 7 + Production-Ready Planning (2026-05-21)

**EU compliance complete + v1.0.0 roadmap.**

Corvin is now structurally complete for EU AI Act Art. 50 and GDPR Art. 6, 7, 17, 30, 32 deployments (Phase 7 complete, ADR-0046). All four enforcement layers ship with tests: L34 data classification + flow guard, L35 network egress lockdown, L37 audit-at-rest encryption with RFC 3161 TSA, L36 GDPR Art. 17 erasure orchestrator. Compliance gate wired into adapter. Compliance documentation package (DPIA template, DSB checklist, privacy notice, pentest scope, reports guide) added.

**Production-Ready roadmap complete (ADR-0042):**
- 12-week v1.0.0 release roadmap (284 engineer-hours)
- 5 parallel streams: Code Quality (51h) + Docs (90h) + Ops (80h) + Security (57h) + Community (6h)
- Installation refactor plan: website-first setup, no CLI needed
- Bug-fix execution guide for all 34 remaining code issues
- See: [`ADR-0042-production-ready-roadmap.md`](docs/decisions/ADR-0042-production-ready-roadmap.md)

### Added — Layer 37: RFC 3161 TSA external timestamping (ADR-001 open item #1 — 2026-05-21)

After a successful `age`/`gpg` seal, `rotate_and_seal` computes the
SHA-256 of the sealed file, builds a minimal RFC 3161
`TimeStampReq` (59-byte DER, pure stdlib), POSTs to the operator's
`tsa_url`, and writes the raw `TimeStampResp` as
`<sealed>.{age,gpg}.tsr` (chmod 444). Emits `audit.segment_timestamped`
(INFO) on success; `audit.tsa_request_failed` (WARNING, non-fatal)
on any network/HTTP failure — the seal stands regardless. TSA is
opt-in via `spec.audit.encryption_at_rest.tsa_enabled: true`.
Closes the insider-fabrication gap identified in ADR-001 open item #1.
40-test suite covers TSA happy-path, failure non-fatal, disabled-default,
correct DER structure, and no-anthropic AST-lint.

### Added — ADR-001: OpenCode EU AI Act compliance architecture

`docs/decisions/ADR-001-opencode-eu-ai-act.md` records the full
EU AI Act + GDPR architecture review for OpenCode-as-engine deployments.
Covers Art. 50 disclosure, GDPR Art. 6/7/17/30/32 controls, the
three-layer defence (ADR-0007 engine identity + L34 data classification
+ L35 egress lockdown), and open items resolved in subsequent milestones.
Companion: `core/compliance/ARCHITECTURE.svg`.

### Added — Layer 37: daily audit-rotate systemd timer (M3.7)

`operator/voice/scripts/systemd/corvin-audit-rotate.timer` +
`corvin-audit-rotate.service` invoke `audit_rotate.py` daily for
scheduled rotation + sealing without operator intervention. Mirrors the
existing `corvin-audit-verify.timer` pattern.

### Added — Layer 37: `voice-audit verify --include-sealed` + `voice-audit unseal` (M3.5)

`voice_audit.py verify --include-sealed [--identity <key>]` walks all
rotated sealed segments, unseals each into a tmpdir, verifies the
per-segment chain, then checks cross-segment `prev_hash` continuity.
`voice-audit unseal <segment>` provides the DPO/legal-hold operator
path: emits `audit.unseal_requested` (WARNING) before decryption,
decrypts into a mode-0600 tmpdir file, caller is responsible for
cleanup. Tests in `test_voice_audit_sealed_verify.py`.

### Added — Layer 36: per-layer ErasureHandler implementations

`operator/bridges/shared/erasure_handlers.py` ships the first real
per-layer handlers: `L28RecallHandler` (full SQL DELETE + FTS5
rebuild), `L33ArtifactHandler` (FS purge of unpinned session artifacts
+ manifest tombstone), `L7SkillForgeHandler` and `L24DataSnapshotHandler`
as documented stubs (operator subclasses / replaces), and
`IdentityMappingHandlerBase` for the operator-owned subject_id ↔
identity mapping. `real_handler_chain()` wires L28 + L33 automatically
in the CLI; `--use-stubs` keeps the M4 stub-only mode for testing.

### Added — Layer 36: admin-console erasure route (M4.6)

`/v1/admin/tenants/{tid}/erasure` REST endpoint in `corvin-admin`
exposes the full `ErasureOrchestrator` for DPO workflows without
needing the CLI. Emits the same audit chain as the CLI path.

### Added — Layer 36: `corvin-erasure` CLI (M4.5)

`operator/voice/scripts/corvin_erasure.py` provides a thin CLI
wrapper: `corvin-erasure <subject_id> [--tenant <tid>] [--use-stubs]`.
Registers handlers via `register_handler()`, runs the orchestrator,
prints a per-layer status table, and exits non-zero on PARTIAL or
FAILED aggregates.

### Added — Layer 35: L35 + L37 doctor checks in `self_test.py` (M2.6 / M3.6)

`_check_egress()` adds `egress.preset_loaded` / `egress.preset_consistency`
checks to the full self-test / `bridge.sh doctor` run. Sealer-binary
availability (`age`/`gpg` on PATH) is also verified when
`encryption_at_rest.enabled: true` in tenant config.

### Added — Layer 34: compliance gate wired into adapter (M2.5)

`adapter.py::_compliance_gate()` wires `DataFlowGuard` and
`EgressGate` together at every engine-spawn callsite. Classification
is computed by `classify_task(prompt, persona)` with mtime-based
hot-reload per tenant. `data_flow.blocked` and `egress.blocked` are
CRITICAL and fail-closed; both guards fail-open when the tenant has
no configuration (backward compat for pre-L34/L35 tenants).

### Added — EU compliance test suite

`operator/bridges/shared/test_eu_compliance.py` provides a
cross-layer E2E suite covering the three-layer defence (L34 + L35 +
ADR-0007 engine identity), erasure orchestrator, and the audit-chain
integrity assertions required by GDPR Art. 30/32 for EU deployments.

### Added — Compliance documentation package

`docs/compliance/` adds: `DPIA-TEMPLATE.md` (Art. 35 template),
`DSB-CHECKLIST.md` (DPO/DSB sign-off checklist),
`PRIVACY-NOTICE-TEMPLATE.md` (Art. 13/14 template),
`PENTEST-SCOPE.md` (pen-test scope), `COMPLIANCE-REPORT-GUIDE.md`
(how to generate GDPR Art. 30 reports using `corvin-compliance-reports`).

### Added — Layer 29.5 Phase 2 — opt-in Haiku OS-turn (`orchestrator-haiku` persona)

The cost-split now reaches the bridge OS-turn itself. A new persona
field `helper_model_default: true` opts a persona into Haiku-4.5 for
its own `claude -p` subprocess — explicit `model:` overrides still
win, the env opt-out works the same way as for helper sites
(`CORVIN_HELPER_MODEL_OS_TURN=none`), and every legacy persona
without the flag stays byte-identical on the subscription default
(Opus / Sonnet). A bundle persona `orchestrator-haiku.json` lands in
`outputs/` for operator-side installation (the Layer-10 v2 path-gate
correctly blocks LLM-side writes into the persona tree). 11 new test
cases in `test_adapter_os_model.py` cover the resolution table plus
the argv-landing E2E. Phase 2 docs in `CLAUDE.md` § "Layer 29.5
Phase 2".

### Added — Layer 29.5 helper-model cost-split (Haiku for OS-overhead)

OS-side helper subprocesses (voice summaries, dialectic judges,
user-style learner, user-model distiller, delegate output-judge,
router auto-mode) now default to Haiku-4.5 via a shared resolver
in `operator/bridges/shared/helper_model.py`. Worker engines
(Claude Code / Codex CLI / OpenCode) keep the user's default model
(Opus / Sonnet) — only the around-the-task overhead flips to the
cheaper / faster model. Resolution: per-site env
(`CORVIN_HELPER_MODEL_<SITE_UPPER>`) > global env
(`CORVIN_HELPER_MODEL`) > built-in default
(`claude-haiku-4-5-20251001`). Opt-out per site (or globally) via
`""` / `"none"` / `"default"` / `"off"`. Seven curated `SITE_*`
identifiers cover every existing helper. 30 new test cases (17
pure-lib + 13 per-site E2E) plus a no-LLM-SDK AST-lint enforce the
cost contract. Full docs in `CLAUDE.md` § "Layer 29.5".

## [0.13.0] — 2026-05-10

Big bundle release covering Phase-4 productionisation (signal routing,
init-daemon supervisor, budget gate), the Layer 22 `WorkerEngine`
protocol with full adapter migration (AWP Phase 1+2), and the bulk of
the CorvinOS → Corvin rebrand (Phases 1–5 complete, Phase 7-1
landed). Also: tag-based auto-update, `/welcome`, `/sig` cleanup.

Phase 7 (hard cut → v1.0) backlog now lives at `docs/phase7-backlog.md`.

### Project rebrand: CorvinOS → Corvin (Phases 1–5 + 7-1 landed)

The framework is being renamed from `CorvinOS` to `Corvin` because
it has become engine-agnostic (Claude Code, Codex CLI, future engines
via the Layer 22 `WorkerEngine` protocol) — the legacy name was
misleading. Migration uses the strangler-fig pattern: legacy
`CORVIN_*` env vars and the `~/.CorvinOS/` data directory keep
working until rebrand-Phase 7. As of this release: Phases 0, 1, 2, 3,
4, 5 complete; Phase 7-1 (`CORVIN_SECRET_VAULT` alias removed);
Phase 6 (repo-folder rename — operator action) and Phase 7 hard cut
still ahead. See `docs/phase7-backlog.md` for the explicit Phase-7
checklist and `CLAUDE.md` "Project rebrand" section for the full
roadmap and inventory snapshot trail.

### Auto-update — tag-based release tracking

Every `bridge.sh up` / `restart` / `fg` and the `SessionStart` hook now
call `operator/voice/scripts/autoupdate.sh`. The script considers only
tags matching `v*` (semver) — pushes on a branch are never pulled in.
Steady-state is detached HEAD on the latest release tag. Skip rules:
`<repo>/.corvin/no-auto-update` marker, `autoupdate: false` in
`~/.config/corvin-voice/config.json`, dirty working tree, repo not git,
HEAD has commits not in the latest tag (dev-tree guard). 10-case shell
E2E in `operator/voice/scripts/test_autoupdate.sh`.

### Layer 22 — `WorkerEngine` protocol (AWP Phase 1+2)

Backend-agnostic engine layer that lets Corvin spawn LLM-CLI
subprocesses through a unified contract. `bridges/shared/agents/`:
`__init__.py` (Protocol + StreamEvent + collect helper),
`claude_code.py` (full claude `-p --output-format stream-json` engine),
`codex_cli.py` (`codex exec --json`). `CORVIN_USE_ENGINE_LAYER`
defaults to `1` since Phase 2.4. Adapter `_call_claude_streaming_via_engine`
mirrors the legacy direct-spawn loop 1:1; `/btw` routes through
`engine.inject()`. Legacy direct-spawn path stays behind the env flag
for the 14-day Phase 2.5 soak. ADR-0001 + ADR-0002 in
`docs/decisions/`.

### `/welcome` slash-command

New slash-command with separate WhatsApp voice-note onboarding
flow — see commit `87963a9`.

### `[CORVIN_SIGNAL: <name>]` dual-fire

The magic-prefix marker emitted by `/sig` is now dual-fired alongside
`[CORVIN_SIGNAL: <name>]` on the same line so persona `append_system`
blocks that grep for either form continue to work; both forms drop in
Phase 7.

### Phase 4 productionisation (4.1.5 + 4.2 + 4.3)

Phase-4 progression. The Phase-4 roadmap from 0.12.0 was 5 items
totalling ~12-15 weeks of engineering plus federation. This release
lands the highest-leverage portion: full /sig signal routing
(Phase 4.1.5), init.py daemon-mode supervisor (Phase 4.2), and the
active pre-flight budget gate (Phase 4.3). bridge.sh migration (4.4)
and federation (4.5) remain explicitly deferred.

### Added

#### Phase 4.1.5 — custom signal routing (`/sig`)

  - **`adapter.py`** — new `_signal` envelope type, recognised by
    `_peek_side_channel` (bypass-lock alongside `_btw` / `_cancel` /
    `_observer`). Handler in `process_one` resolves `session_id` →
    `chat_key` via `process_table.get_session`, then dispatches:
    `KILL` → `_cancel_chat(chat_key)` (SIGTERM the process group);
    `PLAN` / `SUMMARIZE` / `CONTEXT_DROP` / `QUIET` / `RESUME` →
    `inject_btw(chat_key, "[CORVIN_SIGNAL: <NAME>] [CORVIN_SIGNAL:
    <NAME>]")`, a magic-prefix stream-json user message that the
    persona's `append_system` interprets. Both marker spellings are
    sent on the same line until rebrand-Phase 7; either grep pattern
    fires. Unknown markers are graceful no-op (the model treats the
    marker as ambient text). All paths emit
    `bridge.signal_inject` audit events with `delivered` boolean +
    `reason` string.
  - **`phase3_cli.py sig <session_id> <SIGNAL>`** — writes the
    `_signal` envelope to `bridges/<channel>/inbox/`. Validates
    signal name against the curated set before write. `ADAPTER_INBOX`
    env override for tests.
  - **`/sig`** and **`/signal`** slash commands in
    `in_chat_commands.js` route through the unified phase3_cli.
  - 8-case E2E test (`test_signal_routing.py`): envelope shape, CLI
    validation, adapter handler full E2E, unknown-session and
    unsupported-signal flows.

#### Phase 4.2 — init.py daemon mode (Unix-socket IPC)

  - **`init.py daemon`** — long-lived supervisor process. Discovers
    services from plugin roots, starts them in topological order
    (autostart toggleable via `CORVIN_INIT_NO_AUTOSTART=1`),
    listens on `<corvinos_home>/run/init.sock`, ticks the supervisor
    on each `select` timeout (configurable via
    `CORVIN_INIT_TICK_INTERVAL`), reaps SIGTERM/SIGINT into a
    graceful `shutdown_all` in reverse-topological order.
  - Socket protocol: line-delimited JSON over AF_UNIX/SOCK_STREAM.
    One request per connection: `{"command": "...", "args": [...]}`.
    Reply: `{"ok": bool, ...}`. Stale-socket-file cleanup at boot.
  - Commands: `ping`, `list`, `status <name>`, `start <name>`,
    `stop <name>`, `restart <name>`, `deps <name>`,
    `journal <name> [N]`, `reload <name>`, `shutdown`.
  - **`daemon_call(command, *args)`** — small client helper used by
    the CLI and tests. Returns `{ok: false, error: ...}` on
    connection failure (daemon not running) instead of raising.
  - **`phase3_cli.py svc <start|stop|restart|status|journal|reload>`**
    now connects to the daemon via socket. `svc list` queries the
    daemon for live status; falls back to manifest-only listing
    when the daemon isn't running, with a clear inline note. `svc
    deps` stays manifest-only (no daemon required).
  - 10-case E2E test (`test_daemon.py`): real subprocess for the
    daemon, real subprocesses for the supervised services (Python
    sleep loops + `echo`), real Unix-domain socket round-trips,
    real SIGTERM. Covers ping, list, start spawning real PIDs,
    stop reaping subprocesses, unknown-service / unknown-command
    error paths, shutdown command, SIGTERM graceful exit (with
    child reap), stale-socket cleanup with retry, and journal-tail
    capture of child stdout.

### Test coverage

```
14 → 15 Python E2E suites green
+ 16 cases this release across:
  - test_signal_routing.py:    8/8
  - test_daemon.py:           10/10
+ /sig + svc daemon + /pipe handlers all wired through
  in_chat_commands.js phase3Reply
```

#### Phase 4.3 — active pre-flight budget gate

  - **`adapter.py`** — new `_budget_preflight(chat_key, prompt)` runs
    BEFORE the subprocess spawn (and before the FAKE_CLAUDE
    short-circuit, so tests cover the gate too). Auto-registers a
    per-chat budget on first encounter (default quota 100k tokens,
    default policy `compress`; both overridable via
    `ADAPTER_BUDGET_DEFAULT_QUOTA` and `ADAPTER_BUDGET_DEFAULT_POLICY`
    env vars). Calls `context_budget.check_budget` with a
    character-based pending-tokens estimate (`len(text) // 4` —
    well-known approximation for Claude; production can swap in
    `tiktoken`'s `cl100k_base` by overriding `_estimate_tokens`).
  - On `action == "reject"` + `allowed == False`: returns a structured
    German-language refusal text listing the operator's escalation
    options (`/budget policy <chat> evict`, `compress`, `/reset`,
    operator-side quota raise). Subprocess is NOT spawned. Audit
    event `bridge.budget_rejected` lands in the unified hash chain.
  - On `action == "warn"` (≥ 90% of quota): logs a warning, allows
    the turn through.
  - On `action == "ok"` / `evict` / `compress`: passes through.
    `evict` and `compress` are non-blocking actions in this MVP —
    the configured action becomes meaningful once Phase 4.3.5 wires
    automatic eviction into the working-set tracker.
  - **`_budget_account_turn(chat_key, msg_id, prompt, reply)`** —
    fires after every successful turn (both real-claude and
    fake-stream paths) so `/budget show` reflects real per-chat
    usage. Best-effort: budget infrastructure failures fall through
    with a log line, never block production traffic.
  - **`bridge.budget_rejected` audit event** with chat_key, used,
    quota, configured policy.

  - 10-case E2E test (`test_budget_gate.py`):
    - First turn auto-registers a budget with default quota +
      policy
    - Successful turn accounts estimated tokens
    - REJECT + over-quota: subprocess NOT spawned, refusal text
      returned, used count unchanged (reject doesn't consume budget)
    - EVICT + over-quota: subprocess STILL spawns (non-blocking
      action)
    - COMPRESS + over-quota: same
    - WARN at 92% logs but passes; account_turn fires
    - Audit event lands on REJECT
    - Missing `context_budget` module → graceful no-op (allow)
    - `check_budget` raising → graceful no-op (allow), failure logged
    - `format_budget_table` after a turn shows the per-chat usage

### Production-readiness fixes (post-review)

A second code-review pass over the Phase-4.1.5 + 4.2 commits surfaced:

  - **Critical** — `init.py daemon`: Unix socket created with umask-
    derived permissions BEFORE the explicit `chmod(0o600)`, leaving
    a tiny race window where another local user could connect.
    Fixed: `os.umask(0o077)` is set BEFORE `sock.bind()` and
    restored after (in a try/except finally). `chmod(0o600)`
    becomes belt-and-braces.
  - **Important** — `init.py daemon`: backlog `listen(8)` is too
    shallow under burst-connect load — bumped to `listen(64)`
    so a flurry of incoming `/svc` commands doesn't ECONNREFUSED.
  - **Important** — `adapter.py _signal` handler: stale-session-
    window — between `process_table.get_session(target)` and
    `inject_btw(chat_key)` / `_cancel_chat(chat_key)`, a NEWER
    session could have started in the same chat. The signal would
    then go to the wrong process. Fixed: compare the registry's
    pid against the live `_running_subprocs[chat_key]` head; refuse
    with `"session race"` reason on mismatch.

### Phase-4 still deferred (explicitly)

  - **4.4** bridge.sh full migration to call into the daemon —
    structural change to operator entry point, depends on 4.2
    soaking in production
  - **4.5** Layer 21 federation — multi-host, real WireGuard +
    mTLS, intentionally not built (chat-untestable infrastructure)
  - **4.3.5** active eviction / compression — currently the
    `evict` and `compress` policies are non-blocking pass-throughs;
    automatic eviction wiring + claude-side compression are a
    next-slice extension

---

## [0.12.0] — 2026-05-08

Five new layers and a complete integration pass. CorvinOS now legitimately
deserves the OS label for four of the five concept-os-completion gaps —
process model, inter-session pipes, service manager, and context memory
manager. Federation (Layer 21) stays intentionally not implemented because
real WireGuard/mTLS testing isn't safe in a chat. Phase 3 brings all four
new layers into the live messenger surface.

### Added

#### Layer 17 — process model (`bridges/shared/process_table.py`)

Visible session lifecycle backed by `<corvinos_home>/run/sessions.jsonl`:
register / update / deregister / list / get / cleanup_terminated. fcntl.flock
on a sidecar lock file serialises writers; mtime-cached reads. 17 E2E cases
including 4-thread concurrent register (no losses), corrupt-line resilience,
and the `format_ps_table` chat-friendly fixed-width renderer. Adapter
integration in `call_claude_streaming` — register on subprocess spawn,
update on every `tool_use` event with the in-flight tool name, deregister
in `finally` with `exit_reason="killed"` on stream-idle timeout. Slash
command `/ps` and `/ps -a` route through `phase3_cli.py`.

#### Layer 18 — inter-session pipes

  - **`bridges/shared/pipe_registry.py`** — three pipe modes: named FIFO
    (multi-write/multi-read, persistent), anonymous (single-read auto-removes
    the pipe), broadcast (per-subscriber cursor with late-subscriber seeding
    so observers only see writes after subscribe). Validates names against
    path traversal. 17 E2E cases.
  - **`plugins/corvinos-pipe/`** — MCP server exposing nine pipe tools
    (`pipe_create`, `pipe_write`, `pipe_read`, `pipe_subscribe`, `pipe_unsubscribe`,
    `pipe_list`, `pipe_remove`, `pipe_get_meta`, `pipe_queue_depth`) over
    JSON-RPC 2.0 stdio. Domain errors surface as MCP `isError=true` result
    envelopes (per spec); JSON-RPC errors reserved for protocol violations.
    10 E2E cases via real subprocess + real JSON-RPC round-trip.
  - Slash command `/pipe <list|create|write|read|rm|meta>` routes through
    `phase3_cli.py`.

#### Layer 19 — service manager

  - **`plugins/corvinos-init/init.py`** — supervisor with dependency-graph
    init (`requires` hard, `wants` soft, cycle detection, missing-dep
    rejection), three backoff modes (exponential / linear / none),
    `max_restarts` cap, hot_reload signal delivery (`exec` prefix in
    `exec_start` required so signal reaches binary not `/bin/sh`),
    reverse-topo `shutdown_all`, per-service journal capture with
    memory-bounded tail (deque, not slurp-and-slice). 15 E2E cases
    including real-subprocess restart loops with an injectable clock.
  - **Seven `*.service.yaml` manifests** for the existing services —
    forge-mcp, skill-forge-mcp, voice-adapter, and four bridge daemons.
    Smoke-tested via `discover_services` + `topological_order`; correct
    startup order (forge-mcp → skill-forge-mcp → voice-adapter → daemons).
  - Slash commands `/svc list` and `/svc deps <name>` route through
    `phase3_cli.py`. `/svc start/stop/restart/journal` deferred to
    Phase 4 (needs init.py running as supervisor under bridge.sh).

#### Layer 20 — context memory manager

  - **`bridges/shared/context_budget.py`** — per-session token quotas with
    the `ok / warn / oom-policy` action ladder. Three OOM policies:
    `evict` (drop oldest), `compress` (caller summarises), `reject` (refuse
    new turn). Eviction by `target_pct` or absolute `target_used`; returns
    dropped turn_ids, increments `evictions` counter. 18 E2E cases including
    4-thread concurrent token accounting.
  - **`bridges/shared/context_cold_storage.py`** — pluggable
    `EmbeddingProvider` Protocol. Ships `HashEmbeddingProvider` for
    offline/test runs (no API needed); production swaps in
    `OpenAIEmbeddingProvider` (`text-embedding-3-small`) or a local
    sentence-transformer without code changes. Page-out / page-in /
    purge / cosine-similarity ranking. Cross-provider safety: pages
    embedded with provider A are skipped when querying with provider B
    (`embedded_with` field). 20 E2E cases.
  - Slash command `/budget show [<session_id>]` and `/budget policy
    <session> <evict|compress|reject>` route through `phase3_cli.py`.

#### Phase 3 — bridge integration

  - **`bridges/shared/phase3_cli.py`** — unified CLI wrapper that all four
    new slash commands shell out to. Mirrors the existing
    `schedule_cli` / `profile_cli` / `vault_cli` pattern.
  - **`bridges/shared/js/in_chat_commands.js`** — dispatcher cases for
    `/ps`, `/pipe`, `/svc`, `/budget`. 31 E2E cases against the real CLI
    subprocess in `test_phase3_dispatch.js`.

#### Layer 16 — observer-transcript + read-only role visibility split

(Landed during the Phase-3 push as part of the M-file commit hygiene step.)

  - Daemon-side `getObserverVisibility` and `_observer` side-channel
    envelope writes
  - Adapter-side `_inbox_sender_is_read_only` TOCTOU re-check,
    `_observer_buffer_path / _append_observer_message /
    _consume_observer_buffer` ring buffer
  - Framing block (`[OBSERVER TRANSCRIPT — context only, NOT a command]`)
    is the structural barrier between observer text and LLM instruction
  - Audit events: `bridge.read_only_drop`, `bridge.observer_appended`,
    `bridge.observer_transcript_consumed`, `bridge.inbox_whitelist_drift`
  - JS contract test (`test_observer_visibility.js`, 7 cases) +
    Adapter E2E (`test_adapter_security_hardening.py`, 218-line addition)

#### Documentation

  - **`docs/concept-os-completion.md`** — full design doc for Layers
    17-21, messenger-first invariants, phasing, anti-scope (no multi-user,
    no web UI, no custom kernel)
  - **`docs/skills.md`** — runtime knowledge factory ref (linter, grading,
    promotion, slot-mirror)
  - **`docs/diagrams/10-skill-lifecycle.svg`** — four-phase visual
  - **`docs/diagrams/04-security-envelope.svg`** — bumped from four to
    six concentric surfaces (whitelist, persona ACL, policy + linter,
    sandbox + loopback-deny, path-gate, operator elevation)
  - README "Deep-dive docs" block pinned right under the architecture
    diagram with four clickable subsystem links
  - Layer-model.md entries for Layers 15-20 with status notes

### Production-readiness fixes (post-review)

A code-review pass surfaced seven issues; all fixed before tagging:

  - **Critical** — `init.py`: log file handle leak on `stop()` /
    auto-restart caused interleaved-write log corruption. `ServiceState`
    now tracks `log_fh`, `_close_log_fh()` is called on every exit path
    (manual stop, supervised exit-detection). Idempotent + best-effort.
  - **Critical** — `process_table.py`: `_exclusive_lock` now wraps
    `fcntl.flock(LOCK_UN)` in a defensive try/except + only attempts
    unlock when lock was actually acquired. fd close runs in any case.
  - **Important** — `adapter.py`: `secrets.token_hex(3)` (16M combos,
    realistic collision risk on long-running bridges) → `token_hex(6)`
    (281T combos, won't collide before heat-death).
  - **Important** — `context_cold_storage.py`: documented the silent
    cliff that follows an embedder swap (pages with the old `embedded_with`
    are skipped on query) and the fact that `min_similarity` thresholds
    don't transfer between providers.
  - **Important** — `corvinos-pipe/mcp_server.py`: explicit
    `_cleanup_streams()` in a `finally` block flushes stdout and closes
    non-stdin/stdout streams on shutdown, so abrupt-disconnect clients
    don't leave the server hung on `readline()`.
  - **Minor** — `init.py::journal_tail`: switched from
    `read_text().splitlines()[-n:]` (loads entire file) to
    `collections.deque(maxlen=n)` streaming.
  - **Minor** — `context_cold_storage.py::_session_dir`: reject
    `\x00` in `session_id` (truncates filenames on some FS).

### Test coverage at release time

```
Layer 16 observer transcript:     ALL GREEN  (existing, M-file commit)
Layer 17 process_table:           17/17
Layer 18 pipe_registry:           17/17
Layer 18 MCP server:              10/10
Layer 19 init/supervisor:         15/15
Layer 20 budget:                  18/18
Layer 20 cold-storage:            20/20
Phase-3 JS dispatcher:            31/31
Layer-16 secret-injection:        58/58
Layer-16 observer (JS):            7/7
path-gate hook:                   43/0
Existing adapter tests (4):       ALL GREEN
                                  ────
                                  165+ green E2E cases
```

### Commits in this release

```
1d19a9c  feat(layer16/v3): secret-injection (capability-style) for forged tools
db2ff9f  feat(layer16):    observer-transcript + read-only role visibility split
2a8657e  feat(layers 17-20): Phase 3 — bridge integration + slash commands
+ Phase-1+2 commits for layers 17, 18, 19, 20 (eight feat commits + four doc)
```

### Phase-4 deferred (next release)

  - `/kill <session>`, `/sig <session> <SIGNAL>`, `/nice` — daemon-side
    signal-routing for PLAN/SUMMARIZE/CONTEXT_DROP/QUIET
  - `/svc start/stop/restart/journal` — needs init.py running as
    supervisor under bridge.sh
  - Active pre-flight budget gate (auto-evict/compress before each turn)
  - `bridge.sh` full migration to call into init.py
  - Layer 21 federation (intentionally deferred: real
    WireGuard/mTLS/cross-host audit isn't safely testable in chat)

---

## [0.11.0] — 2026-05-08

Layer 14 — **LDD-toggle system**. Every load-bearing LDD discipline can
now be flipped on or off independently — globally, per chat, or per
persona — and the effect is *structural* (skill injection, dialectic
gates, native sites all consult one gate function), not documentation.
Plus a hard-cascade mechanism for sinnlos child/parent combinations and
per-persona LDD profiles tuned to each persona's actual job.

### Added

- **`bridges/shared/ldd.py`** — toggle library with twelve canonical
  layer IDs (`loop_driven_engineering`, `e2e_driven_iteration`,
  `dialectical_reasoning`, `dialectical_cot`, `root_cause_by_layer`,
  `docs_as_dod`, `reproducibility_first`, `loss_backprop_lens`,
  `method_evolution`, `drift_detection`, `iterative_refinement`,
  `per_subtask_e2e`). Storage in `<scope_root>/global/ldd.json` with
  mtime-cache; resolution chain is direct-state (profile per-layer >
  profile master > cfg master > cfg per-layer > default-on) followed by
  the hard-cascade gate. Cost contract enforced by CI lint
  (`test_ldd_lib.py::case_no_anthropic_sdk_import`).
- **Hard-cascade dependencies** (`DEPENDS_ON`):
  `dialectical_cot → dialectical_reasoning`,
  `per_subtask_e2e → e2e_driven_iteration`,
  `drift_detection → docs_as_dod`. Cascade is read-path: child stays
  off when parent is off, even with explicit profile-per-layer
  override; flipping the parent back on auto-reactivates the child.
- **Slash-commands**: `/ldd-on`, `/ldd-off`, `/ldd-status`,
  `/ldd-set <layer> <on|off>`, `/ldd-preset <default|strict|quick|off>`
  in all four daemons. `/ldd-status` shows cascade source per layer
  + dependency table; `/ldd-set` warns when a parent is off and
  prints a cascade hint when a parent is flipped off.
- **Native integration sites** that consult `ldd.is_layer_active()`:
  - `skill_inject.collect_active_skills` filters skills whose
    name maps to an off layer (skill-name canonicalisation handles
    plugin-prefix, hyphen, alias map).
  - `skill_inject.auto_grade_from_output` applies the same filter so
    no auto-grade flows to off layers.
  - `dialectic.resolve_mode` couples Layer 11 to the master:
    every dialectic site degrades to `mode=off` when the
    `dialectical_reasoning` LDD layer is off (globally OR per
    profile). Explicit per-site profile overrides still beat the gate.
- **Per-persona LDD profiles** (`cowork/lib/resolver.py`): every
  persona JSON may carry `ldd_preset`, `ldd_layers`, `ldd_enabled`.
  Resolver merges persona preset + delta + chat-profile overrides;
  a special "kill-should-actually-kill" rule drops persona-injected
  per-layer entries when the chat sets `ldd_enabled: false`.
  Defaults shipped:
  - `coder` → `default` (12/12), only "full LDD" persona
  - `forge` (Tools + Skills) → `quick + reproducibility_first` (5/12)
  - `browser`, `assistant` → `quick` (4/12)
  - `research` → `quick + reproducibility_first` (5/12)
  - `inbox` → `off + dialectical_reasoning` (1/12, master forced on)
  - `os` → `off + 6 disciplines` (drift, docs, dialect, method-evo,
    reproducibility, root-cause)
  - `homeassistant` → `off` (0/12)
- **`/whoami`** shows the persona's effective LDD config in a single
  line (`LDD: preset=quick master=on +reproducibility_first`).
- **Bundle-persona JSON updates** (`operator/cowork/personas/*.json`):
  every bundle persona declares an explicit `ldd_preset` so future
  readers see the intent; new personas without an LDD section fall
  back to "every layer on" as before.

### Tests

- `bridges/shared/test_ldd_lib.py` — 64 assertions: layer set,
  load/save round-trip, mtime hot-reload, master + per-layer +
  profile-override resolution, presets, name-mapping including alias,
  `filter_skills`, CLI round-trip (`status`/`on`/`off`/`set`/`preset`),
  unknown-layer fail-open, and the no-`anthropic` AST lint.
- `bridges/shared/test_ldd_dependencies.py` — 89 assertions covering
  every cascade pair: 4-state base matrix per pair (on×on, on×off,
  off×on, off×off), global + profile master kills, cascade-beats-
  explicit-child-override, profile-parent-lift, `effective_state`
  reasons, `/ldd-status` cascade output, `/ldd-set` warning + cascade
  hint, preset coherence (4 presets × 3 pairs), `skill_inject` filter
  respects cascade, defensive cycle-free check on `DEPENDS_ON`.
- `bridges/shared/test_ldd_dialectic_coupling.py` — 32 assertions: LDD
  master/profile/per-layer kills cascade through every dialectic site,
  re-enable lifts the gate, explicit per-site profile mode beats LDD
  gate, `decide()` returns thesis-only with `mode=off` when gated.
- `bridges/shared/test_skill_inject_ldd.py` — 18 assertions on real
  SkillForge `MultiSkillRegistry`: master ON / OFF / per-layer toggle /
  profile re-enable / profile master kill / auto-grade respects filter.
- `cowork/test/test_persona_ldd_resolution.py` — 148 assertions: spec-
  table cascade-coherence, 8 personas × 12 layer baseline (96), chat-
  profile per-layer + master overrides, combination, cascade applied
  after persona resolution, schema-smoke for every bundle persona JSON.
- `bridges/shared/js/test_in_chat_commands_ldd.js` — 21 assertions on
  the `/ldd-*` slash dispatch round-trip with on-disk state checks.

Total LDD coverage: 6 new test suites, 372 assertions. Wired into
`run-all-tests.sh` (now 58 suites, all green).

### Documentation

- `CLAUDE.md` — new Layer-14 section with cascade dependency table,
  read-path-vs-write-path rule, `effective_state` diagnostics, and
  per-persona-LDD merge order including the "kill-should-actually-kill"
  rule.
- `docs/layer-model.md` — backfilled Layers 11, 12, 13, 14 (the
  document previously stopped at Layer 10).
- `docs/personas-and-routing.md` — extended persona JSON schema +
  field-group table + bundled-persona table with the new LDD profile
  column.
- `README.md` — Layer-14 row in the layer overview, new
  "LDD layers (Layer 14)" slash-command sub-section.

### Bumps

- `operator/voice/.claude-plugin/plugin.json`: 0.2.0 → 0.3.0
- `operator/cowork/.claude-plugin/plugin.json`: 0.2.0 → 0.3.0

## [0.10.0] — 2026-05-07

Layer 13 — **`/btw <text>` mid-stream injection**. The classic case:
Claude is mid-task, and you realise you forgot to mention something.
Type `/btw also please check the env file` while the heartbeats are
still running and the note lands inside the *current* turn instead of
queuing as a new one.

### Added

- **`/btw <text>` slash-command** in all four daemons
  (telegram / discord / slack / whatsapp). Recognised before the
  in-chat-cmd dispatcher, written as a side-channel envelope
  `{_btw: true, text, ...}` — same shape pattern as `_cancel`.
  WhatsApp gates on `m.key.fromMe`, the rest on the existing whitelist.
- **`inject_btw(chat_key, text)`** in `bridges/shared/adapter.py`. Looks
  up the per-chat live stdin in `_running_stdins`, writes one
  stream-json `user` JSONL line, returns True / False so the caller
  can ack appropriately. Thread-safe under `_running_stdins_guard` so
  it cannot race the streaming loop's stdin-close on `result`.
- **`_peek_side_channel(inbox_file)`** in the same module. Returns True
  for envelopes with `_btw` or `_cancel` set; the dispatcher routes
  those past the per-chat lock so they reach the live subprocess
  instead of queuing behind the very turn they want to talk to.
- **`_btw` branch in `process_one`**. ACK strings: "📝 Notiz an den
  laufenden Task durchgereicht.", "Leere /btw — schreib z.B. …", or
  "Gerade läuft kein Task — schick deine Notiz als normale Nachricht
  …" depending on whether the live stdin was found, the text was
  empty, or no subprocess was streaming.
- **`bridge.btw_inject` audit event** keyed by chat with
  `{delivered, len}` — surfaces a regression where /btw appears to
  ack but does not land.
- **`/btw` entry in `/help`** (`bridges/shared/js/in_chat_commands.js`)
  and in the `/skills` shortlist so the command is discoverable.

### Changed (structural)

- **`call_claude_streaming` now spawns with `stdin=subprocess.PIPE`**
  and `--input-format stream-json --output-format stream-json
  --verbose`. The initial prompt is written as a stream-json `user`
  JSONL line on stdin, *not* as a positional `-p <prompt>` arg. This
  is what enables /btw — the subprocess sits with stdin open and
  reads further user-messages until the loop closes the pipe.
- **`_build_claude_args(prompt_via_stdin=True)`** new keyword. When
  set, the args list returns `["claude", "-p", ...]` without the
  positional prompt arg; legacy callers (`call_claude`) keep the
  pre-0.10 behaviour.
- **stdin lifecycle**: registered into `_running_stdins[chat_key]`
  right after spawn, unregistered + closed on the first `result`
  event so claude EOFs cleanly. `_cancel_chat` also unregisters
  defensively so an in-flight /btw can't race a SIGTERM.
- **Adapter restart required** after upgrading. The spawn shape
  changed; existing daemons can stay up but the running adapter must
  be cycled via `bash operator/bridges/bridge.sh restart`.

### Tests

- **`shared/test_adapter_btw.py`** — 9 cases. The load-bearing E2E
  spawns `call_claude_streaming` against a fake `claude` binary
  (Python script, temp-PATH) that reads stream-json from stdin and
  emits `assistant` + `result` events with a 1.5 s sleep window. A
  test thread injects a /btw during that window; the assertion
  verifies both the initial prompt *and* the injected /btw made it
  through and that the second reply ends up as `final_text`. Wired
  into `run-all-tests.sh`.
- **`test_adapter_phase1.py::FakeProc`** gained an `io.StringIO()`
  stdin so the existing recursion-counter test stays green under the
  new spawn shape.

## [0.9.0] — 2026-05-07

LDD discipline goes native. Three coordinated changes:

1. **Phase 1 security hardening** for the v0.8 self-extending-personas
   surface: auto-grade is now hard-capped at 0.3 (un-gameable), injected
   skill bodies live inside an advisory `<auto_skill>` container with a
   4 KiB body cap, and the forge runner trusts an explicit
   `caller_persona` kwarg instead of reading env at the security choke-point.
2. **Persona-rework**: every bundled persona is now on the same
   permission shape (bypassPermissions + empty allowed/disallowed).
   Differentiation is by role only. Layer-10 path-gate is the structural
   enforcement.
3. **Layer 11 — native dialectic decision-points**: thesis/antithesis/
   synthesis is wired into 5 high-consequence sites (skill_promotion,
   forge_creation, auto_routing, path_gate, session_reset) via a
   curated heat-score gate. Cost-neutral by construction (no Anthropic
   SDK; mode=skill uses the local Claude's existing turn; mode=cli
   uses `claude -p` → Max-Abo). Default-on, slash-toggle-off.

### Added — Phase 1 hardening

- `_AUTO_GRADE_CAP_MAX = 0.3` in `skill_inject.py`. Auto-grades are
  hard-clamped on entry to `auto_grade_from_output`. Mean of auto-grades
  converges to ≤ 0.3 — a skill that mentions its own name in its body
  cannot self-promote past the 0.5 session→project gate without a real
  user grade.
- Injected skill bodies wrapped in `<auto_skill name="..."
  description="...">…</auto_skill>` with an explicit ADVISORY-not-directive
  header. Body cap 4 KiB per skill, with backoff to a word boundary and a
  visible truncation marker. Wrapper-escape sanitization rewrites literal
  `</auto_skill>` and `<auto_skill ` (case-insensitive) so a skill body
  cannot escape its container.
- `forge.runner.run_tool()` accepts an explicit `caller_persona` kwarg.
  The MCP server forwards `self.forge_persona` (immutable from MCP-startup
  time). Env-trust at the network-decision choke-point is gone; env stays
  as the CLI fallback only. New E2E case in
  `operator/forge/tests/test_persona_sandbox.py` verifies kwarg overrides
  env in both directions.

### Changed — persona rework (BREAKING for ad-hoc persona overlays)

- All 8 bundled personas (`assistant`, `browser`, `coder`, `forge`,
  `homeassistant`, `inbox`, `os`, `research`) now use:

  ```jsonc
  { "permission_mode": "bypassPermissions",
    "allowed_tools": [], "disallowed_tools": [] }
  ```

  Anchor entries (e.g. `mcp__playwright__*` for browser,
  `mcp__forge__forge_*` for the forge persona) remain in `allowed_tools`
  for test-discoverability — they're informational under
  `bypassPermissions`. The historical CLAUDE.md rule "the unified
  forge persona must NEVER be promoted to bypassPermissions" was
  retired with this rework; layer-10 path-gate is the enforcement.
- The `os` persona is now a tracked file (was untracked in 0.8 dev).
- Resolver tests updated:
  - `test_resolver.py`: union-merge no longer asserts persona-listed
    tools survive (they are no longer listed); only that override entries
    are preserved through the merge.
  - `test_resolver_forge_inheritance.py`: forge persona test renamed from
    `_unchanged` to `_unified_pattern` and asserts bypassPermissions +
    empty disallowed_tools.

### Added — Layer 11 native dialectic

- New `operator/bridges/shared/dialectic.py` — core library with
  `decide(*, site, thesis, antithesis, ...)` returning a `Decision`
  dataclass (audit-bare: choice, synthesis, thesis, antithesis, why,
  mode, heat, decision_id, ts).
- 5 registered sites, each with its own default mode + threshold:

  | Site | Mode | Threshold |
  |---|---|---|
  | skill_promotion | skill | 0.5 |
  | forge_creation  | skill | 0.5 |
  | auto_routing    | fast  | 0.5 |
  | path_gate       | fast  | 0.6 |
  | session_reset   | cli   | 0.5 |

- Heat-Score formula:
  `heat = 0.4*consequence + 0.3*uncertainty + 0.3*(scope/5)`.
  Pre-calibrated against 13 fictive tasks
  (`test_dialectic_lib.py::case_calibration_table`).
- 4 modes: `off`, `fast` (deterministic per-site rule), `skill`
  (returns a wrapper block the caller's Claude completes in-turn —
  cost-neutral), `cli` (`claude -p --max-turns 1 --no-tools` subprocess
  → Max-Abo).
- Recursion guard: nested `decide()` degrades to `mode=off`
  (max depth 1).
- Hot-reload config at `<scope_root>/global/dialectic.json` with
  mtime cache. Audit events `decision.dialectical` written into the
  unified hash chain via `forge.security_events.write_event`.
- 5 new slash-commands: `/dialectic-on`, `/dialectic-off`,
  `/dialectic-status`, `/dialectic-set <site> <mode>`,
  `/dialectic-show [on|off]`.
- Per-chat opt-out via `chat_profile.dialectic_enabled = false`.
- CI-lint: `dialectic.py` MUST NOT `import anthropic` (enforced by
  AST walk in `test_dialectic_lib.py::case_no_anthropic_sdk_import`).

### Added — site integrations (best-effort, fail-safe)

- `adapter.py::_apply_auto_routing` — calls `decide()` after the
  router picks; only flips the choice when the heat-gate triggers AND
  the synthesizer prefers the antithesis.
- `path_gate.py::_emit_dialectic` — emits a dialectic record alongside
  the deny audit. Deny semantics unchanged.
- `forge.registry.create()` — heat raised by name collision or
  namespace-prefix overlap.
- `skill_forge.multi_registry.promote()` — heat raised by reach
  (task→session=0.3, session→project=0.6, project→user=1.0) and
  inversely by grade-count.
- `session_reset.reset_session()` — heat raised by skill+tool count
  in the session workspace.

All sites are best-effort: import failure or `decide()` failure is
silent — the underlying functionality never blocks on dialectic.

### Tests

- New: `test_dialectic_lib.py` (39 cases — formula, gate, modes,
  recursion, calibration, no-SDK lint, footer).
- Updated: `test_skill_auto_grade.py` (cap assertion, new "score=0.95
  hard-capped" case), `test_adapter_skill_inject.py` (B re-asserts
  `<auto_skill>` + ADVISORY + close-tag; new H truncation, new I escape),
  `test_persona_sandbox.py` (kwarg overrides env in both directions),
  `test_resolver.py` + `test_resolver_forge_inheritance.py`
  (persona-rework reflection), `test_adapter_cowork.py` (assertions
  updated for bypassPermissions exports as `--dangerously-skip-permissions`
  flag).

### Migration

- Operators with custom `chat_profiles[<chat>].permission_mode = "default"`
  on a bundled persona will see no change (chat-profile overrides
  beat persona defaults). If you relied on bundled personas being
  restricted by default, migrate that intent into a chat_profile
  override on the bridge level.
- Dialectic is default-on; if you want it silent, send
  `/dialectic-off` once. The setting hot-reloads — no restart.
- The 4 KiB body cap on injected skills means very long SKILL.md
  bodies are truncated in the prompt. The canonical SKILL.md on disk
  is unchanged. If you want the full body in-prompt, split the skill
  into multiple smaller skills (each ≤ 4 KiB).

## [0.8.0] — 2026-05-07

Self-extending personas. Every persona that opts in can now forge tools
and create skills at runtime — safely, because the structural enforcement
moved out of the persona configuration into a path-write hook one layer
below.

### Added — layer 10 path-gate hook (`operator/voice/hooks/path_gate.py`)

- New `PreToolUse` hook on `Write|Edit|MultiEdit|NotebookEdit|Bash|WebFetch`.
  Blocks any direct write into the forge / skill-forge workspaces, the
  unified `audit.jsonl`, all `policy.json` files, and the engine-facing
  slot mirror under `operator/skill-forge/skills/dyn/**` — regardless of
  the calling persona's `permission_mode`. The MCP servers stay the only
  writable path into the generation workspaces.
- Bash parser covers `>` / `>>` / `tee` / `mv` / `cp` / `install` /
  `sed -i` / `dd of=` / `python -c "open('…','w')"` / `rsync`. Fail-closed
  when the command contains `eval` / `exec` / `$(…)` / backticks AND
  references a protected path hint.
- Every block writes a `path_gate.denied` event into the unified hash
  chain (covered by `voice-audit verify`).

### Added — persona-aware sandbox (`policy.persona_sandbox_overrides`)

- `forge.policy.Policy` gained `persona_sandbox_overrides` — relaxes
  single sandbox axes per persona. Today only `network: allow` is
  configurable. Bundle default opens the network namespace for `browser`
  and `research` (their forged tools may now call HTTP/HTTPS, with
  loopback + DNS + TLS via the bound `/etc/resolv.conf` and SSL roots);
  every other persona keeps the strict deny.
- `forge.sandbox.build_bwrap_cmd` now accepts `allow_network=False`;
  `--share-net` lands in the bwrap command only for permitted personas.
- Workspace-level `policy.json` can append entries or flip a default-allow
  persona back to deny.
- Real-E2E in `operator/forge/tests/test_persona_sandbox.py` spawns a
  local HTTP stub, forges a `urllib.request.urlopen` tool, runs it
  under `FORGE_PERSONA=browser` (succeeds) and `FORGE_PERSONA=coder`
  (fails with `Connection refused`).

### Added — capability brief in persona prompts

- `_inject_forge_capability` and `_inject_skill_forge_capability` in the
  cowork resolver now append a runtime-built **capability brief** to the
  persona's `append_system`. The brief reads bundle `policy.json` per
  resolve and substitutes the persona's actual namespace prefix and
  network state, so it never lies about what the runtime permits.
- The brief instructs *Discovery first*: call
  `mcp__forge__forge_list` / `mcp__skill_forge__skill_list` before
  creating new artifacts.
- Idempotent — re-resolving the same persona does not duplicate the brief.

### Added — `forge_list` MCP tool

- Third meta-tool alongside `forge_tool` and `forge_promote`. Returns
  `{tools: [{name, description, scope, call_count}, …]}` in
  `structuredContent`. Optional `scope` filter
  (`task` / `session` / `project` / `user`); meta-tools themselves are
  filtered out so the caller only sees forged artifacts.
- `_inject_forge_capability` adds it to `allowed_tools` automatically.

### Added — output streaming on truncation

- When a forged tool's stdout exceeds `output_cap` (4 MiB default),
  `forge.runner.run_tool` now spills the **full** bytes to
  `runs/<id>/artifacts/full_stdout.bin` *before* truncation and
  surfaces `meta.stdout_truncated`, `meta.stdout_truncated_at_bytes`,
  `meta.stdout_total_bytes`, `meta.stdout_full_artifact` on the
  envelope. Existing `RunResult.stdout_truncated` boolean preserved
  for back-compat.

### Added — skill auto-grade after bridge turn

- `skill_inject.auto_grade_from_output(...)` scans the LLM's reply
  for non-negated mentions of active skills (name variants OR first
  80 chars of the body) and writes `score=0.7` grades automatically.
  Negation filter looks 30 chars before and 20 chars after each
  mention for words like *"not"*, *"won't"*, *"skip"*, *"nicht"*,
  *"statt"*. Outputs shorter than 40 chars are skipped.
- Adapter calls it after `call_claude` with `run_id=msg_id`,
  best-effort. The same `inject_skills: false` opt-out applies.

### Added — `forge` persona is the unified runtime-generation specialist

- `forge.json` now carries `skill_forge_enabled: true` — the persona
  can create both tools AND skills.
- `_PERSONA_ALIASES = {"skill-forge": "forge"}` in the cowork resolver:
  existing `chat_profiles` pinning `persona = "skill-forge"` resolve to
  the unified `forge` persona without operator action. There is no
  separate `personas/skill-forge.json` file.

### Changed — resolver capability gate is symmetrical

- `_inject_forge_capability` now gates only on `forge_enabled: true`,
  not on `forge_enabled: true AND zero_config: true`. The historic
  `zero_config` constraint was a dead-flag bug for `inbox.json`
  (which carried `forge_enabled: true` but never received the tools).
  `inbox` now inherits forge tools as designed; `homeassistant` stays
  opt-in (no flag set).

### Added — wire-level test for skill-forge MCP notifications

- `test_mcp_notification.py`: spawns the real skill-forge MCP server
  as a subprocess, drives stdio JSON-RPC, asserts
  `notifications/tools/list_changed` arrives after `skill_create` /
  `skill_purge` and (semantically correct) does NOT arrive after
  `skill_grade` (which doesn't change the tool list).

### Added — opt-in real-Claude E2E for persona usage

- `test_persona_uses_forge_live.py`: spawns `claude -p` with the
  resolved coder profile + materialized MCP config + the injected
  capability brief, parses the stream-json transcript, asserts
  `mcp__forge__forge_tool` was called and the chosen name starts
  with `code.`. Skipped by default; set `CLAUDE_LIVE_E2E=1` to run
  (real API credits, ~1-3 min).

### Security — defence-in-depth additions

- The four security surfaces in `docs/security.md` are now five.
  Surface 5 is the path-gate hook; it is what makes "every persona
  may forge" safe by structural construction rather than by trusting
  the persona's `permission_mode` to behave.

### Changed — voice / summarisation infrastructure

- `voice_lib.sh`: `.env` lookup now puts `VOICE_CONFIG_DIR/.env` and
  `VOICE_CONFIG_DIR/service.env` first, before the plugin-local
  walk-up. Fixes the "voice silent after fork" regression where the
  legacy repo's `.env` carried the OPENAI key out of walk-up range.
  New regression test: `operator/voice/scripts/test_voice_env_lookup.sh`.
- `summarize.py`: enriched system prompt — TTS-safe summaries now end
  with the practical effect for the listener, with explicit license to
  use grounded metaphors so the listener takes away a model rather than
  a fact-list. Source-text fidelity remains the primary constraint.
- `stop_hook.sh`: minor robustness improvements alongside the above.
- New `test_adapter_stream_idle.py`: E2E for the adapter's stream-idle
  watchdog using a real Python subprocess that pretends to be `claude`,
  emits one stream-json event, and then hangs. Verifies SIGTERM-after-
  timeout, periodic alive-heartbeats during the hang, and stream-idle
  recovery on `--continue` sessions.

## [0.7.0] — 2026-05-06

Runtime tool factory plus a hash-chained audit log that covers both
chat lifecycle events and tool-factory events in one timeline.

### Added — `operator/forge/` runtime tool factory

- New plugin under `operator/forge/`. The agent registers a JSON-Schema-
  bound tool at runtime via `mcp__forge__forge_tool(name, description,
  input_schema, impl, runtime?, meta?)` and the tool is callable as
  `mcp__forge__<name>` from the very next `tools/list`.
- Every forged tool runs in a `bubblewrap` sandbox with no network, a
  fresh `/tmp`, and POSIX rlimits (CPU / address space / file size).
- **Per-call run workspace** at `~/.config/corvin-voice/forge/runs/<id>/`
  with `run_manifest.json` (input + tool sha + budget) and
  `run_completion.json` (status + duration + sandbox + artifacts).
- **Determinism cache**: tools that declare `meta.deterministic=true`
  cache results by `(tool_sha, input_sha, python_version)`; identical
  inputs replay from disk (`sandbox=cache` in the response).
- **Operator policy** at `~/.config/corvin-voice/forge/policy.json`
  controls `forbidden_imports`, `forbidden_tool_names`, `default_budget`,
  `max_budget`, `rate_limit`, and the circuit-breaker thresholds. The
  policy is hot-reloaded — edits take effect on the next `tools/call`,
  no restart required.
- **Static-import check** (AST walk) rejects `import socket / subprocess
  / ctypes` at forge time; `bwrap` is the second layer.
- **`forge_promote(name)`** writes `~/.config/corvin-voice/forge/skills/
  <name>/SKILL.md` so the tool survives across sessions.

### Added — `operator/cowork/personas/forge.json`

- Restrictive persona for chat-driven runtime tool generation:
  `permission_mode: "default"` (NOT bypassPermissions), allowed_tools =
  `[forge_tool, forge_promote, Read, Glob, Grep, TodoWrite]`,
  disallowed_tools = `[Bash, Edit, Write, MultiEdit]`.
- Auto-routed onto trigger phrases like "forge mir ein tool", "build me
  a tool that …", "I need a deterministic tool".

### Added — `operator/cowork/lib/resolver.py`

- `materialize_mcp` now expands `{{REPO_ROOT}}`, `{{HOME}}`, and
  `{{ALLOWED_FORGED_TOOLS}}` template variables in `mcp_servers`
  command/args/env values. Lets a persona declare plugin-relative
  paths and per-persona allowlists without per-user hand-edits.

### Added — `operator/bridges/shared/audit.py` + `voice-audit` CLI

- Bridge-side audit wrapper. Adapter emits `bridge.message_received`,
  `bridge.cancel`, `bridge.persona_routed` into the **same** sha256
  hash-chained file the forge plugin writes to (`~/.config/corvin-voice/
  forge/audit.jsonl`).
- New CLI: `python3 operator/voice/scripts/voice_audit.py verify | tail`
  — verify exits 0 / 1 / 2 with line-level integrity reports;
  `voice-audit` shell wrapper for `$PATH`.
- Cross-process writes (voice adapter + forge MCP server are separate
  processes) are serialized via filesystem `flock`. Tampering with any
  field in any record breaks the chain at that line and `voice-audit
  verify` localises it.

### Added — operator-facing docs

- `docs/forge.md` (mental model, lifecycle, when to forge / not).
- `docs/security.md` (four-surface envelope, audit log, threat model).
- `operator/voice/skills/voice/SKILL.md` gains a "Runtime tool generation"
  section covering the forge persona, per-persona allowlist, the audit
  log, and the workflow policy.
- `CLAUDE.md` gains a "Forge plugin (layer 6)" section codifying the
  rules for future Claude editing the repo.
- `operator/forge/examples/voice_demo.sh` — single-command end-to-end
  demo (forge → tool/list → bwrap call → cache replay → audit verify
  → tamper detection).

### Added — central test runner

- `operator/bridges/run-all-tests.sh` now covers the new audit and
  forge stack alongside the existing 16 suites: 20 suites total.

### Hardened — test hygiene

- The adapter no longer pollutes the real audit log when running under
  a sandboxed `ADAPTER_INBOX` (the entire test fleet). The audit path
  is auto-redirected to a sibling of the sandbox.

### Numbers

- ~7 000 lines of new code across 13 forge modules + 18 test suites
- 422 forge plugin tests + 84 voice-side audit/skill/CLAUDE-md tests +
  the existing 16 adapter suites — all green
- Phase A through Phase G + drift-fix shipped in 13 commits on the
  `claude/forge-mvp` branch

## [0.6.0] — 2026-05-05

Two user-facing improvements: the voice playback is now genuinely
controllable (the old "long reply gets cut" behaviour is fixed), and
every persona — not just `inbox` — can now compose Gmail with real MIME
attachments through a single local helper.

### Added — user-steerable voice playback

- New `voice_mode` config key (`auto` | `full` | `summary`). Default
  remains `auto` (threshold-based behaviour: short replies pass through,
  long ones get summarised), but `full` reads every reply completely
  and `summary` summarises every reply regardless of length.
- Four slash-commands set the persistent default: `/voice-mode <arg>`,
  `/voice-full`, `/voice-summary`, `/voice-auto`.
- **Per-turn override** without any setup: phrases in your *current*
  message override the mode for that one reply.
  - `full` triggers: "lies (mir) das vollständig | komplett | wörtlich
    | im Ganzen vor", "voll vorlesen", "ohne Kürzung", "nicht
    zusammenfassen"; EN: "read it in full / verbatim / completely",
    "no summary", "don't summarize".
  - `summary` triggers: "fass das zusammen", "Kurzfassung", "in kurz",
    "in Kürze"; EN: "summarize", "short version", "TL;DR", "in short".
  - `full` wins when both match.
- `summarize_max_chars` default raised from 4096 → 10 000 so research-
  sized replies no longer get cut. The summarizer's `adaptive_target`
  scales further for very long inputs.
- Module: `scripts/detect_voice_intent.py` (regex-based, no LLM call,
  30 unit tests). Wired into `hooks/stop_hook.sh` between the existing
  `THRESHOLD` / `SUMMARIZE` reads and the `setsid` pipeline launch.
- `voice_cli.sh status` now prints the active `Voice mode` and
  `summarize_max_chars`.

### Added — `gmail-helper` for every persona

- New helper at `operator/cowork/bin/gmail-helper` (symlinked into
  `~/.local/bin/`). Two compose modes:
  - `draft` (recommended) — Gmail API via OAuth, creates a real Draft
    that the user reviews in Gmail before sending. The helper manages
    its own private venv under `~/.config/corvin-voice/google/venv/`
    and refreshes the token automatically.
  - `send` — SMTP via App password. Stdlib only, no extra packages.
- Both modes produce **real MIME attachments** (`--attach FILE`,
  repeatable). The bundled `mcp__claude_ai_Gmail__create_draft` cannot;
  this helper closes that gap.
- `gmail-helper wizard` walks first-time users through both setup paths
  (App password + OAuth client + library install + self-test).
  `gmail-helper status` reports what is configured.
- All six bundled personas (`assistant`, `coder`, `browser`, `research`,
  `inbox`, `homeassistant`) now have `Bash(gmail-helper:*)` in their
  allow-list and prompt-level guidance to prefer `draft` over `send`.
- Docs: `operator/cowork/bin/gmail-helper.md` (full reference) +
  `operator/cowork/README.md` (overview).

### Fixed

- `inbox.json` allow-list referenced the wrong MCP tool namespace
  (`mcp__gmail__*`, `mcp__google_calendar__*`). The actual tools are
  `mcp__claude_ai_Gmail__*` / `mcp__claude_ai_Google_Calendar__*`. The
  inbox persona could not call Gmail or Calendar at all before; it can
  now.

## [0.5.0] — 2026-05-05

First tagged release. Captures the project as it stands after the
phone-first AI workstation has settled into a stable shape: voice + five
bridges + cowork personas + auto-routing + the new bridge-wide memory
system + per-chat audience control.

### Added — bridge-wide memory (three tiers)

- **`/profile` (Tier 1)** — short, always-loaded user profile (name,
  language, tone, timezone, …). Inlined into every system prompt across
  all bridges. Empty profile costs zero tokens. CLI: `profile_cli.py`
  show / get / set / rm / reset. Module: `bridges/shared/profile.py`,
  15 tests.
- **`/memory` (Tier 2)** — episodic Markdown topic files lazily loaded
  by Claude when relevant. Only the topic index (one line per topic) is
  inlined; full bodies live under `~/.config/corvin-voice/memory/` and
  Claude reads them via the Read tool on demand. CLI: `memory_cli.py`
  list / show / write / append / forget. Module:
  `bridges/shared/memory.py`, 14 tests.
- **`/vault` (Tier 3)** — secrets store with audit log. Inventory only
  (names + kinds + tags + flags) appears in the prompt;
  **values never do**. Each fetch is logged to
  `~/.config/corvin-voice/vault.log` with the requesting chat id. Items
  can be `--locked` (require a 5-minute `/vault unlock` first) or
  `--encrypted` (GPG via `gpg-agent`). CLI: `vault_cli.py`
  list / get / set / unlock / forget / audit. Module:
  `bridges/shared/vault.py`, 14 tests (one GPG-roundtrip test skipped
  when no default key).

All three persist under `~/.config/corvin-voice/` so any bridge sees the
same data on the next reply. The system prompt instructs Claude to
proactively offer to save stable user preferences ("soll ich das in
/profile speichern?"); nothing is persisted silently.

### Added — per-chat audience control

- **`/all` toggle** — every chat is owner-only by default. The bridge
  whitelist (existing) is the gate; non-whitelisted senders are dropped
  at the daemon before they reach the inbox. When the owner wants to
  let other people in (a shared group, a team channel), `/all on` opens
  that chat only — the whitelist is bypassed for it. `/all off` flips
  it back. Status (`/all` with no argument) is visible to anyone in the
  chat; the flip itself is owner-only.
- **Loop protection stays active** in `audience=all` mode: each daemon's
  existing self-message and external-bot filters
  (Discord/Slack `is_bot`, WhatsApp `fromMe`) keep external bots and the
  bot's own echoes from triggering replies, so opening a chat does not
  turn it into a loop sink.
- The audience setting persists in
  `bridges/<channel>/settings.json` under
  `chat_profiles[<chat>].audience` and hot-reloads via mtime — no
  daemon restart needed.

### Added — earlier in the cycle (now released as part of 0.5.0)

- **Embedding-based auto-routing** with OpenAI
  `text-embedding-3-small`. Heuristic catches obvious phrasings
  instantly; everything else is embedded and matched against each
  persona's `routing_anchors`. Multilingual (DE/EN match the same
  anchors). Default mode for Max-subscription users — no Anthropic API
  key required.
- **Email bridge** — fifth channel via plain IMAP + SMTP. Send tasks to
  your own inbox; replies arrive as `Re: [Claude] …` with attachments
  preserved both ways.
- **Scheduled reminders** — `/schedule add in 1h::standup ping`,
  `/schedule add 0 9 * * 1-5::weekday brief`. ISO datetimes and 5-field
  cron strings both work; due tasks are materialised as virtual user
  messages and run through the same persona / auto-routing pipeline.
- **HomeAssistant persona** — `/persona homeassistant` for smart-home
  control via voice notes. Auto-routing-excluded; opt-in only.
- **Image generation** — `assistant` persona knows about
  `scripts/generate_image.py` (DALL-E 3 wrapper); ask for an
  illustration in any chat and the PNG comes back as an attachment.

### Security notes

- The whitelist remains the security boundary: anyone on it gets
  shell-equivalent access to the box (because the bridges call `claude`
  with elevated permissions). `/all on` widens that boundary deliberately
  for one chat at a time and only when the owner acts.
- Vault values never appear in the system prompt or in `/vault list`.
- `~/.config/corvin-voice/service.env` and the per-bridge `settings.json`
  files are `.gitignore`d.

### Upgrade notes

- This is the first tagged release; nothing to migrate.
- After pulling: restart the daemons (`bash operator/bridges/bridge.sh restart`
  or `systemctl --user restart 'corvin-voice-bridge-*'`) so the new
  `authOk(uid, text, chatKey)` signature and the new in-chat dispatchers
  are picked up. The adapter doesn't need a separate restart for the
  audience feature.

[0.6.0]: https://github.com/veegee82/ClaudeClaw/releases/tag/v0.6.0
[0.5.0]: https://github.com/veegee82/ClaudeClaw/releases/tag/v0.5.0
