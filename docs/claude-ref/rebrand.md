# Project Rebrand: corvinOS → Corvin — Full Reference

> Loaded on demand when continuing rebrand work.
> Quick summary: see CLAUDE.md § Project rebrand.

## Project rebrand: corvinOS → Corvin (sunset roadmap)

The project is being renamed from **corvinOS** to **Corvin** because the
framework is becoming engine-agnostic (Layer 22 `WorkerEngine` protocol —
Claude Code, Codex CLI and future engines pluggable via the same
contract). The legacy name is now misleading.

**The rename is in progress and runs through phased deprecation.** Every
session that touches this repo must move the project one step further
along the path below — this is a long-running task, not a single PR.
Treat it as a load-bearing repo convention: when you finish your primary
task and have spare bandwidth, complete the next reachable rebrand step
in the same commit (or a sibling commit) and tick it off here.

The migration uses the **strangler-fig pattern**: new identifiers grow
in alongside the old ones, the old ones get deprecation warnings, and
once nothing in the live tree references them they get removed. Live
bridges, the audit hash chain, on-disk skills and forge tools, and the
running systemd timers MUST keep working through every phase — a hard
cut would rip the audit chain and orphan user data.

### Phases

**Phase 0 — Decision recorded (DONE)**
- This block in `CLAUDE.md`. README header announces "Corvin (formerly
  corvinOS)". The rebrand intent is now durable across sessions.

**Phase 1 — Compatibility resolvers (DONE 2026-05-09)**
- `CORVIN_HOME` becomes the canonical env var; `CORVIN_HOME` keeps
  working as an alias with a one-time `DeprecationWarning` log line per
  process. Same pattern for every `CORVIN_*` var (full list at the
  bottom of this section).
- Path resolvers in `operator/forge/forge/paths.py`,
  `operator/bridges/shared/paths.py`, `operator/cowork/lib/paths.py`
  and `operator/forge/forge/scope.py` resolve `~/.corvin/` first, fall
  back to `~/.corvinOS/` if the new dir doesn't exist yet, and log the
  fallback once per process.
- New tests assert both code paths (new var + new path wins; old var +
  old path still works with warning).
- The compat shim is added in ONE module per session and wired through
  the existing call sites; do not rip out the old identifiers in this
  phase.

Sub-task status:

| Module / resolver | Status | Session | E2E |
|---|---|---|---|
| `operator/forge/forge/paths.py` (3 byte-identical copies in forge / voice / cowork) | ✓ done | 2026-05-09 | `operator/forge/tests/test_paths_compat.py` (31 cases — env precedence, repo-walk, fallback, once-per-process log) |
| `operator/forge/forge/scope.py` (`CORVIN_FORCE_SCOPE` / `CORVIN_DEFAULT_SCOPE` / `CORVIN_CHANNEL_ID` / `CORVIN_TASK_ID`, plus `<repo>/.corvin` and `/tmp/.corvin/tasks` workspace fallbacks) | ✓ done | 2026-05-09 | `operator/forge/tests/test_scope_compat.py` (37 cases — env precedence per var, repo workspace, /tmp tasks fallback, once-per-process log) |
| `operator/bridges/shared/adapter.py::_build_spawn_env` (`CORVIN_CHANNEL_ID` / `CORVIN_CALLER_PERSONA` — write-side dual spelling) | ✓ done | 2026-05-09 | `operator/bridges/shared/test_adapter_spawn_env_compat.py` (22 cases — dual write, sanitization parity, persona lookup order, defensive strip on both names) |
| `operator/forge/forge/secret_vault.py` (`CORVIN_SECRET_VAULT`) | ✓ done | 2026-05-09 | `operator/forge/tests/test_secret_vault_compat.py` (14 cases — env precedence, XDG default, path expansion, once-per-process log) |
| `operator/skill-forge/skill_forge/registry.py::plugin_slot_dir` (`CORVIN_PLUGIN_SLOT_DIR` + reads `CORVIN_HOME`) | ✓ done | 2026-05-09 | `operator/skill-forge/tests/test_plugin_slot_compat.py` (16 cases — slot env precedence, HOME-derived slot, repo-walk, once-per-process log) |
| `operator/voice/scripts/session_timeout_sweep.py` (`CORVIN_SESSION_TTL_DAYS`) | ✓ done | 2026-05-09 | `operator/voice/scripts/test_session_timeout_compat.py` (12 cases — env precedence, once-per-process log, argparse default) |
| `operator/bridges/shared/agents/test_engines_e2e.py` (`CORVIN_AGENTS_SKIP_LIVE`) | ✓ done | 2026-05-09 | `operator/bridges/shared/agents/test_engines_skip_live_compat.py` (10 cases — env precedence, one-shot warning, non-'1' value rejected) |

