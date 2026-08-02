# Changelog

All notable changes to this project are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


## [0.10.99] — 2026-08-02 — Fixed: freshly created SkillForge skills were invisible to Claude Code's native plugin loader in every real production session

### Fixed — `SkillRegistry.plugin_slot_dir()` silently misrouted its mirror away from the path the engine actually scans

- Root-caused via the Production-Readiness Roadmap opened in
  `Corvin-ADR/concepts/0001-self-learning-project-concept-archive.md` (item P0-1),
  itself found while adversarially reviewing the Concept Gate mechanism added earlier
  today (see the 0.10.98 entry above).
- `plugin_slot_dir()` treated bare `CORVIN_HOME` presence as a "this is an isolated
  test sandbox" signal and redirected the plugin-slot mirror to
  `<CORVIN_HOME>/plugin-slot/` — but `CORVIN_HOME` is the canonical runtime root set
  in **every** real CorvinOS session, not only tests. In production this silently
  redirected every project/user-scope skill's engine-visible mirror away from
  `operator/skill-forge/skills/dyn/`, the path Claude Code's own native plugin
  loader actually scans (confirmed by `test_engine_visibility.py`'s real `claude -p`
  subprocess round trip, and by the test author's own pre-existing workaround of
  explicitly overriding `CORVIN_PLUGIN_SLOT_DIR` back to the real path). Net effect:
  a freshly created skill was invisible to the native loader on every real install.
- Fixed by removing the `CORVIN_HOME` branch entirely. `CORVIN_PLUGIN_SLOT_DIR` — a
  dedicated, single-purpose variable used nowhere else in the codebase — remains the
  sole, unambiguous test-isolation override; every one of the 15 existing call sites
  across the test suite already set it explicitly, so this changes zero test behavior
  while fixing the real bug.

### Testing

`test_plugin_slot_compat.py`'s two tests that previously asserted the *old* (buggy)
behavior as correct were rewritten to assert the fix directly: bare `CORVIN_HOME`
no longer redirects the slot (falls through to the real repo path), and
`CORVIN_PLUGIN_SLOT_DIR` still wins when both are set. Full regression sweep across
every affected file — `test_engine_visibility.py`, `test_grading.py`,
`test_namespace_gate.py`, `test_plugin_slot.py`, `test_mcp_notification.py`,
`test_registry.py`, `test_cleanup.py`, `test_multi_scope.py`, `test_linter.py`,
`test_skill_inject_ldd.py`, `test_adapter_skill_inject.py`,
`test_skill_outcome_grading.py`, `test_ldd_dependencies.py`, `test_skill_auto_grade.py`,
`test_session_reset.py`, `test_core_quality_skills.py` — 300+ assertions, zero
regressions.

## [0.10.98] — 2026-08-02 — Concept Gate now unconditionally active, matching ADR Gate

### Fixed — a second adversarial review of the just-added Concept Gate mechanism found it was structurally weaker than its own CLAUDE.md table entry implied

- `concept_gate` had no bundled skill file (`operator/bundle/skills/ldd/concept_gate/SKILL.md`)
  — every sibling in the same CLAUDE.md LDD table row (`adr_gate`, `docs-as-definition-of-done`,
  etc.) has one; Concept Gate existed only as CLAUDE.md prose. Added, mirroring `adr_gate`'s
  format and discipline.
- `concept_gate` is now part of `_CORE_QUALITY_SKILL_NAMES` in
  `operator/bridges/shared/skill_inject.py` — the same unconditional, grading-independent
  injection tier `adr_gate`/`e2e-wiring-proof` already use (ADR-0259), since Concept Gate is
  explicitly a sibling gate to ADR Gate (same placement, same discipline), not one of the 10
  process LDD skills that stay off until a persona opts in.
- Wiring this in surfaced a real, distinct bug: `concept_gate` was missing from
  `_SKILL_TO_LAYER`, so `quality-layers.json`'s `disable_layer()`/`enabled: false` toggle
  — which correctly suppresses `adr_gate`/`e2e-wiring-proof` — silently did NOT suppress
  `concept_gate`, breaking an existing security-escape test's isolation assumption
  (`case_skill_body_escape_blocked` expects exactly one core skill's worth of injected content
  when quality layers are globally disabled). Fixed by adding the mapping and registering
  `concept_gate` in `quality_layers.py`'s `DEFAULT_CONFIG` alongside its siblings.

### Testing

Extended the existing real end-to-end test (`operator/bridges/shared/test_adapter_skill_inject.py`
— spawns a genuine sandboxed adapter subprocess and inspects its actual system prompt) to assert
`concept_gate` is present. All 9 cases in that file pass, including the security-sensitive
escape-injection case that would have silently broken without the `quality_layers.py` fix.
`test_core_quality_skills.py` (18 cases) and `test_skill_inject_ldd.py` (18 cases) also green,
no regressions.

## [0.10.96] — 2026-08-02 — A2A ping/Recheck and friendship-ack now relay-routable

### Fixed — a relay-only-reachable A2A peer could never show as reachable

ADR-0258's Stage 3 (encrypted relay fallback) was wired into the real
task-send path (`RemoteTriggerSender.send()`) only. Two other call paths
stayed direct-HTTP-only, so the console's Recheck button and the initial
reciprocal-ack handshake could never reflect a relay-only-reachable peer
even once a relay was correctly configured and enabled on both sides:

- `ping()` / `_http_ping_probe()` gained the same relay fallback
  (`_relay_ping`) `send()` already had. Since the peer-side ack handler
  already calls `ping()` internally for its own reachability proof, this
  closes both directions of the handshake at once.
- `send_friendship_ack()`'s B→A ack POST gained a relay fallback too
  (`_relay_send_ack`), refactored around a shared `_ack_round_trip()`
  core. New `retry_friendship_ack()` lets a Recheck re-attempt the ack
  using the already-derived keys persisted on disk (the raw token key is
  discarded after import and can't be re-derived).
- `RelayListener._handle_deliver()` now dispatches three disjoint payload
  shapes (task envelope / ping request / friendship-ack request) instead
  of one, each to its own shared-core handler — with a new
  transport-only self-delivery guard for the two new shapes (they carry
  no signed `sender_instance_id` slot of their own; adding one would have
  broken direct-HTTP backward compatibility with older peers).
- `friendship_recheck()` now refreshes `_peer_knows_us` when a ping just
  succeeded and it was previously false — previously only the original
  import-time ack ever set that field, so "peer can't reach you back"
  could persist forever even after the issuer became reachable again.

Triggered by a live operator report reproducing ADR-0258's own motivating
scenario (a pairing over an Apple Personal Hotspot address that stopped
existing once the tethering session ended). 40 new/updated tests; the
full existing A2A suite (154 tests) stays green — additive only.

## [0.10.95] — 2026-08-02 — Windows bridge: extra visible terminal + silent duplicate-daemon restart loop

### Fixed — two compounding bugs found while investigating a live report of an unreliable Windows bridge

- **A visible terminal window flashed up that the user had to close/dismiss.**
  Root cause: `ensure_windows_autostart()` (added in 0.10.92) spawns
  `powershell.exe` via a bare `subprocess.run` with no
  `creationflags=CREATE_NO_WINDOW`. Spawning a console app from a
  console-less parent (the web console backend, itself started hidden)
  makes Windows allocate a brand-new, **visible** console window for the
  child — exactly the class of bug 0.10.91 had just fixed for the node
  daemon's own launch, reintroduced here for this one call. Fixed.
- **The bridge supervisor was not actually supervising the bridge.**
  `corvin-supervisor.ps1`'s bridge target launches `bridge.ps1 up` via
  `Start-Process ... -Wait`, expecting that to block until the daemon
  exits. But `bridge.ps1 up` always returned within ~1s of firing off the
  DETACHED node.js daemon, whether or not that daemon was still alive —
  so the supervisor's `-Wait` was only ever waiting on the *launcher*
  script, never the real daemon. Its `while ($true)` restart loop
  therefore launched a **brand-new, additional node.js daemon every ~5s,
  forever**, regardless of the previous one's health — silent duplicate
  daemons competing for the same Discord/WhatsApp session (session-file
  locking, duplicate replies, eventual rate-limit bans). This is the
  actual mechanism behind "the bridge doesn't reliably stay up on
  Windows" reports that looked like crash-restarts but weren't. Fixed:
  `bridge.ps1 up` now blocks on the daemon's own `Process` object via
  `.WaitForExit()` when `CORVIN_SUPERVISED=1` (the same signal
  `corvin-supervisor.ps1` already sets before every launch) — so the
  supervisor's `-Wait` finally reflects the daemon's real lifetime and
  only restarts once it has genuinely died, reconnecting with whatever
  session/credentials the bridge already has on disk (nothing about
  session persistence changes — daemon.js already reuses its saved
  WhatsApp/Discord session on any relaunch; the bug was launching
  redundant *extra* daemons, not losing the session). A manual,
  interactive `.\bridge.ps1 up` (no supervisor, `CORVIN_SUPERVISED`
  unset) keeps returning immediately, unchanged.

### Testing

`tests/test_windows_supervisor_parity.py` gained
`TestBridgeUpBlocksUnderSupervision` (supervised blocking is present and
correctly conditional — a manual run must never hang) and
`TestWindowsAutostartRegistrationHasNoVisibleWindow` (pins the
`creationflags=CREATE_NO_WINDOW` regression). Extended
`operator/bridges/test_bridge_manager_windows_autostart.py`'s existing
argv test to also assert the `creationflags` kwarg. 25 + 8 tests green.

## [0.10.94] — 2026-08-02 — A2A LAN pairing (Windows ↔ Linux) required knowing your own IP; now fully automatic

### Fixed — reported live via screenshot: A2A pairing between a Windows and Linux instance on the same network got permanently stuck

- Root cause #1: `POST /remote-trigger/pair/friendship/create` produced a
  token with `url=None` whenever the "own URL" form field was blank at
  generation time — the importer's side then showed "Imported (URL
  pending)" forever, with no recovery short of the issuer discovering their
  own LAN IP by hand and re-pairing. Fixed: the route now falls back to the
  already-configured "My URL", then to the same mesh-VPN/local-interface
  auto-detection `GET /my-url` already offered — so a same-LAN pairing
  works without the operator ever needing to know or type their own
  address. The auto-detected address is persisted so it's visible under
  Settings → A2A afterward.
- Root cause #2: the "Use this URL" button in Settings → A2A → My URL was
  **hidden** specifically for private/RFC1918 addresses (192.168.x.x,
  10.x.x.x, ...) — exactly the addresses correct for same-LAN pairing —
  forcing a manual retype of the identical value. The warning text also
  read as "this address is wrong" ("not reachable by external peers")
  instead of explaining it's fine for local pairing. Both fixed.
- Root cause #3: neither installer opened the console/A2A port on the
  local firewall, so even with a correct URL, Windows' (and, when enabled,
  Linux `ufw`'s) default inbound-block policy silently dropped the peer's
  reachability probe — indistinguishable from a misconfigured URL from the
  UI. `install.ps1` now adds a best-effort inbound allow-rule (idempotent,
  never fatal, no admin/elevation gate — mirrors the existing autostart
  registration idiom); `install.sh` does the Linux equivalent via `ufw
  allow`, but only when `ufw` is already active and never via `sudo` (this
  installer only ever elevates on the explicit `--always-on` flag).

Net effect: pairing two CorvinOS instances on the same home network (the
scenario this whole feature exists for) now works without either operator
ever needing to discover, understand, or type an IP address — matching the
Layer 38 A2A design's own stated LAN-first intent.

### Testing

`core/console/tests/test_a2a_friendship_create_url_autodetect.py` (5 new
tests): blank-URL fallback to stored/auto-detected address, degrade path
when auto-detection itself fails, explicit URLs are never silently
overridden. `tests/test_windows_supervisor_parity.py` gained
`TestLanFirewallRule` (Windows) and `TestLinuxUfwRule` (Linux, including a
regression guard that the ufw step never shells out to `sudo` unasked).
134 pre-existing + new tests green; `tsc -b` clean on the frontend change.

## [0.10.93] — 2026-08-02 — install.ps1 could claim "CorvinOS is ready!" when the console never started

### Fixed — a fresh Windows install could report success while the console silently failed to start

- Reported live: after a completely fresh Windows install, the console did not
  open at all. Adversarial code review of `install.ps1` (no plain-pip PATH
  quirk applies here — the Windows installer uses `uv tool install`) found
  the real defect: the final banner printed "CorvinOS is ready!" in green
  **unconditionally**, regardless of whether the server ever answered
  `/v1/console/healthz`, whether Scheduled-Task/Startup-shortcut autostart
  registration succeeded, or whether the single-shot fallback start even ran.
  Any real error (a `Write-Warn` earlier in the scrollback) was buried under
  a false-positive success banner at the very bottom of the output — exactly
  what a user reads when judging whether the install worked.
- Compounding it: the fallback path taken when autostart registration throws
  (`try { Start-Process -FilePath "corvinos-serve" ... } catch {}`) used an
  **empty catch block** that silently discarded the failure, and re-attempted
  the bare, unresolved command name `corvinos-serve` — the identical lookup
  that had just failed inside `Install-CorvinAutostart` moments earlier,
  guaranteeing the same failure a second time with zero diagnostic output.
- Fixed both: the fallback now resolves the actual command path (same
  resolution `Install-CorvinAutostart` and the Desktop-shortcut step already
  use) and reports a real error via `Write-Warn` on failure instead of
  swallowing it. The final banner is now gated on real evidence
  (`$ServerReady` / `$ConsoleLaunchAttempted`) and has three honest outcomes
  instead of one false one: a genuine "ready" banner when the server actually
  answered; a yellow "installed, but hasn't answered yet" banner with the
  exact log path and Scheduled-Task recovery commands when a start was
  attempted but not yet confirmed; and a red "could NOT be started" banner
  with the exact manual recovery command (`corvinos-serve` from a new
  terminal) when nothing launched at all.

### Testing

Extended `tests/test_windows_supervisor_parity.py` (regex-based inspection,
no PowerShell interpreter needed) with `TestInstallBannerReflectsRealState`:
pins the banner to be conditional on `$ServerReady`, pins the failure banner
to name the exact recovery command, and regression-guards against the empty
`catch {}` reappearing. 23/23 tests in this file (plus
`test_uninstall_windows_autostart.py`) green.

## [0.10.92] — 2026-08-02 — Bridges started via the Console got no restart-forever supervision on Windows

### Fixed — a fresh Windows install / pip upgrade still left the bridge dead after a crash or reboot

- Reported live, even after 0.10.91's terminal-detach fix: on a fresh Windows
  install or after a `pip` upgrade, the bridge and A2A still weren't running —
  neither Windows 10 nor 11 kept them up in the background with self-restart.
- Root cause: `start_channel_detached()` (the engine behind the web Console's
  "Start bridge" button — the only real bridge-start entry point on Windows)
  already genuinely detaches the daemon and adapter from the caller's
  terminal, but a detached process is still a one-shot spawn — nothing
  supervises it afterward. Console autostart (Scheduled-Task
  restart-forever supervision) was already registered by default via
  `install.ps1`; bridge autostart was **opt-in only**, gated behind a
  separate `bridge.ps1 install-autostart` command nobody knew to run.
- Fixed by adding `ensure_windows_autostart(channel)` to `bridge_manager.py`,
  called automatically at the end of every `start_channel_detached()` run
  (Windows-only, no-op elsewhere; best-effort — never blocks the bridge start
  that already succeeded). It registers the exact same Scheduled-Task
  restart-forever supervision the manual opt-in command already used, so a
  bridge started via the Console now gets restart-on-crash and
  restart-on-reboot/relogin automatically, with no separate step required.

### Testing

New `operator/bridges/test_bridge_manager_windows_autostart.py` (8 tests):
argv correctness, idempotent per-channel skip, POSIX no-op, missing-`bridge.ps1`
and non-zero-exit error paths, exceptions caught not raised, and a source-level
reachability proof that `start_channel_detached()` actually calls the new
function. 23/23 pre-existing tests exercising `start_channel_detached()` still
green (zero regression).

## [0.10.91] — 2026-08-02 — Windows bridge daemon no longer tied to the launching terminal

### Fixed — `bridge.ps1 up` attached the daemon to the caller's own terminal

- Reported live: "on Windows the bridge starts in a new terminal — closing that
  terminal kills the bridge." `bridge.ps1`'s `up` case launched the Node daemon via
  the call operator (`& node $BridgeScript`), which runs it as a direct child of the
  invoking PowerShell session, sharing its console — closing that window killed the
  whole process tree, daemon included.
- Fixed the same way the `console` subcommand in the same file already worked:
  `Start-Process -WindowStyle Hidden` gives the daemon its own independent, invisible
  console, fully detached from the caller's terminal. stdout/stderr — no longer
  visible in an attached terminal once detached — are now captured to
  `<CORVIN_HOME>\logs\<bridge>-daemon.log` instead of being silently lost (the
  WhatsApp pairing QR code and daemon startup errors both go through this output).
- Also closed a smaller, related defense-in-depth gap: `corvin-supervisor.ps1`'s
  `bridge` autostart target recurses into `bridge.ps1 up` via a nested
  `powershell.exe`, whose own `-WindowStyle` switch was missing (every other
  Scheduled-Task action string in this codebase already passes `-WindowStyle Hidden`
  as belt-and-suspenders alongside the outer `Start-Process -NoNewWindow`).
