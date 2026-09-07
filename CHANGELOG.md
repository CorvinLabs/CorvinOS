# Changelog

All notable changes to CorvinOS are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The package version is the single `version` in `pyproject.toml`; `corvin --version`
prints it. Older per-release notes are archived under
[`docs/archive/releases/`](docs/archive/releases/) and linked from the
[Historical releases](#historical-releases) section below.

## [Unreleased]

## [2.0.0] - 2026-09-07

Major version: the persona system was removed (100 % of traffic runs on Skills), and
the Hermes / Local worker engines were deleted — both are breaking for operators
who configured either. Everything below is taken from the commit history between
`v1.0.0-PRODUCTION` (2026-08-20) and this release; ADR ids refer to the Corvin-ADR repo.

### BREAKING

- **Personas eliminated.** Deprecated personas, the personas console UI and the
  remaining persona references in core code were removed after the staging canary
  promoted the Skills-based routing to 100 % (`e7e3560e`, `17764883`, `97dbab56`,
  `1c41e137`).
- **Hermes and Local worker engines removed.** `EngineType` no longer carries
  `HERMES` / `LOCAL`; their implementations, tests, matrix entries and
  `install.sh` / `tenant.corvin.yaml` branches were deleted — Claude Code is the only
  bundled worker engine (`63dd9438`, `15b89717`, `741060fc`, `243690e8`,
  `af9e3b40`, `60ffce64`).
- Version bumped `1.0.0` → `2.0.0` in `pyproject.toml`; `install.sh` keeps
  `CORVIN_MIN_VERSION` in sync (guarded by `tests/test_wheel_content_guard.py`).

### Added — Learning (ADR-0613 … ADR-0637)

- ACP learning loop closed end-to-end: shadow-mode `os.delegation_router` decision
  source, per-task OUTCOME sink, audit-first event store, real (non-mock)
  `/v1/console/learning/*` endpoints (`1a19e321`, `8dd9bf8a`, ADR-0613).
- Context filtering via intent classification (`8f153f13`, ADR-0528); user-profile
  learning with GDPR deletion (`3e18fae1`, ADR-0529/0530); learning optimizer with
  deploy scripts and monitoring (`20fd3712`).
- Unified learning loops / NineD learning vector: foundation + memory loop
  (`0540bff6`, `8df1131b`, ADR-0614/0615/0616).
- Phase 2A meta loop self-tuning: `MetaOptimizer` + watchdog, integration tests and
  convergence validation, adversarial review (`4f9c5b5d`, `ecff483e`, `629783a1`,
  `7d4a0221`, `37ff6003`, ADR-0623/0624/0625).
- Phase 2B coupled learning: gradient-backprop DAG + correlation filter,
  integration tests and adversarial review (`34abd51a`, `5800e511`, `abde3597`,
  ADR-0615/0616, ADR-0626/0627/0628).
- Phase 4 feedback closure + meta-loop tuning (`2bbcd7ed`, ADR-0632/0633/0634);
  Phases 3–5 operator interface, feedback and dashboard (`042a9461`,
  ADR-0629/0632–0637).

### Added — Skills / ACP

- ACP Skills Phase 1: 3-tier hybrid context model, L5/L10 wiring, learning
  integration (`6550563b`).
- Method Discovery & autonomous learning (Phase 3.5) with follow-up quality fixes
  (`9ac12206`, `7b9495b7`).
- Engine layer Phase A/B/C production-ready (`5e8dbe7a`, ADR-0598–0609).

### Added — Infinite-session engine

- Phase A: infinite-session task engine (graph-DAG executor) (`3ed67faf`); production
  go-live at 100 % rollout (`ce3c6739`).
- Phase B: session bridging + cryptographic signatures (`3b2de3f5`, ADR-0541).
- Phase C: rollback atomicity + drift detection (`a4e68742`, ADR-0617).
- Phase E: production deployment + final validation (`92fbb03a`, ADR-0540–0545).
- Adversarial-review security hardening (`8454babc`, ADR-0635).

### Added — Plugins / marketplace

- Plugin orchestration via ACP Skills (`4073e00e`, ADR-0610/0611/0612);
  `CapabilityType` extended for Segment-A marketplace plugins (`5533cb56`).
- `context_retriever` provider type with fail-open CEL/TDE seams (`57cd49c3`,
  ADR-0599); semantic-context-retriever plugin (BM25) proven to load and activate
  through real boot (`80508d44`, `a739dd21`).
- Real builtin marketplace install + running-builtins listing in the console
  (`c3bdb01c`, ADR-0511/0247), with an E2E test through the real FastAPI route
  (`257c7dba`).

### Changed — Plugins

- Plugin source now lives in the Corvin-Marketplace repo, not in CorvinOS
  (`84ec0603`); 22 dead `builtin_plugins` stubs deleted (`13e6e303`).
- TDE seam contract narrowed to narrows-only and re-defanged after join
  (`02c522db`, `ea575391`).

### Added / Changed — Console

- Learning Dashboard with Summary / Patterns / Config / Preferences tabs and the
  Maturity Metrics theme (`4a1aa3a8` … `b5c2083c`, `11804914`); Method Discovery
  API routes wired (`3655d392`, `40344a28`).
- Engines page and Setup tab reworked to Single-Harness + Model Providers
  (`1663480b`, `e597698c`).
- Manifest-driven console layout (`9de226e7`, ADR-0561).
- All 11 architecture diagrams redesigned and simplified; README overhauled with the
  ACP + learning feature set (`f5490387`, `b8dd30bb`, `56f4f080`, `55869636`).

### Security / compliance

- Phase 3 GDPR compliance hardening after adversarial review (`97092e21`, ADR-0558).
- Week-4 gate: P99 baseline benchmark + 20 adversarial tests (`2103a52a`); final
  E2E + adversarial E2E suite (`cb842c54`).
- Live production alerts + daily review procedures (`f8540573`).

### Tests

- Dead-API test modules retired, stale clusters aligned, two latent product bugs
  fixed along the way (`f60db6e3`, `8758aa46`).

### Hardening (2026-09-07 adversarial review)

- **Audit chain (ADR-0640):** forged hash-less records are now detected (`unchained_record`), tail truncation is detected via an out-of-tree anchor, the detail floor is default-deny (writers register per-event allowlists), tenant mismatch is enforced in `write_event`, boot-time truncation replaced by a chained seam record, `license.reload_throttled` aggregated.
- **Boot + L34 (ADR-0641):** the compliance tripwire runs unconditionally in both hosts (absent `corvin_plugins` = boot failure); unknown data classifications deny; default matrix keeps CONFIDENTIAL off `us_cloud`; erasure covers learning events, sessions and infinite-session snapshots; console `/erase` is real.
- **Skills / ACP (ADR-0642):** `core.skills` imports again in a fresh process (persona leftovers removed; registry would otherwise have been silently empty after restart); `lom`/`lom_hash` mandatory on every `skill.executed`; compliance-tier skills cannot be disabled; namespace gate in the registry; `SkillAdapter` locked.
- **Plugins (ADR-0643):** origin derived from location (never from the request body), audited boot-layer downgrades, `plugin.loaded` carries provenance, all 30 marketplace manifests load, `corvin plugin install` works, ~10 dead registries/routers deleted.
- **Learning (ADR-0644):** learning routes serve real EventStore data or 503 (no constants), WebSocket authenticated via session cookie, free-text feedback never persisted, hyperparameter changes audit-first, 9D orchestrator importable and watchdog-guarded, collector reads real sources.
- **Infinite session (ADR-0645):** one tenant-bound store, strict ids + path containment, real audited revert with CSRF, HMAC-keyed rollback chain with WAL replay, whole-event bridge signatures, the task worker now produces snapshots.
- **Engine + bridges (ADR-0646):** prompts never on argv (`--`/stdin), engine substitution audited, per-clause DKIM/DMARC alignment, Discord `/task` gated, atomic inbox writes with poison quarantine, secrets 0600, PAT via askpass, dead engine simulators deleted.
- **Console:** frontend build repaired (was red since 2026-09-06 16:59), routes hardened (CSRF/session on vibe + l5 + learning + infinite-session), double-prefixed routers fixed, XSS sinks removed, SSRF guard on custom providers, lint 986 → 0, vitest green, Docker/ops entrypoints use `standalone:create_app`.
- **Repository:** 840 MB of tracked virtualenvs untracked, root scratch reports removed/moved, wheel build repaired, `corvin --version`, CI gates fixed (ADR gate verifies commit-referenced ADRs against Corvin-ADR), README/docs links repaired, NOTICE third-party section, governance files back at the root.

## Historical releases

Per-release notes and the pre-2.0 changelog are archived unchanged:

- [1.0.0 — 2026-08-20 — Tenant-native data persistence (ADR-0433)](docs/archive/releases/RELEASE_NOTES_v1.0.0.md) · git tag `v1.0.0-PRODUCTION`
- [0.10.64 … 1.0.0 — 2026-07-28 … 2026-08-20 — full Keep-a-Changelog history](docs/archive/releases/CHANGELOG.md)
- [v0.3.0 — 2026-08-18 — Glass-box vibe engineering + cross-device learning sync](docs/archive/releases/RELEASE_NOTES_v0.3.0.md)
- [v0.2-rc1 → v0.3.0 changelog — 2026-08-10 … 2026-08-18](docs/archive/releases/CHANGELOG_v0.2_to_v0.3.md)
- [v0.2-rc1 — 2026-08-17 — Unified architecture (Context Engineering Layer v2, Tool Forge, Skill Forge)](docs/archive/releases/RELEASE_NOTES_v0.2-rc1.md)
- [0.10.53 — 2026-07-20 — Geo-tracking Phase 3 complete (git tag only, never on PyPI)](docs/archive/releases/CHANGELOG-0.10.53.md)
- [0.10.52 — 2026-07-20 — Geo-tracking Tier 2/3 visualization (git tag only)](docs/archive/releases/CHANGELOG-0.10.52.md)
- [0.10.51 — 2026-07-20 — PostgreSQL geo-tracking live integration](docs/archive/releases/CHANGELOG-0.10.51.md)
- [0.10.50 — 2026-07-20 — Multi-tier GDPR-compliant geo-tracking (git tag only)](docs/archive/releases/CHANGELOG-0.10.50.md)

Note: the `v0.2-rc1` / `v0.3.0` tags were an internal numbering track that ran in
parallel with the `0.10.x` PyPI line; both predate `1.0.0`.

[Unreleased]: https://github.com/CorvinLabs/CorvinOS/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/CorvinLabs/CorvinOS/compare/v1.0.0-PRODUCTION...v2.0.0