Each row above is one bridge-session of work: implement the resolver
with the once-per-process deprecation log, write a per-subtask E2E,
run `bash operator/bridges/run-all-tests.sh`, update the inventory
snapshot below.

**Phase 2 — Identifier migration in non-public code (DONE 2026-05-09)**
- All internal Python / JS that references `corvinOS` / `corvinos` / `CORVIN_*`
  is rewritten to use the new names, going through the compat resolver.
  Tests ride along.
- Public surface (env vars, on-disk paths, audit-event names) stays
  dual-named for users — the warnings keep firing.
- One subsystem per session (forge, skill-forge, voice-adapter, voice-
  hooks, cowork, daemons). Run `bash operator/bridges/run-all-tests.sh`
  after every subsystem before committing.

Sub-task status:

| Subsystem | Status | Session | Notes |
|---|---|---|---|
| `forge` (runner / registry / mcp_server / forge_cleanup / service.yaml + 4 tests) | ✓ done | 2026-05-09 | callsites use `corvin_home` and `CORVIN_*` env reads with legacy fallback; cleanup script scans both `/tmp/.corvin/tasks` and `/tmp/.corvinOS/tasks`; one skill-forge test (`test_e2e_demo_task.py`) ride-along to use `scope_root` instead of hard-coded `.corvinOS` path |
| `skill-forge` (registry / mcp_server / cleanup / 8 tests) | ✓ done | 2026-05-09 | registry + mcp_server caller-persona dual-read; service.yaml `<corvin_home>`; skill_cleanup scans both /tmp/.corvin and /tmp/.corvinOS task trees + corvin_home() sessions; all 8 test files migrated to `CORVIN_*` env-vars; hardcoded `/tmp/.corvinOS/tasks` test paths now scan both trees |
| `voice-adapter` (adapter.py read-side + module-level constants) | ✓ done | 2026-05-09 | adapter.py test-hygiene block writes both `CORVIN_HOME` and `CORVIN_HOME` (legacy alias) when defaulting from `ADAPTER_INBOX` sandbox; engine-layer flag reads `CORVIN_USE_ENGINE_LAYER` first with `CORVIN_USE_ENGINE_LAYER` fallback; the `_corvin_home()` helpers in 7+ shared/ sibling modules (proposal/consent/roles/quota/disclosure/auth_elevation/audit_view) are deferred to a follow-up commit (mechanical pattern-replace, no behavioral change) |
| `voice-hooks` (path_gate, stop_hook, auth_elevation_gate, notification_relay) | ✓ done | 2026-05-09 | path_gate `_corvin_home()` + dual env reads + `.corvin` hint added; auth_elevation_gate CORVIN_CHANNEL_ID first; notification_relay CORVIN_HOME with .corvin/.corvinOS fallback; stop_hook.sh CORVIN_CALLER_PERSONA→legacy; deferred `CORVIN_SECRET_VAULT` migration in test_secret_injection landed here |
| `cowork` (resolver, paths, tests) | ✓ done | 2026-05-09 | resolver.py spawn-env writes both `CORVIN_DEFAULT_SCOPE` + `CORVIN_DEFAULT_SCOPE`; personas/os.json adds `.corvin/` to add_dirs alongside `.corvinOS/`; tests assert both env spellings; paths.py was already migrated in Phase 1 |
| `daemons` (telegram / discord / slack / whatsapp + shared/js) | ✓ done | 2026-05-09 | the four daemon.js files have no `corvinos` references; shared/js/auth_elevation.js renamed `corvinosHome` → `corvinHome` with .corvin-first / .corvinOS-fallback path resolution; in_chat_commands.js docstrings reference `<corvin_home>` paths and CORVIN_DEBUG_DEPTH / CORVIN_CHANNEL_ID with legacy aliases noted |

**Phase 3 — Documentation, READMEs, comments, audit-event names (DONE 2026-05-09 except SVG re-renders)**
- Every prose mention of `corvinOS` becomes `Corvin`. Comments and
  docstrings get rewritten. ADRs gain a one-line note that they predate
  the rebrand if the original term is preserved for historical accuracy.
- New audit-event-name aliases (`session.reset` etc. stay; only
  identifiers that contained the literal `corvinos` get renamed —
  none today, verified via `grep corvinos operator/forge/forge/security_events.py`
  → no matches).