- Verified (not assumed) that two other Windows startup paths were already correct:
  the web Console's "Start bridge" button (`bridge_manager.py::
  start_channel_detached`) and the opt-in `bridge.ps1 install-autostart`
  Scheduled-Task path were both already properly detached before this fix.

### Testing

New regex-based tests in `tests/test_windows_supervisor_parity.py` (no PowerShell
interpreter needed) pin both fixes — the `up` case must not attach to the caller's
console, must use `Start-Process -WindowStyle Hidden` with output captured to a log
file, and must never fall back to `-NoNewWindow`. 14/14 tests in this file green.

## [0.10.90] — 2026-08-02 — console audit AttributeError + path-gate MSYS hardening

Investigated a diagnostic report from a live Windows CorvinOS instance (a different
machine than this dev environment).

### Fixed — console model-catalog refresh crashed every 5 minutes with AttributeError

- `routes/models.py`'s periodic background refresh called
  `console_audit.system_event(...)`, which `corvin_console/audit.py` never defined.
  Every call raised `AttributeError`, silently swallowed by the refresh loop's broad
  `except`, so the audit trail for model-catalog events was permanently empty and the
  failure recurred on every scheduled refresh — confirmed via static code inspection,
  matching the reported symptom exactly ("repeated every 5 minutes").

### Hardened — path-gate Git-Bash/MSYS2 drive-path bypass on Windows (defensive, unconfirmed)

- `path_gate.py`'s protected-path comparison had zero platform-aware handling. Windows'
  Bash tool commonly runs through Git-Bash/MSYS2, which translates a `/c/Users/...`-style
  path to the real `C:\Users\...` at shell-execution time — but the hook runs as a
  separate Python subprocess with no MSYS runtime, so `Path("/c/Users/.../forge/
  policy.json")` would resolve relative to "root of the current drive" and never equal
  the real protected path, letting a write through undetected. Added a Windows-only MSYS
  drive-prefix normalizer (no-op on POSIX) as a plausible root-cause hardening for the
  report's other claim (path-gate self-test failures) — not independently confirmed on
  real Windows, no Windows box available in this environment.

### Testing

131 existing path-gate tests + all sibling suites green (no regression). New:
`test_audit_system_event.py` (4 tests, including an AST check that pins the real call
sites' kwargs against the fixed function's signature) and
`test_path_gate_windows_msys.py` (6 tests, `sys.platform`-mocked).

Also fixed a pre-existing CI test-gate bug found while cutting this release (unrelated
to the two fixes above): `operator/forge/tests/test_forge.py`'s
`test_windows_preexec_fn_skipped` mutated the real, process-wide `sys.platform`
attribute to simulate Windows, which leaked into `shutil.which()`'s own internal
`sys.platform` check and crashed with `AttributeError` on real POSIX CI runners
(`_winapi` is only ever bound on real Windows). Fixed by passing `use_sandbox=False`
to skip the affected code path without touching what the test actually verifies.

## [0.10.88] — 2026-08-02 — fresh-install-vs-upgrade parity + remaining Windows/bridge gaps

Iterative adversarial review across the whole codebase, framed around one question:
what behaves differently on a fresh install vs. an existing/upgraded system, with
Windows and the messenger bridges (A2A, Discord, WhatsApp, Telegram, Slack, Signal,
Teams, Email) held to the same stability/robustness/fault-tolerance bar as Linux.
0.10.87 was tagged but never actually published to PyPI; this release supersedes it
and folds in its content plus the fixes below.

### Fixed — a2a_http_server.py crashed on a shallow/standalone deployment

- Same unguarded `Path(__file__).resolve().parents[2]` pattern ADR-0265 already fixed
  in the A2A sender/receiver, but missed here (module-level `_DEFAULT_COWORK_DIR` and
  `build_server()`'s `eff_origins_dir`). Fixed with the same repo-marker walk-up
  pattern; verified via a real subprocess import from a shallow tmp directory.

### Fixed — Telegram/Slack/Teams/Signal stayed open to any sender forever on a fresh install

- An empty whitelist (nobody has logged in yet) meant every sender was treated as
  owner, with no lock, indefinitely — unlike Discord (`AutoOwnershipBridge`) and
  WhatsApp/Email (fail-closed `authOk()`), which each already handle this correctly.
  Added a shared `lockFirstSender` option to `auth.js`'s `makeAuth()` factory: the
  first sender claims the whitelist, persisted, everyone after is denied. Wired into
  all four affected bridges, with a drift-guard test so a fifth bridge can't silently
  reintroduce the same gap.

### Fixed — Windows supervisor could silently loop doing nothing, or race a slow-to-answer instance

- `Find-Python` fell back to a bare `"python"` string that "succeeds" launching a
  Windows Store Python stub (or a stray pre-3.11 install) that never runs CorvinOS —
  burning the restart budget on a doomed launch. Now fails fast with a clear
  CRITICAL log instead of entering the restart loop at all.
- The console port-busy check treated any exception lacking `.Exception.Response`
  as "port is free," which also covers a connect *timeout* (not just a genuine
  refused connection) — risking a second competing instance launched against one
  that just hadn't answered yet. Now requires the specific `ConnectFailure` status.
- `bridge.ps1`'s Scheduled Task action string quoted `$Supervisor` but not
  `$TargetArg`/`$BridgeArg` — an operator-supplied bridge name with a space or quote
  could break the action's own CLI parsing. Both are now quoted.
- Pinned in both `corvin-supervisor.ps1` and `install.ps1`'s generated equivalent
  (drift-guard tests), since the two must stay behaviorally identical.

### Fixed — `corvin-install` re-run on an existing system didn't reliably apply an upgrade

- `build_frontend()` checked `dist/index.html` before distinguishing a wheel install
  (no source, correctly skip) from a source-tree checkout — `corvin-install` is the
  documented upgrade path after `git pull`, so a `dist/` left over from a previous
  version was treated as "already built" and `npm run build` never ran, silently
  serving the old compiled SPA against the new gateway/API.
- `ServiceManager` had no `restart_service()` — only `start_service()`, which is a
  no-op against an already-active systemd unit on Linux. Step 14 rewrites the unit
  file on every `corvin-install` run, but the old process (old `ExecStart`/env) kept
  running through the upgrade untouched. Added `restart_service()` (systemd
  `restart` on Linux, unload+load on macOS, stop+start on Windows) and switched
  every call site, including the two remaining `start_service` calls inside
  `corvin-restore` an adversarial re-check found.

### Fixed — browser automation failed identically on every retry when the console ran as root

- Chromium refuses to launch as root without `--no-sandbox` — a deploy shape the
  installer explicitly supports (root systemd service). That failure matched
  neither existing launch-error classifier, fell through to a bare `raise`, and got
  flattened by the generic exception handler into "browser action failed — retry,"
  which can never succeed since the launch fails identically every time. Added a
  third classifier with an actionable message (run as non-root, or opt into
  `CORVIN_BROWSER_NO_SANDBOX=1`).

## [0.10.87] — 2026-08-01 — Windows bridge/A2A parity, verified on real hardware (ADR-0265)

### Fixed — A2A completely non-functional on Windows (send AND receive)

- `RemoteEndpointRegistry.load()` / `OriginRegistry.load()` unconditionally rejected
  every endpoint/origin file on Windows: `os.stat().st_mode & (S_IRWXG|S_IRWXO)` always
  reports non-zero on NTFS (no POSIX group/other bits), so the world-readable guard
  fired on every read. Fixed with a `sys.platform.startswith("win")` guard mirroring
  `instance_identity.py`'s existing correct precedent.

### Fixed — bridge stop/restart killed in-flight turns and orphaned processes on Windows

- `Popen.terminate()` on Windows calls `TerminateProcess()` (no signal delivery, no
  graceful drain); the tracked PID was `cmd.exe`, not the real `node.exe`/`claude`
  process it wraps, so killing it orphaned the actual work. Fixed with the standard
  Windows subprocess-tree pattern: `CREATE_NEW_PROCESS_GROUP` at spawn +
  `CTRL_BREAK_EVENT` (graceful) + `taskkill /T /F` (hard-kill fallback).

### Fixed — adapter.py crashed on the first non-ASCII inbound message on Windows

- 11 file reads lacked `encoding="utf-8"`, falling back to
  `locale.getpreferredencoding()` — a legacy code page on a default Windows install,
  not UTF-8. The first emoji/umlaut in an inbound message raised `UnicodeDecodeError`.

### Fixed — outbox poller could dead-letter a valid, in-flight-written message

- 14 outbox `.write_text()` sites replaced with an atomic temp-file + `os.replace()`
  helper, so `daemon.js`'s 500ms poller can never observe a partially-written envelope.
  `outbox.js` now also distinguishes a transient read failure (retry) from a genuine
  JSON-parse failure (dead-letter).

### Fixed — `CORVIN_HOME` whitespace/`~`/`%VAR%` handling missing from the Node side

- `bridge_paths.js` / `auth_elevation.js`'s `corvinHome()` resolved `CORVIN_HOME` via
  `path.resolve(env)` only — no whitespace-guard, no `~`/`${VAR}`/`%VAR%` expansion,
  unlike the Python resolver. Added matching `_expandVars()`/`_expandUser()` helpers.

### Fixed — claude CLI not found on Windows even when installed

- `_resolve_claude_bin()` (Python engine spawn) and `resolve_claude_bin()` (helper
  subprocess spawn) only probed POSIX fallback locations. npm's global installer drops
  `claude.cmd`/`claude.exe` under `%APPDATA%\npm` on Windows — the Windows equivalent of
  `~/.local/bin` — which was never searched. Added env-derived Windows fallback paths.

### Fixed — a standalone/minimal A2A sender crashed at import time (found via real hardware)

- Verified the above fixes on a real Windows-11 VM (not simulated): a real signed A2A
  envelope sent from genuine Windows NTFS to a Linux receiver, HMAC-verified round trip.
  The first real run surfaced a second, previously-unknown bug: `remote_trigger_sender.py`
  and `remote_trigger_receiver.py` each computed a module-level default directory via
  unguarded `Path(__file__).resolve().parents[2]`, which raises `IndexError` at import
  time for any deployment shallower than the full repo tree (e.g. a minimal standalone
  sender bundle). Fixed with a repo-marker walk-up; added a real-subprocess regression
  test (`test_a2a_shallow_path_import.py`) that doesn't require a VM to catch a regression.

### Testing

- 14 test files (7 A2A security suites, 7 Windows-parity suites) that were previously
  wired into **no CI pipeline at all** are now run on every push/PR via
  `run-all-tests.sh`. All Windows-specific branches are verified via `sys.platform`
  simulation on Linux CI, PLUS the A2A sender path additionally verified on a real
  Windows-11 VM this release.
- Not covered by this release: the A2A **receiver** running on Windows (no inbound
  network path was available to test against the VM), macOS (no Apple hardware/legal
  cloud-Mac in this environment), and live Discord/WhatsApp bridge crash-recovery
  specifically on Windows. See `Corvin-ADR` ADR-0265 for the full verification record.

## [0.10.74] — 2026-07-30

### Fixed — Discord Console setup was completely broken (CRITICAL)

- **Root cause:** the token-validation helper constructed
  `AutoOAuth2Generator({log: () => {}})` — an object where the constructor
  expects a bare function. Every `/validate-token` and `/save-token` call
  failed with a `TypeError`, regardless of whether the pasted token was
  valid — a new user could not complete Discord setup through the Console
  at all.
- **Fix:** corrected the constructor call. Verified live: an invalid token
  now correctly returns "Invalid token (401 Unauthorized)" instead of
  crashing. Also added whitespace/newline stripping (with a proper
  post-strip length re-check) to Discord and Telegram token fields — a
  copy-pasted token with a trailing newline previously threw a cryptic
  Node error instead of a clean validation message.

### Fixed — Discord answered everyone, forever, on an empty whitelist

- A tested `AutoOwnershipBridge` (locks ownership to the first sender, then
  denies everyone else) existed but was never wired into the daemon — the
  live behavior on a fresh install was "empty whitelist = every sender is
  owner, with no lock, ever." Wired it in; the zero-config "just start
  talking to it" setup is preserved, only the "stays open forever" gap is
  closed.

### Fixed — WhatsApp's reconnect loop could hammer WhatsApp's own servers

- A persistent non-logout disconnect (seen in the wild: reason 405/unknown)
  retried on a fixed 1-second delay forever — a real ban-risk DoS against
  WhatsApp's infrastructure, only stopped by a human manually killing the
  service. Added exponential backoff (1s → 2s → 4s → ... capped at 60s,
  with jitter) for every non-logout, non-515 disconnect; code 515 (the
  expected close right after a QR scan) keeps its fast ~1s reconnect so
  pairing still feels instant.

### Fixed — the bridge watchdog could only detect crashed processes, never wedged ones

- `watchdog.sh` checked only the HTTP response code — a wedged-but-alive
  daemon still answers 200. Rewrote it to parse the `/status` JSON body and
  restart on a sustained stall (Discord's `poller_stalled_s`/
  `precheck_stalled_s`, a new WhatsApp `disconnected_s`), with a threshold
  safely above the new WhatsApp backoff cap so a legitimate backoff window
  is never misread as a wedge.

### Fixed — assorted core/plugin bugs found during an overnight adversarial review

- `recall_backend.py`/`summary_provider.py`: a path-resolution bug
  (`parents[6]` instead of `parents[4]`) silently degraded conversation
  recall and LLM summarization to no-ops for any caller other than the
  bridge adapter; fixing it exposed a second bug (a module-registration
  ordering issue in the manual import loader), also fixed.
- `tripwire.py`'s audit-chain auto-healing (from an earlier release) never
  actually wrote the audit event its own docstring claimed to, and wrote
  its healed file non-atomically (crash-unsafe). Both fixed, with new test
  coverage — this path had zero tests before tonight.
- The A2A relay's routing table had no size cap, a latent memory-exhaustion
  DoS; bounded it.
- Two silent `except: pass` blocks around notification-backend calls now
  log instead of swallowing.
- A 5th PYTHONPATH-building site (`corvinOS/installer/steps/console.py`,
  used on Windows and in the restore/self-heal flow) was missing
  `core/plugins`, independently of the fix in 0.10.70/c4e2684.

### Process note

An adversarial refutation pass (a second, independent review specifically
tasked with trying to break everything above) found two real gaps in the
first draft of this release: the WhatsApp backoff was initially
comment-only (the actual reconnect call was never rewired), and the token
length re-check was backwards (checked the raw, pre-strip value) and only
applied to Discord, not Telegram. Both are corrected in what shipped here,
with new tests specifically covering the failure modes that let them slip
through the first time.

## [0.10.70] — 2026-07-29

### Fixed — Console SyntaxError shipped in 0.10.69 (CRITICAL)

- **Root cause:** 0.10.69's Console Auto-Build feature (`mount_static()` in
  `core/console/corvin_console/app.py`) shipped with a stray trailing `)`,
  a bare `SyntaxError` that broke the ENTIRE console app import. CI never
  caught it: no branch protection on `main` (commits land before CI can
  run), the last 13 consecutive Coverage Check runs were already red for
  unrelated reasons, and even a healthy run's first pytest invocation
  failed before reaching the later step that actually imports the file.
- **Fix:** Removed the stray paren. Console boots correctly again.
- **CI hardening:** Added `python -m compileall -q core operator corvinOS ops`
  as the first, dependency-free step in both `test.yml` and `coverage.yml` —
  catches a bare syntax error in ~1-2s regardless of what else is broken
  downstream, before it can ship in a tagged release again.
- **If you installed 0.10.69:** upgrade to 0.10.70 — the console will not
  start on that version.

### Fixed — Voice TTS silently stopped summarizing (regression from 0.10.68)

- **Root cause:** 0.10.68 added a `system_generated` trust flag to skip
  summarization for the first-boot welcome greeting, but gated it on
  `system_generated OR sid is absent` — and no frontend caller ever
  actually set `system_generated=True`, so in practice EVERY TTS request
  without a session id (not just the welcome screen) silently skipped
  summarization and read the raw, un-localized answer back verbatim —
  reintroducing the exact bug the 2026-07-24 release fixed.
- **Fix:** Gate on the explicit `system_generated` flag only. Wired it
  properly end-to-end: the welcome screen now sets it explicitly instead
  of relying on session-id absence as a side-channel signal.
- **Also fixed:** a German/English region-locale bug (`de-DE`, `en-US`)
  where the voice summarizer emitted an unnecessary extra translation
  directive instead of treating the region variant identically to the
  bare language code.

## [0.10.69] — 2026-07-28

### Added — Console Auto-Build, Model-Routing Consistency, Production Robustness

- Console SPA auto-builds on startup if missing (`npm install && npm run
  build`), with a graceful 503 fallback page if the build fails, instead
  of a bare 404.
- E2E tests for OS-model routing consistency across console and bridges.
- **Known issue, fixed in 0.10.70:** this release shipped with a
  `SyntaxError` in the auto-build code that broke console startup — see
  the 0.10.70 entry above. Upgrade immediately if you installed 0.10.69.

## [0.10.67] — 2026-07-28

### Fixed — Audit chain corruption blocks platform boot

- **Root cause:** Portable Tenant changes (Phase 1a/1b) corrupted the audit chain.
  The tripwire correctly refused to boot, but with no healing mechanism users
  were stuck with no recovery path except `rm -rf ~/.corvin`.
- **Audit Chain Healing:** When recent records are broken, truncate the chain at
  the last good record and allow boot to continue. The corruption is logged but
  does not prevent startup. Historical records are preserved.
- **Impact:** Fresh installs and corrupted chains now heal automatically instead
  of crashing with "audit writer is not sound". Users can now start CorvinOS
  without manual intervention.


## [0.10.66] — 2026-07-28

### Fixed — Console SPA not served (Windows / all platforms)

- **Root cause:** The React SPA (`web-next/dist/`) was not built before PyPI release,
  so the wheel contained no compiled HTML/JavaScript. On fresh install, `/console/`
  returned 404 because the SPA files did not exist.
- **Fix:** Build the SPA before packaging the wheel. The built `dist/` folder is
  now included in every PyPI wheel, so fresh installs work out of the box.
- **CI process:** Added SPA build as part of release checklist to prevent recurrence.


## [0.10.65] — 2026-07-28

### Fixed — Fresh-install startup robustness

- **Error messages:** Service bootstrap now provides actionable error messages
  when startup fails (missing config, corrupted audit chain, plugin errors).
  Instead of silent timeout, users see: "Try: rm -rf ~/.corvin && corvin start"
- **Fail-closed logging:** Bootstrap errors are logged with context (error type,
  error message) so operator can diagnose fresh-install issues from logs.
  No swallowing of exceptions — tripwire failures still abort the boot, but
  with clear visibility.

## [0.10.64] — 2026-07-28

### Added — a way back out of `headless_api_mode`

`headless_api_mode` was a one-way door. Turning it on unmounts `/console/`, which
is where Settings → Features lives — so the switch removed the only supported way
to un-flip it. It rendered as an ordinary checkbox, promising a reversibility it
did not have, and the only recovery was hand-editing `features.json`. It is a
deployment mode wearing a rollout flag's clothes.

- **CLI off-ramp.** `corvin config set features.<flag_id> <true|false>` now writes
  the same per-tenant overlay the Settings route writes
  (`<tenant>/global/features.json`). That overlay is the highest-precedence layer,
  so `corvin config set features.headless_api_mode false` also overrides a
  `spec.features.headless_api_mode: true` in `tenant.corvin.yaml` — no YAML editing.
  A non-boolean value is refused instead of being stored as a truthy string, and an
  unregistered flag id is refused with the registry printed. Deliberately **not** an
  env var: an env override would be the kill-flag shape this repo has ruled out.
  The command prints that a restart is needed, because `mount_static()` decides the
  SPA mount once at app creation.
- **Confirmation gate in the Console.** The registry entry now carries
  `self_locking=True`, and `/settings/features` exports `self_locking` plus a
  `recovery_command`. The Features panel shows a warning icon and a "no way back
  from the UI" badge, and a toggle opens a confirmation dialog that names the
  consequence and prints the recovery command *before* the door shuts. Nothing is
  persisted until the operator confirms. The UI reads `self_locking` from the
  backend and never hard-codes a flag id, so the next self-locking flag inherits
  the warning. The REST route stays a plain `PUT` — putting the gate there would
  have broken the CLI off-ramp, which must write the flag with no dialog anywhere.

## [0.10.63]

### Fixed — `/new` and every other shell-out command, on the bridges that had them broken

Seven rounds of adversarial review over the 2026-07-25…28 changes. Grouped by
what an operator would actually have noticed.

- **`/new` and `/reset` were dead on Signal and Teams.**
  `session_reset.VALID_CHANNELS` listed five of the seven shipped channels, so
  `--channel signal` was an argparse error: the user got "session reset failed"
  and the session was never reset. Two sibling copies had drifted the same way —
  `settings_view` hid Signal/Teams from `/settings`, and `bridges_migrate` never
  migrated their legacy state. The list now lives once, in
  `operator/bridges/shared/channels.py`, and
  `shared/test_channel_list_ssot.py` pins every consumer (including
  `bridge_manager`, the `corvin_plugins` supervisor copy and the installer)
  against it. Four `paths.py` files also held a private `_BRIDGE_CHANNELS`
  frozenset that was assigned, never read, and stale — removed, since a reader
  reasonably took it for the canonical list.

- **Every in-chat command that shells out to Python was broken on a pip
  install.** A daemon spawned by `bridge_manager` runs from
  `<corvin_home>/bridges/<channel>/`, and only `shared/js/*.js` is mirrored
  beside it — so `path.resolve(__dirname, '..', 'x.py')` pointed at a directory
  containing no Python at all. `/new`, `/reset`, `/engine`, `/role`, `/grant`,
  `/quota`, `/audit`, `/consent`, `/join`, `/pass`, `/goal`, `/objective`,
  `/propose`, `/dialectic*`, `/ps`, `/kill`, `/lang`, `/profile`, `/settings`
  and `/a2a` all failed with ENOENT — and all worked in a git checkout, where
  `__dirname/..` happens to be the source tree. That asymmetry is why nothing
  caught it. Resolution now goes through `bridge_paths.operatorRoot()`, seeded
  by `CORVIN_BRIDGE_OPERATOR_ROOT` from all three spawn sites, with the
  self-locating fallback preserved. `shared/js/test_operator_root_resolution.js`
  reproduces the runtime layout and fails if any CLI constant regresses.

- **`corvin plugin install|uninstall|list|enable|disable` never ran.** Every one
  imported `core.plugins.tenant_plugins`, a module that does not exist; the real
  path is `corvin_plugins.tenant_plugins`. All five exited 2 with "plugin system
  not available". Fixed and exercised end to end (`new` → `check` → `install` →
  `list` → `disable` → `enable` → `uninstall`), which surfaced two more: the
  scaffold emitted the legacy `layer:` key so `corvin plugin check` warned about
  its own freshly generated manifest, and `installed_at` was written as
  `…+00:00Z` — two timezone designators, invalid RFC 3339.

- **The Plugin-Builder crashed on a skill idea on every pip install.**
  `templates/skill_plugin.md` was eaten by the wheel's `**/*.md` exclude, so
  `PluginKind.SKILL` hit `FileNotFoundError`. Verified against a built wheel
  before and after. The same build also shipped `core/plugins/templates/` twice,
  once at a path that is not on `sys.path` — now excluded.

- **A bare `/delegate` on a bridge spawned an empty ACS run** and charged a
  `compute_units_per_day` for it. The console has always guarded this; the
  ADR-0255 bridge path inherited the directive without the guard.

- **`/new`'s model summary reported the wrong OS model.** It consulted only the
  env override and otherwise printed `high_model()`, so an operator who had
  pinned `spec.engine_models.<engine>.os_model` was told "Sonnet" while every
  turn ran their pin. It now calls the same `resolve_os_model()` cascade the turn
  does — the surface-specific re-derivation this release removed from
  `chat_runtime`, left behind on one ack.

- **`poller_stalled_s` / `precheck_stalled_s` were exposed by Discord only.**
  Signal, Slack, Teams and Telegram drive the same shared poller and can wedge
  identically; the two counters a watchdog needs existed but were unreadable on
  four of five bridges.

- **The installer offered five of seven bridges**, so a fresh install could
  never select Signal or Teams — and its Windows uninstall sweep listed five
  too, leaving their Scheduled Tasks auto-launching a bridge after uninstall.

### Fixed — three "announced, never silent" degrades that were silent

- **No `notice` event was ever rendered.** The backend has emitted
  `{"type": "notice"}` since ADR-0201 (`quota_fallback`, `acs_fallback`, and the
  new `artifacts_truncated`) and nothing in the frontend handled the event at
  all — every one was dropped on the floor, so each "announced" degrade was
  announced only in the server log. `chat-registry` now carries a `notice`
  message part and `chat.tsx` renders it distinctly from the model's answer.
- **The ACS artifact branch truncated at 20 chips with no notice**, `break`-ing
  instead of counting — the one place where a dropped artifact genuinely read as
  "the run produced nothing". Both branches now emit the one shared
  `_artifacts_truncated_notice()`, in English (the first draft was a hard-coded
  German literal, and a runtime notice has no model answer whose language it
  could follow).

### Fixed — two test suites whose verdict depended on the developer's own config

- **`core/plugins/tests/` read the operator's live feature-flag overlay.**
  Measured: 1073 passed with a clean `CORVIN_HOME`, **17 failed** with the
  maintainer's (15 flags on, `bridge_supervisor_plugins` among them, which made
  `bootstrap_declared()` inject seven bundled bridge supervisors into assertions
  expecting one plugin). The split pointed the wrong way — a clean CI runner is
  green, so the gate this suite became on 2026-07-27 reported success while the
  person running it locally saw red. Both plugin conftests now pin
  `CORVIN_HOME` to tmp.
- **`test_adapter_big_data_delegation.FlagOffTest` pinned nothing** and read the
  same overlay, so the dark-flag half of the MANDATORY bridge gate went red on
  any box where the flag was on, while its two weaker siblings passed by
  accident. It now has a `_FlagOff` to match `_FlagOn`.

### Fixed — the live-state tripwire cried wolf about a destroyed audit log

- **`conftest.py::_live_state_tripwire` reported "LIVE OPERATOR STATE DESTROYED"
  on a clean run.** It re-resolved `Path.home()` for its "after" snapshot, and
  two tests in `tests/` patch the home directory — one with
  `monkeypatch.setenv("HOME", …)`, one with `monkeypatch.setattr(Path, "home",
  …)`, which rebinds the shared `pathlib.Path` class the tripwire itself uses.
  Their monkeypatch teardown runs after the tripwire's, so the comparison looked
  into an empty tmp home and announced that `~/.config/corvin-voice` and every
  `corvin-*` systemd unit had been deleted. Both were fully intact — verified
  immediately after: 30 units present, every service running.
  This guard exists because of the 2026-07-08 run that really did delete the
  running bridge's session state, budgets and hash-chained audit log, so a false
  alarm in exactly that wording is the failure that gets it muted. The four
  protected roots are now frozen at import, before any test can patch a home —
  which also makes the guard *stricter*, since a patched lookup could previously
  have hidden a real deletion. `tests/test_live_state_tripwire.py` pins both
  directions, including the 2026-07-08 shape (tree survives, audit chain does
  not).

### Fixed — a test that was committed red and stayed red

- **`test_cost_ratio_persistence_across_reloads`** (landed 2026-07-25 with
  "LDD k=2 COMPLETE") called `record_delegation_result(schema_valid=…,
  downstream_ok=…)`. Neither argument has ever existed on that method — the
  required one is `loss_pct` — so it raised `TypeError` on every run since it
  landed, while the other twelve tests in the file used the real signature. It
  also set `os.environ["CORVIN_HOME"]` without restoring it, leaking a tmp home
  into every test that ran after it in the same process; monkeypatch now owns it.

### Fixed — a third suite whose verdict depended on the developer's own state

- **`test_build_spawn_env_refreshes_openai_key_for_non_claude_code_engines`**
  asserted the key it had just written to a tmp `service.env` and got the
  operator's real one. The Phase-1b encrypted SecretsStore (2026-07-25) resolves
  from `CORVIN_HOME` and now takes precedence over `service.env`, so the test's
  `VOICE_CONFIG_DIR` isolation no longer covered the path it exercises. Green on
  a clean runner, red on a machine with a real store — the third instance of that
  split found in this pass.

### Fixed — 73 tests and two new bridge suites that ran in no CI

- `core/plugins/plugin_builder/tests/` (7 modules) could not even be collected:
  `index_store` imported `forge` at module level, which also meant
  `import plugin_builder.turn` — the shared entry point both the Console and the
  bridges drive — raised `ModuleNotFoundError` anywhere the host bootstrap had
  not run. Deferred to call time, like `turn.output_dir` already did.
- `core/plugins/plugin_builder/tests/` and `ops/launcher/corvin/tests/` added to
  `coverage.yml`; `test_bridge_worker_engine_parity.py` and
  `test_os_model_single_source_of_truth.py` added to `run-all-tests.sh`. All
  four had shipped without a runner — the defect class `coverage.yml`'s own
  comment documents.

### Fixed — documentation that contradicted the code in the same tree

- **CLAUDE.md still said bridges "never call `worker_engine_target`, never read
  the setting … and there is no `/delegate` there".** Both halves of that were
  false with `bridge_big_data_delegation` shipped and ADR-0255 landing, and it
  contradicted `delegation-routing.md` §4 in the same commit. Replaced with a
  per-flag reach table. It is the always-loaded orientation file, so it was the
  most-read wrong statement in the repo.
- **The bridge `/help` advertised `/engine acs` and `/engine tiered_delegation`**
  — `engine_switch` rejects both (`VALID_ENGINES` has no such members) and the
  text conflated the worker-CLI axis with the native/acs/tde delegation mode,
  which has no chat command at all.
- **The Plugin-Builder reply lost its pointer** to Settings → Plugins →
  Scaffolded by Plugin-Builder when the logic moved into the shared
  transport-agnostic module; `index_store.record` is what puts it there, and
  without the line nothing told the author the scaffold has a home in the UI.
- `bridge_worker_engine_parity` added to the `tenant.corvin.yaml` flag block.

### Open — reported, not fixed

- **The audit chain gained two new MAC-mismatch windows on 2026-07-27** (19:12
  and 19:28, 156 records) in `.corvin/global/forge/audit.jsonl`. Full picture:
  154 469 records, 536 broken across **six** windows — four historical
  (2026-07-11…13, 380 records: exactly the "known window" the boot tripwire's
  docstring describes) and two new. Zero in the tail, so the boot tripwire
  correctly does not block and the writer is sound now; an append-only break is
  permanent and must not be rewritten.
  **Nobody was told.** `corvin-audit-verify.service` detected it and exited 1 as
  designed, but its only alerting path logged `chain-break notification: relay
  not configured or no targets — skip`: `~/.config/corvin-voice/relay.json` does
  not exist on this install. The mechanism works when configured; configuring it
  needs a channel and a target chat, which is an operator decision.
  The likely cause of the new windows is a test run appending to the live chain
  before `core/plugins/tests/conftest.py` gained its `VOICE_AUDIT_PATH` redirect
  — which landed the same day.

### Documented — two surfaces that are built, tested and unreachable

Recorded rather than quietly wired: giving either a call site is a design
decision, not a review fix.

- **`execution_context_badge` gates nothing.** Zero readers — grep it. The
  per-turn metadata IS captured, persisted and read by the audit view and the
  turn filter, but no console component renders a badge, and on the bridges
  `execution_context_renderer.js` plus its six daemon call sites never fire
  because `adapter.py` never puts an `execution_context` key on an outbox
  payload. Its `show_execution_context` setting is a second truth for the same
  thing and is dead for the same reason. Flag left registered (the capture half
  is real) with a description that now says so, so Settings → Features stops
  offering a toggle that does nothing.
- **Google-A2A interop is absent outbound and manual-only inbound.**
  `GoogleA2ASender` has no CLI, no route and no importer outside its two test
  files; `/a2a` and the orchestration MCP server both use native
  `remote_trigger_sender`. The inbound adapter is mounted only by
  `a2a_http_server`, which an operator must start by hand — the FastAPI gateway
  every normal install runs exposes no Google routes.

### Fixed — a chat turn could emit 144 artifact chips of the same two files

- **Runtime bookkeeping is no longer a chat artifact.** Every model-chosen
  `delegate_*` MCP call writes a WDAT run record under
  `<session>/acs/runs/<run_id>/{manifest,result}.json`, and the direct OS-turn's
  artifact scan diffed the entire session workdir with no exclusion. In the
  console chat `web:ISGd-xIvqn` one turn made 72 such calls, so the chat showed
  144 chips — 72× `manifest.json`, 72× `result.json`. The ACS delegation branch
  already filtered these, but only relative to its own `run_dir`, so the direct
  turn — the path that actually ran that chat, in `native` mode with
  `will_delegate: false` — had no filter at all. The scan now lives in
  `chat_runtime._scan_turn_artifacts()`, skips `acs/`, `tasks/`, `tde/` and
  `voice/`, and caps a turn at 20 chips with an announced
  `artifacts_truncated` notice rather than a silent drop.

### Changed — the auto-ACS route is now a narrow structured-data rule

- **`_is_big_data_task()` fires only on CSV/spreadsheet files, database work, or
  genuine tabular mass data.** It is the one auto-delegation a `native` install
  performs and each run charges one `compute_units_per_day`, so the rule is
  affirmative and narrow: big-data vocabulary · a pipe/markdown table of ≥10
  rows · a CSV/Parquet/XLSX/JSONL file or a database/SQL operation **paired**
  with a bulk data verb or a volume · the legacy volume-plus-data-noun clause.
  Naming a source is no longer enough — "Wie verbinde ich mich mit MySQL?" and
  "Erkläre mir SQL" stay in-process. A code clause is now carved out like the
  hardware one, so "2 Millionen Zeilen Code refaktorieren" no longer fans out,
  which finally makes "coding never routes into the ACS fan-out" true for
  big-count coding prompts. The ReDoS bounds are preserved; the table scan gets
  its own larger cap because rows are payload, not description.

### Fixed — the boot tripwire ran on one of two shipped hosts (ADR-0252)

- **`corvin_console.standalone` now runs the compliance boot sequence.** It is
  the host `corvinos-serve` starts and the one `install.sh` launches, and it ran
  no tripwire and loaded no plugins. Found by booting a wheel install with one
  corrupted `hash` in the audit chain: the console served requests, while
  `assert_all()` inside that same process correctly refused. The sequence now
  lives once in `bootstrap.boot_platform()` — tripwires → plugin load →
  post-boot tripwire, no flag and no override — and both hosts call it.
  `test_boot_platform_call_site.py` pins which hosts must.
  - **Upgrade hazard:** an install whose audit chain has a break within the last
    200 records will now refuse to start the console. That is the documented
    fail-closed contract and there is deliberately no flag to soften it; a
    pristine install is unaffected. No operator-facing way to seal a historically
    broken chain exists yet — that is owed.
- **`engine_registry` can build `hermes` and `copilot`.** Both engine classes
  ship and both are offered in Settings → Engines, but the registry had no
  builder for either, so `/settings/engine/capabilities` reported them with zero
  capabilities and no command manifest while the objects themselves have ten.
