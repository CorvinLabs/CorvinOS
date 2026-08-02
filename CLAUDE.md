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

## Feature Flags — Ship Dark by Default (load-bearing)

**Goal: a stable CorvinOS core.** New functionality must never change the behavior of an
existing install until the operator turns it on deliberately.

**Every new feature MUST:**
1. sit behind a named flag in `spec.features.<flag_id>` of `tenant.corvin.yaml`;
2. default to **`false`** — off on a fresh install and off after an upgrade
   (absent key = off; never "on because unset");
3. be toggleable from the Console **Settings → Features** panel, no file editing, no restart;
4. degrade to the pre-feature code path when off — off must be a *quiet* path, never an error;
5. carry tests for BOTH states (flag-off = old behavior preserved, flag-on = new behavior).
   A flag that is only ever tested in one state rots.

**Flag lifecycle:** every flag gets an owner and a target release in which it either
flips to default-on or the feature is removed. Flags are not permanent architecture.

**Exceptions — these MUST NOT get a flag (they stay always-on and non-disableable):**
security and compliance mechanisms of the Compliance Baseline above — bot disclosure,
audit hash-chain, consent gate, L10 path-gate, L44 house-rules, L34 flow guard, licensing
gates. "New feature" is never an excuse to ship a compliance mechanism default-off, and a
default-off switch on any of them is the same violation as an env kill-flag.
Telemetry keeps its documented default-ON / opt-out shape (maintainer decision) — do not
convert those three channels to default-off flags.

### Worker Engine Selection (Settings → Engine)

The engine that performs a turn is operator-selectable — one setting,
`spec.web_chat.worker_engine`, resolved through the shared `delegation_policy`
module. No surface may carry its own routing rule.

**Reach today (verified 2026-07-28).** The **Console web-chat** calls the full
rule on every turn. The **messenger bridges** now call it too, but only behind
a flag that ships dark, so a default install is unchanged:

| Flag (both default OFF) | What a bridge turn gets |
|---|---|
| neither | the direct OS-turn, always. Matches `native` by wiring now, not by accident. |
| `bridge_big_data_delegation` | the big-data carve-out only — no `/delegate`, no triage, no mode-awareness (`_maybe_delegate_big_data`). |
| `bridge_worker_engine_parity` | the full rule (ADR-0255, `_maybe_delegate_worker`): the operator's `worker_engine` mode, an explicit `/delegate`, and the console's own triage heuristic — the same `should_delegate_bundled` function, not a copy. Supersedes the row above while on. |

**TDE is still unreachable from a bridge either way** — `_worker_engine_target`
hard-codes `tde_available=False`, so `mode: tde` degrades to the direct turn
there (ADR-0221 P3/P4 stay frozen behind ADR-0222's measured gate). **Remote
triggers still have no Tier-1 delegation at all.** Don't describe the setting as
fully cross-surface: two of four surfaces reach it, one only on an opt-in.

The OS-model side is now genuinely single-source: both surfaces resolve through
`model_selector.resolve_os_model()` (ADR-0024/0119/0123/0043). Before
2026-07-27 `chat_runtime` hand-rolled Tiers 1+3 only, so the console's own
"OS Model" setting had no effect on the console's own chat.

| Value | Meaning |
|---|---|
| `native` (**DEFAULT**) | Claude Code does the work in-process. The ONLY auto-delegation left is **structured-data**-shaped work → ACS. |
| `acs` | Delegate qualifying turns to the ACS manager/worker fan-out. |
| `tde` | Delegate qualifying turns to the Tiered Delegation Engine. **Off by default** — TDE only ever runs on an explicit operator opt-in. |

Invariants:
- Default install = `native`. TDE is **never** entered without an explicit setting change.
- **Structured-data**-shaped work routes to ACS even in `native` mode (per-worker context
  isolation genuinely wins there); everything else stays native. Narrowed 2026-07-28 to
  four affirmative shapes — big-data vocabulary · a tabular paste of ≥10 rows · a
  CSV/spreadsheet file or database/SQL operation PAIRED with a bulk data verb or a volume ·
  a volume/count tied to a data noun, minus hardware and **code** clauses. An ordinary
  request, prose, or a coding task must never trigger it: every ACS run charges one
  `compute_units_per_day`. Don't widen it back, and don't make a bare mention of
  "Datenbank"/"SQL"/"Tabelle" sufficient on its own. → delegation-routing.md § 2a.
- A chat turn shows at most 20 artifact chips, and runtime bookkeeping (`acs/`, `tasks/`,
  `tde/`, `voice/` under the session workdir) is never a chat artifact — a `delegate_*`
  call writes `acs/runs/<id>/{manifest,result}.json` per invocation, which once flooded one
  turn with 144 identical chips. → delegation-routing.md § 7a.
- An explicit `/delegate` from the user still beats the classifier (delegation-routing.md §6).
- Every degrade ladder ends at **`native`**, not at another delegation engine: ACS quota
  exhausted / TDE unavailable → run the turn natively, never silently swap engines.

**Must NOT do:** ship a feature without a flag · default a new flag to `true` ·
flag a compliance/security mechanism · read the engine choice from an env var or from a
second config key · let a degrade path route into an engine the operator did not select.

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
→ ADR: `Corvin-ADR: decisions/0264-adr-decision-graph-hermeneutic-traversal.md`

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

**Destination:** `Corvin-ADR/concepts/CONCEPT-NNNN-slug.md` (sibling repo, own numbering,
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
→ Concept: `Corvin-ADR: concepts/0001-self-learning-project-concept-archive.md`
→ Concept: `Corvin-ADR: concepts/0002-live-report-driven-root-cause-method.md`
→ Skill: `assistant.corvinOS_live_report_root_cause` (project scope, `learned-experience`)

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

→ Full reference: [quality-discipline.md](docs/claude-ref/quality-discipline.md) ·
Skill: `operator/bundle/skills/ldd/e2e-wiring-proof/SKILL.md`

---

## Language

**All repository content: English.** Includes: source code, docs, SVGs, inline comments, commits.

**User-facing runtime text: Defaults to English.** Bot answers in user's language (German/English)
at runtime per `adapter.py` system prompt — that is runtime behavior, not repo content.

---

**For full details, read the ref files in `docs/claude-ref/` as tasks require them.**
