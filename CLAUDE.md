# Repo Conventions for Claude Code

This document aims at *Claude Code itself* when working in this repo.
**Reference files** with full details live in `docs/claude-ref/` and are loaded via `Read` on-demand.
CLAUDE.md stays small so it fits in every session; the ref files are the source of truth.

---

## Maintainer Status

**Git user: shumway.** Claude Code is explicitly permitted to `git push` directly to `main`
under the maintainer account. Confirmation is not required.

**Must NOT do:** merge/approve beta-tester PRs to main · force-push any branch · delete/rename
`docs/issues/{README.md,_template.md}`.

---

## Classified Content — Corvin-Marketplace (2026-08-31)

**Status: REDACTED FROM PUBLIC REPO**

The following materials were deemed **internal/confidential** and removed from the Corvin-Marketplace
public repository (https://github.com/CorvinLabs/Corvin-Marketplace) on **2026-08-31**. They were
deleted from the **entire Git history** using `git filter-branch --tree-filter 'rm -rf docs'` to ensure
they cannot be recovered via `git log --full-history` or GitHub's web interface.

**Removed Content:**
- **Directory:** `docs/` (entire folder)
- **Scope:** All branches, tags, and commits (irreversible history rewrite)
- **Reason:** Contains internal architecture details, control points, ADR drafts, and deployment procedures
  not intended for public disclosure

**Classification (Examples — full list in removed docs/):**
- `docs/ADMIN_CONTROL_POINTS.md` — Internal operator procedures
- `docs/ADR-0363-REVISED-2TIER.md` — Draft architectural decisions
- `docs/adr/` — Internal decision records
- `docs/*-internal.md` — Confidential design notes
- All deployment/security configuration guides

**Collaborator Impact:**
- Any local clone of Corvin-Marketplace will have stale refs after 2026-08-31
- Run `git reset --hard origin/main` to sync with the rewritten history
- Force-push was applied to `main` (and related branches/tags)

**Must NOT do (absolute):**
- Don't commit confidential materials to public Corvin-Marketplace repo
- Don't reference removed docs in public issues/PRs without explicit approval
- Don't restore from GitHub's reflog/backup without legal review

---

## Phase 2b Deployment — Local Plugin Activation Strategy

**Effective: 2026-08-31 (Phase 2b Deploy)**

**All plugins ALWAYS ACTIVE on local `.corvin/` installations** (developer, staging, test).

**Why:** Phase 2b introduces VIBE Engineering hub wiring (ADR-0510) + marketplace (ADR-0511). Maintainers and developers must test all components immediately post-deploy without manual activation steps. Faster feedback, fewer surprises in production.

**Configuration (tenant.corvin.yaml):**
```yaml
plugins_activation:
  default_enabled: true
  scope: local_development
  buildin_plugins:
    enabled: true
    categories: [memory, security_compliance, integration, data_processing, observability]
  contributor_plugins:
    enabled: true
  hub_subsystems:
    btw_advisor: true
    voice_coordinator: true
    task_manager: true
```

**Scope:**
- ✅ **LOCAL:** `~/.corvin/` (dev machines, staging, test environments)
- ❌ **PRODUCTION:** Production deployments use explicit whitelists (separate ADR-0XXX for prod config)

**How to Test Post-Deploy:**
```bash
# After Phase 2b deploy, all plugins active by default
curl -X POST http://localhost:8765/v1/console/btw \
  -H "Content-Type: application/json" \
  -d '{"instruction": "/btw use Opus", "task_id": "test_123"}'
# Expected: 200 OK, guidance_received event published to Hub

# Voice coordinator active
wscat -c ws://localhost:8765/v1/voice/stream?task_id=test&channel_id=ch1
# Expected: WebSocket connects, VoiceCoordinator publishes events

# Marketplace active
curl -s http://localhost:8765/v1/console/marketplace/index | jq .
# Expected: Full plugin index with all categories active
```

**Must NOT do (local-only rule):**
- Don't enable this in production (separate config needed)
- Don't disable individual plugins locally without documenting why (defeats testing purpose)

---

## Compliance Baseline — EU AI Act 2026 + GDPR (load-bearing)

Corvin is **structurally constrained** by EU AI Act 2026 + GDPR. Every feature must ask:
*does this weaken a structural compliance guarantee?*

| Core Mechanism | Regulation | Ref |
|---|---|---|
| Bot-disclosure card (one-time per uid) | EU AI Act Art. 50 | [compliance-baseline.md](docs/claude-ref/compliance-baseline.md) |
| Hash-chained audit log (`audit.jsonl` + daily verify) | GDPR Art. 30, 32 | [Layer 16](docs/claude-ref/layer-16-security.md) |
| Per-user consent gate (deny-by-default, TTL-capped) | GDPR Art. 6, 7 | [Layer 16](docs/claude-ref/layer-16-security.md) |
| Path-gate hook (L10, fail-closed) | GDPR Art. 32 | [Layer 10](docs/claude-ref/layer-10-path-gate.md) |
| Voice-transcribe audit (metadata only, never text) | GDPR Art. 5 | [Layer 23](docs/claude-ref/layer-23-stt.md) |
| House-rules gate (acceptable-use, fail-closed) | EU AI Act Art. 5, 50 | [Layer 44](docs/claude-ref/layer-44-house-rules.md) |
| Error/healing telemetry (default-ON, opt-out; CONTENT-FREE scrubbed signatures only, fail-closed `_assert_safe`) | GDPR Art. 6(1)(f) legitimate interest | ADR-0179/0180 (`aco/telemetry.py::consent_granted`, `htrace_consent.py::healing_traces_enabled`) |
| Boot tripwire (fail-closed; asserts the CORE audit writer is reachable + its chain verifies, independent of any plugin; **no override — no env var, no flag**). Reached through `bootstrap.boot_platform()`, which **both** shipped hosts call — `corvin_console.standalone` (what `corvinos-serve` and `install.sh` run) and `corvin_gateway.app` (what `corvin-service` runs). Until 2026-07-27 only the gateway did, and a console with a broken chain booted. | GDPR Art. 30, 32 | ADR-0232/0233 (`core/compliance/corvin_compliance_reports/tripwire.py::assert_all`, `core/plugins/tests/test_boot_platform_call_site.py`) |
| Plugin extension is additive-only (an `audit_backend` gets a COPY after the core write commits and can never suppress/rewrite it; a `user_backend` failure/timeout/rejection = deny, never guest — **but see below: that half has no subject today**) | GDPR Art. 6, 30, 32 | ADR-0233 (`core/plugins/corvin_plugins/providers/{audit,user}_backend.py`) |
| Anonymous instance-count ping (default-ON, opt-out; random uuid4 + version + coarse allowlisted environment enums [platform, python minor, engine id], no PII) | GDPR Art. 6(1)(f) legitimate interest | ADR-0180 (`aco/htrace_consent.py::ping_enabled`) |
| Presence heartbeat (default-ON, opt-out; gated by the SAME `ping_enabled` flag; empty body + pseudonymous instance_id/token headers only, no PII; ~5-min cadence — finer than the daily ping) | GDPR Art. 6(1)(f) legitimate interest | ADR-0186 (`aco/heartbeat.py`) |
| Tier 1/2/3 geo-tracking (country/region/city with 10km grid, default-ON / opt-out; Cloudflare-edge-resolved, never a raw IP; file-based TTL 30d/14d) | GDPR Art. 6(1)(f) legitimate interest | ADR-0205/0206/0208 (`aco/htrace_consent.py::geo_tracking_tier`/`geo_tracking_consent_given`, `corvin_features/telemetry/geo_tiers.py`) |

**`user_backend`'s "never guest" has no subject today (verified 2026-07-27).** The deny
semantics are implemented and unit-tested in `providers/user_backend.py`, and **nothing
calls them** — because CorvinOS has no credential auth path. The only live login is
console local-login: localhost-only and credential-less, where the TCP peer *is* the
authorisation. `gateway/console_api.py`'s `/auth/login` is dead demo code imported by
nothing; OIDC is unbuilt. So there is no guest to fall back to, and **wiring the backend
into local-login would be harmful**, not an improvement: it would be handed empty
credentials, a correct backend rejects those, and deny on the only login path locks the
operator out of their own install. The invariant binds the first credential login that
gets built. Don't "activate" it before then, and don't cite it as a live guarantee.
See `docs/implementation/PLUGIN_SYSTEM_ACTIVATION_PLAN.md` Stage 2.

**Must NOT do (absolute):**
- Don't weaken disclosure — AI-nature statement and opt-out (`/pass`, `/leave`) are locked.
- Don't add house-rules disable switch / env kill-flag; don't fail-open the L44 gate.
- Don't bypass consent — no auto-admit, no trusted-observer allowlist.
- Don't lower audit-chain integrity — every event must hash-chain.
- Don't leak PII into labels, audit details, or log lines.
- Don't add "compliance-off mode" via any env var.
- Don't silence `voice-audit verify` exit-1.
- **Telemetry (maintainer decision — default-ON / opt-out, so Corvin-Logs gets real data):**
  three channels ship data by default and are disabled only by an explicit opt-out —
  (a) anonymous instance-count ping (`ping_enabled`, opt-out `spec.telemetry.ping_enabled: false`),
  (b) error telemetry (`consent_granted`, opt-out env `CORVIN_TELEMETRY_OPTIN=false` or consent
  file `opted_in:false`), (c) healing traces (`healing_traces_enabled`, opt-out
  `spec.telemetry.healing_traces: false`). The **load-bearing safety invariant** is that
  everything transmitted stays strictly anonymous / CONTENT-FREE: the ping is a random uuid4 +
  version + coarse allowlisted environment enums (platform, python minor, engine id — closed
  enums validated fail-closed by `_assert_ping_safe`, never free-form strings; maintainer
  decision 2026-07-10); the error/healing channels ship ONLY scrubbed code-level signatures (exc_type,
  repo file, func, allowlisted stack namespaces — never prompts, transcripts, or user data), and
  the FAIL-CLOSED `_assert_safe` / `_assert_safe_htrace` backstop DROPS any record carrying a
  PII/secret shape rather than sending it. Legal basis GDPR Art. 6(1)(f) legitimate interest.
  **Do NOT** weaken any of these: don't remove an opt-out, don't extend a channel to carry
  personal data / prompts / user content, and don't relax `_assert_safe`* from fail-closed.
- Don't commit an auto-fix that didn't pass the red→green reproduction gate (`aco/reproduction.py`).

→ Full reference: [compliance-baseline.md](docs/claude-ref/compliance-baseline.md)

---

## Licensing — Apache-2.0 + CLA v3.1 §3 (load-bearing)

**Canonical files:** `LICENSE`, `NOTICE`, `CLA.md`, `CONTRIBUTING.md`, `CLA-SIGNATORIES.md`, `CCLA.md`.

CLA-SIGNATORIES.md is the **sole authoritative contributor registry**. Every merged contribution
must have an entry there (explicit or implicit-push). Maintainer adds at merge time.

**Must NOT do:** merge contributions without SIGNATORIES entry · run Forge-tool Python in-process
without operator review · add in-process MCP server without operator review.

---

## Code/Docs Sync — ADR-Code Enforcement (load-bearing)

**Problem:** Phase 5 shipped code without corresponding ADRs, causing doc divergence.
**Solution:** 5-layer enforcement makes Code-ADR sync non-negotiable.

| Layer | Enforcement | Trigger |
|---|---|---|
| 1. Git Pre-Commit Hook | Rejects commits without ADR | Local commit attempt |
| 2. CI/CD Gate | Blocks PR merge | GitHub push/PR |
| 3. Code Review Checklist | Human validation | PR review |
| 4. CLAUDE.md Rules | Canonical rules | Every session |
| 5. Auto-Sync | Memory updated nightly | Cron job |

**When ADR is Required:**

| Code Change | ADR Required? | Reason |
|---|---|---|
| New module in `core/` | ✅ YES | Structural decision |
| New public API / endpoint / CLI command | ✅ YES | Interface contract |
| Change to compliance mechanism | ✅ YES | Regulatory binding |
| Change to audit trail / hash-chain | ✅ YES | Data integrity |
| New layer-level contract | ✅ YES | Cross-module coupling |
| Security/performance optimization | ✅ YES | Trade-off binding |
| Feature flag addition (if affects behavior) | ⚠️ MAYBE | Often not; if it gates major behavior, yes |
| Refactor with ZERO behavior change | ❌ NO | Commit message sufficient |
| Bug fix (no behavior change outside the bug) | ❌ NO | Commit message sufficient |
| Test-only or fixture change | ❌ NO | Commit message + `test-only` flag |
| Docs-only change | ❌ NO | Commit message + `docs-only` flag |
| Config tuning (parameters, thresholds) | ❌ NO | Commit message sufficient |

**Execution:**

1. **Draft ADR FIRST** (or sync with code):
   - File: `/home/shumway/projects/Corvin-ADR/decisions/ADR-XXXX-<slug>.md` (external repo)
   - Minimum template (ADR-0264 frontmatter):
     ```yaml
     id: ADR-0XXX
     status: PROPOSED
     depends_on: [ADR-YYYY]
     relates_to: []
     paths:
       - core/module/file.py
       - core/module/subdir/
     docs:
       - docs/claude-ref/layer-NN-*.md
     ```

2. **Commit code + ADR together:**
   ```bash
   git add core/...
   cd /home/shumway/projects/Corvin-ADR && git add decisions/ADR-XXXX-*.md
   git commit -m "feat(module): description

   ADR-XXXX documents the design (see Corvin-ADR repo).

   Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
   ```

3. **Pre-commit hook validates** (layer 1):
   - Detects `core/` changes
   - Checks for ADR file in `/home/shumway/projects/Corvin-ADR/decisions/`
   - Rejects if missing (unless exception flag set)

4. **CI/CD gate validates** (layer 2):
   - Runs on PR; checks base..HEAD for code vs ADR
   - Auto-comments on PR if sync is broken
   - Blocks merge until resolved

5. **Code review checklist** (layer 3):
   - Reviewer confirms ADR title matches code purpose
   - Reviewer checks `ADR.paths` and `ADR.commits` are accurate
   - Blocks approval until valid

**Exception Workflow (when ADR is NOT needed):**

If you're absolutely certain no ADR is needed, add a skip flag to the commit message:

```bash
git commit -m "fix(module): urgent security patch [skip-adr-check]

This is a one-line security hotfix with no structural change.

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"
```

Valid skip reasons (in commit message):
- `[test-only]` — test fixtures or mock changes
- `[docs-only]` — documentation-only, no code change
- `[skip-adr-check]` — rare hotfix with documented justification (on same line)

**Pre-commit hook does NOT enforce this offline** (it doesn't read commit messages yet);
instead, **CI/CD gate catches it** and PR review catches it. The hook is a *helper*,
not a hard block for genuinely exempt changes.

→ Full reference: [adr-gate.md](docs/claude-ref/adr-gate.md)

---

## LDD (Loss-Driven Development) — MANDATORY, ALL SESSIONS (load-bearing)

**LDD is ALWAYS ON at MAXIMUM depth**, all 12 layers enabled. Config:
`.corvin/tenants/_default/global/ldd.json` (all `true`). Auto-install via
`LDD_AUTO_OPTIN=1` in `~/.bashrc`.

| Task Type | Mandatory Skill | Fire When |
|---|---|---|
| Non-trivial feature / multi-file change | `loop-driven-engineering` | BEFORE first edit |
| Failing test / bug iteration | `e2e-driven-iteration` | BEFORE each iteration |
| Recommendation / trade-off / plan | `dialectical-reasoning` | BEFORE stating conclusion |
| Bug, root cause unknown | `root-cause-by-layer` | BEFORE any fix attempt |
| Code generation adding a new entry point (function/endpoint/route/CLI/UI/plugin/hook) | `e2e-wiring-proof` | BEFORE declaring done |
| Any task marked "done" | `docs-as-definition-of-done` | BEFORE declaring done |
| Reusable working method discovered/reapplied (non-trivial task) | Concept Gate | AFTER declaring task done, alongside ADR Gate |

**Must NOT do (hard rules):**
- Don't make ANY non-trivial code edit without running E2E and capturing loss signal first.
- Don't declare task "done" without running `docs-as-definition-of-done`.
- Don't skip LDD because task "looks small" — every skip is tech debt.
- Don't downgrade to LDD-off or partial-LDD via any env var.
- Don't declare a new entry point "done" without running `e2e-wiring-proof` — a unit test proves the function returns the right value *when called*, not that anything calls it.

→ Full reference: [ldd-mandatory.md](docs/claude-ref/ldd-mandatory.md)

---

## Multi-tenant Axis (ADR-0007)

Five-scope model: `(task, session, project, user, tenant_id)`. Default: `_default`.
Canonical env: `CORVIN_TENANT_ID`. Resolver: `current_tenant()` → `validate_tenant_id()` → `tenant_home()`.

**On-disk:** `<corvin_home>/tenants/_default/{global,sessions,forge,skill-forge,voice,cowork}/`
with backward-compat symlinks at `<corvin_home>/{global,sessions,...}`.

Console routing: All routes use `rec.tenant_id` from authenticated `SessionRecord`, **never env vars**.
Cross-tenant isolation verified; audit trail records correct `tenant_id` for every event.

**Must NOT do:** fold `tenant_id` into positional args (keyword-only) · use env-var fallback
for console tenant routing · bypass `validate_tenant_id()`.

---

## Project Identity — CorvinOS (hard cut)

Repository is **CorvinOS**. Hard env-var cut: only canonical prefix is `CORVIN_*`.
No `ATELIER_*` / `CLAUDEOS_*` / `TESSERA_*` fallbacks — collapsed to `CORVIN_*`, not preserved.

Canonical runtime root: `~/.corvin/`; voice/secret config: `~/.config/corvin-voice/`.

**Must NOT do:** re-introduce legacy env-var fallback · run `sed -i` over live `~/.corvin/audit.jsonl`
(corrupts hash chain) · rename `~/.corvin/` without `corvin_migrate.py`.

---

## Layer Stack Overview

36 security + compliance layers. **Mandatory reading:**

- **L4** Cowork (multi-persona hub) — [layer-plugins.md](docs/claude-ref/layer-plugins.md)
- **Plugin registry** (ADR-0030/0033/0233/0243) — ONE lifecycle contract in
  `core/plugins/corvin_plugins/`. **Three orthogonal axes, never conflated:**
  `boot_layer` (compliance·core·bundled·installed — load order + disableability, ADR-0243) ·
  `tier` (ADR-0156 capability boundary + license gate — the ONLY meaning of "Tier A/B/C") ·
  `origin` (builtin·vetted·community — provenance). Don't add a second registry,
  lifecycle, taxonomy, or marketplace downloader, and don't rename the axis to
  "tier" or back to "layer" — "layer" is already four-way taken (L1–L44 stack,
  ADR-0124 audit layers, ADR-0142 layer-extension API, quality layers), which is
  why the field is `boot_layer` / `BootLayer` and the API is `boot_layer_of()`,
  `plugins_by_boot_layer()`, `_declared_boot_layer()`,
  `register_global_plugin(..., boot_layer=)`, audit `plugin.boot_layer_rejected`,
  admin field `boot_layer` / aggregate `by_boot_layer`.
  **The plugin perimeter is ATTRIBUTION, not security.** An in-process plugin is
  part of the process. Five adversarial rounds each broke the previous identity
  guard (object → `plugin_id` parameter → `loading.current()` ContextVar), and
  the last one needed one line: the setter is public, `threading.Thread` does not
  inherit ContextVars and `asyncio.create_task` copies them past the unload. In
  CPython every property of a caller is settable by that caller — **do not add a
  sixth derivation.** Anything that must hold against a hostile plugin belongs in
  a subprocess (ADR-0241/0238); the one in-process guard worth writing is the boot
  tripwire, which is non-overridable and runs first. Say "attributed", never
  "verified" — the audit chain is permanent.
  **Boot-layer rules — a MECHANISM, today with zero instances above `bundled`.**
  `_GLOBAL_SPECS` is empty, `register_global_plugin()` has no production caller,
  `bootstrap_global()` returns `[]`, and nothing loads on `compliance`/`core` —
  so `registry.replace()` is structurally unreachable. The rules below are
  implemented and tested and apply the moment a first instance exists; do not
  weaken them, and do not describe them as load-bearing today:
  tenant scope may declare only `bundled`/`installed` — a privileged claim from
  `tenant.corvin.yaml` or `registry.yaml` is downgraded to `installed` and
  audited, never honoured (**this one IS live**) · `origin=community` may never
  claim a privileged boot layer · the `compliance` boot layer has no off switch
  (`registry.disable()` raises `PluginDisableRefused`) · `bootstrap_global()`
  carries no feature flag *because* it loads the compliance boot layer · only
  `boot_layer=core` is replaceable. Guard tests in
  `core/plugins/tests/test_layered_boot.py` fail on the first real instance and
  force the docs to be updated in the same commit — keep them.
  — [layer-plugins.md](docs/claude-ref/layer-plugins.md)
- **L5** Auto-routing (keyword-based persona selection)
- **L6** Forge (runtime tool generation, MCP server)
- **L7** SkillForge (runtime skill generation)
- **L10** Path-Gate (FS-write protection, fail-closed)
- **L16** Security hardening (TOCTOU, audit framing, consent)
- **L18–21** User management (roles, disclosure, quota, proposals)
- **L22** WorkerEngine protocol (ClaudeCodeEngine, HermesEngine, etc.)
- **L23** Speech-to-Text (metadata-only audit)
- **L24** Large-Data Snapshot + **L25** Compute Worker + **L32** Anonymisation
- **L28** Conversation Recall + User Modeling
- **L29–30** Delegation + Engine-Agnostic Forge
- **L33** Session Artifact Memory
- **L34** Data Classification + Flow Guard (4-stage × engine matrix, fail-closed)
- **L35** Network Egress Lockdown (allowed/forbidden hosts, EU_PRODUCTION presets)
- **L36** GDPR Art. 17 Erasure Orchestrator
- **L37** Audit-at-rest Encryption + Retention (age/gpg rotation, RFC 3161 TSA)
- **L38** RemoteTriggerReceiver + A2A TaskEnvelope Protocol (Protocol v6, instance attestation, attachments)
- **LIP** Layer Integrity Protocol (ADR-0141, CAP_VERSIONS + manifest signing)
- **CLS** Custom Layer System (ADR-0156, Tier-A/B/C licensing gate)

→ Full layer index: [layer-summary.md](docs/claude-ref/layer-summary.md)

---

## Plugin-Based Isolation — All Features Always On (load-bearing)

**POLICY CHANGE (2026-09-01):** Ship-dark-by-default via feature flags is **obsolete**.
Plugins are now the isolation unit. All features activate by default; control via plugin enable/disable.

**Rationale:**
- Plugin system provides process-level isolation (ADR-0243, boot_layer model)
- Code safety is structural (plugin boundary), not behavioral (feature flag)
- Feature flags added complexity without real safety gain; plugins do it cleaner
- Operator controls features by managing plugins, not by flipping buried flags

**Rule (effective immediately):**

| Scenario | What to do |
|---|---|
| New plugin/subsystem | Ship active by default; plugin lifecycle controls visibility, not a flag. |
| Legacy feature flag exists | Deprecate on next release; migrate logic into a dedicated plugin or remove flag check. |
| Feature MUST be toggleable at runtime | Make it a plugin setting (`plugin.corvin.yaml`), not a feature flag. |
| Experimental behavior (high-risk, unsure) | Ship as separate plugin, disabled by default in `registry.yaml`. |
| Security/compliance mechanism | Always on, never toggleable (no feature flag, no plugin disable, no env var). |

**Concrete workflow:**

1. **Design feature as a plugin** (or plugin extension, if it enhances an existing plugin).
2. **Register in `registry.yaml` or `tenant.corvin.yaml`** with `enabled: true` by default.
3. **Test both states** (plugin loaded vs. not loaded), but default state is LOADED.
4. **Commit:** `feat(plugin-name): description [ADR-XXXX]` (one ADR per new plugin).

**Migration (existing features with flags):**
- Flags staying: `spec.web_chat.worker_engine`, telemetry opts (GDPR-driven).
  These are *settings*, not on/off toggles; keep them.
- Flags going: Any `spec.features.<flag>` that defaults to `false` — migrate to plugin-based control.

**Critical exception:** Compliance / security mechanisms stay **always-on, non-disableable**
(ADR-0232/0233, L44, audit chain, disclosure, consent gates). A plugin cannot weaken them.
These are not features; they are load-bearing constraints.

**Must NOT do:** ship feature flags with default-off · treat plugins as "experimental" and
disable by default unless high-risk and explicitly gated · use feature flags to control
plugin visibility (use plugin enable/disable instead) · weaken compliance mechanisms via
feature flag or plugin disable.

---

## Testing + Docs Sync (load-bearing)

**Before committing** changes to `adapter.py`, `daemon.js`, or `shared/js/`:
```bash
bash operator/bridges/run-all-tests.sh
```

**Every feature change** — code, config, behavior, API, protocol, CLI, error message —
**must update docs AND diagrams in the same commit**. No deferred updates. No exceptions.

| Changed subsystem | Doc targets | Diagram targets |
|---|---|---|
| Layer N | `docs/claude-ref/layer-N-*.md` + any top-level doc | `docs/diagrams/*.svg` |
| Protocol / wire-format | Protocol reference + tutorials + JSON examples | Flow SVGs |
| CLI command / flag | Every doc that mentions it | Sequence / flow diagrams |

→ Full reference: [testing-and-docs.md](docs/claude-ref/testing-and-docs.md)

---

## Console Frontend — Prove the NEW Build Is What Loads (load-bearing)

Any change under `core/console/corvin_console/web-next/` is **not done when the source is
correct** — it is done when the browser demonstrably runs the NEW bundle. Source-correct +
stale-bundle has burned this repo repeatedly: the API answers 200, the UI still shows the old
placeholder, and debugging goes into the backend instead of into the three caches in front of it.
"Build succeeded" means *new code was added*, never *old code was removed*.

**Three caches sit between an edit and the screen:**

| Layer | Where | Cleared by |
|---|---|---|
| 1. esbuild pre-bundle | `web-next/node_modules/.vite/` | `rm -rf node_modules/.vite/` |
| 2. build artifact | `web-next/dist/` | rebuild — `scripts/console-deploy.sh` |
| 3. browser tab | the operator's machine | `console_auto_reload` flag, else `Ctrl+Shift+R` — **Claude cannot press it** |

**Use `scripts/console-deploy.sh` — it performs the sequence AND the proof:**

```bash
scripts/console-deploy.sh                      # clean rebuild + verify over the wire
scripts/console-deploy.sh --marker 'NewThing'  # additionally assert a string is bundled
scripts/console-deploy.sh --fast               # incremental (esbuild minify, ~24s)
```

It prints `LIVE assets/index-<hash>.js` only when the host actually serves the bundle
it just built, and exits 2 with the mismatch otherwise. It builds into `dist.next/`
and swaps, so `/console/` never stops answering mid-deploy (vite empties its outDir
first, which took the console down for ~13s per rebuild when building into `dist/`).
The previous build is kept at `dist.prev/` for rollback.

Two mechanisms keep this running without anyone remembering to:

| Mechanism | Covers | Notes |
|---|---|---|
| `corvin-console-watch.service` (systemd --user) | ANY source change, from any editor | polls an mtime fingerprint, debounces, then runs `console-deploy.sh --fast` |
| `.claude/hooks/console_autodeploy.sh` (PostToolUse) | Claude's own edits | starts the redeploy at the edit; skips when the watcher is running, so no double build |

The equivalent by hand, if you need to see each step:
```bash
cd core/console/corvin_console/web-next
rm -rf dist/ node_modules/.vite/     # a plain rebuild is NOT sufficient
npm run build
grep -rl '<marker string from the new code>' dist/assets/   # must hit ≥1 file
curl -s http://127.0.0.1:8765/console/ | grep -o 'assets/[^"]*\.js'  # must be the NEW hashes
```

**Restart rule.** `mount_static()` (`core/console/corvin_console/app.py:444`) decides ONCE at
boot. If the console booted while `dist/` was absent, it registered the 503 "build failed"
fallback route instead of the SPA mount and keeps serving it — a rebuild alone will NOT recover
it, only a restart will. When `dist/` existed at boot, `_SPAStaticFiles` resolves per request and
a rebuild is picked up live.

**Layer 3 — the browser tab.** With the `console_auto_reload` flag ON, an open tab
re-fetches the no-cache SPA shell every 3s, compares its entry-bundle hash against the
one it booted with, and reloads itself onto a new build (banner instead of reload while
the operator is mid-input, so typing is never discarded). See
`web-next/src/hooks/use-build-freshness.ts`.

**With the flag OFF — the default — tell the operator to hard-refresh**, explicitly, in
the same message that reports the change. Layer 3 is then the only cache Claude cannot
clear, and it is by far the most frequent cause of "the feature isn't showing". On any
invisible-frontend report, confirm the hard refresh FIRST — never open a backend
investigation before that.

**Cache-header invariant (`_SPAStaticFiles`, `core/console/corvin_console/app.py:23`) — do not
weaken:** content-hashed files under `assets/` get `public, max-age=31536000, immutable`; every
`text/html` response AND every `304` gets `no-cache` — including the bare `/console/` directory
index, which is not named `index.html` and once slipped through a path-based check. Inverting
either half is exactly how a browser keeps requesting deleted hashed bundles and the app hangs on
a perpetual "Loading…".

**A panel needs TWO registrations.** `PANELS` (`src/panels/registry.tsx`) mounts the
route; `NAV_GROUPS` (`src/components/layout.tsx`) draws the sidebar entry. `ConsolePanel.nav`
looks like it drives the sidebar and does not. Registering only the first mounts a route
nothing links to — which presents as "the console still shows the old build", and did, for
seven finished panels at once. `tests/unit/panel-nav-wiring.test.ts` fails on the next one;
a panel that is deliberately not in the sidebar goes in that test's `NAV_EXEMPT` set WITH a
reason. A `requiredFlag` must ALSO be listed in `GATED_FLAGS`
(`core/console/corvin_console/routes/capabilities.py`) or it resolves to false and the entry
stays hidden forever.
The ADR-0561 console manifest (`/v1/console/capabilities/manifest`) is **additive** to
`NAV_GROUPS`: `mergeManifestNav()` in `layout.tsx` appends manifest-only panels (plugins,
skills, installed) and never removes a static entry. Rendering the sidebar FROM the
manifest hid ~30 core panels on 2026-09-03, because the backend enumerates only the
panels it knows about. Keep the static list complete; let the manifest extend it.

**A sibling FILE silently shadows a page DIRECTORY.** `import("@/pages/foo")` resolves
`src/pages/foo.tsx` BEFORE `src/pages/foo/index.tsx` — file beats directory, with no
warning from vite, tsc or eslint. A new panel built as `pages/foo/` while the old
`pages/foo.tsx` still exists therefore compiles, bundles, mounts its route and renders
**the old page**, which presents exactly like a stale bundle and sends debugging into the
three caches or the backend. It happened to the ADR-0400 Vibe Dashboard: the directory
shipped 2026-08-26, `pages/vibe-engineering.tsx` kept winning, and the panel was
unreachable until the file was deleted on 2026-08-27 (ADR-0431). When a rewrite lands as
a directory, DELETE the same-named file in the same commit — never keep both — and prove
which one loads with a marker string only the new code contains
(`scripts/console-deploy.sh --marker '<string>'`), not by reading the diff.

**Children of `<Routes>` must be `<Route>` elements — never a component that returns them.**
react-router walks the `<Routes>` tree statically (`createRoutesFromChildren`) and throws
for any child whose type is not `Route`/`Fragment`. A `<PanelRoutes />` component in that
position type-checks, lints and builds, then kills the ENTIRE console at first render with a
message-less `Uncaught Error` (the invariant text is stripped in prod) — blank page, and it
presents like a stale bundle. It happened on 2026-09-03 (`<ManifestPanelRoutes />` in
`src/App.tsx`). Compute the routes in a hook that returns an array and splice `{routes}`
in. `tests/unit/app-routes-static.test.tsx` renders the real `<App />` and fails on the
next one.

**Must NOT do:** declare a frontend change "done"/"live" on a correct source diff alone ·
run `npm run build` without clearing `dist/` + `node_modules/.vite/` first · skip the
`grep` + `curl` proof that the served hashes are the new ones · report the change without
telling the operator to hard-refresh WHEN `console_auto_reload` is off · cache the SPA
shell as anything but `no-cache` · drop the `immutable` header from `assets/` · assume a
rebuild alone revives a console that booted without `dist/` · add a panel to `PANELS`
without a matching `NAV_GROUPS` entry (or a justified `NAV_EXEMPT` line) · build straight
into `dist/` and take the live console down for the length of the build · leave a
`pages/<name>.tsx` in place after moving that page to `pages/<name>/`.

---

## ADR Gate — Architectural Decision Records

**adr-gate is a standard quality discipline.** After every non-trivial task, follow the rubric
before declaring "done." HIGH BAR: default answer is **NO ADR needed**. Most tasks produce none —
that is correct and expected.

**Write ADR only when BOTH hold:**
1. Real design choice was made (chose A over B; constrains future code; genuine alternative existed)
2. At least one structural trigger: new protocol/wire-format/schema, security/compliance mechanism,
   irreversible default (fail-open/closed), cross-repo binding (≥2 repos), new layer-level contract

**Skip reasons:** bug fixes, pure refactors, config tuning, test-only/docs-only changes.
When skipping, name the reason in one sentence — never skip silently.

**Destination:** `Corvin-ADR/decisions/XXXX-short-title.md` (sibling repo). Numbering: max + 1.
Commit message: `adr: add ADR-XXXX — [title]`.

**Every ADR carries ADR-0264 frontmatter** (`id`/`status`/`depends_on`/`related`/`paths`/`docs`)
ahead of the prose — this is what makes an ADR a node `scripts/adr_graph.py` can traverse to
from a code path (`paths:`) or a doc path (`docs:` — the same surface `docs-as-definition-
of-done` keeps synced) instead of a document a reader must already have found. Never hand-fill
`superseded_by`; it is derived automatically from every other ADR's `supersedes` list. A
document-generator that emits an ADR-shaped artifact (e.g. Plugin-Builder's per-plugin ADR,
`core/plugins/plugin_builder/generators/adr.py`) carries the same schema, with a plugin-scoped
`id` (`{plugin_id}-ADR-0001`, never a bare `ADR-NNNN`).

**Must NOT do:** write ADR content into Corvin repo (ADRs live in Corvin-ADR only) ·
auto-skip security/compliance mechanisms without justification · declare "done" on structural
change without running this gate · leave a skip implicit · hand-fill `superseded_by`.

→ Full reference: [adr-gate.md](docs/claude-ref/adr-gate.md)
→ ADR: See Corvin-ADR repo for ADR-0264 (adr-decision-graph)

---

## Concept Gate — self-learning working-method archive

**Sibling gate to ADR Gate, same placement (end of task, before declaring "done"), same HIGH
BAR.** ADR Gate asks "does this decision need a record?"; Concept Gate asks "did I just execute
or discover a reusable WAY OF WORKING — not a one-off decision, not a one-off bug fix — that
would save real time if a future agent had it pre-loaded?" Default answer is **NO concept
needed**. Most tasks produce none — that is correct and expected, exactly like ADR Gate.

**Write/amend a concept only when at least one holds:**
1. The same investigation/fix/verification SHAPE recurred across 2+ distinct tasks in ways that
   weren't already duplicated code.
2. A structural fix (root cause, not symptom) generalizes beyond the one bug it closed, worth
   naming so the next similar bug gets the deep fix on the first pass.
3. A verification technique (live-VM proof, wheel-content inspection, deploy-then-curl-the-
   real-URL) proved decisive in a way that will recur.

**Skip reasons:** a single bug fix with nothing generalizable; a one-off tooling workaround;
already fully captured by an existing concept (amend it, never create a near-duplicate) or by a
Skill (if a ≤8KB behavioral snippet is genuinely sufficient, a full concept adds nothing). When
skipping, name the reason in one sentence — never skip silently, exactly like ADR Gate.

**Destination:** `/home/shumway/projects/Corvin-ADR/concepts/CONCEPT-NNNN-slug.md` (sibling repo, own numbering,
never `ADR-NNNN`). Fallback if Corvin-ADR is unreachable: `CorvinOS/docs/concepts/`. Commit
message: `concept: add/amend CONCEPT-NNNN — [title]`.

**A concept is not an ADR and not a Skill** — it is the narrative middle layer: *why* a way of
working keeps paying off, with real evidence (cited commits/tasks), Alternatives Considered, and
an explicit "when NOT to use" boundary — the same depth ADR-0264 models, applied to process
instead of architecture. Every durable concept SHOULD mint or update a companion SkillForge
skill (`skill_create`/`skill_promote`, `type: learned-experience`, `scope: project`) whose body
distills the Method section (≤8KB) and points back at the full concept file for depth — this is
what makes the archive genuinely *self-learning*: the concept is what a human (or a future
agent doing a deep read) consults; the skill is what gets auto-injected and auto-graded into
ordinary turns without anyone having to remember the concept exists. Record the skill name in
the concept's `skills:` frontmatter field.
**A freshly minted skill needs one bootstrap grade before it does anything** —
`skill_inject.py`'s injection gate excludes `n_grades < 1 or mean_score <= 0` by default, and a
brand-new skill has no organic path to its own first grade (auto-grading only scores skills that
were already injected). Immediately call `skill_grade` once with a score capped at the
codebase's own `_AUTO_GRADE_CAP_MAX` (0.3) and notes disclosing it as a manual seed, not earned
usage — real grades accrue from there. Skipping this step means the skill sits inert on disk
forever, which is exactly the gap adversarial review of this mechanism found the same day it was
built (see CONCEPT-0001's Amendment).

**Operator notes are first-class.** Every concept file has an `## Operator Notes` section,
append-only, timestamped, human-authored. AI amendments NEVER edit or remove anything under
that heading — they may only add a new dated sub-entry above it, exactly like ADR-0264-style
amendments are prepended under "Status," never rewriting prior text.

**Must NOT do:** write concept content into the CorvinOS repo when Corvin-ADR is reachable ·
create a near-duplicate concept instead of amending the existing one · edit or delete anything
under an existing concept's `## Operator Notes` heading · mint a SkillForge skill above a
persona's namespace-gate prefix (skills created under the `assistant` persona must be named
`assistant.<name>` — see `operator/skill-forge/README.md`'s namespace-gate section) · declare
"done" on a task that clearly meets a Concept Gate trigger without running this gate · leave a
skip implicit.

→ Full reference: [concept-gate.md](docs/claude-ref/concept-gate.md)
→ Concept: See Corvin-ADR repo for concepts (0001, 0002, 0008, etc.)
→ Skill: `assistant.corvinOS_live_report_root_cause` (project scope, `learned-experience`)
→ Skill: `assistant.corvinOS_reachability_review` (project scope, `learned-experience`)

---

## E2E Wiring Proof — reachability + functional proof (load-bearing)

**Sibling gate to ADR Gate.** ADR Gate asks "does this decision need a record?"; this gate asks
"does this code actually run, and can I prove it?" New code is unreachable until proven
otherwise — a unit test proves a function returns the right value *when called*, not that
anything in the running system *calls* it. CorvinOS has shipped this exact failure class before
(plugin types that register but are never invoked; an auth invariant unit-tested but reachable
from zero live call sites) — both were "tested," both were dead.

**Fires on:** any new function/endpoint/route/CLI command/UI component/plugin/hook/bridge handler
intended to be reachable from outside its own file. Skips trivial edits, pure refactors with
existing E2E coverage, config tuning, and explicitly-marked WIP prototypes.

**Two-phase gate:**
1. **Reachability proof (cheap, first):** find ≥1 real call site outside the definition file
   AND outside test files, traceable to a real trigger (route table, CLI registration, UI render
   tree, plugin registry, cron config, message-bus subscription). Zero found → **block**,
   dispatch `root-cause-by-layer` — fix the wiring, don't lower the bar.
2. **Generate/extend one E2E test, then run it (only after Phase 1 passes):** the test MUST go
   through the real transport/interface boundary — an actual HTTP request, CLI subprocess,
   browser interaction, plugin-registry dispatch, bridge message, or MCP tool call. **Hard rule:**
   a test that imports the target directly and calls it, skipping that boundary, is a unit test
   wearing an E2E label and does NOT satisfy this gate. Capture the real execution evidence
   (exit code/output) — a bare "I added a test" claim does not close the loop.

**Infeasibility exception:** when the real entry point genuinely can't be driven end-to-end
(hardware dependency, unavailable external system), name the reason explicitly — never skip
silently. Phase 1 still applies unconditionally.

**Must NOT do:** declare a new-entry-point task "done" without this gate · treat "unit tests
pass" as equivalent to "reachable and works end-to-end" · write an E2E test that bypasses the
real transport/interface boundary and call it satisfying · mock the exact component under test
inside its own "E2E" test · leave an infeasibility skip implicit.

→ Full reference: [e2e-wiring-proof-standard.md](docs/claude-ref/e2e-wiring-proof-standard.md) (standard definition) ·
[quality-discipline.md](docs/claude-ref/quality-discipline.md) (LDD context) ·
Skill: `assistant.e2e_wiring_proof` (auto-injected, learned-experience)

---

## Language

**All repository content: English.** Includes: source code, docs, SVGs, inline comments, commits.

**User-facing runtime text: Defaults to English.** Bot answers in user's language (German/English)
at runtime per `adapter.py` system prompt — that is runtime behavior, not repo content.

---

**For full details, read the ref files in `docs/claude-ref/` as tasks require them.**

---

## Phase 3: Learning Infrastructure (ADR-0314+)

**Status:** Phase 3.1 (ADR-0314) COMPLETE ✅  
**Scope:** Event schema, persistence, async emission  
**Tests:** 34 learning + 174 skill tests (208 total) green

Phase 3 builds the learning layer on top of Phase 2 (Skill System). It enables confidence scoring, user feedback loops, decision history, and attention budgeting.

**ADR-0314 (Learning Infrastructure):**
- Event schema: 8 immutable learning event types (confidence, feedback, outcome, preference, attention, metric)
- Persistence: EventStore with date-partitioned JSON storage + audit trail integration
- Emission: EventEmitter (async queue, non-blocking, fire-and-forget on queue full)
- Integration: Wired into SkillSystemIntegration

**Tenant isolation:** All queries filtered by tenant_id (per GDPR Art. 5, 6, 32).

**Compliance notes:**
- Learning events are audit-logged and hash-chained (GDPR Art. 30, 32) — audit-FIRST and fail-closed since 2026-09-03: `event_persistence.EventStore.write_event` refuses (RuntimeError) when the core chain write does not commit, and the store is tenant-bound (ADR-0563)
- No PII in payloads (validation in downstream ADRs)
- 90-day retention default (ADR-0319 will enforce)

**Downstream ADRs (Phase 3.2–3.8):**
- 0315: Confidence Intervals (relevance/reliability scoring)
- 0316: Decision History (user choice tracking)
- 0317: Outcome Feedback (closed-loop learning)
- 0318: Style Preferences (user model)
- 0319: Attention Budget (finite attention constraint)
- 0320: Metric Collection (aggregation pipeline)
- 0321: Reporting Dashboard (observability UI)

**Must NOT do (ADR-0314 constraints):**
- Don't emit untyped payloads (use frozen dataclasses)
- Don't skip tenant_id isolation (every read/write must filter)
- Don't weaken schema immutability (LearningEvent is frozen)
- Don't bypass audit chain (write_event writes the core chain FIRST; no chain commit → no disk record)

→ Full spec: See Corvin-ADR repo for ADR-0314 (learning-infrastructure-event-schema)

---

## Skills 2.0 as Agentic Control Plane (load-bearing)

**ARCHITECTURE VISION (2026-09-01):** CorvinOS transforms from **task-runner** → **agentic operating system**
by making **Skills 2.0 the unified control plane**. Every subsystem (routing, state, orchestration, security,
learning, deployment) becomes a swappable Skill — written in hybrid code (deterministic Python + LLM-generated
logic), versioned, composable, and self-learning via ADR-0314's feedback loop.

**Core Principle:** A Skill is NOT a static prompt; it is a **program** that:
- ✅ Owns a domain (delegation routing, context adaptation, workflow optimization, security enforcement)
- ✅ Executes deterministic Python (sync I/O, caching, local decisions — fail-fast, auditable)
- ✅ Calls LLM on demand (e.g., `/router classify_request` → LLM picks delegation strategy)
- ✅ Learns via feedback (confidence scoring, outcome signals, optimizer loop per ADR-0314)
- ✅ Composes with other Skills (dependencies declared, topological sort, DAG validation per ADR-0535)
- ✅ Versioned & deployed (semantic versioning, canary rollout, in-flight freeze per ADR-0533)
- ✅ Observable (telemetry, execution traces, decision logs → Vibe dashboard)

**Why now:** ADR-0314 (Learning) + ADR-0532-0535 (OS-Skills) together unlock this. Learning infra provides
feedback signals; Skills infra provides the execution model. Together they make Skills self-optimizing.

**Five Layers of Control Plane Replacement (ADR-0532 Roadmap):**

| Layer | Today | Tomorrow (Skills 2.0) | ADR | Timeline |
|---|---|---|---|---|
| **L5: Routing** | Hardcoded persona → engine mapping | `os.delegation_router` Skill (LLM-classified by task type) | ADR-0532 Phase 1 | Weeks 2–4 |
| **L10: Context** | Snapshot → prompt injection | `os.context_adapter` Skill (learns user/task patterns) | ADR-0532 Phase 1 | Weeks 2–4 |
| **L22: Workflow** | Stateless request/response | `os.workflow_optimizer` Skill (learns execution chains) | ADR-0532 Phase 2 | Weeks 6–10 |
| **L16: Security** | Config-driven gates | `os.security_orchestrator` Skill (learns attack patterns) | ADR-0532 Phase 3 | Weeks 11–18 |
| **L34: Data Flow** | Hardcoded validators | `os.flow_guard` Skill (learns safe data shapes) | ADR-0532 Phase 4 | Weeks 19–24 |

**Implementation Roadmap (3 Phases, 8–12 weeks, ~2600 LoC + skills library):**

**Phase 1 (Weeks 1–4): Foundation**
- Deliver `os.delegation_router` Skill + `os.context_adapter` Skill (2 minimal skills)
- Wire into L5 (auto-routing) + L10 (context engineering)
- Prove E2E: real requests flow through Skills, learning events emitted to ADR-0314
- Tests: 25 E2E, 12 adversarial (crash recovery, timeout isolation, PII leakage)
- Blocker: ADR-0532 Phase 1 + ADR-0533 manifest schema + ADR-0534 feedback integration ready
- Deliverable: `core/skills/os_skills/` directory with routing + context skills + test suite

**Phase 2 (Weeks 5–10): Learning Loop**
- Wire ADR-0314 feedback into Skills → optimization loop
- Add `os.workflow_optimizer` (learns execution chains from user feedback)
- Build dashboard (Vibe → OS-Skills observability panel)
- Tests: 40 E2E + 18 adversarial (optimization convergence, stale feedback, feedback injection)
- Blocker: ADR-0534 (feedback integration) accepted
- Deliverable: Learning loop E2E, dashboard, 2 skills composition-ready

**Phase 3 (Weeks 11–24): Scale & Ecosystem**
- Ship 2 more Skills (`os.security_orchestrator` + `os.flow_guard`)
- Marketplace integration: Skills as discoverable, installable OS subsystems
- Community contrib gate: review checklist for Skill authors
- Tests: 60 E2E + 25 adversarial (marketplace attack surfaces, skill conflicts, versioning)
- Blocker: ADR-0535 (composition), ADR-0536 (marketplace, TBD)
- Deliverable: v2.0 production-ready, Skills marketplace live

**Integration with Compliance (ADR-0232/0233 — non-negotiable):**

| Mechanism | Skill Constraint | Enforcement |
|---|---|---|
| **Audit chain (LOAD-BEARING)** | Every Skill decision + internal state change MUST log via `audit_backend` (appendix-only) | Boot tripwire checks before any Skill runs; audit trail hash-chain verified on every Skill load |
| **Skill-Internal Audit** | All Skill.execute() calls, config changes, feedback processing, optimization steps → audit event | `SkillAuditEvent` (immutable, tenant_id, timestamp, skill_id, input, output, latency, errors, lom) |
| Consent gate | Skill output validated against user consent (L16) | Fail-closed: deny on any PII signal, no bypass |
| House-rules (L44) | Skill code CANNOT disable `house_rules_enforcer()` | Compiled into Skill manifest as immutable `required_checks` |
| Bot disclosure | Skill decisions attributed in transparency log | Every Skill event includes `skill_id`, `version`, `lom` (line-of-moral-responsibility) |

**Integration with Learning Infrastructure (ADR-0314 — self-optimizing):**

```
Skill Execution Loop:
  1. Input → Skill.execute() [deterministic Python]
  2. Emit SkillExecutedEvent (input, output, latency, errors)
  3. User gives feedback (→ FeedbackEvent)
  4. Optimizer reads {FeedbackEvent, SkillExecutedEvent}
  5. Adjusts Skill config (e.g., router thresholds, context window, retry strategy)
  6. Next invocation uses tuned config
  7. Dashboard shows confidence score, convergence rate
```

Feedback types (ADR-0314):
- `outcome_feedback` — "was that routing decision correct?" (yes/no/other)
- `preference_feedback` — "prefer this style next time" (LLM, deterministic, neither)
- `confidence_score` — estimated P(Skill makes correct decision | input)
- `metric_observed` — latency, error rate, cost — optimizer adjusts thresholds

**Audit-First Design (LOAD-BEARING):**

Every Skill is a black box from compliance perspective — internal behavior MUST be fully observable via audit trail.

| Stage | Audit Event | Payload | Immutability |
|---|---|---|---|
| **Skill Load** | `skill_loaded` | skill_id, version, config_hash, boot_layer, dependencies, required_checks | Hash-chain verified |
| **Skill Execute** | `skill_executed` | skill_id, version, input, output, latency_ms, errors, lom, tenant_id | Audit-backend appendix-only |
| **Feedback Received** | `skill_feedback` | skill_id, feedback_type (outcome/preference/confidence/metric), signal, timestamp | Audit-backend appendix-only |
| **Config Optimized** | `skill_config_updated` | skill_id, version, param_delta, confidence_before/after, reason | Audit-backend appendix-only |
| **Skill Disabled** | `skill_disabled` | skill_id, reason, requestor, timestamp | Audit-backend appendix-only |

No Skill is a black box: audit trail proves what it did, when, to whom, with what result. **No audit bypass, no silent optimization.**

**LDD for Skills — Dialectical Reasoning Only (SPECIALIZED CYCLE):**

Standard LDD (12 layers) is overkill for Skill changes. Skills use a **lightweight 2-gate cycle:**

| Gate | When | What |
|---|---|---|
| **Gate 1: Dialectical Reasoning** | BEFORE any Skill change (config tuning, feedback-loop adjustment, dependency change) | `/dialectical-reasoning` — argue for/against the change, surface hidden assumptions, confirm tradeoffs |
| **Gate 2: E2E Wiring Proof** | AFTER coding, before merge | Real E2E test proving the Skill runs end-to-end + audit event is emitted + feedback loop processes it |

**NOT required for Skills:** full LDD k=1–5, `docs-as-definition-of-done`, `e2e-driven-iteration` per task, Concept Gate (use existing CONCEPT-XXXX instead).

**Required for Skills:** Dialectical reasoning (surface design choices) + E2E proof (Skill is called and audited).

Example workflow:
```
# 1. Propose Skill change
/dialectical-reasoning
  Is tuning os.delegation_router's confidence_threshold from 0.7→0.65 correct?
  Argue: for/against, alternatives, risks

# 2. Code + test
core/skills/os_skills/delegation_router.py [edit]
tests/skills/test_delegation_router_e2e.py [add/edit E2E test]

# 3. E2E proof
pytest tests/skills/test_delegation_router_e2e.py -v
# Must show: Skill.execute() called → output produced → SkillAuditEvent logged

# 4. Audit verification
grep "skill_executed.*delegation_router" ~/.corvin/audit.jsonl | tail -1
# Must show: output matches test expectation + lom (line-of-moral-responsibility) present

# 5. Commit + no ADR skip needed
# (Skills inherit ADR requirement from their parent L-layer ADR; incremental tuning is [skip-adr-check])
```

**Must NOT do (hard rules):**

- **Don't hardcode** subsystem logic outside Skills — every L-layer contract must be a Skill
- **Don't skip Dialectical Reasoning** for Skill changes — surface assumptions, confirm tradeoffs (cheap, mandatory)
- **Don't merge without E2E proof** — unit tests ≠ called; prove real execution + audit event
- **Don't audit-bypass** — every Skill.execute() + feedback + optimization MUST log (no silent learning)
- **Don't weaken feedback loop** — no opt-out, no stale-feedback bypass, no learning-off flag
- **Don't let Skill disable compliance** — audit chain, consent, house-rules are meta-Skills (immune to versioning/disable)
- **Don't leak PII into Skill manifests** — manifests are public; learned params must be scrubbed
- **Don't add OS-level Skill without ADR** — one ADR per new L-layer Skill (after ADR-0535)
- **Don't fork SkillForge architecture** — one `skill_forge/` registry, not `skill_forge_v2/`, `os_skill_builder/`, etc.

→ Full reference: See Corvin-ADR repo for ADR-0532–0535 (os-skills-architecture through composition-dependencies)
→ Implementation: `/home/shumway/projects/Corvin-ADR/implementation/ADR-0532-IMPLEMENTATION-PLAN.md` (detailed roadmap, risk matrix, phase gates)

---

## Audit Chain as Ground Truth — Complete User Proof (load-bearing)

**CORE PRINCIPLE:** CorvinOS is a **proof system**. Every action — plugin load, Skill decision, A2A dispatch, consent check, data flow — creates an immutable audit event. Together they form a **complete, tenant-scoped proof of work** that the operator can inspect, verify, and cite. This is NOT just logging; it is the system's memory and legal foundation.

**What Gets Audited (Everything):**

| Subsystem | What | Event Type | Payload | Chain Link |
|---|---|---|---|---|
| **Plugins** | Load, init, execution, error, disable | `plugin_loaded`, `plugin_executed`, `plugin_disabled` | plugin_id, version, boot_layer, input, output, error, tenant_id | Hash-chained |
| **Skills 2.0 (ACP)** | Execute, config, feedback, optimization | `skill_executed`, `skill_config_updated`, `skill_feedback` | skill_id, version, input, output, latency, lom | Hash-chained |
| **Consent / House-Rules** | Consent given, checked, denied | `consent_granted`, `consent_checked`, `house_rule_denied` | user_id, consent_type, tenant_id, timestamp | Hash-chained |
| **A2A (App-to-App)** | Task received, processed, result sent | `a2a_task_received`, `a2a_task_executed`, `a2a_result_sent` | task_id, source_app, target_app, tenant_id, payload_hash | Hash-chained |
| **Audit System Itself** | Chain verification, key rotation, snapshot | `audit_chain_verified`, `audit_key_rotated`, `audit_snapshot` | chain_height, last_hash, verification_result | Self-verifying |
| **Learning (ADR-0314)** | Feedback received, config optimized | `learning_event_received`, `optimizer_config_updated` | event_type, skill_id, signal, confidence_delta | Hash-chained |
| **Context Engineering** | Snapshot taken, context adapted | `context_snapshot`, `context_adapted` | context_id, tenant_id, user_id, preserved_fields, added_fields | Hash-chained |
| **Data Flow Guard (L34)** | Input classified, flow allowed, blocked | `data_flow_classified`, `data_flow_allowed`, `data_flow_blocked` | data_class, engine, dest, reason | Hash-chained |

**Tenant Isolation (GDPR Art. 5, 6, 32):**

Every audit event is **immutable and tenant-scoped:**
```
{
  "tenant_id": "<tenant>",                    # Fail-closed: null tenant → denied
  "timestamp": "2026-09-01T12:34:56.789Z",
  "event_type": "skill_executed",
  "skill_id": "os.delegation_router",
  "input": "classify_request(...)",
  "output": "route_to_agent=opus",
  "latency_ms": 42,
  "lom": "assistant.Forge::route_request:L237",  # Line of Moral Responsibility
  "hash": "sha256(...)",                      # Chained to previous event
  "prev_hash": "sha256(...)"                  # Immutable backward reference
}
```

Queries: All reads filtered by `tenant_id`. No cross-tenant leakage.

**Why This Matters (Proof System):**

1. **Operator Proof:** "Show me every Skill decision for task XYZ" → audit trail proves what happened, when, to whom
2. **Compliance Proof:** "Does CorvinOS respect consent?" → audit shows every consent check + decision (accept/deny + reason)
3. **Security Proof:** "Was this A2A task really processed?" → audit shows source, destination, payload hash, result, no tampering
4. **Learning Proof:** "Did the Skill really learn?" → audit shows feedback received, config before/after, optimizer delta
5. **Auditability (GDPR Art. 30):** Every event is attributed, timestamped, signed; no silent operations

**Integration with LDD + Dialektical Reasoning:**

When designing any new subsystem (Plugin, Skill, A2A handler, Consent gate):

| LDD Phase | What | Audit Implication |
|---|---|---|
| **k=1 (Dialectical)** | Surface design choice | "What audit events will this subsystem emit?" *must* be answered before code |
| **k=2 (E2E)** | Prove it works | E2E test must verify: (1) subsystem does X, (2) audit event Y is logged, (3) hash-chain intact |
| **k=3 (Red/Green)** | Iterate | Each iteration updates audit schema if needed (immutable: never delete prior events, only append) |
| **k=4–5 (Refinement)** | Polish | Audit schema is finalized; all future instances follow it |

**Mandatory Dialectical Prompts:**

Before coding any new Skill, Plugin, or A2A handler:
```
/dialectical-reasoning
  "New Skill: <name>"
  
  Questions to surface:
  1. What audit events will this Skill emit (input, output, config changes)?
  2. What tenant scopes will it cross (single tenant, multi-tenant)?
  3. Are all events immutable + hash-chained?
  4. Can an operator reconstruct the Skill's entire behavior from audit logs?
  5. Are there any "silent" operations (optimizations, cleanups) that should be audited?
```

**E2E Wiring Proof (must include audit verification):**

```bash
# 1. Run the Skill / Plugin / A2A handler
pytest tests/test_skill_xyz.py::test_e2e -v

# 2. Verify audit events were logged
grep "skill_executed\|plugin_loaded\|a2a_task_executed" ~/.corvin/audit.jsonl \
  | jq -c 'select(.skill_id == "os.xyz" or .plugin_id == "xyz")'

# 3. Verify hash-chain integrity
python3 scripts/verify_audit_chain.py --tenant=_default --since=<start_time>
# Output: ✅ Chain intact (N events, N-1 hash links verified)

# 4. Commit only if audit proof succeeds
git add ... && git commit -m "feat(xyz): description [audit-verified]"
```

**Must NOT do (absolute):**

- **Don't code without Dialectical audit design** — audit schema is not an afterthought
- **Don't emit unattributed events** — every event must have tenant_id, timestamp, lom, event_type, hash
- **Don't merge without hash-chain verification** — E2E tests MUST verify `prev_hash` + `hash` integrity
- **Don't weaken tenant isolation** — audit reads MUST filter by tenant_id; no fallback to "any tenant"
- **Don't alter past audit events** — immutable append-only; never update/delete/rewrite
- **Don't silently optimize** — if a Skill optimizes config (ADR-0314), emit `skill_config_updated` event with delta
- **Don't skip audit for "internal" operations** — Plugin init, context adaptation, data flow guard—all audited
- **Don't assume audit is "just logging"** — it is the system's proof of work and legal foundation (GDPR Art. 30, 32)

**Audit Verification Workflow (for operator + auditor):**

```bash
# Inspect full proof for a task
corvin audit show-task <task_id>
# Output: full chain of events for this task across plugins, skills, consent, data flow

# Verify chain integrity (daily)
corvin audit verify-chain --tenant=_default
# Output: ✅ Chain height 142857, all hashes verified, 0 gaps

# Extract proof for compliance report
corvin audit export --tenant=_default --format=pdf --since=2026-09-01 --until=2026-09-30 \
  --events=skill_executed,plugin_loaded,consent_granted,a2a_task_executed
# Output: compliance-report-2026-09.pdf (auditor-ready, signatures included)

# Trace a Skill decision (operator debugging)
corvin audit trace skill os.delegation_router --task=<task_id>
# Output: every event in the chain (config → execute → feedback → optimize)
```

→ Full audit spec: See ADR-0232/0233 (boot tripwire, chain integrity), ADR-0537 (audit event schema + LoM cryptographic binding), RFC 3161 (TSA timestamping)
→ LoM Binding (Gap 2, MEDIUM): ADR-0537 binds each LoM cryptographically to source code via SHA256 (lom_hash field), preventing spoofing attacks
→ Integration: Every new ADR MUST define its audit events (required frontmatter field: `audit_events`); every audit event carrying LoM must include lom_hash for verification