- **The voice summariser no longer deletes the sentence that mattered.**
  `CRITICAL:` / `WARNING:` / `DANGER:` sentences were dropped entirely — the
  generator narrated from classification labels, and a label cannot carry
  "restart the workers or it hangs". They are now carried verbatim and spoken
  second, ahead of both truncation points. Three related defects fixed with it:
  the work-type classifier matched substrings (`"handlers"` matched `"handle"`,
  so a refactor was reported as a fix), `polish_for_audio` defaulted to German
  while every template in the module is English, and lifted sentence fragments
  lost their terminator to the regex split.
- **Live Anthropic model catalogue.** `providers.anthropic.model_source` was
  `static`, which froze the model picker until the next CorvinOS release. It now
  walks `GET /v1/models`, caches the result, and `engine_models.load_registry()`
  merges it into the curated lists at read time — additively, so the curated
  entries and their defaults are untouched and an install with no
  `ANTHROPIC_API_KEY` (a Claude Code subscription login has none) keeps exactly
  what it has today. Curated Opus entries moved to `claude-opus-5`.

### Fixed — CI ran a fraction of the suite

- `coverage.yml` named 6 of the repo's 22 test directories; the other 16 ran in
  no workflow at all, among them `core/gateway` (227 tests), `core/delegate`
  (317) and `operator/license` (236). The job also died ~1.5 s in with an
  `INTERNALERROR` — two test modules called `sys.exit(0)` at import time as a
  skip, which pytest sees as a `SystemExit` during collection — so it ran
  essentially nothing. 1122 previously-unrun tests are added, in per-directory
  sessions, and `core/console/tests` gets its own wall-clock-bounded step.
- **The plugin suite no longer depends on the import mode.** It passed 1040
  under pytest's default mode and failed 15 under `--import-mode=importlib`, the
  mode CI actually uses: the fakes declared a bare `class_path` module name that
  only resolves under prepend mode. `class_path` now comes from `__name__`.

## [Unreleased]

### Added — Plugin boot layers (ADR-0243, phases 0–7)

- **`boot_layer` axis** on the plugin registry: `compliance` · `core` ·
  `bundled` · `installed`. Decides load order and whether an operator may switch
  a plugin off. Orthogonal to `tier` (ADR-0156 capability boundary) and `origin`
  (provenance) — the three are never conflated, which is why the field is not
  called `layer`: that word was already four-way taken in this repo.
- **Layered boot.** `bootstrap_global()` loads bundled compliance-then-core
  plugins before any tenant plugin. A compliance failure aborts the boot; a core
  failure degrades and audits. No feature flag, deliberately — a switch on the
  pass that loads compliance plugins would be the kill-flag the baseline forbids.
- **Extension-point bus** (`plugin_extension_points`, default off) with four
  named points and a `fail_closed` marker for gates. All four now have call
  sites (ADR-0251), each enforcing its own bound: a hook may de-escalate the
  engine but never widen the operator's choice, suppress a delegation but never
  start one, name any model in the engine's registry and nothing else, and deny
  a workflow run but never permit one the core refused.
- **Admin control plane** (`admin_control_plane`, default off): REST under
  `/api/admin/plugins`. Disable is refused with 403 on the compliance layer.
  gRPC deliberately deferred.
- **Bridge supervisor plugins** (`bridge_supervisor_plugins`, default off):
  Python supervisors for the seven existing Node daemons. No daemon rewrite.
- **Headless mode** (`headless_api_mode`, default off): suppresses every browser
  surface — SPA, `/local-stats`, the `/` and favicon redirects, the experiment
  HTML report. Does **not** touch bridges; the two flags are independent so
  "core + CLI + bridges, no browser UI" stays reachable.
- **Post-boot compliance tripwire.** No plugin may sit on `boot_layer=compliance`
  that `bootstrap_global` did not itself grant. Non-overridable, runs after the
  plugins load.

### Changed — the perimeter is attribution, not security

Six adversarial review rounds established that in-process plugin identity is not
enforceable in CPython: each round broke the previous derivation (the object in
the provider slot, then the `plugin_id` argument, then a ContextVar), and the
last needed one line. Recorded in CLAUDE.md and `docs/claude-ref/layer-plugins.md`
rather than answered with a seventh guard. The audit field `tenant_check` now
records `attributed` / `unattributed`; the old spellings remain as aliases for
one release. Anything that must hold against a hostile plugin belongs in a
subprocess.

### Fixed — plugin lifecycle

- A disabled plugin could keep the provider slot and go on receiving every
  tenant's audit events (three distinct shapes: a helper object, a foreign
  `plugin_type`, and a slot taken from a worker thread).
- The circuit breaker was an automatic off switch for compliance plugins,
  reachable from an ordinary admin page load and from a hot reload.
- `register`/`unregister` could interleave for the same plugin, so `on_unload`
  ran during `on_load` and the append-only chain recorded them in the wrong
  order.
- `health_check_all` had no deadline, so one wedged plugin held every
  `GET /api/admin/*`.
- Tenant B could stop tenant A's plugin when both installed the same id.
- Entry points were imported at boot despite `auto_discover_entry_points: false`
  — in the plugin loader and, separately, in the nervous system (ADR-0177).
- `corvin.global_plugins` entry-point discovery removed: any third-party wheel
  could publish `compliance:whatever` and become undisableable.

### Fixed — the console ignored its own OS-model setting (ADR-0119/0123)

- **`chat_runtime.py` never read `spec.engine_models.<engine_id>.os_model`.**
  The console web-chat hand-rolled only Tier 1 (env override) and Tier 3
  (adaptive autoselect) of the OS-model cascade — the per-engine tenant
  default configured under Settings → AI Engines had an effect on the bridge
  adapter only, never on the console's own chat. The full 6-Tier cascade
  (`operator/bridges/shared/adapter.py::_resolve_os_model_bundled`) is now
  extracted into `model_selector.resolve_os_model()`, and both surfaces call
  that one function — a genuine single source of truth instead of two
  implementations that happened to overlap.

### Added — bridge worker-engine parity (ADR-0255, default off)

- **`bridge_worker_engine_parity`** flag: a Discord/Telegram/Slack/WhatsApp
  turn can now reach the operator's Settings → AI Engines `worker_engine`
  mode (native/acs/tde), an explicit `/delegate` override, and the console's
  own ADR-0202/0203 triage heuristic (imported directly from
  `corvin_console.chat_runtime.should_delegate_bundled` — one implementation,
  not a second copy) — not only the narrow big-data shape
  `bridge_big_data_delegation` already covered. Makes
  `spec.engine_models.<engine_id>.worker_model` reachable on bridges for
  ordinary conversation, closing the gap where a bridge turn had no
  reachable worker-turn call site at all outside the big-data special case.
  Off (default): byte-identical to the existing `bridge_big_data_delegation`
  path. TDE stays unreachable from bridges either way — ADR-0221 P3/P4 stay
  frozen pending ADR-0222's measured gate; `mode=tde` always degrades to the
  direct turn on a bridge, by construction, not by a runtime probe.

## [0.10.61] — 2026-07-24 — TDE production-readiness review + shared agentic-compute pool (ADR-0216)

Four-axis adversarial review of the last 3 days (TDE core, audit chain, TDE
audit graph/visualization, ADR-0215 infrastructure) with every confirmed
finding fixed, plus the new daily agentic-compute limit. Default gates green:
repo-root pytest 721 passed, console suite green, web-next tsc + build +
844 vitest tests, license suite 240+ passed.

### Added — Licensing (ADR-0216)

- **Shared agentic-compute pool, free tier 10 turns/day.** TDE now charges
  the SAME daily pool (`compute_units_per_day`, one counter file) as ACS,
  compute/grid-search runs, forge `compute_run` and A2A compute — the limit
  is the sum across all engines. Free-tier default raised 1 → 10; member
  stays unlimited. TDE was previously the only unmetered engine. Fail-closed
  chokepoint in `TieredDelegationEngine.execute` (missing license module →
  deny; invalid plans refund their unit; stub/mock test configs unmetered).

### Fixed — Audit chain (GDPR Art. 30/32, load-bearing)

- **`tde.l34_prescan` now hash-chains.** The L34 gate decision event was
  written to the unchained per-tenant web log; it now goes through
  `tde_audit.emit` onto the canonical chain (with an allowlist entry —
  the naive port would have scrubbed the payload empty).
- **TDE audit-graph endpoint is tenant-scoped (fail-closed).** Every
  console-originated tde.* record now carries the authenticated
  `tenant_id` (reserved audit_event arg, not details-injectable); the
  endpoint 404s cross-tenant and unstamped-legacy runs identically.
- **Whole-chain verdict surfaced.** `meta.chain_verified_global` +
  `chain_problems_total` ride alongside the segment-scoped
  `chain_verified` (prev-hash linkage is transitive; a break before the
  segment un-anchors it).
- tde_audit hardening: backend-unavailable now logs WARNING (was silent
  DEBUG drop of all tde.* events), `delegate`/`step_num` type-pinned.
- Tests no longer append to the live audit chain (repo-root conftest
  redirects `VOICE_AUDIT_PATH` to tmp; verified zero growth over the full
  suite — previously every pytest run left permanent `tde.*` noise in the
  GDPR Art. 30 record).

### Fixed — TDE visualization (ADR-0214 k=8, end-to-end)

- **`tdeProgress` chain was dead end-to-end:** the backend persisted it, but
  no frontend code ever assigned it — the metrics card never rendered, live
  or after reload. Now: `engine_progress` reducer in chat-registry stamps
  the live message; history hydration maps the persisted `tde_progress`
  (and re-derives `tdeRunId`, so the TDE Graph tab survives reload).
- **`tde_progress_dict` UnboundLocalError** killed every degraded TDE turn
  (analysis timeout/CLI missing) after the k=8 commit; init moved before
  the try.
- web-next `tsc` build was broken (unused imports) — repaired + SPA rebuilt.
- TDE Graph panel: run id + metrics resolved from the same message; manual
  run-id override validated against the `tde-<epoch>-<hex>` shape (no more
  one 404 per keystroke); polling while the turn streams (no more sticky
  404 latched at turn start); fabricated "30-70% estimated savings"
  placeholder replaced by the genuinely measured latency delta and an
  honest "not measured" for token savings.

### Fixed — TDE core

- Streaming executor: 10MB chunks exceeded the L34 5MB scan ceiling —
  every ≥500MB value silently yielded an EMPTY stream while reporting
  success; chunk tiers now capped under the ceiling (bytes account for
  str()-repr inflation), and chunk seams are re-scanned with a one-chunk
  lookahead so a secret straddling a boundary can no longer reassemble.
- Delegation prompts >128KB hit Linux MAX_ARG_STRLEN (`E2BIG`) — snapshot
  capped at 100KB with visible truncation; analysis runner maps `OSError`
  to the degraded path instead of crashing the turn.
- `/debug-engine` now kills its analysis subprocess on client disconnect
  (ProcHolder try/finally — was a leaked real LM call per abandoned use).
- Shadow-run loss measurements get their own ProcHolder (same disconnect
  leak); executor-level `ExecutionError` returns an error result instead
  of crashing the turn; wiring gate reports malformed manifest entries as
  FAIL findings instead of crashing on KeyError.
- Compliance auditor (`eu_ai_act_audit.py`) existence checks anchored to
  the repo root — previously reported spurious L10/L16/L23/L35/L37/L38
  failures whenever cwd ≠ repo root.

### Fixed — Benchmark honesty (operator/benchmarking)

- **Removed the fabricated p-value** (bucketed pseudo-p forced to 0.01 for
  any mean delta > 500) and all "statistically significant / real and
  reproducible" claims from code, committed result artifacts, docs,
  diagrams and README. The package is now labeled throughout as what it
  is: a deterministic SIMULATION of assumed savings ratios. "95% CI" from
  n<10 samples replaced by an honest observed range; dead
  `token_collector.py` deleted; fabricated result dirs replaced by a
  regenerated, honestly-labeled run. `tde.bench`'s unwired
  `benchmark_target: true` manifest claim removed; its target registry is
  now actually consulted (deduped) by `run_default_suite`.

### Fixed — Tests

- `test_adr_0214_engine_visibility` / `phase3_streaming` /
  `phase4_discovery` async tests ran never (missing pytest-asyncio
  markers under strict mode) — marked and, where they would have spawned
  a real `claude` CLI, given offline stub executors.
- Two aspirational routing examples xfail'd with the real reason
  (conservative canary fallback) instead of failing the suite.

### Known / documented (not fixed here)

- Pre-existing cross-file test-isolation flake:
  `test_license_hardening.py` (module-identity churn via
  `_fresh_validator`) can poison
  `test_compute_license_gate.py::test_enforce_chat_turns_leaks_across_tenants_cross_tenant_dos`
  in specific multi-file batches; all files green standalone.
- Live chain segment 21643–31689 (2026-07-11→13) has 380 pre-existing
  `mac_tampered` verify problems (MAC-anchor-key mismatch, prev-hash
  intact) — predates this window, now visible via
  `chain_verified_global=false`.
- `MockWorkerIPC` fabricated successes are self-describing in the chain
  (`ipc: "MockWorkerIPC"`); production console wiring uses real IPC.

## [0.10.59] — 2026-07-22 — iterative adversarial review of the last 3 days (6 axes + refutation round)

Six-axis adversarial review over everything shipped since v0.10.58 (ADR-0199 /
0210 / 0211 / 0212, Discord zero-config, console UX), followed by a dedicated
refutation round attacking the fixes themselves. Every default test gate is
green again (`tests/`: 549 passed; bridge suite; web-next tsc + build).

### Fixed — Compliance (GDPR, load-bearing)

- **Geo-tier auto-migration retired (GDPR Art. 21).** The v0.10.58 tier-1→3
  migration could not distinguish the legacy default from an EXPLICIT
  `geo_tracking_tier: 1` opt-out — the exact mechanism ADR-0208 documents —
  and silently re-upgraded user opt-outs on the next heartbeat. Retired
  loss-free (v0.10.58 heartbeats already migrated upgrading installs; absent
  keys still default to tier 3). Explicit tier values now always stick.
- **Reverted ADR-0212 "Ecosystem Feature Telemetry"** (committed today by a
  parallel session): the new `aco/telemetry/` package shadowed the existing
  `aco/telemetry.py` module, which silently killed the ADR-0186 presence
  heartbeat AND the ADR-0198 reconnect broadcast at import time; the payload
  carried free-form filesystem-derived strings (PII-capable, violates the
  CONTENT-FREE invariant); the stats endpoint 500'd on a nonexistent
  `SessionRecord` field; and the change to the heartbeat's "empty body"
  contract was never ratified (draft ADR in the wrong repo, no maintainer
  decision). A compliant re-implementation needs a real ADR-0212 first.
- **`tests/test_installer_piper.py` no longer overwrites the operator's live
  `~/.config/corvin-voice/profile.json`** on full pytest runs (sandbox-trap
  class; `display_language` was being flipped by the E2E language matrix).

### Fixed — A2A ping receiver (ADR-0199)

- Route moved to POST (sender and the ADR always specified POST; the GET
  route made every real ping fail), signed response now echoes
  `task_id = ping_id` (Decision 3 — pings failed AUTH even after the method
  fix), and the gateway serves `/v1/a2a/ping` too — both backends delegate to
  one shared `process_ping_request()` core, so ADR-0199's parity requirement
  holds by construction.
- Anti-enumeration: unknown-origin and bad-signature collapse into one opaque
  `403 ping_rejected`; freshness (`stale_ping`) is only diagnosable with a
  valid signature. Ping-only bounded rate-limit buckets (60 rpm) run BEFORE
  any disk work — deliberately separate from the post-HMAC `/receive`
  buckets so fake origin floods cannot evict real buckets (refutation-round
  finding against the first version of this very fix).

### Fixed — Console / bridges

- **Discord/Telegram save-token endpoints write the canonical runtime
  settings path** (`<corvin_home>/bridges/<ch>/settings.json`) instead of the
  source/vendor tree — token rotation via Console never reached the daemon
  after first boot (writer≠reader split, same class as the fresh-install bug
  fixed in 0.10.56). Both validate/save endpoints now require CSRF; the
  setup dialogs send `x-csrf-token` (new `csrf` prop).
- **Ten structurally non-functional zero-config endpoints now return an
  honest HTTP 501** instead of dead-ends or fake state: Slack OAuth (wrong
  API paths, wrong content-type, cannot yield the `xapp-` Socket-Mode
  token), Teams OAuth (delegated Graph token vs Bot-Framework daemon
  credentials), Email OAuth (form-encoding violation; token consumed by
  nothing), Signal QR/poll (mock; unconditional fake `linked=true`),
  WhatsApp QR/poll (mock QR; success branch mathematically unreachable).
  Manual bridge settings remain the supported setup path (ADR-0211 amended).
- Bridge disconnect: the SPA now surfaces `restart_needed` (daemon could not
  be stopped on non-systemd hosts) instead of a false-green toast, and
  disconnect removes `settings.json.bak` so no cleartext token survives a
  revocation. Telegram setup dialog rebuilt against the real API response
  shape (was a broken Discord copy-paste with a fictional OAuth step).

### Fixed — ADR-0210 scaffolding + test infrastructure

- `make_task_analysis_prompt()` crashed with `KeyError: 'path'` on every call
  since Phase 1 (unescaped template brace); decision cache no longer resets
  the TTL clock on SQLite reload (stale decisions up to ~2× TTL), keeps LRU
  book-keeping in sync on `invalidate()`/`clear()`, and wires the previously
  dead `context_hash_cache_size` parameter; `fallback_strategy` is a failure
  policy again, not a mode selector (Phase-3 parallelism was off by default);
  the 46-test ADR-0210 suite is green again (10 tests were red on main —
  two fix commits had broken their own earlier phases without re-running the
  suite). ADR-0210 status amended: scaffold-only, NOT integrated — the
  headline 56%/50% savings remain unrealized.
- `tests/` default collection no longer aborts: the new profile-identity
  tests import the shared profile module the documented way (stdlib
  `operator` collision) and actually test the 2000-char cap; the
  hatchling-dependent build test skips cleanly outside CI.

## [0.10.56] — 2026-07-22 — fresh-install bridge repair (WhatsApp/Discord/all channels) + adversarial review sweep

The bridges were silently dead on a fresh install. This release fixes every
channel end-to-end, then runs a six-axis iterative adversarial review over the
last three days of work (20 findings confirmed after a three-lens refutation
pass, all fixed). It is a strict superset of the git-only builds 0.10.52/0.53
(geo-tracking scaffolding) and the published 0.10.54/0.10.55 (ecosystem
dashboard) — see the version-hygiene note at the end.

### Fixed — Bridges (fresh install)

- **WhatsApp: replies vanished after a re-pair.** A re-pair issues a new `@lid`
  identity; the inbound gate and `/on` handle both JID forms, but the outbound
  gate checked the single raw reply JID without alias expansion, so every reply
  was dropped as "disabled chat" while inbound kept working. `isAnyChatEnabled`
  now alias-expands the query symmetrically (the deny-list still wins, so `/off`
  is never bypassed). On `loggedOut` the daemon now moves `auth/` aside
  automatically so the next start shows a fresh QR instead of wedging on stale
  `registered=true` creds.
- **Discord was completely dead with a valid token.** The daemon hardcoded the
  privileged `MessageContent` intent; a fresh app has that portal toggle OFF, so
  the gateway rejected the login (4014 "used disallowed intents") and the daemon
  exited with a misleading "token rotation needed". A REST preflight now checks
  the portal state before the IDENTIFY and boots in **token-only mode** when the
  intent isn't granted — DMs and @mentions work with just a bot token, no portal
  changes. Leading @mentions are stripped from inbound text. Onboarding across
  the console, installer and docs now states the token is all that's required.
- **The adapter never started on the console Start-button path**, so nothing
  polled the shared inbox and the bot never answered. The button now also
  ensures the adapter (cmdline-verified, cross-launcher-aware idempotency — it
  won't spawn a duplicate that double-processes the inbox).
- **Adapter queues split from the daemons on a wheel install.** The adapter
  defaulted its queues to its vendored `shared/` dir while daemons polled
  `<corvin_home>/bridges/shared`; they now agree. A related trap — the adapter
  treated a pinned `ADAPTER_INBOX` as "test sandbox" and forked its GDPR audit
  chain + re-rooted `CORVIN_HOME` — is closed by an explicit
  `CORVIN_ADAPTER_SANDBOX` signal instead of the overloaded inference.
- **Console-saved bridge tokens never reached the daemon.** `PUT /bridges/{ch}/
  settings` wrote the vendored source dir while daemons read the runtime path;
  writer and reader now agree (with legacy read-fallback + migration), and both
  the console and `bridge.sh channel_configured` honour the `service.env`
  `CORVIN_HOME` pin.
- **Signal & Teams could not be started by anything** despite the console
  offering them — now wired into `bridge_manager` and `bridge.sh`, with canonical
  settings paths, the disable gate, real templated systemd units, and a fixed
  health-port collision (Teams 7895 → 7897).
- **Email** now names the `auth_results_authserv_id` fix in its drop log when the
  IMAP provider isn't in the built-in receiver list.
- POSIX wheel installs can now start bridges at all (native_backend falls back to
  the vendored `bridge_manager.py` when `bridge.sh` is absent).

### Fixed — Telemetry / stats (adversarial review)

- **Country was `XX` for nearly every instance.** `CF-IPCountry` is a
  Cloudflare-managed header the Workers runtime strips on subrequests to the
  Railway origin. The Pages proxy now also sends `X-Edge-Country`, which the
  origin prefers (ADR-0207).
- **Public stats were geo-spoofable.** Railway is directly reachable, so a direct
  POST could forge country/region/city onto the public dashboard. A shared
  `GEO_PROXY_SECRET` (`X-Proxy-Auth`) now lets the origin trust geo headers only
  from the edge proxy; staged rollout — trust-all until the secret is set on both
  sides (ADR-0208).
- **The healing-traces CLI opt-out/erase was a no-op** (GDPR Art. 7(3)/21): it
  wrote an unread `{revoked:true}` stub while the runtime gate reads only the YAML
  flag, so collection and uploads continued after "No further data will be sent."
  The CLI now writes `spec.telemetry.healing_traces: false`.

### Fixed — Orchestration

- **`acs_delegate` re-opened the F7 hole**: the caller's `budget_s` bounded only
  the watchdog, so a chat-triggered delegation could hold an un-metered engine
  turn up to the 24 h quota-fallback ceiling. The inner turn's `max_wall_time` is
  now capped at `budget_s` (MIN wins).
- **The Settings → Profile language pin still lost on bridges.** The 74575ea fix
  updated the console but `adapter.py`'s system prompt kept an auto-detect rule
  that contradicted the appended authoritative pin; rule 1 is now pin-aware, so a
  pinned language wins for text as it already does for voice.

### Version hygiene

CHANGELOG.md is the single source of truth. Note on the immediately preceding
tags: **0.10.50 / 0.10.52 / 0.10.53 were git tags only and never published to
PyPI** (their per-file `CHANGELOG-0.10.5x.md` install instructions do not apply);
**0.10.54 and 0.10.55** shipped the production ecosystem dashboard to PyPI. This
0.10.56 supersedes all of them.

## [0.10.55] — 2026-07-20 — production ecosystem dashboard (PyPI)

Full 8-tab stats dashboard (Stats + Tier 1–3 geo + Config + Bridges + Features +
Health), real telemetry API wired to the Railway backend, deployed to
corvin-labs.com/stats. Published to PyPI.

## [0.10.54] — 2026-07-20 — first PyPI upload since 0.10.51

Republish to close the PyPI gap: 0.10.52 and 0.10.53 existed only as git commits.
Published to PyPI.

## [0.10.51] — 2026-07-20 — adversarial review sweep #2: telemetry-geo, browser-attach, delegation-routing, A2A locking

A full iterative adversarial review (six review axes, a fix round, and a
refutation round that broke and re-fixed one of its own fixes) over the last
three days' work. This build is a strict superset of 0.10.50 (the multi-tier
DSGVO geo-tracking feature, ADR-0205, shipped in git as v0.10.50 but never
published to PyPI — its code ships here).

### Fixed — Telemetry (the presence/geo channel)

- **The presence heartbeat was 100% dead.** It routed through the Cloudflare
  proxy (ADR-0204) but sent no `User-Agent`, so Cloudflare bot-protection
  answered 403 at the edge before the Pages Function ran — `online_now` and the
  online geo distribution never received a single beat. Now sends the explicit
  `CorvinOS-Heartbeat/<version>` UA (verified live: 403 → 401-through). This is
  load-bearing for 0.10.50's online-geo aggregation to receive any data.
- **A transient 401 no longer self-destructs valid tokens fleet-wide.** The
  401-recovery path deleted and re-provisioned tokens on any single 401 — a WAF
  blip or a rate-limiter answering 401 would trigger a synchronized fleet-wide
  token wipe + unthrottled hourly re-provision storm. The destructive delete is
  now gated on two consecutive 401s; a success resets the counter; the 1×/day
  cap remains.
- **Healing-trace upload moved off the message hot-path.** It ran synchronously
  in the bridge inbox/SIGTERM loop (up to ~60 s stalls on a network outage); it
  now runs in a daemon thread. Bridge-only deployments now also start the
  presence heartbeat (were undercounted in online geo).

### Fixed — Browser automation (real-Chrome attach)

- **`screenshot()` bypassed the attach consent recheck.** It was the only action
  without the guard, so a revoked/expired real-Chrome consent (or an attach
  take-over pause) still served live JPEGs of the user's real tabs via the
  REST/MCP pull path — the exact leak the screencast hardening closed on the
  push path. Now guarded, with screencast parity (a paused *managed* session
  keeps streaming; only attach mode refuses).
- **The WebSocket egress gate closed every WebSocket, including allowed ones**
  (`check_egress` hard-rejects the `ws`/`wss` scheme), silently breaking every
  WS-using page. The gate now maps `ws→http`/`wss→https` for the host check so
  allowlisted hosts connect and private/metadata/off-allowlist hosts still fail
  closed.

### Fixed — Delegation routing (ADR-0202/0203)

- **Bug-report vocabulary no longer burns the daily compute unit.** An ordinary
  monitoring verb ("überwache"/"watch"/"monitor") or an incidental "worker"
  token plus a bare "parallel"/"gleichzeitig" adverb used to hijack coding and
  scheduler tasks into the quota-metered ACS fan-out. Rule 1b now fires only on
  an *explicit* worker/fan-out demand (a count + "worker(s)", "fan-out",
  "parallele <work-noun>`"); a bare adverb defers to the LOOP/GOAL/COMPUTE
  blueprint (→ direct) and the fan-out-shape gate.