- The `[CORVIN_SIGNAL: <name>]` persona-protocol marker is dual-fired
  alongside `[CORVIN_SIGNAL: <name>]` on the same line in adapter.py;
  either spelling fires the persona's append_system interpreter. Both
  drop in Phase 7.
- Diagrams in `docs/diagrams/*.svg` and the banner asset
  (`assets/banner.svg`) are **deferred to a follow-up commit** —
  SVG re-rendering needs the source-of-truth (Mermaid? hand-edited?)
  and is best done in a separate session focused on visual assets
  rather than mixed with prose edits.

**Phase 4 — On-disk migration helper (DONE 2026-05-09)**
- `~/.corvinOS/` → `~/.corvin/` move helper that runs at adapter boot
  when the new dir doesn't exist and the old one does: atomic rename if
  same filesystem, else copy + verify + leave a `MIGRATED` marker in
  the old location pointing at the new one. The helper logs to the
  unified audit chain (`session.path_migrated`).
- Same for the systemd unit names: introduce `corvin-audit-verify.timer`
  and `corvin-session-timeout.timer` alongside the old ones; `bridge.sh
  up` enables both, `bridge.sh down` disables both.
- Module: `operator/bridges/shared/corvin_migrate.py` —
  `migrate_home_if_needed(*, new_home, legacy_home, dry_run, audit_path)`
  helper, idempotent + opt-out via `CORVIN_MIGRATE=0`. Detects same-FS
  via `st_dev`; falls back to `shutil.copytree` + size-verify + MIGRATED
  marker on cross-FS. Audit event `session.path_migrated` written into
  the unified hash chain (severity INFO, details from/to/method/files).
- E2E: `operator/bridges/shared/test_corvin_migrate.py` (28 cases —
  no-legacy no-op, target-exists idempotent, CORVIN_MIGRATE=0 opt-out,
  dry-run, same-FS rename, cross-FS copy via monkey-patched os.rename
  raising EXDEV, MIGRATED marker placement, hash-chain verifiable
  across migration, invalid-args raise).
- adapter.py boot integration: gated on `ADAPTER_INBOX` absence (=
  production only — tests never trigger the helper); idempotent if
  `~/.corvin/` already exists.
- `bridge.sh up` enables both `corvin-*` (legacy) and `corvin-*`
  (canonical) timers; `down` disables all four. Both fire the same
  exec script; the migration is idempotent on either path.

**Phase 5 — Plugin-folder renames (DONE 2026-05-09)**
- `plugins/corvin-init/` → `core/init/`
- `plugins/corvin-pipe/` → `core/pipe/`
- Done with `git mv` to preserve blame. References get updated through
  Phase-2 grep sweep.

**Phase 6 — Repo-folder rename (NEXT — operator action)**
- `<projects>/corvinOS/` → `<projects>/corvin/`
- Done LAST. Requires updating `~/.claude/CLAUDE.md`, any IDE bookmarks,
  systemd unit `WorkingDirectory=` paths, and the `bridge.sh` symlinks.
  Operator action; Claude Code only proposes the move, doesn't execute
  it.

**Phase 7 — Hard cut**
- Trigger: zero `CORVIN_*` env-var reads in the user's `service.env`
  for ≥ 30 days AND zero `corvinos`-substring matches in user-facing
  docs AND no `~/.corvinOS/` directory left on the operator's box.
  Verify by running `audit-verify` against the chain — must be clean.
- Action: remove every `CORVIN_*` alias resolver, every `~/.corvinOS/`
  fallback path, every "formerly corvinOS" footnote. The string
  `corvinos` should only survive in git history and in the CHANGELOG
  entry that records the hard cut.
- Bump major version (operator decision — likely v1.0).

### Inventory snapshot

