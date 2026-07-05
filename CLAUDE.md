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
| Anonymous instance-count ping (default-ON, opt-out; random uuid4 + version only, no PII) | GDPR Art. 6(1)(f) legitimate interest | ADR-0180 (`aco/htrace_consent.py::ping_enabled`) |

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
  version only; the error/healing channels ship ONLY scrubbed code-level signatures (exc_type,
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
| Any task marked "done" | `docs-as-definition-of-done` | BEFORE declaring done |

**Must NOT do (hard rules):**
- Don't make ANY non-trivial code edit without running E2E and capturing loss signal first.
- Don't declare task "done" without running `docs-as-definition-of-done`.
- Don't skip LDD because task "looks small" — every skip is tech debt.
- Don't downgrade to LDD-off or partial-LDD via any env var.

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

**Must NOT do:** write ADR content into Corvin repo (ADRs live in Corvin-ADR only) ·
auto-skip security/compliance mechanisms without justification · declare "done" on structural
change without running this gate · leave a skip implicit.

→ Full reference: [adr-gate.md](docs/claude-ref/adr-gate.md)

---

## Language

**All repository content: English.** Includes: source code, docs, SVGs, inline comments, commits.

**User-facing runtime text: Defaults to English.** Bot answers in user's language (German/English)
at runtime per `adapter.py` system prompt — that is runtime behavior, not repo content.

---

**For full details, read the ref files in `docs/claude-ref/` as tasks require them.**