- Budget ceilings threaded through the MCP/scheduler chokepoint fallback; the
  L34/L35 residency re-gate now covers all quota-fallback branches; the daily
  fallback counter is file-locked; fallback run-ids are collision-free; the
  fallback-concurrency limiter no longer leaks a slot on an audit-write error.

### Fixed — A2A (Layer 38)

- Cross-process config writes (console PATCH, bridge reconnect, and the
  `corvin-a2a` CLI `label`/`migrate-attestation` writers) now share a
  file-lock, closing a lost-update race on the per-connection rights/name files.
- `resolve()` — an exact endpoint-id always wins over a colliding peer-supplied
  label, closing a peer-triggerable addressing DoS. Peer labels are sanitized on
  every delivery point (listing, MCP, PATCH echo) against bidi/ANSI spoofing.
- `--strict-mcp-config` on the claude_code worker spawn closes the MCP-tool
  inheritance bypass of the per-connection denylist; granting `allow_subagents`
  while bash/write/network are denied is now force-restricted (was a silent
  escalation).

### Fixed — Voice + Installer

- TTS provider chain gained a total wall-clock deadline (default 22 s, below the
  25 s console cap) so a slow provider is skipped, not SIGKILLed mid-run
  (orphan process + leftover temp file); the console now cleans up the `.wav`
  sibling. Timeout path surfaces `X-Corvin-Voice-Reason`; a dead OpenAI tier
  logs once at WARNING; unterminated code fences are stripped before TTS.
- Browser-provisioning recovery commands now point at real, PATH-valid commands
  (`corvin-install --browser`) instead of a bare `playwright`/`pip` that a
  `uv tool` install never exposes; the auto-update path re-provisions Playwright
  + Chromium so existing installs are not left without a browser.


## [0.10.48] — 2026-07-19 — adversarial review sweep: telemetry-compliance, A2A hardening, voice + installer robustness

This is a hardening release. A full iterative adversarial review of the previous
three days' work (installation, voice, A2A) found — and this release closes —
one compliance breach that never shipped, one unauthenticated endpoint that did,
and a cluster of A2A/voice correctness and security defects.

### Security & Compliance

- **Telemetry ping kept CONTENT-FREE.** A work-in-progress change had expanded the
  anonymous daily instance ping from its 4 documented fields
  (`corvin_version`, `platform`, `python_minor`, `active_engine`) to 15 — adding
  geolocation and behavioural fields (`country_code`, `voice_usage_rate` derived
  from the audit log, `install_type`, `container_runtime`, …) over a red
  compliance-guard test, with no ADR. It never reached PyPI; the ping is reverted
  to the documented enum-only baseline and the guard test is green again. The
  anonymous/CONTENT-FREE invariant (GDPR Art. 6(1)(f)) is intact.
- **Removed the unauthenticated `/api/v1/telemetry/instances/live` endpoint**
  (shipped in 0.10.47). It parsed the local `~/.corvin/audit.jsonl` inside an
  auth-free gateway route. Instance aggregation belongs on the Corvin-Logs server,
  not on every user install pointed at that user's own audit log. The dead
  `telemetry_instances_api.py` module and unwired `routes/telemetry.py` are deleted.
- **A2A error taxonomy no longer leaks secrets into the audit log (ADR-0197).**
  `error_detail` now comes from a fixed template set + an allowlist of exception
  type names — never `str(exc)`. Tokens (`sk-ant-…`), Bearer JWTs, Discord UIDs,
  hostnames and 64-hex keys can no longer reach `audit.jsonl` or `SendResult`. A
  fail-closed `_assert_audit_details_safe` backstop redacts any non-enum value
  before it is written.
- **A2A reconnect hardened against SSRF/redirect (ADR-0198).** A paired peer could
  repoint our signed task POSTs at an internal address (`127.0.0.1`, cloud
  metadata `169.254.169.254`, another internal service). Endpoint-URL changes are
  now gated by *danger category*, not global-vs-private: loopback, link-local /
  metadata, unspecified, multicast and reserved addresses are always rejected —
  including when embedded in an IPv6 form (NAT64 `64:ff9b::/96`, IPv4-mapped,
  6to4) that Python otherwise reports as global — and `https→http` downgrades are
  rejected. A change to a private/LAN address is allowed only when the previous
  endpoint was already private (LAN renumbering), so a `global→private` "pull us
  inward" move is blocked while genuine LAN/hotspot self-healing keeps working.
  Outbound A2A POSTs (reconnect *and* ping) no longer follow HTTP redirects.
  Endpoint mutation is durable-write-first, then audited (`reconnect_applied` only
  after a successful fsync+rename, else `reconnect_failed`). A residual
  DNS-rebinding window for an already-paired peer is documented and accepted.
  ADR-0198 written retroactively.
- **A2A audit backstop closed for numeric values.** `_assert_audit_details_safe`
  now magnitude-bounds numeric audit values, so a Discord-UID-shaped integer can
  no longer reach `audit.jsonl` where its string form was already redacted.

### Fixed — A2A (ADR-0199 a2a_ping, sender-side)

- Ping URL was malformed (`…/v1/a2a/receive/v1/a2a/ping` → permanent 404); base is
  now derived correctly.
- Liveness was forgeable: an unsigned response with `ok:true` marked a peer
  reachable. Reachability now requires a cryptographically signed response.
- Removed a dead in-memory heartbeat-cache fast-path that referenced a
  non-existent symbol (silently swallowed at HEAD). Each ping now emits one
  enum-only audit event.

### Fixed — Voice

- **In-process OpenAI TTS honours the pipeline again.** The console's new OpenAI-first
  TTS branch bypassed summarization, provider/voice resolution and the
  `CORVIN_TTS_LOCAL_ONLY` egress guard, and had no request timeout (SDK default
  600 s × 3). It now runs *after* summarize + provider/voice resolution, only when
  the resolved provider is OpenAI, with `timeout` pinned and `max_retries=0`, and
  skips entirely under `CORVIN_TTS_LOCAL_ONLY=1`. A pinned `piper`/`edge` provider
  never constructs the OpenAI client.
- **Operator TTS pin honoured.** `CORVIN_TTS_PROVIDER=piper` (or `edge`) is now
  resolved before the in-process OpenAI branch, so an operator pin to a local
  provider can no longer ship reply text to OpenAI's cloud.