| Date | Step | substring matches | files | `CORVIN_*` env-var matches | files | plugin dirs | systemd units | repo dir |
|---|---|---|---|---|---|---|---|---|
| 2026-05-09 | Phase 0 close | 1105 | 162 | 464 | 109 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 1 (`paths.py` × 3 + E2E) | 1194 | 165 | 578 | 115 | 2 | 4 | 1 |
| 2026-05-09 | Phase 0 amend (target name LoomOS → Corvin in `README.md` + `CLAUDE.md`) | 1193 | 164 | 592 | 115 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 2 (`scope.py` + per-subtask E2E) | 1226 | 166 | 608 | 116 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 3 (`adapter._build_spawn_env` dual-spelling + E2E) | 1246 | 167 | 624 | 117 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 4 (`secret_vault.py` + E2E) | ~1255 | 168 | ~635 | 117 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 5 (`plugin_slot_dir` + E2E) | ~1280 | 169 | ~655 | 118 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 6 (`session_timeout_sweep` + E2E) | ~1295 | 170 | ~665 | 118 | 2 | 4 | 1 |
| 2026-05-09 | Phase 1, Modul 7 (`agents` skip-live env + E2E) — Phase 1 close | ~1310 | 171 | ~675 | 118 | 2 | 4 | 1 |
| 2026-05-09 | Phase 2, Subsystem 1 (`forge` callsite migration) | 1265 | 170 | 652 | 120 | 2 | 4 | 1 |
| 2026-05-09 | Phase 2, Subsystem 2 (`voice-hooks` migration + deferred VAULT) | 1226 | 168 | 621 | 116 | 2 | 4 | 1 |
| 2026-05-09 | Phase 2, Subsystem 3 (`skill-forge` callsite migration) | 1136 | 161 | 549 | 106 | 2 | 4 | 1 |
| 2026-05-09 | Phase 2, Subsystem 4 (`voice-adapter` core — adapter.py only; shared helpers deferred) | _measured next commit_ | _ | _ | _ | 2 | 4 | 1 |
| 2026-05-09 | Phase 2, Subsystem 4b (shared/-helpers × 7) | 1132 | 162 | 556 | 107 | 2 | 4 | 1 |
| 2026-05-09 | Phase 3 (docs / READMEs / ADRs / persona prose / SIGNAL dual-fire) | 1053 | 157 | _unchanged_ | _ | 2 | 4 | 1 |

**Strangler-fig note on rising counts.** Substring + env-var counts
are *expected to rise* during Phase 1 — every compat shim spells both
the old and the new identifier explicitly, and the deprecation-log
payload itself mentions the legacy name. The counts only start falling
in Phase 2 when internal call-sites are rewritten. The honest signal at
Phase-1 close is "every required compat resolver landed AND its
per-subtask E2E is green," not a number going down. Re-run the counts
at the end of each phase-step and append a new row.

The Phase-7 trigger is "the last four columns are at 0 (or only in git
history); column-1 only references inside `outputs/` / docs are
acceptable historical anchors."

### The CORVIN_* env vars (for the resolver)

Every alias must produce a one-time `DeprecationWarning`-style log line
on first read per process — exact format:

```
[deprecation] CORVIN_HOME is deprecated; use CORVIN_HOME instead. Will be removed in Phase 7.
```

| Old name                  | New canonical name        |
|---------------------------|---------------------------|
| `CORVIN_HOME`           | `CORVIN_HOME`             |
| `CORVIN_FORCE_SCOPE`    | `CORVIN_FORCE_SCOPE`      |
| `CORVIN_PLUGIN_SLOT_DIR`| `CORVIN_PLUGIN_SLOT_DIR`  |
| `CORVIN_CALLER_PERSONA` | `CORVIN_CALLER_PERSONA`   |
| `CORVIN_CHANNEL_ID`     | `CORVIN_CHANNEL_ID`       |
| `CORVIN_SESSION_TTL_DAYS`| `CORVIN_SESSION_TTL_DAYS`|
| `CORVIN_SECRET_VAULT`   | `CORVIN_SECRET_VAULT`     |

### What you, as Claude Code, must NOT do (rebrand)

- Don't run a repo-wide `sed -i` from `corvinOS` to `Corvin`. The audit
  hash chain, live skill files, and on-disk forge tools are byte-stable
  artifacts; bulk-renaming inside them rewrites past audit lines and
  reads as tampering when `voice-audit verify` runs.
- Don't drop the `CORVIN_*` env-var fallbacks before Phase 7. Every
  bridge that's currently running has them in its environment via the
  user's `service.env`; pulling the rug brings live bridges down.
- Don't rename `~/.corvinOS/` without going through the Phase 4 helper.
  An ad-hoc `mv` orphans the audit chain (the absolute path is part of
  the hash chain context for many events) and breaks the symlinks the
  legacy `~/.config/claude-cowork/personas/` chain points at.
- Don't merge a phase commit that doesn't update the inventory snapshot
  numbers above. The numbers are the only honest signal of progress;
  stale numbers turn the roadmap into wishful thinking.
- Don't skip phases. Phase 6 (repo-folder rename) before Phase 4 (data
  migration helper) leaves the operator with a renamed repo pointing
  at an unmoved data directory — exactly the kind of half-rename that
  motivates the strangler-fig pattern in the first place.