- **Removed `voice_bootstrap.py`** — dead code (never imported; its
  `urlretrieve(..., context=)` call raised `TypeError` on every invocation and its
  model URLs 404'd). Piper models are provisioned by `installer/steps/piper.py`.
- **Custom audio player** no longer keeps playing after a session switch (unmount
  now stops + releases the element), shows an "audio unavailable" state on decode
  errors instead of a dead `0:00/0:00`, guards `duration === Infinity`
  (webm/opus), and uses the element's `muted` property. A benign `AbortError` /
  `NotAllowedError` from `play()` no longer flips the player to a permanent error
  card, and the error state resets when the source changes (chat cards are
  index-keyed, so the component instance is reused).
- Test isolation: pytest now defaults `CORVIN_TTS_LOCAL_ONLY=1` so a route-level
  test can never make a live billable OpenAI call from the host.

### Fixed — Installation

- **Browser automation survives auto-update.** `install.sh`/`install.ps1` now install
  `corvinos[browser]`, so Playwright is in the uv receipt instead of a pip-inject
  that the next `uv tool upgrade` wiped (silently killing agent browsing).
- On minimal Linux, a Chromium that is downloaded but missing system libraries now
  produces its own actionable message (`sudo playwright install-deps chromium`)
  instead of being misreported as a missing browser.

### Docs

- `docs/claude-ref/layer-38-a2a-network.md` gained the ADR-0197 error-taxonomy and
  ADR-0199 ping sections and corrected reconnect protocol/compat claims;
  `docs/claude-ref/layer-voice-ldd.md` documents the final TTS chain. ADRs 0197,
  0198, 0199 recorded in Corvin-ADR.

## [0.10.47] — 2026-07-18 — live world map telemetry API (superseded)

### Added

- Live world-map telemetry aggregation endpoint on the gateway. **Superseded by
  0.10.48**, which removes the endpoint: it was unauthenticated and read the local
  audit log; aggregation is moving server-side.

## [0.10.46] — 2026-07-18 — 20-language voice detection + ADR-0043 fast-chat routing hardening

### Added — Voice

- **20-language voice auto-detection.** `detect_lang.py` grew from a de/en
  binary detector to 20 languages (en de es fr it pt nl pl ru ja zh ko ar tr
  sv da no fi el cs) with a redesigned scorer: shared words count for every
  language that owns them (no margin from shared evidence), script bonuses
  require ≥2 distinctive word hits, and one-way script vetoes stop confident
  WRONG answers (Ukrainian is not `ru`, Persian/Urdu are not `ar`, kana is
  never `zh`) — those force-translated summaries in the first draft.
- **Smart-hybrid voice language resolution.** No pin → per-turn text
  detection → system locale; an explicit `display_language` pin stays
  authoritative. The console language dropdown now offers all 20 languages,
  stores `zh-Hans` (round-trip-safe), and the voice-test button speaks the
  actually selected language. `say.py` gained `no`→Bokmål and Greek edge
  voices so every dropdown option resolves to a real voice keylessly.

### Added — Model Routing (ADR-0043)

- **Fast-chat workload routing is now actually wired** (opt-in, default off:
  profile `fast_chat_mode` or tenant `spec.features.fast_chat_mode`). The
  classification hint travels as a function parameter into
  `_resolve_os_model` Tier 2.7 — the initial implementation was dead code
  (relative-only imports swallowed by blanket excepts; producer wrote the
  child spawn-env while the consumer read the daemon's own environ).
- **Classifier redesigned around asymmetric risk:** CHAT (→ fast tier) is
  only returned when a message carries NO code signal — syntax, error
  output, file references, or EN/DE coding intent ("Schreib mir eine
  Funktion…" was previously CHAT with confidence 1.0). Ambiguity keeps the
  user's model. Real sliding-window rate limiting; sha256 message hashes;
  every routing decision audited (`bridge.workload_model_selection`).

### Fixed

- **Voice TTS diagnostic header could 500.** `X-Corvin-Voice-Reason` carried
  raw say.py stderr; non-latin-1 characters (`…`, `→`, CJK) raised
  `UnicodeEncodeError` and turned the silent-204 degradation into a 500.
- **Workload routing fail-closed hardening:** tier map pruned to real
  registry engines (phantom `gemini`/`codex`/`ollama_local` entries with
  retired model ids removed), unknown engines/registry failures refuse tier
  models, out-of-range confidence counts as 0.0, empty input classifies
  UNCERTAIN (was CHAT 1.0 → fast tier), `code`/`uncertain` turns fall
  through to adaptive tiers instead of hard-pinning Sonnet, and the
  process-wide `CORVIN_FAST_CHAT_ENABLED` env switch (cross-tenant, never
  sanctioned by the ADR) was removed.

## [0.10.45] — 2026-07-17 — Live voice-attach (ADR-0194) + test-isolation fix

### Added — Voice

- **The archived voice player now attaches to the open chat tab live**, instead
  of only appearing on the next page reload. `/voice/tts` and `/voice/segment`
  push a `publish_voice_event(sid, path, label)` the moment the archive write
  lands; a tiny per-sid `asyncio.Queue` fanout (`subscribe_voice_live`) lets
  `chat_stream`'s WebSocket forward it to the client as a new `"voice"` stream
  event. The frontend attaches the player to the still-streaming turn, or —
  the common case, since `/voice/tts` resolves after `"done"` already fired —
  to the just-completed one via a new `lastAssistantId` tracker. Scoped to the
  single open tab (not the tenant-wide pubsub); a stalled/closed subscriber
  drops the event rather than blocking the archiving REST response.

### Fixed — Tests

- **`test_acs_quota_fallback.py` leaked a fake `acs_runtime` module into
  `sys.modules`** across the whole test process (never popped in `tearDown`,
  unlike its sibling `license.*` fake-module injections), which made any test
  file importing the real `acs_runtime` after it — e.g.
  `test_acs_local_engine_pin.py` — fail with
  `AttributeError: module 'acs_runtime' has no attribute '_resolve_worker_engine'`
  when run in the same process. Order-dependent; invisible running the file in
  isolation. `tearDown` now pops `acs_runtime` alongside the license modules.

## [0.10.43] — 2026-07-17 — Voice Mode 2.0 hardening sweep + delegation budget honesty

Three adversarial review/refutation rounds over the last five days of changes
(Voice Mode 2.0 / ADR-0194, browser tool / ADR-0193, delegation budgets),
fixing every confirmed finding. See ADR-0195 for the delegation-budget
decision record. (0.10.42, published a few minutes earlier the same day from
the then-committed tree, contains only the first batch of voice fixes below
under its own heading — everything in this section ships with 0.10.43.)

### Fixed — Voice (the silent-bug class)

- **A turn could stay unspoken forever after a WebSocket drop** in the
  annotation window (`annotation_pending` had no client-side fallback). The
  client now speaks the plain text on done-without-final-result, WS close, or
  a ~25 s timer — and the dedupe flag resets on every new turn so the
  fallback can never mute the next turn.
- **A blocked (autoplay) read-aloud lost the rest of its playlist** and left
  the chip stuck on "Speaking" forever; blocked-resume is now part of the
  playlist loop, and `playBlocked` got the same generation/abort guards as
  every other play path (a Stop in the play() window no longer shows a false
  error banner or kills the next turn's fetch).
- **The session-recap button was permanently dead for a session** whose first
  message was a huge paste (unbudgeted transcript head → `E2BIG` crash past
  the Hermes fallback), and a zero-config install's recap click did nothing,
  silently, forever. The head is budget-clamped, all CLI spawn paths catch
  `OSError` (fallback chain survives), and explicitly-clicked voice buttons
  now show a one-line hint on 204 instead of nothing.
- **Recap synthesis starved the automatic turn voice**: the ~120 s summarize
  phase held one of 4 TTS slots (and, after the first fix round, briefly ran
  unbounded in the shared threadpool). It now runs under its own 2-slot
  bound; only the say.py phase takes a TTS slot. Stop/supersede also aborts
  the in-flight fetch client-side.
- **Short German answers flipped the new text-first voice language to
  English** ("Was war in Datei A los?" → English voice + English summary,
  self-consistently wrong). `detect_confident()` now strips code fences
  before scoring (a German answer with a Python block flipped confidently to
  "en"), requires a confidence margin, and falls back to the profile pin.
- **`de-DE`/`en-US` locales lost the verbatim fast-path** (and got a spurious
  OUTPUT-LANGUAGE directive): every output-language gate now compares the
  primary subtag. Short texts with a real foreign `output_language` (fr, …)
  are no longer returned untranslated.
- **A concurrently-written read-aloud playlist could be evicted mid-write**
  by another turn's archive prune, resurrecting the renumbered-playlist
  defect; groups younger than a playback-cadence grace window (300 s) are
  never evicted.
- **`say.py <sentence>` wrote Ogg audio into a file NAMED the sentence**
  (trailing-dot filenames — illegal on Windows — had already landed in the
  repo root); swapped arguments are now rejected with a usage error.
- Hermes engine path re-spoke annotated turns twice (`annotation_pending`
  was missing on its result events) and non-de/en session recaps came back
  in German — both only fixed in-tree before, now released.

### Fixed — GDPR Art. 17 erasure (compliance)

- **Erasure left PII behind with an APPLIED receipt**: the session meta file
  (`<sid>.json`, whose title is LLM-derived from the user's first message),
  user uploads (`attachments/`) and background-task results
  (`compute_inbox/`, carrying the task text) all survive no longer.
  Engine-side transcripts remain a documented known gap (needs its own ADR).

### Fixed — Delegation budgets (ADR-0195)

- **The Settings "Worker timeout" and `max_worker_turns` knobs never reached
  a worker spawn** (read from the manager-LLM's allocation dict, which never
  carries them, then hard-clamped). They now ride the validated spec budget;
  the manager-LLM allocation can only lower them, never raise them, and
  every spawn is deadlined against remaining wall time (root-aware, so
  recursive sub-trees cannot outlive the run).
- **Reaching a budget no longer reads as a crash**: `budget_exhausted` is a
  bounded stop end-to-end — plain-language bilingual chat message naming the
  limit and where to raise it (following the user's own prompt language, so
  the voice doesn't switch language mid-session), `rc=0` in audit,
  `task.completed`, and the post-run artifact scan now runs so the promised
  partial results are actually delivered. Defaults raised on the
  time/iteration axes only; fan-out axes unchanged (worker-hours pinned by
  test).

### Fixed — Misc

- Whitespace-only `CORVIN_HOME` in `operator/bridges/shared/paths.py` (the
  third copy the 0.10.34 sweep missed) no longer resolves to a bogus path.
- `/browser` in the web console answers with a pointer to the native browser
  tool instead of `Unknown command` (ADR-0193 retired the command).
- Six standalone bridge test suites red since 2026-07-12
  (`--append-system-prompt-file` migration, opencode stdin prompt, a
  test-isolation gap in the OS-turn-model suite) are green again; the
  removed experimental OpenAI-key button (stored a key in localStorage that
  nothing consumed) never shipped.
- `dialectic.py` CLI spawns degrade on `OSError` (E2BIG) instead of
  crashing, and no failure path prints user text to stderr any more
  (summarize.py, say.py).
- The compute artifact-preview endpoint 500'd on every install ("'pandas'
  is required but it was not installed"): `routes/compute.py` uses duckdb's
  `fetchdf()`, but the `compute` extra never declared pandas. Added to the
  `compute` and `all` extras.

### Changed

- Consolidated the triplicated acquire/run/release semaphore wrapper shared
  by `voice_tts`, `voice_session_summary`, and `voice_segment` into one
  `_run_with_tts_slot()` helper.
- Consolidated the gateway's duplicated built-in-tool-seeder path-bootstrap
  (one copy per seeder) into a single bootstrap plus a loop, keeping
  per-seeder failure isolation.

## [0.10.42] — 2026-07-17 — Adversarial review of the ADR-0193/ADR-0194 diff

Iterative adversarial + dialectical code review of the native browser MCP
tool (ADR-0193) and voice session archive (ADR-0194) changes, per LDD
discipline: a 10-angle finder pass, single-vote verify, gap sweep, then a
second adversarial pass re-attacking each proposed fix before accepting it.
4 of the original 21 findings did not survive that second pass (deliberate
ADR-0193 design decisions locked in by existing tests, or CPython's own
`asyncio.Semaphore.acquire()` already closing the suspected race) and are
documented as refuted rather than silently dropped.

### Fixed

- **The Hermes/Ollama engine path spoke every annotated reply twice.**
  `_stream_hermes_turn` never set `annotation_pending` on its result
  events, unlike the `claude_code` engine path — the frontend's
  double-speak guard (added for exactly this class of bug) had nothing to
  gate on for Hermes-engine turns.
- **Stopping playback mid-read-aloud could leave the voice UI stuck showing
  "tap to hear."** `playFull`'s `catch` block set `voiceState="blocked"`
  unconditionally on any `audio.play()` rejection, including the
  `AbortError` a `pause()` call raises on a superseded request — `playTts`
  already guarded against this exact race, `playFull` did not.
- **The session-recap voice button spoke non-German/English sessions in
  German.** `voice/session-summary` collapsed any locale other than `de`/
  `en` to German before both the LLM recap-generation call and the TTS
  voice-selection call. `generate_session_recap()` now threads an
  `output_language` BCP-47 pin through to both backends (mirroring the
  pattern the regular summarizer already uses), and TTS voice selection
  now gets the caller's real requested locale instead of the de/en-only
  template selector.

### Changed

- Consolidated the triplicated acquire/run/release semaphore wrapper
  shared by `voice_tts`, `voice_session_summary`, and `voice_segment` into
  one `_run_with_tts_slot()` helper.
- Consolidated the gateway's duplicated built-in-tool-seeder path-bootstrap
  (one copy per seeder) into a single bootstrap plus a loop.

## [0.10.41] — 2026-07-15 — ACS delegation: unbounded-memory crash fix

### Fixed

- **A very large ACS (Autonomous Compute Shell) delegation workflow could
  crash with unbounded memory growth**, especially noticeable on Windows.
  Manager/worker subprocess calls buffered their ENTIRE stdout/stderr in
  memory with no size limit (the constants that looked like a cap were
  never actually wired in). Compounded by two further gaps: the total
  worker-count budget was only enforced between dispatch batches, not
  within one (a single manager decision could burst up to 100 concurrent,
  unbounded-output worker subprocesses), and the wall-clock budget had no
  upper ceiling, giving a run more time to compound both. Subprocess
  output is now drained continuously (never blocking the child on a full
  pipe) but only retained up to a size cap; a dispatch batch is now
  clamped to the actually-remaining worker budget before any subprocess is
  started; and the wall-clock budget is now bounded like every other
  budget field.

## [0.10.40] — 2026-07-14 — Console voice summary parity + imagegen budget fix

### Fixed

- **Console web chat's voice output spoke the raw, full answer verbatim**
  (truncated blindly at 4000 chars) instead of a real, condensed voice
  summary — every messenger bridge (Discord, WhatsApp, ...) already spoke a
  proper summary via `build_voice_summary()`. A fully-working, tested
  `POST /voice/summarize` endpoint existed but the frontend never called
  it. `POST /voice/tts` now summarizes server-side before speaking (same
  `summarize.py` script bridges use), with a safe fallback to the old
  raw-truncated behavior if summarization is unavailable — no frontend
  change needed.
- **Three consecutive image-generation calls each hit the generic
  "please try again" timeout** instead of a specific, actionable message.
  The L44 house-rules safety gate's real worst-case latency (cloud
  classifier retries + local Hermes fallback, up to ~93s) was not
  accounted for in the image tool's own step-budget math. The provider
  call now runs against a deadline-aware budget that correctly reserves
  time for the gate, surfacing the real per-step failure reason instead of
  a generic timeout.

## [0.10.39] — 2026-07-14 — Fresh-install image generator + Claude Code auth + ACS Windows path

### Fixed

- **Fresh install: the zero-config image generator hung ~240 s, then retried.**
  `imagegen-zero-config`'s free-tier Pollinations call could get stuck past its
  own httpx timeout (a socket-level edge); the whole-call backstop caught it but
  at a far-too-generous 240 s, so the model waited 4 minutes and blindly retried.
  The provider call now runs inside its own hard `_PROVIDER_TIMEOUT_S` (75 s)
  deadline for BOTH Pollinations and OpenAI — a stuck free-tier request degrades
  to the friendly "service unavailable — add an OpenAI key for reliable
  generation" message in ~75 s instead of the 4-minute generic timeout (the
  whole-call backstop also drops 240 s → 180 s). The OpenAI key path is unchanged:
  when a key is configured it is used first (dall-e-3), with the free tier only as
  fallback. Secondary contributor fixed too: the L44 house-rules classifier that
  gates image generation now sends `think:false` to the local qwen3 model so a
  cold fresh-install safety check stays fast (same class as the summarize fix);
  the gate is unchanged in behavior (still fail-closed / degrade-to-floor).
- **Fresh install: the voice summary just read the raw answer word-for-word.**
  Claude Code was left permanently unauthenticated because the installer ran the
  stale `claude login` (the 2.x CLI only knows `claude auth login`, and the old
  name silently became a chat prompt), and non-interactive installs skipped login
  entirely. Fixed repo-wide; non-interactive installs now drive the OAuth login in
  the background. `summarize.py`'s real Hermes/Ollama calls also gained
  `keep_alive` so the classifier/summarizer model stays warm past the installer's
  one-off prewarm window.
- **Windows 11: every ACS delegation run died with `WinError 123`.** The ACS
  run-dir builder used the raw chat_key `web:<sid>` as a path component — the `:`
  is illegal on Windows. It now routes through the same `safe_session_subdir` SSOT
  the web session workdir uses (sanitised to `web_<sid>`), and, if an ACS run
  still can't be set up, the turn falls back to the normal Claude Code delegation
  instead of surfacing the raw error.

## [0.10.38] — 2026-07-14 — Setup-wizard key drift + vault test-isolation fix

### Fixed

- **Settings/Setup-wizard engine-key display could disagree with what an
  engine actually authenticates with.** `GET /setup/engines`, `GET
  /setup/status` and `POST /setup/test-engine` had their own hand-rolled
  `service.env` reader (reversed file-before-env precedence, no
  quote/inline-comment handling) — a fourth, independent copy of logic the
  canonical `provider_keys` resolver already centralises for every other
  reader. Now delegates to `provider_keys.resolve_by_env_var`, so the
  Setup page's "configured" indicator for Anthropic/OpenAI/OpenRouter/Ollama
  keys always matches what the engine spawn path actually uses.
- **Local secrets vault could silently write into the real, live
  `~/.config/corvin-voice/vault`** instead of an isolated path during test
  runs — the vault's storage paths were computed once at import time
  instead of re-read on every access, so any code that imported the vault
  module before an isolated config directory was configured got permanently
  bound to the real path for the rest of the process's life. Paths are now
  resolved fresh on every access.
- **Image-generator MCP server** could raise an unhandled `RuntimeError`
  instead of a clean timeout error if the OS ever refused to create a new
  worker thread (the tail end of the 0.10.37 timeout-hardening fix).

### Added

- New regression test proving the Claude Code provider-redirect used by
  ACS-delegated (manager/worker) turns and the one used by the primary
  OS-turn spawn path can never independently drift apart again.

## [0.10.37] — 2026-07-14 — Image-generator hang fix + provider-routing hardening

### Fixed

- **Image generator tool could hang forever** on some systems (reported on
  Windows 11) with zero feedback to the user. Root cause: no timeout anywhere
  in the call chain from the MCP tool down to the file-save step. Both the
  file-save and the overall tool call now run on a bounded daemon thread
  (`_run_bounded()`), abandoning a stuck call after a generous timeout
  instead of blocking indefinitely; a stuck write or a stuck generation now
  surfaces a clear, catchable `ImageGenTimeout` instead of silence.
- **OpenRouter provider routing could redirect Claude Code to a guaranteed-
  broken endpoint.** When no model was configured for OpenRouter, the local
  translating proxy used to start anyway with the invalid model id `"auto"`
  — every subsequent turn then failed with an opaque upstream 400. It now
  falls through to the existing routing instead, and the same fix now lives
  in one shared place (`engine_models.resolve_claude_code_provider_env`)
  used by both the OS-turn spawn path and the ACS manager/worker spawn path,
  closing a second, independent copy of the same class of bug in the latter
  (which also never started the translating proxy for ollama/openrouter and
  read its credential via a bare `os.environ.get`, missing a key an operator
  just saved through Settings → API Keys until the daemon restarted).
- **Local Anthropic↔OpenAI translating proxy streaming gaps**: a
  `message_start` event could be missing from an otherwise-empty SSE stream
  (malformed Anthropic protocol output), a stalled upstream mid-stream was
  not caught (client would hang instead of getting a clean close), streamed
  responses never requested `stream_options.include_usage` (token-usage
  accounting silently reported 0 for every streamed turn), and the proxy's
  server-reuse cache key omitted `disable_reasoning` (could silently reuse
  the wrong cached proxy instance for a rotated flag).
- **BYOK key resolution silently failed for any provider credential name
  outside the small hardcoded canonical set** — `resolve_by_env_var()` now
  falls back to resolving the literal env-var name (process env, then
  `service.env`) instead of returning `None`.
- **codex_cli / opencode engine spawns used stale credentials** until the
  bridge daemon restarted — they had no credential-refresh of their own
  (unlike Claude Code's provider-routing path). `_build_spawn_env` now
  refreshes `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` from the live vault-aware
  resolver on every spawn.
- **`_save_image_bytes` lost its "never raises" guarantee** in an earlier
  refactor (path/env construction had moved outside the try/except) —
  restored so a malformed output-directory value degrades to "not saved"
  instead of crashing the whole tool call.

### Added

- Settings → API Keys: the "eye" button on a saved key now reveals its
  actual saved value on first click (self-hosted only) instead of only
  toggling visibility of a new, not-yet-saved value being typed. Every
  reveal is audited (`byok.secret_revealed`, key name only, never the
  value).

## [0.10.36] — 2026-07-13 — Fresh-install voice language is preset for everything

### Fixed

- **Fresh install (especially Windows): the welcome greeting and the first
  chat voice came out in the wrong / inconsistent language** — the welcome
  spoke English while the bridge TTS spoke German on the same box. The language
  SSOT `profile.display_language` (read by the console welcome, the bridge TTS,
  and the console chat) was seeded ONLY as a side effect of a *successful* Piper
  voice-model download. Every other path returned without seeding it: the user
  skipping the voice model, an unparseable menu choice, a failed/partial ONNX
  fetch (Windows CDN reset / offline), or a model *prefetched* by the installer
  (early-return on "already configured"). Unseeded, the three surfaces fell back
  to three different hardcoded defaults. Compounded on Windows by
  `_detect_language()` using the deprecated `locale.getdefaultlocale()`, which
  returns `(None, None)` on some configs → a German box detected as English.
  `_setup_model` now seeds `display_language` **unconditionally and before the
  download**, on every branch (including the prefetched-model early-return,
  seeded from `config.json`'s `lang_default`); `_detect_language()` uses the
  non-deprecated `GetUserDefaultLocaleName` on Windows; the seed is normalised
  through `i18n.normalise()`.

### Changed

- **Defence-in-depth: a consistent OS-locale fallback for the language.** When
  `display_language` is somehow still unset, all surfaces now resolve the user's
  actual OS locale via the new `i18n.system_language()` (POSIX `LC_ALL`/`LANG`,
  else Windows `GetUserDefaultLocaleName`), inserted below the explicit profile
  pin and above each surface's constant — so the console welcome and the bridge
  TTS agree instead of diverging (English vs German). The console web chat's
  first-reply race is closed too: `ttsLang` falls back to `navigator.language`
  (the browser's UI language) instead of a hard `"en"` before the profile query
  resolves.

## [0.10.35] — 2026-07-13 — Fix chat artifact display/download for nested paths (images, ACS output)

### Fixed

- **Chat artifact cards (generated images, ACS analysis output) could render
  as a broken-image icon with a non-functional download button.** Any
  artifact nested one directory level below the session workdir —
  `imagegen-zero-config`'s generated images (`outputs/corvin-image-….jpeg`)
  and ACS output files (`acs/runs/<id>/output/chart.png`) both do this
  routinely — had its relative path serialized via `str(Path)` in
  `chat_runtime.py`'s artifact-event emitters, which renders with the
  **OS-native separator**. On a Windows-hosted console this embedded a
  literal backslash into the `"path"` field sent to the browser; the
  frontend's `filePath.split("/")` and the serving route's forward-slash-only
  `_SAFE_SUBPATH` regex both then rejected it. Since the chat UI's `<img>`
  and download link share the exact same URL, both failed identically with
  no visible error beyond the broken-image placeholder. Fixed by using
  `Path.as_posix()` at every artifact-path emission site instead of
  `str(Path)`. Added the confirmed-missing regression coverage: every
  existing `test_workdir_route.py` case only ever served a file sitting flat
  at workdir root, never a nested one.

## [0.10.34] — 2026-07-13 — Adversarial security/reliability sweep: WF-A3 approver binding, packaging git-awareness, cross-daemon sticky progress

Triggered by an automated blind-spot sweep plus a 5-dimension adversarial code
review of the resulting changes (security/authz, concurrency/correctness, JS
messenger daemons, test-suite honesty, release mechanics) — every finding
below was independently verified, and every fix was itself re-reviewed before
landing.

### Security / compliance

- **Packaging-hygiene gap: both the wheel and the sdist could ship whatever
  UNTRACKED files happened to be sitting in a developer's working tree at
  build time** — an adversarial release-readiness review found that the
  already-published `corvinos-0.10.33` wheel carries a stray untracked audio
  file (`operator/voice/scripts/Testnachricht mit Nova.`, left over in
  someone's working tree at build time) inside its vendored
  `corvin_console/_vendor/operator/voice/scripts/` copy, and the sdist —
  built straight from the raw working tree with no git-awareness at all —
  picked up further untracked scratch files the same way. `hatch_build.py`'s
  vendor-copy step only ever had a DENYLIST of recognized test-file patterns
  (`test_*`, `conftest.py`, `tests/`, `__pycache__`, `.pyc`), never an
  allowlist of what git actually tracks, so anything else on disk — a stray
  secret-bearing file some day — would ship to every real `pip install`
  regardless. Fixed by making `git ls-files` the single source of truth for
  "would actually ship": `hatch_build.py` now installs a git-tracked-only
  filter for BOTH targets — a `path_is_excluded` monkeypatch for Hatchling's
  default project-file walk, used by both the wheel and the new
  `[tool.hatch.build.targets.sdist.hooks.custom]` hook in `pyproject.toml`,
  plus the same tracked-file check wired into the vendor-copy step's own
  `shutil.copytree` `ignore=` callback (a separate code path Hatchling's file
  selection never sees). The filter only ever tightens exclusion — it can
  never loosen it, and the intentional gitignored-on-purpose force-include
  (the pre-built `web-next/dist` SPA) is unaffected. Building a wheel FROM an
  already-extracted sdist tarball (no `.git` present at all) correctly skips
  the filter rather than wrongly treating "no git repo" as "nothing is
  tracked" — verified by building a wheel from an extracted sdist and
  confirming byte-identical file counts to a live-checkout build. Verified
  end-to-end against the real stray-file shapes found in this session's own
  working tree.
- **New GDPR Art. 17 erasure layer for paused Task-Engine workflow checkpoints
  (ADR-0188 M5).** A human-in-the-loop workflow run (`ask_human`/`answer`)
  checkpoints to `<tenant>/workflow_runs/<run_id>.json` with no TTL while it
  waits for a reply — the file stores the raw `chat_id`/`approver` plus the
  entire `inputs`/`state` dict verbatim. Previously the only removal path was
  the run reaching a terminal state, so an erasure request against a subject
  with a paused run in flight would silently miss it. New
  `WorkflowCheckpointHandler` matches on the same raw identifier
  `L28RecallHandler` already uses and deletes both the canonical checkpoint
  and any in-flight `.json.claimed` sidecar.
- **`workflow_resume` MCP tool bypassed the WF-A3 per-approver binding.** An
  `ask_human` checkpoint pauses a workflow for a specific chat's approval, but
  `_call_workflow_resume()` always called `resume_workflow(..., replier=None)`,
  which is the always-authorized posture reserved for genuinely privileged
  callers (the local console/CLI). Since this MCP tool is chat-reachable by
  design (ADR-0190), any chat participant with access to the persona could
  resolve an approval directed at a different chat. Fixed: the resume path now
  derives `replier` from `CORVIN_CHANNEL_ID` (set by the bridge adapter at
  process-spawn time, not spoofable via tool-call arguments) and rejects a
  mismatch against the checkpoint's recorded approver.
- **WDAT datasource-embed gate disagreed with the encryption-at-rest write
  path on key validity.** `_resolve_acs_datasources()` decided whether to embed
  a live CONFIDENTIAL/SECRET snapshot using a presence-only
  `bool(os.environ.get("CORVIN_WDAT_KEY"))` check, while the write path
  validated a real 64-hex/32-byte key. An invalid or whitespace-only key value
  passed the embed gate while the write path silently fell back to storing
  that data unencrypted. Both sides now use the same real key validator.
- **ADR-0137 (audit-chain external anchor) status corrected.** Re-audit found
  the keyed-MAC anchor (M2) already fully implemented and tested in
  `security_events.py` — the ADR's own status was stale ("Proposed"). Updated
  to Accepted; M1 (chain_dna/NBAC genesis wiring into the default verify path)
  and anchor-key rotation remain open, documented in the ADR.

### Reliability

- **AWP-DAG workflows launched via the orchestration MCP server (`workflow_run`/
  `workflow_resume`) had no recovery path once they outlived their wall-clock
  budget** — no `run_id` was returned on timeout, and nothing registered the
  run with the durable completion-notify queue that the scheduler/`/task`/ACS/
  compute-worker already use. A long-running workflow launched from a
  messenger bridge was silently unrecoverable once the originating per-turn
  process exited. Fixed: timeout responses now include `run_id`, and an
  orphaned run registers with `completion_notify` so its result still reaches
  the originating chat once it finishes. See ADR-0192.
- **Concurrency race in `profile.py::set_value()`/`save()`** — concurrent
  writers could crash (`FileNotFoundError`) racing on a shared temp path, or
  silently lose an update. Fixed with a write lock spanning the full
  load-mutate-save cycle plus a per-call-unique temp filename.
- **Cross-chat TTS skip-reason leak** — `_voice_engine_state["last_skip_reason"]`
  was pure process-wide state; one chat's TTS call could overwrite the skip
  notice a concurrent chat was about to read. Fixed with a thread-local mirror
  alongside the shared state; no public API changed.
- **Engine `cancel()`/`_cleanup_proc()` never killed the process group** in any
  of the 4 engine adapters (`claude_code`, `codex_cli`, `opencode_cli`,
  `copilot_cli`) despite `start_new_session=True` being set specifically to
  enable that — grandchild processes survived cancellation. Ported the
  existing `killpg`-based cleanup from `adapter.py::_cancel_chat` into all 4;
  `copilot_cli.py` was additionally missing `start_new_session=True` itself.
- **Messenger status heartbeats were Discord-only in UX quality.** Telegram,
  Slack, WhatsApp, Signal and Teams sent every progress/tool-status update as
  a brand-new message with no guard against a stale update landing after the
  final answer. All 5 now get the same sticky edit-in-place + finalize guard
  Discord already had, via a shared `sticky_progress.js` helper.

### Voice

- Hermes/Ollama calls now send `"think": false` — a fresh install (no Claude
  login, Hermes/qwen3 fallback path) previously blew the summary/annex
  timeouts on reasoning tokens before ever emitting the answer, silently
  degrading to verbatim text with no LERN-ZUGABE/METAPHER annex.
- A literal `<voice>` mention in a reply's visible prose could pair with a
  model's real trailing `<voice>…</voice>` block and truncate the chat text;
  the last `</voice>` now pairs with the nearest preceding `<voice>`.
- Fixed the LERN-ZUGABE/METAPHER annex being spoken twice on the `<voice>`-
  override and short-text paths (missing dedup guard) and widened the
  dedup lookback window (400→900 chars) so a metapher bridge sitting after
  the annex opener no longer pushes it out of range.

### Other

- `imagegen`'s `_save_image_bytes()` now receives an explicit
  `CORVIN_IMAGE_OUTDIR` instead of relying on cwd inheritance through the
  `claude` CLI subprocess, closing a cross-process assumption that could
  leave a generated image showing only as a download card instead of inline.
- Installer's bridge-service auto-discovery scan read a re-hardcoded local
  `~/.config/systemd/user` instead of `self.systemd_user_dir`, so tests that
  sandboxed the instance attribute were silently ignored and the scan always
  hit the real host's systemd directory. Now reads the instance attribute.
- The DSI v1 datasource-manifest validator crashed on a non-string `name`
  field instead of raising the intended `DSIv1PolicyError`; now checked
  explicitly before the regex match.
- The console's `_resolve_safe()` path helper raised an unhandled 500 instead
  of the intended 400 when `rel` contained an embedded null byte (raised by
  `Path.resolve()` itself, before the traversal check's `try` began); the
  `resolve()` call now runs inside the same `try`.
- `useVoicePlayback`'s `audio.onerror` handler now revokes the blob URL and
  clears `blobUrlRef`, mirroring `onended`'s cleanup — previously a playback
  error left a stale, unrevoked blob URL referenced until the next
  `stopVoice()`/`playTts()` call happened to clean it up incidentally.

## [0.10.33] — 2026-07-12 — Voice-fallback correctness sweep: TTS content, language selection, i18n coverage, packaging

Prompted by a live report ("the welcome voice summary came out in Chinese
instead of German") — traced through the whole voice pipeline instead of
patching the one symptom.

- **CRITICAL packaging bug, found while verifying this very release's build
  artifact: `operator/voice/i18n/` (the `/lang`, `/consent` and
  welcome-greeting translation bundles) was never vendored into the wheel.**
  `hatch_build.py`'s `_VENDOR_MAP` had no entry for it — every PyPI release
  to date shipped with zero bundle files on disk, so `i18n.t()` always fell
  through its entire fallback chain to the literal-key tier: `/lang`,
  `/consent` and the welcome greeting showed/spoke raw dotted keys (e.g.
  `welcome.intro`) verbatim, in every language, on every real pip install —
  never caught locally because dev/source-tree checkouts always find the
  file directly. Fixed by adding the missing vendor-map entry; verified by
  actually installing the built wheel into a fresh venv and confirming
  `i18n.t()` resolves real German/Chinese text from the vendored path (not
  just "file present in the zip"). New regression test locks in the vendor
  map entry itself, since that's exactly the gap that let this slip through
  unnoticed for 40+ prior releases.

Four further real, independently-verified fixes:

- **TTS no longer reads raw CLI syntax aloud.** Every engine-unreachable
  fallback string (`call_claude()`, the primary Claude streaming path, Codex,
  OpenCode, Hermes, and the `ClaudeCodeEngine`-unavailable guard — all in
  `operator/bridges/shared/adapter.py`) used to double as both the visible
  chat text and the *unmodified* spoken text, so a fresh install with no
  reachable engine yet got backticks, `--flags` and `ALL_CAPS_ENV_VARS` read
  aloud verbatim. Fixed via a new shared helper,
  `voice_tag.with_voice_override(visible, spoken)`, which also neutralizes
  any literal `<`/`>` in the visible text so untrusted subprocess stderr /
  provider error bodies can never smuggle a stray `<voice>` tag that hijacks
  the extraction. `completion_notify.mark_done()` now strips the tag at the
  one choke point every background-task producer already calls, closing a
  worse regression an adversarial review caught: a `/task` completion could
  otherwise leak the raw `<voice>...</voice>` markup straight into the
  visible chat message.
- **Voice summaries no longer force-translate an already-German/English
  reply.** `build_voice_summary`/`_resolve_audience_block` used to read
  `profile.display_language` directly and unconditionally pin
  `summarize.py`'s output to it — a persona configured with a non-de/en
  static default produced e.g. a Chinese voice-summary audio for a
  German-language reply. A confident per-turn de/en detection
  (`_detect_confident_de_en`, reusing the existing STT-locale-hint
  heuristic) now wins over the static pin when the text being spoken this
  turn is unambiguously de/en; a genuinely non-Latin-script profile default
  is untouched.
- **Root-caused why the welcome greeting itself could still be wrong even
  after the fix above**: two write paths for `profile.display_language` —
  the generic in-chat `/profile set display_language=<value>`
  (`operator/voice/scripts/profile_cli.py`) and the console's
  `PUT /v1/console/profile` — never validated what they stored, unlike the
  purpose-built `/lang set`. A bare, un-normalized `"zh"` (instead of the
  canonical `"zh-Hans"`) written through either path broke every downstream
  `i18n.t()` lookup. Both paths now normalize/reject through the same
  `i18n.normalise()` call `/lang set` already uses.
- **Added a real `zh-Hans.json` i18n bundle** (`operator/voice/i18n/`) —
  previously only `de.json`/`en.json` existed, so ANY non-de/en
  `display_language` silently fell back to an English welcome greeting,
  regardless of what the profile said. A genuinely Chinese-preferring user
  now gets a real, fully-translated greeting instead.

Also closed two previously-untested blind spots along the way: sequential
multi-task voice delivery (N concurrent background-task completions each
getting their own correctly-matched voice note, no cross-contamination) —
proven correct, was already working, just unverified; and the web console's
single-`<audio>`-element "latest request wins" playback behavior — confirmed
intentional by its own regression-guard comments, not a gap.

Verified: real invocation tests (not just source inspection) for the TTS
fallback fix, an adversarial hijack-regression test for the tag-neutralization
mechanism, and end-to-end language-resolution tests against the actual live
profile — 65+ tests green across the touched surface.

## [0.10.32] — 2026-07-12 — Uninstall clean-slate + critical voice-playback regression fix

- **`corvin-uninstall` now always resets onboarding + engine selection.** A plain
  run (every prompt declined, the `[y/N]` default) used to keep the
  onboarding-complete markers and the tenant's selected engine, so a
  subsequent `corvin-install` silently skipped onboarding and reused the old
  engine. These are UI/session state, not secrets, so they're reset
  unconditionally now (API keys and audit logs stay behind their existing
  confirmation prompts). The frontend's `SetupGate` had a related bug: it
  cached "setup complete" in `localStorage` and used that cache to disable
  the server status check entirely — so a flag left over from a previous
  install permanently skipped onboarding in that browser even after a real
  server-side wipe. The server is now the sole source of truth.
- **Critical: the gesture-unlock mechanism (2026-07-12, "every later chat
  turn goes silent") could break the spoken first-boot welcome message
  itself** — confirmed by 5 of 10 independent review angles. It reused the
  same `<audio>` element real TTS playback uses and touched it
  unconditionally on the very first click/keypress/tap anywhere on the page
  — including the click on "Tap to hear Corvin" (the autoplay-blocked
  fallback), whose own handler was about to resume playback on that exact
  element. `unlock()` now bails whenever real content is already loaded on
  the element (loaded, playing, or blocked-awaiting-a-tap), and a related
  async race (real content arriving while the priming clip's own play was
  still in flight) is closed too. Fix in
  `core/console/corvin_console/web-next/src/lib/useVoicePlayback.ts`, with
  new regression tests verified to fail without the fix.
- `corvinOS/installer/core.py`: the engine-selection reset writes
  `tenant.corvin.yaml` atomically (tmp+replace) instead of a bare write that
  could leave a truncated file behind on a crash/AV-lock mid-write.
- `chat.tsx`: the "open artifacts folder" banner no longer shows a
  nonsensical duplicated message (and copies the wrong text) when the
  request itself fails, as opposed to the server successfully answering
  "could not open".
- `routes/chat.py`: the workdir-reveal-failure log line no longer includes
  the full path, which leaked the operator's OS account name via the home
  directory prefix.
- `chat_runtime.py`: the metaphor annotation pass's subprocess timeout now
  shrinks to what's left of a single 8s window since turn start instead of
  always getting a fresh 8s, tightening the composer-freeze bound closer to
  what it's documented to be.
- `corvin-webui.service`: removed dead shell fd-cleanup code and added a
  per-attempt timeout to the port-liveness probe so a single hung connect
  can't exceed the documented 15s startup-wait bound.

Verified: 75 Python + 829 frontend tests green (12 new regression tests). The
uninstall/onboarding and voice-playback fixes were adversarially re-reviewed
in a dedicated 10-angle pass before release.

## [0.10.31] — 2026-07-12 — Adversarial review: fresh-install + voice-welcome correctness

10-angle adversarial review of the last 3 days of commits, prioritized on the
fresh-install and spoken first-boot welcome experience. 17 confirmed findings, fixed:

- **Welcome-message onboarding could silently lose the spoken greeting.** The
  first-boot self-check ran 4 independent checks (house-rules probe, Hermes
  warm-up, STT/TTS round-trip, engine test) sequentially — worst case 105s+ —
  against the frontend's 60s poll budget, so a cold/default install fell back
  to the generic non-voice greeting even though the check finished correctly
  moments later. The checks now run concurrently (bounded by the single
  slowest one), and the frontend poll budget was raised to 100s for margin.
  Also fixed in the same mechanism: the check state was a single process-wide
  dict (cross-tenant clobbering/leak), and every non-hermes engine (e.g.
  opencode) was collapsed to "claude_code", mislabeling a healthy install as
  broken. Fix in `core/console/corvin_console/routes/setup.py`.
- **STT provider-chain timeout could multiply the caller's wait.** An explicit
  timeout budget was reissued unchanged to each provider after a non-terminal
  timeout instead of being decremented — up to ~180s+ across the default
  2-provider chain instead of the intended ceiling. Fix in
  `operator/voice/scripts/stt/resolver.py`.
- **Non-speech-marker stripping matched mismatched brackets** (e.g.
  `(BLANK_AUDIO]`) and could strip legitimate dictated bracketed words; model
  self-heal now grants one bounded redownload attempt for a full-size-but-
  corrupt model file too (was a permanent, unrecoverable failure), still
  capped by the existing per-window heal budget. Fix in
  `operator/voice/scripts/stt/local_whisper.py`.
- **Installer ignored `CORVIN_STT_LOCAL_MODEL`** at install time (install-time
  prefetch and first-use load could target different model files); the
  offline RAM-detection fallback now also clamps to a cgroup memory limit.
  Fix in `corvinOS/installer/steps/stt.py`.
- **WA-22 key-resolution follow-through:** `say.py`'s quote-stripping now
  matches `provider_keys.py` byte-for-byte (a stray trailing quote character
  was cleaned differently by the two "must stay identical" implementations);
  `adapter.py` and `voice_doctor.py` now delegate to the canonical
  `provider_keys.resolve_key()` instead of two more hand-rolled, untested
  copies of the same candidate/precedence logic.
- **Voice summarizer's verbatim short-circuit** (added 2026-07-12) now only
  fires when no persona/audience is configured — it was silently skipping the
  LLM styling pass for users who explicitly opted into one on every short
  reply. It also now re-checks length after prepending the task-prefix
  (could exceed the caller's spoken-length budget with no truncation).
  Fix in `operator/voice/scripts/summarize.py`.
- **Console TTS playback race:** overlapping `playTts()` calls could let an
  older, slower request's response resolve after a newer one already started
  playing, clobbering it and leaking the newer blob's object URL. Fix in
  `useVoicePlayback.ts`.
- Windows Stufe-2 (always-on) service install now prints an operator-visible
  warning that the boot-time PyPI auto-update check is not yet wired for that
  platform (was silently accepted-and-discarded).
- `INSTALLATION.md`'s RAM/model table updated for the 3-tier ladder shipped
  in 0.10.25 (was still describing the old 2-tier split).

Verified on a genuinely fresh, isolated install (`install.sh --editable . --no-hermes`
in a clean `$HOME`, zero ambient env/API keys): zero errors end to end, and the
fixed welcome-check produces a real, valid Opus/OGG greeting audio file within
17s — comfortably inside the new poll budget. 244 tests green (14 new regression
tests added); one pre-existing, unrelated failure in `test_corvin_erasure.py`
reproduces identically on a clean checkout.

## [0.10.30] — 2026-07-12 — Forge sandbox on uv installs + engine-free workflows (fresh-install robustness)

Two fresh-install robustness fixes surfaced by running the CI suite the way a
genuinely fresh box runs it (no `claude` CLI on PATH, a `uv`-managed venv), plus
the test-harness fixes that let CI exercise those paths on a credential-less box.

- **Forge sandbox was broken on every `uv` install (the default installer path).**
  The bwrap jail runs the tool with `sys.executable`, but a `uv`-managed venv
  symlinks `bin/python3` through several hops to an interpreter OUTSIDE the venv
  (`~/.local/share/uv/python/cpython-3.11-…` → `cpython-3.11.15-…`). The runner
  bound only the venv root, so an intermediate hop dangled inside the jail —
  `bwrap: execvp …/python3: No such file` — and **every** forged tool failed to
  execute. The runner now also binds the interpreter version store read-only
  (guarded against system-wide / home-root dirs). A classic `python -m venv`
  (interpreter under `/usr`) is unaffected. Fix in `operator/forge/forge/runner.py`;
  proven by the full forge sandbox suite now executing real tools end-to-end.

- **`workflow_run` required the `claude` CLI even for code-only workflows.** The
  orchestration MCP server eagerly constructed the LLM engine before running any
  node, so a Hermes-only / no-Claude install (and CI) could not run a pure
  `code`/`compute`/`merge` pipeline at all. The engine is now built only when the
  graph contains an engine-requiring node; engine-free workflows run with no CLI,
  and an engine-requiring workflow with no CLI still fails fast with a clean
  `engine_unavailable` result. `workflow_resume` likewise defers engine
  construction so an unknown run-id returns its real error instead of masking it.
  Fix in `core/orchestration/corvin_orchestration/mcp_server.py`.

- **Test-harness (CI, no credentials): four suites now establish their own
  preconditions.** The helper-model and voice-summary-judge sites gate on
  `_claude_authenticated()` and short-circuit before spawning `claude` when no
  OAuth session / `ANTHROPIC_API_KEY` exists — so on a credential-less CI box the
  mocked-subprocess assertions never fired. Those suites now set the
  authenticated precondition they assume. `say.py`'s key-precedence probe was
  updated to write `service.env` (the single provider-key file since WA-22), not
  the retired `.env`. `test_voice_persona` now asserts the forge MCP `command` is
  an absolute interpreter path (the `{{PYTHON}}` template), not the bare
  `python3` that broke under the adapter's stripped-PATH spawn.

## [0.10.29] — 2026-07-12 — Auto-updater downgrade fix (critical)

- **The auto-updater could DOWNGRADE the install.** It compared `latest != current`
  and upgraded to whatever version PyPI's JSON index reported — so a transient
  PyPI CDN lag right after an upload (index still reporting the previous release
  for a few minutes), or a yanked newer release, caused it to install an OLDER
  version. Caught live: a fresh `pip install corvinos==0.10.28` auto-"upgraded"
  to 0.10.27 on its first `corvinos-serve` boot, un-doing the release it had just
  installed and corrupting its vendored tree (the older PyPI build lacks the
  imagegen seeding module). Now upgrades only when PyPI's version is strictly
  newer (PEP 440 version compare via `packaging.version`, tuple-compare fallback,
  fail-safe to no-op on any parse ambiguity). Regression tests in
  `ops/launcher/tests/test_autoupdate_uv.py`.

## [0.10.28] — 2026-07-12 — Fresh-install command-center fixes (5-axis adversarial review, real E2E)

A 5-axis adversarial review (ADR-0190 chat command center, ADR-0191 image
generation, Windows/macOS install, voice zero-config, fresh-install Linux)
driven by REAL end-to-end runs: fresh venv + fresh HOME installs of the built
wheel, real MCP stdio handshakes, real images generated. Focus: what is
different on a genuinely fresh installation vs. the maintainer's checkout.

### Fixed — critical (fresh installs / command center)
- **Every ADR-0190 orchestration/delegate MCP server was unstartable on every
  pip/uv installation:** resolver PYTHONPATH templates pointed at
  `_vendor/core/*`, which does not exist in a wheel (core/ ships top-level in
  site-packages). New wheel-aware `{{CORE_ROOT}}` template; verified by real
  spawns (cwd=/) on a fresh venv: corvin_orchestration (6 tools), forge (14),
  skill_forge (7), corvin_delegate (5) all handshake.
- **Forge MCP was dead on every wheel install (pre-existing):** the spawn
  script `operator/forge/forge.py` was never vendored. Now shipped; this also
  un-kills `compute_submit`/`compute_gate`/`datasource_connect` (ADR-0190
  M2/M3) on fresh installs.
- **The console web-chat — the designated command center — attached NO MCP
  servers** while advertising the full capability map in its system prompt.
  `chat_runtime` now materializes the persona's servers + mcp_manager catalog
  into `--mcp-config` (bridge parity); `--allowedTools` under strict
  permission modes.
- **Windows: PYTHONPATH was ':'-joined in three resolver injectors** — one
  unusable path on Windows for ALL default personas (third incarnation of
  this class). Now `os.pathsep` everywhere; new `{{PYTHON}}` template replaces
  the last bare `python3` persona command.
- **Image generation (ADR-0191) was never seeded on the primary pip/uv start
  path:** the catalog seeding lived only in the gateway lifespan;
  `corvinos-serve` has none. Now seeded on both paths — a plain
  `pip install corvinos && corvinos-serve` has working image generation
  (verified with a real generated image on a fresh venv).

### Fixed — image generation hardening (ADR-0191)
- Seeding is marker-based and respects operator intent: user deactivation,
  uninstall, and catalog edits survive reboots (previously silently
  overridden every boot); stale venv paths are refreshed on upgrade.
- Old persona-hardcoded `imagegen` npx server removed from
  assistant/forge/research (BYOK-only, always-401 via unresolved `${VAR}`
  template, invisible to L34/L35).
- MIME type sniffed from magic bytes (Pollinations serves JPEG; blocks were
  mislabeled `image/png`); non-image 200s rejected; full
  429/5xx/timeout/connect taxonomy → friendly no-SLA message; redirects
  refused (prompt-in-URL leak class); `quote(safe="")` + prompt/response
  caps; broken Tier-1 OpenAI key degrades to Tier 0 with a note; disclosure
  marked before first egress, English text, never-raises storage, tenant-id
  shape validation; `CORVIN_HOME`/`CORVIN_TENANT_ID` threaded via new
  plaintext `runtime.env` catalog passthrough.

### Fixed — capability honesty & gates (ADR-0190)
- Capability map no longer claims persona-hardcoded capabilities (Playwright)
  for personas that never attach them; catalog-attached tools counted via the
  tenant's active set.
- `workflow_run` now enforces the console's `workflows_concurrent` license
  limit (fail-closed), closing the ADR's own gate-reuse rule violation.
- The ADR-promised CI reverse-check now exists: every tool advertised by any
  `mcp_server.py` (AST-scanned) must be registry-tracked; unmapped server
  files fail CI. `core/pipe` is now honestly tracked as `planned`
  (`pipes.sessions`) instead of invisible; `forge_exec` registered.
- Dead `acs_runtime._worker_mcp_config` (wrong path, zero callers) removed.

### Fixed — install / platform
- Windows: installer-wizard vs. autostart-supervisor port-8765 collision no
  longer burns the crash-loop budget and kills supervision — the supervisor
  (install.ps1-generated + dev `corvin-supervisor.ps1`, parity-tested) stands
  by while another healthy instance serves.
- Windows PS 5.1: unguarded `2>$null` under `$ErrorActionPreference=Stop`
  could abort the install at the uv version probe — wrapped.
- Gateway lifespan L44 boot-health-check used a wrong repo-relative path
  (4× `..`) and only worked by sys.path luck; both lifespan hooks now
  bootstrap via `corvin_console` (works in checkout AND wheel).

### Fixed — voice
- The three TTS synth writers honored `ADAPTER_OUTBOX` again (were hardcoded
  to the package dir — broken on read-only site-packages installs).
- `profile.json`/`memory/`/BYOK-vault resolvers now honor the
  `VOICE_CONFIG_DIR` pin like every other voice-config consumer (SSOT test
  extended to 9 resolvers).

### Fixed — tests / docs
- 5 stale L34 tests updated to ADR-0173 opt-in-residency semantics (blocking
  now asserted via the strict-matrix opt-in; live-`~/.corvin` reads isolated).
- Stale L44 spawn-gates test updated to the 254a5a6 degrade-to-Tier-0-floor
  semantics (benign allowed + prohibited still blocked with classifier down);
  `check_l44` docstring corrected accordingly.
- `mcp` dependency floor raised to `>= 1.2` (`mcp.server.fastmcp` first
  shipped there).
- New regression suites: wheel-layout (`test_resolver_wheel_layout.py`),
  console MCP wiring (`test_chat_mcp_wiring.py`), imagegen (22 tests),
  workflow concurrency gate (3 tests).

## [0.10.27] — 2026-07-11 — Big-release security & correctness hardening (8-agent adversarial review)

A full 8-agent adversarial review of CorvinOS + Corvin-Features (install ×2, voice,
SSOT, browser, identity/IBC, console, workflows + licensing server). Every fix below is
unit-tested; the deferred items are documented, not silently dropped.

### Security — critical
- **Windows web-chat RCE closed:** the console chat spawn built `cmd /c … --append-system-prompt <user profile/memory>` and let `create_subprocess_exec`'s `list2cmdline` hand it to cmd.exe, which re-parses `\"` as a quote toggle — so `" & powershell … & "` in a profile/memory broke out and executed on the host (BatBadBut), outside L10/L44/audit. The argv no longer carries a `cmd /c` wrapper; a Windows `.cmd` shim is spawned via `create_subprocess_shell` with `_win_shim`'s cmd.exe-safe quoting. POSIX / `.exe` launches are byte-for-byte unchanged.
- **IBC revocation is now enforced on the A2A receive path (ADR-0145 M3):** a revoked instance (lost/stolen device, GDPR erasure) previously kept authenticating until its 1-year cert expired because the CRL was never consulted for a peer. A confirmed-revoked `jti` is now rejected unconditionally (cached CRL, never blocks the receive path).
- **IBC verifier pins token class + issuer + kid:** `type=instance_binding`, `iss=corvinlabs.io`, and only an `ibc-` kid resolves from the shared Ed25519 trust ring — a `sess-`/`lic-` kid signed by the same key can no longer be replayed as an IBC.
- **Instance-identity fail-closed no longer self-heals:** deleting `instance_id.json` and calling `create_if_missing=False` previously fabricated + persisted a NEW identity (via the missing-identity audit's re-entrant attestation) before raising — silently replacing the deleted file. A thread-local guard now returns a transient, non-persisted placeholder; a deleted file is detected, never replaced (ADR-0052 F10).
- **Workflow `code` node fails closed off bwrap:** on macOS/Windows/no-bwrap Linux a `code` node now raises rather than running unsandboxed with full user privileges — opt in per host with `CORVIN_ALLOW_UNSANDBOXED_CODE=1`. `awpkg install` refuses a package containing a `code` node unless its manifest signature verifies (or `allow_unsigned_code=True`).
- **Browser cross-host injection defense covers all navigating actions:** the human-confirm that guards indirect prompt injection now fires on `click`/`key`/`select`/`drag`/redirect, not only `navigate()`. Egress blocks private/link-local/cloud-metadata targets by default, including DNS-rebind-to-IMDS.

### Fixed — workflows / resume
- Checkpoints are tenant-scoped (non-default tenants can resume); `ask_human`/approval binds the intended approver; retry / fan-out / delegation get hard caps; `deliver`/`answer` are non-retryable. `resume_run` is license-gated (`workflows_concurrent`), takes an atomic checkpoint claim (409 on double-resume), hash-chains resumed node events into the audit log, and no longer leaks raw exception detail.

### Fixed — voice
- **STT RAM/CPU gate is cgroup-hierarchy aware:** it walks the process's own cgroup and honors `cpu.max` quota, so a systemd `MemoryMax=` / Docker / K8s parent-cgroup limit no longer reads as "unlimited" → no OOM on the `medium` tier.
- Corrupt-model self-heal only re-downloads a *truncated* file, at most once per hour (was an unbounded full re-download on any load failure).
- Voice-summary child timeouts (CLI + Hermes) now fit inside the parent cap, so the local Hermes fallback is actually reachable when the CLI hangs.
- `corvin_a2a` session-key lookup honors `XDG_CONFIG_HOME` (reader = writer).

### Fixed — install (all platforms)
- Stufe-2 always-on now runs the WA-19 auto-update check; systemd units set `TimeoutStartSec=300` so a slow boot-time upgrade can't trip the crash-loop lockout; POSIX uninstall detects + reports a root-owned Stufe-2 service.
- macOS: Stufe-1 quiesce persistently `launchctl disable`s (no more next-login port fight); re-install unloads before load; fresh install waits for the launchd WebUI instead of spawning a second competing one; the launchd log dir is created and honors `CORVIN_HOME`; plists XML-escape every value; the Linux sudo quiesce uses `env` (stock-sudoers safe).

### Added — voice (local STT accuracy)
- **Local STT model ladder gained a high tier:** on a capable box (≥ 16 GB usable RAM **and** ≥ 8 CPUs) the keyless local default is now `medium-q5_0` — an automatic accuracy upgrade over `small-q5_1`, especially for German/accented audio. Below that it stays `small-q5_1` (3–16 GB) and `base-q5_1` (< 3 GB). Runtime provider and installer prefetch pick the SAME file (Single Source of Truth: `operator/voice/scripts/stt/local_whisper.py::_default_local_model`). Explicit `CORVIN_STT_LOCAL_MODEL` still overrides every tier. The offline fallback now picks the best present model family instead of the alphabetically-first.

### Corvin-Features (licensing/stats server — requires Railway redeploy)
- Paddle webhook replay/ordering guard is atomic (a stale event can no longer leave a cancelled customer active or regress the ordering cursor); `/v1/licenses/revoked` keyset pagination gained a stable `(revoked_at, id)` tiebreaker so bulk same-second revocations no longer stall the cursor; retired signing keys get a staleness warning; `online_now` window unified to 15 min.

### Deferred (documented, ADR fast-follow — not blind-fixed)
- IBC email→peer minimization (needs a dual-token / bind-response protocol change so the holder still sees their binding without disclosing the email to paired peers).
- awpkg content-hash signing (the signature binds the manifest digest, not component file bytes — a wire-format change).
- SSOT forge-audit "split" — confirmed **by design** (the dual-track audit architecture: bridge-events vs tenant-events, both read by `audit_tail`).

## [0.10.26] — 2026-07-11 — Adversarial-review hardening (workflows/awpkg, voice model ladder, chat command-center, fresh-install UX)

Overnight full-repo adversarial review (workflows/awpkg, voice, console/chat, install/update) plus fresh-install E2E on Linux + a Windows VM. The install completed end-to-end on both platforms (console up, healthz green, voice provisioned, autostart via fallback); the fixes below close the real defects the review and the E2E runs surfaced.

### Fixed — workflows / awpkg (ADR-0188)
- **`code` nodes were broken on every venv/uv install** (13 workflow tests red on `main`): the bwrap sandbox never bound the interpreter when it lived outside `/usr`, so `code` nodes failed `bwrap: execvp .../python: No such file or directory`. Now resolves the real interpreter and binds its install prefix (handles uv-managed Python's multi-level symlink chain). All 67 workflow tests green.
- **HITL consent could be granted on a refusal:** `_coerce_reply("not ok")` returned `True` because the negation wasn't recognized and the affirmative "ok" won. Added negation words (checked first, they win over any affirmative token) and digit tokenization; a negated/mixed reply is now fail-closed. Regression tests added.
- **Console `start_run` silently mis-ran DAG-only node types:** `code`/`merge`/`route`/`answer`/`ask_human` fell through to a generic `claude -p` step — executing a deterministic `code` node via an LLM and letting an LLM "answer" an `ask_human` consent gate (a human-in-the-loop bypass). `start_run` now fail-closes on those types and points the user at the `corvin-flow` CLI.
- **awpkg install-time workflow validation was a silent no-op** (it called a non-existent `WorkflowDoc.from_dict` and swallowed every exception), so any malformed/invalid workflow installed clean. Added `WorkflowDoc.from_dict`, made the validator actually run, and made a `WorkflowInvalid` abort the install with `InstallError`. Regression tests added.
- **A failed resume destroyed a paused run:** the checkpoint was deleted on `failed` as well as `complete`, so a transient error after the human replied made the run unrecoverable. Now deleted only on clean completion.
- Documented awpkg workflow coverage honestly (`docs/awpkg.md`): the definition round-trips losslessly; checkpoints and cron schedules are deliberately out of scope; three standard extensions (embedded-code permission axis, declarable channel requirements, awp-version pin) named as follow-ups.

### Fixed — voice (fresh-install, all platforms)
- **Local STT default raised to a quality model:** `base-q5_1` mis-transcribes German/accented audio; the default is now RAM-aware — `small-q5_1` on a normal machine (~190 MB, fits 4 GB), auto-downshifting to `base-q5_1` on a low-RAM box. Installer and provider pick the same file.
- **Voice summarizer/annex hardened against stripped-PATH / Windows:** replaced hardcoded `python3` spawns with `sys.executable` and widened the fallbacks to catch `OSError`/`FileNotFoundError`, so a spawn failure degrades to the answer head instead of dropping the turn.
- **Dialectic CLI judge auth-gated:** the research/forge summary judge spawned `claude -p` even when Claude wasn't logged in, burning the full judge timeout on every summary. Now short-circuits to the fallback via the same `_claude_authenticated()` probe the summarizer uses (completes the 41c174e fix).

### Fixed — console (chat as command center)
- **`/delegate` was dead:** the slash dispatcher rejected the flagship force-delegation verb as "Unknown command" before it could reach the delegation path. It now passes through to `stream_turn`'s delegation branch (verified live).
- **Delegation was blocked on non-claude engines:** the engine-unavailable guard refused the whole turn (with text that itself pointed at delegation) even though ACS delegation is engine-independent. The guard now skips turns that will delegate.
- **Task-Engine router was mounted nowhere** → the chat live-update polling fallback and task-recovery hit a permanent 404. Router mounted; added the batch `GET /tasks?ids=` route the frontend expects.
- **Workflow runs stranded as "running" on client disconnect** → because the terminal-status write sat outside the abort path, a closed tab left the run counting toward the `workflows_concurrent` license cap forever (a few closed tabs locked a free-tier tenant out of starting any run). The run now finalizes as `aborted` on `GeneratorExit`/cancellation.
- **BYOK secret *writes* 503'd on self-hosted installs** (no Instance Agent): added the self-hosted write branch that decrypts and stores via the same vault + `service.env` pipeline the agent uses, so adding a key actually takes effect (and voice auto-upgrades to the better provider). Also fixed the always-failing `operator.agent` import (stdlib `operator` shadowing) → `agent.byok`.
- **Mermaid diagrams rendered with `securityLevel: "loose"`** on LLM/workflow-derived output (a stored-XSS vector) → set to `"strict"`.
- Raw session-token fragment no longer written to the audit log (`rec.sid[:12]` → `rec.sid_fingerprint`).

### Fixed — install / fresh-install UX
- **Hermes model ladder made RAM-safe:** `qwen3:8b` (~5.2 GB) was pulled on machines with as little as 6 GB, where it can't run alongside the OS + console. Three-tier ladder now: `<6 GB → qwen3:1.7b`, `6–12 GB → qwen3:4b`, `≥12 GB → qwen3:8b` (the running engine auto-selects whatever tag is present). Both `install.sh` and `install.ps1`.
- Whisper model download now prints a "this can take a minute on a slow connection" line so the connection-latency wait doesn't read as a hang.
- `corvin-uninstall` no longer suggests `rm -rf <site-packages>` on wheel installs (it would delete the whole Python environment).

### Changed — provider-key resolution consolidated into a single source of truth (WA-22)
- Audited every place in the repo that resolves an API key/provider secret
  and found real, user-visible drift: say.py (TTS), stt/openai_whisper.py
  (STT), the console's byok.py, and the console's setup.py each carried an
  independently-maintained copy of "where is the OpenAI key" — different
  precedence orders, and two of them scanned a second,
  independently-drifting `~/.config/corvin-voice/.env` file that nothing
  wrote to programmatically. Added `operator/bridges/shared/
  provider_keys.py` as the one canonical resolver (env var → service.env →
  legacy alias, `service.env` the ONE file consulted) and aligned every
  consumer to it — say.py/stt/openai_whisper.py keep private,
  import-independent copies for portability, but a new parity guard
  (`tests/test_secrets_ssot.py`, same pattern as the existing
  `test_voice_config_ssot.py`) asserts all implementations agree, byte for
  byte, under every fixture.
- Closed the single biggest structural gap found: BYOK's write path
  (`operator/agent/byok.py::apply_byok_secret`) only ever wrote into the
  BYOK vault (`operator/bridges/shared/vault.py`) — a store nothing reads
  back for provider keys (confirmed: the vault's only reader is the
  Tier-3 "ask before reveal" LLM tool, an unrelated use case). A key saved
  through the BYOK UI silently never took effect anywhere. Now also writes
  into `service.env`, the file every real consumer resolves through.
- Fixed a genuine cross-contamination bug along the way: the console's
  generic "OpenAI" BYOK presence check treated a TTS-only
  `CORVIN_TTS_OPENAI_KEY` as satisfying the *general* key slot — backwards,
  since TTS is a fallback *consumer* of the general key, not the other way
  around. The general slot now only reflects a real `OPENAI_API_KEY` (or
  its legacy `OPENAI_APIKEY` alias).
- Named the new module `provider_keys.py`, not `secrets.py`: a module by
  that name on `operator/bridges/shared`'s sys.path would shadow the Python
  stdlib `secrets` module for every other file in that tree — caught during
  testing when `adapter.py`'s own `secrets.token_hex(8)` broke under
  exactly this collision.
- Incident caught during this work: the first test run of the new BYOK
  write-path silently overwrote the real
  `~/.config/corvin-voice/service.env` with test fixture values, clobbering
  a working `CORVIN_STT_OPENAI_KEY`. Fixed immediately (real file restored)
  and closed structurally with an autouse pytest fixture isolating
  `VOICE_CONFIG_DIR` for every test in `operator/agent/tests/
  test_agent_e2e.py`, plus an explicit `path_override`/`service_env_path`
  parameter threaded through `provider_keys.write_key()` and
  `apply_byok_secret()` for any future direct callers.
- Deliberately out of scope for this pass (flagged, not touched): the two
  independent license-verification systems (Ed25519 operator token vs
  RS256 JWT — both live, one backing paid Member-tier licensing), the
  Anthropic/Claude-Code provider-redirect subprocess-spawn logic in
  adapter.py/acs_runtime.py (drives the engine running this very
  conversation), the bash-side voice_lib.sh/bridge.sh and systemd-unit
  path resolvers (hardcode `$HOME`, bypass `VOICE_CONFIG_DIR`/
  `XDG_CONFIG_HOME`), and the broken BYOK vault symlink on this specific
  install. Each carries materially higher blast radius than the provider-key
  read/write paths closed here.

### Added — autostart now runs the PyPI auto-update check (WA-19)
- The auto-update logic (`ops/launcher/corvin/serve_backend.py::
  maybe_pypi_autoupdate`) only ever ran when a user manually invoked
  `corvin`/`corvin serve` — the actual autostart entrypoint registered by
  the installer (`corvinOS/installer/core.py::step_14_register_services`,
  Linux systemd user unit / macOS LaunchAgent) execs `uvicorn` directly,
  bypassing it entirely. Any user who relies on autostart and never
  manually runs the CLI never received an update. Added `pre_exec` support
  to `ServiceManager.install_service` (Linux: `ExecStartPre=-...`; macOS:
  wraps the launched program in a `bash -c '<check>; exec <program>'` so
  `KeepAlive` still tracks one process) and wired the WebUI service
  registration to it via a new standalone entrypoint,
  `ops/launcher/corvin/_autoupdate_entrypoint.py` (a plain two-token
  `<python> <script>` command needs no shell quoting, unlike inlining
  `python -c "..."` into a unit file / plist). The equivalent "Stufe 2"
  always-on `SystemServiceManager` got the same Linux/macOS wiring; Windows
  is unaffected either way — `install.ps1`'s supervisor already runs its
  own one-shot `uv tool upgrade corvinos` per logon/boot.

### Fixed — Settings → API Keys showed "not set" for keys that were actually configured and in use
- `GET /byok/secrets` only ever checked the opt-in BYOK vault
  (`operator/bridges/shared/vault.py`) — which nothing writes to unless a
  key is entered through that specific UI form. A key configured the way
  this codebase's own docs and comments tell users to configure it (an env
  var, or a line in `~/.config/corvin-voice/{.env,service.env}`) always
  showed "not set" even while actively in use, because the code that
  actually resolves these keys at call time (`say.py`, `stt/
  openai_whisper.py`) reads those variables directly and never touches the
  vault. `list_secrets` now also checks the same env vars / config files
  each key's real resolver falls back to (self-hosted mode only — hosted
  mode's "present" already reflects a remote agent, where local files are
  meaningless).
- Along the way: found the OpenAI key added for Whisper transcription
  earlier was placed under `CORVIN_TTS_OPENAI_KEY` (say.py's TTS-output
  variable) instead of `CORVIN_STT_OPENAI_KEY` (the STT provider chain's
  own variable) — a stale, mislabeled comment ("OpenAI key for say.py
  (STT) only") likely caused the mix-up. STT was silently falling back to
  the local model instead of the intended paid OpenAI Whisper API this
  whole time. Added `CORVIN_STT_OPENAI_KEY` alongside it and corrected the
  comment.

### Fixed — adversarial review: BYOK "paste a new key" fields could be silently contaminated by browser/password-manager autofill (WA-21)
- The API Keys settings page showed a plaintext value in the "OpenAI STT
  Key" field that was clearly not a real key — root cause: the input's
  `autoComplete="off"` is well documented as ignored by Chromium and most
  password managers specifically for `type="password"` fields, so a saved,
  unrelated credential got silently offered and accepted into what is meant
  to always start blank. Nothing downstream ever checked whether the
  decrypted value even looked like the provider's key format, so it would
  have been stored as though it were a working credential with zero
  feedback. Fixed on both ends:
  - Frontend (`api-keys.tsx`): `KeyCard` and `AddCustomKeyForm`'s value
    inputs now use `autoComplete="new-password"` plus `data-lpignore`,
    `data-1p-ignore`, `data-bwignore` hints (LastPass/1Password/Bitwarden)
    to stop autofill from targeting these fields at all.
  - Backend (`operator/agent/byok.py`): added `_check_key_shape()`, checked
    right after decryption in `apply_byok_secret()` — rejects
    `anthropic_api_key` / `openai_api_key` / `stt_openai_api_key` values that
    don't start with their provider's documented prefix (`sk-ant-` / `sk-`).
    `custom_<slug>` and `stt_local_whisper_api_key` are intentionally exempt
    (no fixed format).
  - Along the way, found `core/console/corvin_console/routes/byok.py::
    _agent_post` caught `HTTPError` with the generic `except URLError`
    (`HTTPError` is a `URLError` subclass) — so the new 400 from a rejected
    key shape (or any other legitimate 4xx from the agent) was reported to
    the user as "503 Instance Agent unreachable" instead of the real reason.
    Added an `except HTTPError` branch first that forwards the agent's
    actual status code and `detail` message.

## [0.10.25] — Adversarial-review hardening sweep (fresh-install voice, agentic compute, telemetry, licensing)

Full adversarial review across four surfaces (install/voice on a fresh unknown
system, the agentic-compute engine + console UI, the anonymous instance ping +
telemetry channels, and the licensing bypass surface). Fixes below; the two
highest-value install blockers are the Hermes model mismatch (B1) and the piped
install skipping voice provisioning (B2).

### Fixed — fresh install / voice-first first run
- **Hermes default model mismatch broke chat on low-RAM machines (BLOCKER).**
  The installer pulls `qwen3:1.7b` on <6 GB boxes, but the engine (and the L44
  house-rules classifier) resolved the default to a hardcoded `qwen3:8b` that was
  never pulled → every Hermes turn errored "model not available" with no recovery.
  `hermes_engine._resolve_default_model()` now discovers the qwen3 tag actually
  installed in Ollama and uses it (env override still wins; unreachable Ollama
  still falls back to the built-in default). This also fixes the L44 classifier
  false-blocking every chat/voice turn on those installs.
- **Piped `curl … | sh` install skipped ALL voice provisioning + services
  (BLOCKER).** The setup wizard only ran on a TTY, so the documented one-liner
  left Whisper STT + Piper TTS unprovisioned while printing "CorvinOS is ready!".
  The piped path now runs `corvin-install --yes` (voice models + Stufe-1 login
  services), matching the interactive install; Stufe-2 always-on stays opt-in.
- **Console voice transcription froze the whole console on first use (HIGH).**
  `voice_transcribe` ran the blocking STT (up to 120 s, plus a first-run model
  download) directly on the asyncio loop, freezing every SSE stream/tab. It now
  offloads to the threadpool.
- **Claude installed but not logged in → raw CLI auth error instead of Hermes
  fallback (HIGH).** `_effective_os_engine` now falls back to Hermes when the
  claude binary is present but unauthenticated (same credential signal the rest of
  the product uses), not only when the binary is missing.
- **Voice replies were cut mid-sentence on the default Hermes-only install.**
  Added a Hermes/Ollama backend to the voice summarizer between the Claude-CLI and
  the structural-truncation fallback, so the shipped default gets a real summary.
- Reconciled the drifted edge-tts voice table on the bridge path with the
  canonical 29-language table (uk/cs/ro/he/hi/… no longer fall back to English;
  Arabic voice matched); refreshed stale `qwen2.5:7b` model guidance to `qwen3`;
  guarded the macOS RAM probe against an empty result; corrected `INSTALLATION.md`
  / `INSTALL-UNIVERSAL.md` drift (uv-not-pip, realistic disk/RAM, removed the
  bogus `ollama install corvinOS`, fixed the Windows voice-config path).

### Fixed — Agentic Compute
- **HAC mode was 100 % broken on any install without scikit-learn.** Sub-managers
  defaulted to the `bayesian` strategy, which only registers when sklearn is
  present, so every sub-manager raised `UnknownStrategy` and every HAC run failed.
  Sub-managers now fall back to the always-available `grid` walker when the
  requested strategy is not installed. The swallowed failure log (`log.exception`
  with no active exception → "NoneType: None") now records the real cause.
- **Pipeline/HAC jobs permanently leaked their concurrency slot (HIGH).** Non-flat
  engine jobs held a `RUNNING` placeholder that never went terminal; because it
  counted against the flat `max_concurrent_runs` budget, two of them starved every
  subsequent flat run. Flat-slot accounting now counts only flat runs.
- **HAC detail view showed fabricated "complete" sub-managers with no loss data.**
  The route derived per-manager state from an always-empty stage scan
  (`all([]) == True`); the coordinator now persists real `manager_states`,
  `sub_manager_losses`, and `root_loss_history`, and the route uses them.
- **Pipeline `$ref.best_params` resolved to a fingerprint dict, not real
  parameters** (the a64a50d HAC fix was never applied to the pipeline coordinator)
  — downstream stage steering now reads the winning iteration's actual params.
- **L25 run-graph rendered every successful run's Result node red/"failed"**
  (`state == "complete"` is never true) — now green for `converged`/`stalled`/
  `budget_exhausted`.
- **A transient compute-worker error burned a free-tier user's entire 1/day quota
  with nothing run** — the unit is now refunded when the worker rejects the submit.
- Fixed a test-collection import error that took down the entire compute test
  suite under `pytest`.

### Fixed — telemetry / instance ping
- **Ping / bundle / CorvinLogs POSTs followed redirects and had no https-only
  guard (HIGH)** — they carried the Bearer + instance tokens (and a GitHub PAT) in
  headers and would forward them across a 302 or over `http://`. All three now use
  the hardened no-redirect/https-only opener (F8).
- An unquoted YAML `ping_enabled: 0` / `healing_traces: 0` did not opt out
  (docstring promised "0" does) — the int form is now honoured.
- Multi-tenant ping opt-out enumeration now fails **closed** on a discovery error.
- Version regex uses `\Z` (no trailing-newline bypass).
- Corrected stale "opt-in, deny-by-default" docstrings that misdescribed the
  now-default-ON/opt-out error + healing channels (the safety invariant is
  content-freeness, not consent).

### Notes — licensing
- Adversarial review confirmed the fork/Kerckhoffs ceiling documented in
  ADR-0139/0167 (a pure-Python gate can be NOP'd in a fork; the deterrent is
  economic/legal, not technical). Unmodified-binary findings that need issuer/
  server coordination (long-lived-token instance binding, offline-revocation
  fail-open, clock-rollback of an expired token) are documented for maintainer
  decision rather than patched with a change that could lock out paying customers.
  Fixed one safe item: compute grace no longer defaults an indeterminate tier to
  paid `pro`.

### Fixed — Agentic Compute page always showed "0 completed" / empty Analytics no matter how many runs succeeded
- The flat/l25 compute engine (`core/compute/corvin_compute/budget.py`
  `RUN_STATE_*`) never produces a state literally named `"complete"` — its
  real terminal states are `converged`, `stalled`, and `budget_exhausted`.
  Every `r.state === "complete"` check across `compute.tsx` (the top KPI
  strip's "Success Rate"/"Best Loss"/"Avg Convergence" tiles, every run
  card's "done" styling, the Cross-Experiment ROI panel, and both Analytics
  tables — Strategy Effectiveness, Tool Performance Ranking) compared
  against a value that can never occur, so they always read as empty/zero
  regardless of how many runs actually succeeded. Same bug, different
  vocabulary, for pipelines and HAC (both only ever reach `"converged"` at
  the top level). Added `isRunDone`/`isPipelineDone` helpers with the
  correct state sets and replaced every affected check; pipeline *stage*
  cards genuinely do use `"complete"` (per this session's earlier
  `pipeline_detail` fallback) and were left unchanged.

### Fixed — Console "license lost" / scattered errors after a tier change
- **Dashboard showed "Free" for an active Member subscription.** `GET
  /license/status` (`core/console/corvin_console/routes/license.py`,
  consumed by `dashboard.tsx`) had the identical bug already fixed on
  `/compute/license`: it hardcoded `tier: "free"` whenever no Enterprise
  on-prem `license.jwt` existed, even though `/license/info` (the dedicated
  License page) correctly showed "member" for the exact same
  `operator/license` `license.key` one function away. Two pages disagreeing
  about the same customer's tier is exactly what reads as "the license
  keeps disappearing." Falls back to `active_tier()` the same way.
- **Root cause of the actual disappearing-session symptom:** ADR-0154's
  session-derived license proof (SDLP) is a deliberate deterrent — a
  license tier change invalidates outstanding sessions server-side, by
  design, so a session cookie can't outlive a license swap. But the
  frontend's `api()` client treated every 401 as a plain per-request error;
  each page's own query 401'd independently and rendered its own generic
  "Could not load X" message, while the shared `auth/whoami` poll (every 5
  minutes) hadn't yet noticed and redirected to `/login` — several minutes
  of confusing, page-scattered errors before the user was ever told to
  sign in again. `api()` now calls a registered on-401 handler
  immediately; `AuthProvider` wires it to invalidate the shared session
  query right away instead of waiting on the next scheduled poll.

### Fixed — Agentic Compute run graph: every node except "completion" was an unstyled white box
- `GET /compute/runs/{id}/graph` (flat/l25 mode) emits its own group
  vocabulary (`run`/`strategy`/`iteration`/`best_iter`) that is entirely
  different from the ACS graph endpoint's (`task`/`manager`/`decision`).
  `ComputeGraphView.tsx`'s `NODE_TYPES` map, `NODE_W`/`NODE_H` sizing, and
  its whole layout algorithm (`buildReactFlowGraph`) key off the ACS
  vocabulary only, so every l25 node fell through to React Flow's unstyled
  `react-flow__node-default` (a plain white box) *and* never received a
  layout position — the graph rendered as a handful of misplaced white
  boxes instead of the intended themed run/strategy/iteration chain. Added
  a group-alias step so l25 names map onto their ACS equivalents before
  anything else touches them; the previously-unused `best_iter` distinction
  is preserved as a highlight border on the winning iteration node instead
  of being discarded.

### Fixed — Agentic Compute "Pipelines" tab always showed "no data" per stage
- `PipelineCoordinator` (`core/compute/corvin_compute/pipeline/
  coordinator.py`) only ever writes the rolling `pipeline_summary.json` —
  per `PipelineStore`'s own documented on-disk layout, a per-stage
  `stage_summary.json` was never part of the write-side contract. The
  console's `pipeline_detail` route expected that file for every stage's
  `state`/`best_loss`, so every stage card showed "waiting for prev
  stage…"/"no data" even for a fully converged pipeline with real,
  correct `best_losses` sitting one field over in `pipeline_summary.json`.
  Derives stage state/best_loss from there when no richer
  `stage_summary.json` exists.

### Fixed — Agentic Compute run graph edge labels always showed a white box
- React Flow's own stylesheet (`reactflow/dist/style.css`) hardcodes
  `.react-flow__edge-textbg { fill: white; }`. `ComputeGraphView.tsx`'s edge
  builder already sets `labelBgStyle: { fill: "transparent" }`, but that
  targets a different element and never overrode the library default, so
  every edge label on the compute run graph (e.g. the best-iteration
  callout) rendered as a solid white box regardless of theme. Added a
  targeted override in `index.css` using `var(--card)`.

### Fixed — HAC sub-managers crashed on every run (AttributeError)
- `HACCoordinator._exec_manager` (`core/compute/corvin_compute/hac/
  coordinator.py`) read `rec.best_params` off the `ComputeRun` result —
  `RunRecord` (`corvin_compute/state.py`) has never had a `best_params`
  field (only `best_loss`/`best_iter`), so every sub-manager run raised
  `AttributeError`, caught by the coordinator's `asyncio.gather(...,
  return_exceptions=True)` and recorded as a plain `"sub-manager X failed"`
  with no traceback surfaced anywhere — no HAC job had ever completed
  successfully, in any configuration. Now looks the winning iteration's real
  params up via `RunStore.read_iterations`, which does persist them.
  Tightened `test_hac_submit_returns_hac_prefix`'s assertion (previously
  tolerated `"failed"` as an acceptable outcome, which is exactly why this
  went undetected) and added a direct regression test.

### Fixed — Agentic Compute "Runs" tab never actually executed a submitted job
- `POST /compute/runs` (`core/console/corvin_console/routes/compute.py`)
  only wrote a `manifest.json` and returned — no poller ever read that
  directory (`recovery.scan_resumable` requires a `summary.json` this
  endpoint never wrote), so every run submitted through the console API sat
  forever with no error surfaced. Also, the fields it wrote
  (`params`/`objective`/`budget.timeout_s`) didn't match what the worker's
  real `submit_run` op expects (`param_grid`/`loss_metric`/
  `budget.max_wall_clock_s`) even if something had polled it. Rewired to
  submit over the worker's Unix socket via `WorkerClient`, with the field
  names translated to the worker's real contract, and a clear 503/502
  instead of silent no-op when the worker isn't running or rejects the run.
- Pipeline and HAC compute engines (`core/compute/corvin_compute/pipeline/
  engine.py`, `hac/engine.py`) are fully implemented and tested but were
  never constructed at the production worker's startup
  (`corvin_compute/cli.py::_cmd_serve`), so every `engine="pipeline"` /
  `engine="hac"` submission failed with "unknown engine" regardless of how
  it was submitted. `register_pipeline_engine`/`register_hac_engine` looked
  like the intended wiring but populate a separate, disconnected
  `engine_registry.py` singleton that `WorkerServer` never reads — it only
  consults engines passed via its own `extra_engines` constructor argument.
  `_cmd_serve` now constructs both engines and passes them there directly.
- Regression tests added for the `/compute/runs` field-name translation.

### Fixed — Agentic Compute panel showed "Trial · free" for licensed Member-tier customers
- `GET /compute/license` (`core/console/corvin_console/routes/compute.py`)
  hardcoded `tier: "free"` whenever no Enterprise on-prem license
  (`global/license/license.jwt`, the separate `corvin_license` RS256 plugin)
  was installed — which is the normal case for a Paddle/consumer Member-tier
  subscriber, licensed instead through the operator/license system
  (`global/license.key`, EdDSA). The endpoint already read the correct,
  unlimited `daily_limit` from that same operator/license system one line
  above, so a paying Member customer saw a self-contradictory panel: "Trial ·
  free" badge next to an effectively unlimited quota. `LicenseFileMissing`
  (no Enterprise key) now falls back to the operator/license system's
  `active_tier()` before defaulting to free. Regression test added.

### Fixed — ACO anomaly detector flagged every in-flight turn as "stalled"
- `_check_stalled_turns` in `core/console/corvin_console/aco/anomaly_detector.py`
  paired the nth `turn.start` with the nth `turn.done` and flagged any
  unpaired start as a HIGH `stalled_turn` anomaly — with no check against the
  module's own `TURN_TIMEOUT_MS` (5 min) constant, which was defined but never
  read. Every turn that is still legitimately running (a few seconds or
  minutes old, mid-tool-call) always lacks a `turn.done` yet, so it was
  flagged as "stalled" the instant a scan ran — confirmed firing on every
  single turn in a real session's `chat_debug.jsonl`, including a turn still
  actively in progress. Currently harmless (Layer 5's `repair.py` only writes
  a log annotation; the actuating registry in `repair_actions.py` has no
  action registered for `stalled_turn`), but it produced 100% false-positive
  HIGH-severity noise on the self-healing dashboard and would start acting on
  healthy turns the moment any future actuating repair is wired to this
  class. Now gates on elapsed time since `turn.start`, using the timeout
  constant it was always meant to.

### Fixed — ACS worker/manager JSON parsing silently mis-scored successful runs
- `_parse_manager_decision` and `_parse_worker_output` in
  `operator/bridges/shared/acs_runtime.py` used a single-level regex
  (`\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}`) to pull the JSON object out of LLM
  output. It cannot match a result object nested 3+ brace levels deep (e.g.
  `{"result": {"top5": [{...}, {...}]}}`), so it silently matched an inner
  leaf object instead — one with none of the expected `status`/`confidence`/
  `result` keys. Every worker whose result contained a nested array of
  objects was recorded as `"partial"` at `confidence 0.0` in the run
  graph/index even though the LLM call fully succeeded (only visible in the
  raw trace). Replaced with a shared `_extract_json_object` helper that does
  real bracket-counting (string/escape aware) to find the true top-level
  object at any nesting depth. Regression test:
  `test_parse_worker_fenced_deeply_nested_result`.

## [0.10.24] — 2026-07-09

### Security — Windows `cmd /c` argument-injection (host RCE) closed
- **A user's messenger message could execute arbitrary commands on a Windows
  host.** Worker-engine spawns (`claude`, `codex`, `opencode`, `copilot` — npm
  `.cmd` shims) ran through `subprocess.Popen(["cmd", "/c", …])`. On Windows a
  list is flattened by `list2cmdline` and **re-parsed by `cmd.exe`**, whose
  `\"` quote-toggle semantics let a prompt like `a" & powershell … & "b` break
  out and run the `&`-separated payload — outside the AI sandbox, bypassing the
  house-rules/path-gate/audit layers. New `agents/_win_shim.py` builds a
  cmd.exe-safe command **string** (each arg wrapped, inner `"` doubled as `""`,
  trailing backslashes doubled) that keeps `cmd` in quoted mode end-to-end
  while the target program still receives the argument verbatim. Applied at all
  six spawn sites (`claude_code`, `codex_cli`, `opencode_cli`, `copilot_cli`,
  and both `acs_runtime` manager/worker spawns). No-op on POSIX and for direct
  `.exe` launches — Linux/macOS behaviour is byte-for-byte unchanged. Unit
  tests in `agents/test_win_shim.py`. (Windows-VM end-to-end verification of
  the quoting against a live `cmd.exe` is recommended before relying on it in a
  regulated deployment.)

### Fixed — running sessions crashed on every bridge restart (incident 2026-07-09)
- **A `systemctl restart` of the bridge (including one triggered from within a
  chat, e.g. "restart both services") SIGKILLed every in-flight engine run.**
  The adapter's SIGTERM handler called `sys.exit(0)` immediately; interpreter
  teardown then joined the non-daemon `ThreadPoolExecutor` workers, which keep
  streaming their `claude` run, so the process hung until systemd's 20s
  `TimeoutStopSec` fired a cgroup-wide SIGKILL — dropping all active sessions
  with `exit_code=143`. Worse, a normal turn's inbox envelope is only moved to
  `processed/` at end-of-turn, so a mid-turn SIGKILL left the file in place and
  the restarted adapter **re-ran the same instruction** (observed: the
  "restart both services" message executed twice). The SIGTERM handler now only
  sets a `_shutdown_event`; the main loop stops accepting new inbox items and
  **drains in-flight runs** (up to `ADAPTER_DRAIN_TIMEOUT`, default 90s) before
  exiting, so a restart lets running turns finish (and move their inbox file)
  instead of killing them. The unit's `TimeoutStopSec` is raised 20→120s and
  `KillMode=mixed` sends SIGTERM to the main process only. New regression tests
  in `test_adapter_cancel.py`.
- **Five systemd timers stopped firing after any mid-uptime restart.** Timers
  with `OnUnitActiveSec=` but no `OnActiveSec=` anchor never re-arm once
  restarted while the machine is already up (watchdog + bg-monitor dead since
  2026-06-30 / 07-06). Added `OnActiveSec=` to
  `corvin-voice-bridge-watchdog`, `corvin-bg-monitor`, `corvin-hermes-health`,
  `corvin-operator-ui-watchdog`, and `corvin-claude-creds-sync`.
- **License reload throttle spammed the log** (2236 WARNING lines in ~19h). A
  throttled `reload_from_disk()` is the expected, self-healing outcome of the
  per-authenticated-op reload path — downgraded WARNING→DEBUG. The
  `license.reload_throttled` audit event is unchanged.

### Fixed — adversarial review of the 0.10.21–0.10.23 changes
- **Always-on (Stufe-2) install baked `/root/.corvin` into the service under
  sudo.** `_webui_env_vars()` resolved `CORVIN_HOME` in the elevated process,
  so a wheel install under `sudo` wrote `/root/.corvin` into a unit running as
  the invoking user → crash-loop until `StartLimitBurst`, leaving the machine
  with no console. Now anchors to the invoking user's home via `current_user()`.
- **STT (voice) fixes from the 453f026 chain-flip:** a blackholed OpenAI
  endpoint no longer hard-kills transcription — a non-terminal timeout falls
  through to the local provider (only a terminal-provider timeout re-raises);
  the local model's language hint is reset to `auto` per call (a prior `de`
  hint no longer sticks on the process-wide singleton); an offline install
  whose configured default model isn't on disk falls back to any present model
  instead of an in-band download that offline can't satisfy.
- **`say.py` TTS key resolution** now prefers the dedicated
  `CORVIN_TTS_OPENAI_KEY` over `OPENAI_API_KEY` (env and file) and strips
  inline `# comment`s, matching the STT side (ADR-0193).
- **Windows uninstall shortcut roots are now injectable.** `uninstall()` read
  `os.environ["APPDATA"/"USERPROFILE"]` directly, so a win32-mocked test could
  unlink a real Windows dev's Startup/Desktop `CorvinOS.lnk` — the 4th
  incarnation of the live-state-wipe class. Roots resolve in `__init__`
  (default env, sandbox under test) with a tripwire test.
- **codex/opencode `/stop` in the register→spawn window** no longer false-ACKs
  while the turn keeps running: `cancel()` latches `_cancel_requested`, and
  `spawn()` honours it right after the process starts.

### Fixed — /stop falsely reported "No task was running"
- **`/stop` (and `/cancel`/`/halt`/`/abbruch`) could silently do nothing and
  tell the user nothing was running, even while a task genuinely was.**
  `_cancel_chat` only ever inspected `_running_subprocs`, but HermesEngine,
  OpenCodeEngine, and CodexCliEngine drive HTTP/CLI streams with no Popen at
  all (most commonly hit on Discord via the stripped-PATH → Hermes
  auto-downgrade, ADR-0159 M1) — so `/stop` had no way to reach them. A
  second, narrower race window existed even for the Claude path: a `/stop`
  arriving between turn-start and subprocess registration also saw an empty
  registry. `/stop` now also cancels via the existing `_running_engines`
  registry (all three engines register there, alongside Claude, immediately
  on dispatch) and gives an honest "just started, try again in a moment"
  message instead of a flat false negative during the remaining race window.
- **Email bridge had no `/stop`/`/cancel` handling at all** — every other
  channel (Discord/Telegram/Slack/WhatsApp/Teams/Signal) already did. An
  email user had no way to abort a stuck turn.
- **Teams and Signal recognized `/stop`/`/cancel`/`/abbruch` but not
  `/halt`**, unlike every other channel — added for parity.

### Fixed — Windows autostart on non-admin accounts
- **Windows autostart silently failed with "Access is denied" on some
  standard (non-admin) accounts** (managed/family/education Windows images
  restrict the Task Scheduler store). `install.ps1` now falls back to a
  Startup-folder shortcut (`CorvinOS.lnk`) when `Register-ScheduledTask` is
  denied — no admin rights needed either way. `corvin-uninstall` removes this
  shortcut too, alongside the Scheduled Task.
- **Added a Desktop shortcut** (`CorvinOS.lnk`) so the console can be started
  by hand, independent of autostart. Also removed on uninstall.

### Changed — voice STT accuracy defaults
- **Default local Whisper model raised from `tiny-q5_1` to `base-q5_1`.**
  `tiny` mis-transcribed real voice notes often enough to be a recurring
  issue, even though `corvin-voice doctor` always passed (it only
  round-trips a clean fixture WAV, not real mic audio). Still a small,
  auto-downloaded, fully offline quantized model — no cost, no API key.
  Applies to fresh installs (`corvin-install`) and existing installs via
  `CORVIN_STT_LOCAL_MODEL`.
- **STT provider chain default flipped from `local → openai` to
  `openai → local`.** Cloud Whisper is more accurate than any
  auto-downloadable local model, so it now wins whenever an API key is
  configured; unchanged behavior with no key (falls straight through to
  local, exactly as before). Override with `CORVIN_STT_CHAIN=local,openai`
  to keep local-first.

## [0.10.23] — 2026-07-09

Multi-agent adversarial review of the last days' changes (installation,
voice in/out, autostart, cross-platform), plus the live-state-wipe
incident fix.

### Fixed — voice input (STT)
- **Concurrent voice notes could crash the bridge or cross transcripts.**
  whisper.cpp contexts are not thread-safe; the shared model singleton is now
  serialized by an inference lock (budget-aware, times out as a clean STT
  error instead of blocking unbounded).
- **Local STT on fresh Windows failed for every real voice note.** pywhispercpp
  shells out to a bare PATH-`ffmpeg` for non-WAV input, which stock Windows
  doesn't have. The local provider now converts .ogg/.opus itself using the
  same FFMPEG_BIN → PATH → bundled imageio-ffmpeg resolution the TTS side
  uses, and feeds whisper.cpp its dependency-free WAV path.
- **A truncated model download bricked local STT forever.** An aborted first
  download (Ctrl-C, power loss) left a half-written ggml file that every
  exists()/size check accepted. A failed model load now quarantines the file
  (`.corrupt`) and retries once, which re-downloads.
- **A stalled model download blocked cloud fallback indefinitely.** Lock-wait
  and load timeouts now raise "provider unavailable" (falls through to
  OpenAI STT) instead of a hard timeout that failed the turn.
- OpenAI STT: whitespace-only keys no longer report "ready"; `.env` inline
  comments (`KEY=sk-x  # prod`, `KEY="sk-x" # prod`) no longer poison the key
  value while a `#` inside a quoted value is preserved; the SDK client uses
  `max_retries=0` so a slow call can't overrun its time budget 3×.
- `.WAV` (uppercase) voice notes are converted rather than mis-dispatched to
  whisper.cpp's case-sensitive bare-ffmpeg path; ffmpeg conversion time is
  charged against the STT budget so total wall time stays bounded.

### Fixed — voice output (TTS)
- **Adapter's OpenAI TTS had no timeout** (SDK default: 600 s × 2 retries) —
  a degraded network parked messenger turns in TTS for minutes before
  edge/piper were tried. Now 15 s, no retries (parity with say.py).
- say.py Piper binary tier: UTF-8 stdin (cp1252 broke umlauts and killed
  ru/uk/zh/tr outright), overwrite-safe `replace` on Windows, PIPER_BIN +
  interpreter-neighbor binary resolution (uv-tool installs), no console
  window flash, orphan-WAV cleanup on failure.
- say.py model resolution: an any-language configured model no longer
  shadows the correct same-language model on disk; cross-language speech is
  a last resort and logged.
- `synthesize_voice_note` creates the outbox dir — `corvin-voice doctor`
  before first bridge boot no longer reports misleading per-tier failures.

### Fixed — installation & autostart
- **`corvin-supervisor.ps1` failed to parse on Windows PowerShell 5.1**
  (em-dashes in BOM-less UTF-8 read as ANSI terminate strings) — the repo
  supervisor, install.ps1 and bridge.ps1 are ASCII-clean now; autostart and
  crash-loop protection work again on stock Windows.
- The repo supervisor now sets `CORVIN_SUPERVISED=1` (parity with the
  install.ps1-generated one) so a pending self-update no longer burns the
  restart budget against a locked venv.
- The Windows self-update handoff script is written with a BOM
  (`utf-8-sig`) so non-ASCII install paths survive PS 5.1.
- **Stufe-2 (always-on) install now disables the Stufe-1 login autostart**
  on all three platforms — both bound port 8765 and the loser crash-looped
  at every login. `corvin-service uninstall` prints how to re-enable it.
- `corvin-service` command quoting: interpreter paths with spaces
  (`C:\Users\John Doe\...`) no longer tear; the unit also gets
  CORVIN_HOME/PYTHONPATH (reader≠writer class).
- Windows always-on: the Scheduled Task starts immediately after
  registration (was: only after the next reboot) and is stopped before
  deletion on uninstall (was: kept running on port 8765).
- macOS uninstall removes the webui service and sweeps `com.corvin.*`
  LaunchAgents — the console no longer relaunches at every login after
  "uninstall" (WA-7 class).
- install.ps1: the pre-install lock sweep now also matches the
  `corvin_gateway`/uvicorn console (wizard + Stufe-2), only targets
  python/corvin processes (an editor with a corvin path in argv is no
  longer collateral), and disables the autostart task during the install
  so restart-on-failure can't re-lock the venv mid-upgrade.
- Fresh Windows install no longer crashes writing the first API key
  (`service.env` FileNotFoundError).
- Uninstall hint now shows `uv tool uninstall corvinos` for one-liner
  installs.

### Fixed — cross-platform correctness
- ACS delegation spawns (manager + worker) wrap npm `.cmd` shims through
  `cmd /c` and pin UTF-8 decoding (WinError 193 / mojibake); same wrap for
  the codex/opencode/copilot engine CLIs.
- `/task` background worker: detached via `DETACHED_PROCESS` on Windows
  (start_new_session is a POSIX no-op — closing the console killed the
  "detached" task), spec file and completion-queue records pinned to UTF-8
  (emoji killed finished results with UnicodeEncodeError).
- **Every adapter outbox/observer/completion write is now UTF-8-pinned.**
  On Windows the locale default (cp1252) raised `UnicodeEncodeError` on any
  emoji/umlaut reply — a `ValueError`, uncaught by the `OSError` guards — so
  finished answers were quarantined as poison. All ~15 write/read sites
  (acks, warnings, progress, voice-note, observer ring-buffer, completion
  queue) now pin `encoding="utf-8"`.
- A failed pre-turn output/artifact snapshot no longer re-delivers the
  session's entire file history as attachments (empty-baseline → sentinel
  that skips attachment detection for that turn).
- Stufe-2 install on macOS now targets the invoking user's GUI domain
  (`SUDO_USER` uid, not the elevated `gui/0`) so the Stufe-1 LaunchAgent is
  actually stopped/disabled; `install.ps1` re-enables the autostart task if
  the install aborts; system-service units quote `Environment=` values so a
  path with spaces doesn't truncate PYTHONPATH.

### Fixed — live-state wipe incident (2026-07-08)
- The uninstall test wiped the running bridge's in-repo `.corvin` (sessions,
  budgets, audit chain), the user's systemd `corvin-*` units and the Claude
  Code plugin cache on every pytest run: every root `uninstall()` deletes
  from is now instance-injectable and the test sandboxes all of them; a
  repo-root conftest tripwire fails any test that deletes live operator
  state; the adapter heals a mid-turn-vanished session tree instead of
  quarantining finished answers as poison; stale-dropped messages notify
  the user instead of vanishing silently.

## [0.10.22] — 2026-07-08

### Fixed
- **Windows install: console never opened (readiness-probe drift).** Both
  installers polled `/api/health` after starting the console server — a route
  the standalone app never serves (only `/v1/console/*`, `/console/*`,
  `/local-stats`, `/`). On POSIX `curl -s` (without `-f`) silently treated the
  resulting 404 as success, masking the bug; on Windows, PowerShell's
  `Invoke-WebRequest` raises on any non-2xx status, so the readiness loop
  always ran into its timeout and the browser-open step was never reached —
  the console simply never appeared after a fresh Windows install. Both
  installers now poll the real, mounted, unauthenticated `/v1/console/healthz`
  route; a new drift-guard test (`test_installer_health_probe.py`) pins this
  across both scripts and the app's own route table so it can't silently
  regress again. Retry budget raised 30s→60s (cold Python import + AV/Defender
  scan can legitimately take longer), and both installers now open the browser
  regardless of whether the probe succeeded in time — the console is durable
  via autostart either way, so a slow-but-eventually-ready server is better
  served by "open and let it reload" than "never open at all."
- **Windows autostart: a fallback path left a closable window.**
  `install.ps1`'s `Install-CorvinAutostart` registers a per-user Scheduled
  Task (login-triggered, `-RunLevel Limited`, no admin needed) that
  auto-restarts the console forever. If that registration ever throws, the
  code fell back to starting the console once with `-WindowStyle Minimized` —
  which still creates a taskbar entry a user can click and close, silently
  killing the "background" process exactly like closing a visible console
  window would. The fallback now uses `-WindowStyle Hidden` (no window at
  all), and both Scheduled Task registrations (the standalone `install.ps1`
  path and the dev-checkout `bridge.ps1 install-autostart` path) now also mark
  the task itself `-Hidden` in Task Scheduler. A new regression test
  (`TestNoClosableWindow` in `test_windows_supervisor_parity.py`) asserts no
  `-WindowStyle Minimized/Normal/Maximized` can reappear anywhere in either
  installer script.
- **CI**: the coverage workflow installed a hand-maintained `pip install`
  list that had drifted — it silently omitted the package itself (breaking
  every `corvin_console`/`corvin_license` import), the `limits` rate-limiting
  library, the `hatchling` build backend, and `python-multipart`. It now
  installs via `pip install -e ".[dev]"` plus the same three extras, so it
  tracks the real dependency graph instead of a copy that can go stale. The
  `test`/`voice-e2e` workflows now install `ffmpeg`, which `corvin-voice
  doctor`'s real (non-mocked) edge-tts round-trip shells out to — without it
  every TTS check failed with "no audio produced" regardless of network/API-key
  availability.

## [0.10.21] — 2026-07-08

### Security
- **Iterative adversarial hardening sweep (multi-round review).** Closed a
  batch of residual issues found by a full-repo penetration re-review:
  - **L10 path-gate**: four confirmed write-bypasses closed — newline/CR
    command separators, quoted redirect targets, inline-interpreter writes
    (`python -c`/`perl -e`), and `cd`-into-tree relative redirects/`tee`; the
    voice config dir (`~/.config/corvin-voice`, holding the BYOK secret vault)
    is now a protected root against direct `rm`/`mv`/`chmod` as well as
    file-level writes.
  - **Webhook SSRF (DNS-rebind TOCTOU)**: the gateway webhook dispatcher now
    pins the validated IP into the httpx connection (connect-to-pinned-IP with
    SNI/cert kept on the real hostname, `follow_redirects=False`), matching the
    datasource-ping resolver — a hostname that rebinds to a link-local/internal
    address between validation and connect can no longer reach it.
  - **Email inbound-auth**: DMARC/DKIM-alignment gate hardened — a forged
    `Authentication-Results` line from a non-stamping IMAP provider is rejected
    unless its authserv-id matches the operator-pinned `auth_results_authserv_id`
    or a built-in well-known-provider allowlist; empty whitelist stays
    deny-by-default (owner via PIN `/auth`).
  - **SQL identifier gate**: anchored with `\Z` (not `$`) so a trailing newline
    can no longer slip through the identifier/ORDER BY allowlist.
  - **Self-heal**: the reproduction subprocess env now also strips
    `SSH_AUTH_SOCK` (no forwarded ssh-agent for pre-review test code); the
    maintenance loop treats a non-`ReproResult` as NOT proven (fail-closed).
  - Telemetry public-mirror bundle is filtered through the fail-closed
    `_assert_safe_htrace` backstop before send; browser-automation prompt moved
    off argv (Windows `cmd` RCE); local-stats endpoint gated to the real TCP
    loopback peer (not spoofable via `X-Forwarded-For`).

### Fixed
- **Background-completion notify — dead-worker reaper**: a long-running
  (>30 min) compute job is no longer falsely reported as "worker stopped" and
  its real result is no longer dropped — the reaper now acts only on records
  whose producer actually claimed them, and its liveness probe is
  non-destructive on Windows (`OpenProcess`/`GetExitCodeProcess` instead of
  `os.kill(pid, 0)`, which would terminate the very worker it checks).
- **Windows autostart**: service commands with a spaced or metacharacter
  program path (`C:\Users\John Doe\...`, `C:\Users\A&B\...`) no longer tear or
  shell-inject — the Task Scheduler command is registered via list-form
  `schtasks` (no `shell=True`); `enable_autostart`/`disable_autostart` now use
  `/change /enable`|`/disable` instead of `/tr "onstart"`, which previously
  clobbered the task's run-command and broke the boot task; the Windows
  self-update convergence marker gained a TTL so a transient upgrade failure
  self-heals instead of freezing auto-update.
- **Voice**: `test_say.sh` Piper isolation can no longer false-pass via the
  edge-tts fallback (new opt-in `CORVIN_SAY_NO_FALLBACK=1` strict mode);
  runtime TTS model names reconciled with the installer across all 12 languages.
- **CI/CD**: the compliance gate no longer skips its PR annotation on a critical
  finding (stays fail-closed at the decision step); publish is gated behind the
  test job with a tag↔version assertion; coverage/e2e-nightly/compliance steps
  no longer fail-open.
- **Delegation budget**: the runtime `BudgetEnvelope` default worker ceiling is
  realigned (8, not 500) with the delegation-budget defaults and validator —
  both the dataclass default and the spec-driven `_budget_from_spec` path
  (default 8, ceiling 64) so a workflow spec can no longer silently default to a
  500-worker fan-out.
- **Test isolation**: the skill-inject adapter test no longer writes its
  `settings.json` into the repo tree (`operator/bridges/skillinject/`) — it uses
  a private tmp bridges dir via `ADAPTER_BRIDGES_DIR`, closing a
  test-vs-real-config contamination class.

### Added
- **Console Settings toggle for ADR-0184 Stufe 2 (always-on system service).**
  Previously only reachable via the `corvin-service install`/`uninstall` CLI;
  the Settings page now has an "Autostart" switch that calls the same
  opt-in path (`GET`/`PUT /settings/service-tier`). Stufe 1 (login autostart)
  remains the default for every install; the console never self-elevates and
  never silently registers Stufe 2 — without admin/root it reports back the
  exact manual command instead of failing.
- **Zero-config voice on every platform (ADR-0185).** `pywhispercpp` replaces
  `faster-whisper` as the base-dependency local STT engine — no more Windows
  exclusion, no `av`/`torch`/`ctranslate2` dependency. `piper-tts` is now a
  base dependency too, giving `edge-tts` a genuine offline fallback tier
  instead of a permanently-skipped opt-in extra. `corvin-install` downloads
  both models automatically with visible progress and degrades gracefully
  with no network. New `corvin-voice doctor` command round-trips real
  STT+TTS and reports pass/fail loudly; a new Voice settings panel in the
  Console shows live per-provider status instead of a raw error dump in
  chat. The `voice` extra is now opt-in-only `faster-whisper` (for operators
  who want its accelerated inference and already have a working `av`
  install) — never required.

## [0.10.20] — 2026-07-07

### Fixed
- **Windows self-update silently died mid-upgrade, leaving `corvin-serve`
  stuck at an empty prompt.** The detached PowerShell helper that performs
  the actual upgrade (Windows locks a running process's own interpreter
  files, so the upgrade is handed off to a separate process that waits for
  `corvin-serve` to exit) launched the upgrade command with
  `Start-Process -NoNewWindow`, which requires an attached parent console —
  but the helper itself has none (`DETACHED_PROCESS`). The resulting
  exception was uncaught, killing the updater right after logging
  `running upgrade: ...`, before it could run the upgrade or relaunch the
  server. Fixed by using `-WindowStyle Hidden` (already used correctly for
  the relaunch step) and wrapping both `Start-Process` calls in try/catch so
  any future failure is logged to `corvin-self-update.log` instead of dying
  silently.

## [0.10.19] — 2026-07-07

Security- and compliance-hardening release from a multi-round adversarial
review (four rounds, real-LLM/real-data E2E). Every change is fail-closed or a
tightened default; no compliance guarantee was weakened.

### Security — path-gate (L10)
- **CRITICAL: recursive / glob / root destructive commands over the corvin
  runtime tree were allowed.** `is_protected_path()` only flags specific leaf
  names / subdir tokens, so `rm -rf ~/.corvin`, `rm ~/.corvin/*.jsonl` (glob),
  `rm -rf ~/.corvin/tenants`, `chmod -R ~/.corvin/global`, `mv ~/.corvin/tenants
  /tmp/x`, and `find ~ -name '*.jsonl' | xargs rm` all passed the gate and could
  erase the hash-chained `audit.jsonl`. New `_touches_corvin_tree()` helper
  fails closed whenever a destructive target IS / is UNDER / is an ANCESTOR of
  the corvin home; wired into the `find` mutating branch, `_TARGET_ALL_CMDS`
  (rm/rmdir/unlink/shred/truncate/chmod/chown/chgrp/chattr/ln), `_DEST_LAST_CMDS`
  (mv/cp/install/rsync — now checks the source, not just the dest), and the
  `xargs`-pipe form. Reads and unrelated-path destructive ops are unaffected.
- **`find` alias + ancestor-root bypass** (`gfind`/`bfind`, `find ~ … -delete`,
  `find ~/.corvin/tenants -delete`) fixed by the same helper.
- Corrected an overclaiming comment: the path-gate is a WRITE boundary and does
  not gate reads (`cat`/`less`/`grep` of the vault) — read confinement belongs
  to the sandbox / tool-allowlist layer.

### Security — console & gateway
- **SSRF + credential-exfiltration in the HTTP-datasource ping**
  (`routes/datasources_http.py`): a paid-tier console user could point
  `base_url` at `169.254.169.254`/`localhost`/RFC-1918 and attach any
  `os.environ` var. Now: `http(s)`-only scheme allowlist, private/loopback/
  link-local/IMDS IP block with full `getaddrinfo` resolution (incl. IPv4-mapped
  IPv6 and decimal/hex/octal encodings), no-redirect opener, `auth_env`
  restricted to a `^CORVIN_DS_[A-Z0-9_]+$` allowlist, and connectivity refused
  unless `network_egress` is explicitly declared (default `none` ⇒ refused).
- **Audit chain / secrets were downloadable via the file API**
  (`routes/files.py`): `_access()` returned on the first path-component match, so
  `global/forge/audit.jsonl` resolved to the READ_ONLY `forge` before reaching
  the NO_ACCESS `audit.jsonl`. It now scans all components — NO_ACCESS
  (audit.jsonl, policy.json, secrets.json, recall.db, .env, instance_id.json,
  vault) wins over READ_ONLY.
- **`get_mcp_config` leaked resolved secrets** (`routes/connectors.py`): the
  client endpoint returned vault/`os.environ`-resolved token values. It now
  returns the config with `${…}` placeholders unexpanded; resolution stays
  server-side in `build_mcp_config_for_node`.
- **Gateway EXECUTE path enforced only L44** (`gateway/dispatcher.py`): it now
  also runs the shared L34 residency + L35 egress gates (SSOT with
  console/adapter/a2a), before the compute meter. The CancelledError path now
  cancels the engine and sets a terminal `failed` state instead of stranding a
  `running` run and orphaning the `claude -p` subprocess.

### Security — spawn gates & ACS
- **Gate construction / classification now fail closed**
  (`spawn_gates.py`, `egress_gate.py`): an L44 gate-construction error, an L34
  `classify_task` crash, and an L35 validate error each return a refusal instead
  of falling open; transient L34 errors are no longer cached as allow.
- **Worker secret-env strip extended** (`acs_runtime.py`): the worker/manager
  spawn env now strips the full credential set — added `CORVIN_WDAT_KEY` (WDAT
  at-rest key, ADR-0109 M4) and `PGPASSWORD` alongside the Anthropic/OpenAI/
  Google/Ollama/Gmail/Hetzner/license keys — applied after the extra-env merge at
  both spawn sites.

### Compliance / telemetry
- Healing-trace upload dropped its free-text `error_template`; the instance-count
  ping is trimmed to `{corvin_version}` only with an `_assert_ping_safe`
  content-free backstop (CONTENT-FREE invariant tightened, still default-ON /
  opt-out per the maintainer decision).
- **Voice-audit `verify --all`** now runs the per-chain segment-manifest check
  and exits non-zero on any tamper (was verifying only the primary chain).
- **Compute-fabric residency + revocation fail closed** (`datasources/`,
  `license_gate.py`): residency is enforced via a single `_residency_gate`
  helper, `IN`/`NOT IN` bind each element as a placeholder, and a corrupt
  revocation cache denies rather than allows.

### Security — licensing
- **Revocation-check host could be overridden outside test mode**
  (`license/validator.py`): the features base URL now ignores the test-only
  override unless `CORVIN_TEST_MODE=1`, closing a revocation-bypass vector at
  forks.

### Cross-platform paths & SSOT
- **Voice-config directory unified to one resolver** across all six call sites
  (forge/paths, shared/paths, adapter, say, whisper STT, bridge_manager):
  `VOICE_CONFIG_DIR → XDG_CONFIG_HOME → ~/.config/corvin-voice`, identical on
  every platform (the divergent Windows `%APPDATA%\Local` branch is gone). A
  guard test pins all six resolvers to the SSOT.
- **`python3` hardcodes replaced with `sys.executable`** (MCP-config builder,
  cowork resolver, chat-runtime/workflows voice-summary, summary_provider) so
  worker/tool spawns use the interpreter that actually has the deps on Windows.
- Windows import-crash hardening: `fcntl` guarded in workflows; `/tmp` checkpoint
  paths moved to `tempfile.gettempdir()` with a per-PID name.

### Hermes install
- `setup-hermes-pib.sh` / the systemd unit render `__PYTHON_BIN__`/`__REPO_ROOT__`
  at install time (no `User=%u`), use an `EnvironmentFile`, and gate on RAM with a
  locale-independent `awk` (`LC_ALL=C`) so the health timer installs correctly on
  a non-C locale host.

### Deferred (documented, not shipped)
- Pre-existing items tracked for follow-up: `query.py` identifier quoting
  (needs expression-aware validation), `conversation_recall` raw `chat_key`
  (GDPR backlog), webhook optional-HMAC, and the L44 low-confidence clear-branch
  (a deliberate 2026-06-30 friction-reduction decision — left as-is).

## [0.10.18] — 2026-07-06

### Fixed
- **Windows: STT and TTS voice notes didn't work at all.** Reported via a
  fresh Windows 10 console install (`no STT provider available;
  chain=('local', 'openai'); failures=['local: not available', 'openai:
  not available']`, plus no TTS audio).
  - **STT:** `OpenAIWhisperProvider._resolve_api_key()` only checked
    `os.environ`. `bridge.sh`/`voice_lib.sh` export `OPENAI_API_KEY` into
    the shell before Python starts on Linux/macOS, but `bridge.ps1` on
    Windows launches the console/daemon directly with no equivalent
    `.env`-loading step, so the key was never visible to the process even
    when configured. It now also falls back to reading
    `~/.config/corvin-voice/.env` / `service.env` directly, mirroring
    `say.py`'s existing TTS key resolution.
  - **TTS, root cause:** `adapter.py` never imported `asyncio` at module
    level even though `_try_edge_tts` calls `asyncio.run`/`asyncio.wait_for`
    — every edge-tts call (the API-key-free fallback engine, a base
    dependency on every platform) raised a silent `NameError`, logged but
    swallowed, on **every** platform, not just Windows. Fixed by adding the
    missing `import asyncio`.
  - **TTS, Windows-specific:** edge-tts and Piper both need `ffmpeg` to
    convert their MP3/WAV output to OGG-Opus, but the installer explicitly
    skips installing system tools on Windows. Added `imageio-ffmpeg` (a
    pure-Python dependency bundling static ffmpeg binaries for
    Windows/Linux/macOS) as a base dependency and a `_resolve_ffmpeg_bin()`
    fallback used by both engines when no system ffmpeg is on PATH.

## [0.10.17] — 2026-07-06

### Added
- **Browser automation hardening + tool-surface expansion (ADR-0183 S1/S2):**
  builds on the ADR-0182 managed-browser layer.
  - **S1 hardening:** stale-mark self-healing (an accessible-name/role
    fingerprint taken at `observe()` is re-checked before every `click`/`fill`;
    a mismatch forces a re-observe instead of acting on a possibly-wrong
    element after an in-place DOM re-render); a decoupled confirm channel
    (`/browser confirm <sid> <yes|no>` in chat) so the human approving a
    sensitive action no longer has to be the same authenticated tab as the
    live view; sensitivity model v2 (URL-path signals like `/checkout`,
    `/delete`, `/settings/security` plus form-context signals such as a
    password/card-number field on the same form, additive to the existing
    keyword heuristic).
  - **S2 tool-surface expansion:** `hover`/`key`/`select_option`/
    `upload_file`/`drag` added to the existing Set-of-Marks action model;
    multi-tab awareness (`target="_blank"`/`window.open` popups are now
    tracked and switchable); same-origin and cross-origin iframe traversal
    (marks get a globally-unique index across every frame on the page);
    structured extraction (`extract_table`, `extract_form_schema`) returning
    JSON instead of flat text.
  - Stage 3 (macro cache, warm context pool, batched planning) and Stage 6
    (session replay, regression eval suite) from ADR-0183 are **not** part of
    this release — scoped for a follow-up.

### Fixed
- **Windows: engine CLI probes could report "not found" for real npm global
  installs.** `shutil.which()` resolves the npm shim (`claude.cmd`,
  `codex.cmd`, `copilot.cmd`, …) via `PATHEXT`, but `subprocess.run([name,
  "--version"])` without `shell=True` cannot launch a bare `.cmd`/`.bat` file
  on Windows (`WinError 193`) — every probe that resolved a binary this way
  then failed to actually run it. Added a shared `windows_wrap()` helper
  (`operator/bridges/shared/engine_detector.py`, mirrored in
  `engine_detection.py`) that runs such shims via `cmd /c`, applied to every
  probe site (`engine_detection.py`, `engine_detector.py`, `self_test.py`,
  `routes/setup.py`'s connectivity test). Also bumped the probe timeout from
  5s to 8s with a clearer "often antivirus scanning a freshly spawned shell"
  message — the previous timeout was tight enough to misreport a slow-but-
  present CLI as absent.

## [0.10.16] — 2026-07-06

### Fixed
Adversarial review of the anonymous instance-count ping (ADR-0180 §3 — the
metric behind the README active_7d/active_30d badges).

- **The documented opt-out command was a complete no-op.** The one-time
  telemetry notice tells the user to run `corvin config set
  telemetry.ping_enabled false` — but that wrote into
  `~/.config/corvin-launcher/config.json` (the file used for
  `ollama_url`/`model`/`bridge`/`image`), while the actual gate reads
  exclusively from `<corvin_home>/tenants/_default/global/tenant.corvin.yaml`
  (`spec.telemetry.ping_enabled`). Two disjoint files — running the exact
  command the software prints had zero effect. `telemetry.*` keys now
  correctly read/edit/atomically write the YAML path the gate actually
  consults.
- **The daily ping never re-fired for pip/uv standalone installs** — the
  primary distribution path. It was sent exactly once at boot in a
  fire-and-forget thread (`corvin_console.standalone` has no lifespan to run
  the recurring cycle the gateway path relies on), so a long-running
  `corvin-serve` process was counted on day 1 and then silently dropped out
  of `active_7d`/`active_30d` for the rest of its uptime — the opposite of
  an accurate active-install count. Added an hourly recheck loop (the ping
  itself still self-throttles to once/24h) that also re-evaluates the
  opt-out on every iteration, so a mid-lifetime opt-out takes effect within
  the hour instead of requiring a restart.
- **TOCTOU race could double-count a single instance-day**: the "already
  pinged today?" check and the stamp write bracketed the network call with
  no locking, unlike the healing-trace uploader in the same file (which
  already uses a file lock for exactly this reason). Two processes sharing
  one `CORVIN_HOME` booting close together could both pass the gate and
  both send a same-day ping. Now locked with the same pattern.

Verified end-to-end against a real temp install directory, not just
unit-mocked. 125 tests green (102 existing + 23 launcher), including 9 new
regression tests.

## [0.10.15] — 2026-07-06

### Fixed
Four parallel adversarial code-review passes over ACS, web-chat, licensing,
and Windows install/paths/permissions. 3 CRITICAL + 7 HIGH confirmed
findings fixed, all backed by new regression tests.

**CRITICAL**
- **ACS `budget_override` bypassed workflow validation entirely** — applied
  via blind `setattr()` *after* `validate_workflow_dict()` had already run,
  so it never got the `max_depth` ceiling (R31/R32) at all, and had no field
  allow-list, letting a caller overwrite internal accounting state
  (`start_time`, `loops_used`, ...) through the same HTTP field. Now merged
  into the spec's own budget dict, restricted to an explicit allow-list,
  *before* validation.
- **License revocation was never checked on reload** — only at process
  boot. A cancelled subscription's token kept re-activating on every
  authenticated console request until the whole process restarted.
- **Windows self-update PowerShell injection** — `_ps_quote()` didn't escape
  `$`, so a CLI arg (e.g. `--host`) containing `$(...)` was arbitrary command
  execution in the generated self-update script.

**HIGH**
- ACS: `max_loops`/`max_total_workers` set to 0 or negative (via YAML or
  `budget_override`) silently disabled that specific cap instead of falling
  back to a sane default — now clamped to `[1, ceiling]`.
- ACS: a cancelled run could leave a live `claude -p` worker subprocess
  running (burning CPU/tokens/API cost) for up to 30 more minutes, since
  `asyncio.to_thread()` doesn't interrupt a blocking call already running in
  the executor thread — the process is now tracked and killed on cancel.
- License: a permissive-mode `global/license.key` was only warned about and
  still trusted (despite a comment claiming parity with `session.key`,
  which actually rejects) — now rejects, matching `session.key`.
- License: `apply_license_key` wrote via `O_CREAT|O_TRUNC`, which only
  applies its mode argument on *new* file creation — a once-permissive
  `license.key` never self-healed on repeated "Apply Key". Switched to the
  same `tempfile.mkstemp`+`chmod`+`os.replace` pattern used elsewhere (also
  closes a symlink-follow risk).
- Windows self-update: `Log "..."` lines spliced raw command text into the
  generated PowerShell source with zero quoting — a stray `"` there aborts
  the whole script at parse time, and the caller had already exited (no
  relaunch, self-inflicted outage).
- Windows self-update: the relaunch command was a bare name relying on the
  detached script's inherited PATH — now resolved via `shutil.which()` in
  this process's own environment first.
- Chat: `/browser <task>` referenced `_spawn_gates` without ever importing
  it — every call raised `NameError`, silently caught and reported as
  "safety check failed"; the acceptable-use gate never actually ran and the
  feature was entirely non-functional (failed closed, but dead).
- Chat: the ACS-run exception handler referenced an undefined `logger`,
  masking every real ACS delegation crash behind a second `NameError`.
- Chat WebSocket: a syntactically-valid non-object JSON message (e.g. the
  bare text `"42"`) crashed the handler with an `AttributeError`, dropping
  the connection (two call sites fixed).

**Also**: `secret_vault.py`'s Windows permission-check bypass now logs a
one-time warning instead of being completely silent.

Several additional findings were confirmed but deferred as lower-severity
or requiring deeper structural work / real Windows hardware to verify —
see the commit message for the full list.

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
