---
name: corvinOS_review_gates
description: CorvinOS Code Review: Pre-Review Gates + Structural Analysis (ADR, Layers, Tenants)
---

# CorvinOS Review: Gates & Structural Analysis

## PHASE 1: PRE-REVIEW GATES (Reject Fast)

### 1a. Commit Hygiene
- [ ] Messages in English
- [ ] No force-push / rebase-on-main
- [ ] audit.jsonl, policy.json, license/, memory/ untouched
- [ ] Keine legacy env-var renames (hardcut)
- **REJECT if violated**

### 1b. Compliance Red-Lines
- [ ] No weaken: disclosure (AI-card/`/join`/`/consent`)
- [ ] No bypass: consent-gate (L16 Phase 4)
- [ ] No break: audit hash-chain (L16)
- [ ] No PII leak: Prometheus/audit-details/logs
- [ ] No expand engine-reach past `allowed_engines`
- **CRITICAL REJECT if violated**

### 1c. Licensing Red-Lines
- [ ] No change LICENSE/NOTICE/CLA.md without approval
- [ ] New contribution: CLA-SIGNATORIES.md entry
- [ ] CLA §3 (Relicense) intact
- **CRITICAL REJECT if violated**

---

## PHASE 2: STRUCTURAL ANALYSIS

### 2a. ADR-Gate (High-Bar Test)
**Questions:**
1. Real design choice made? (A chosen over B?)
2. Trigger present? (protocol/schema/security/irreversible/cross-repo/new-layer?)

**If YES → ADR required:**
- [ ] Exists in `Corvin-ADR/decisions/XXXX-*.md`?
- [ ] Referenced in commit message?

**If NO → Name skip reason (1 line):**
- "Bug fix" / "Pure refactor" / "Test-only" / "Config tune"

### 2b. Layer Invariants (per changed code)
- [ ] **L10 Path-Gate:** Write-ops to forge/skill-forge/audit/policy/protected trees gated?
- [ ] **L16 Audit-Chain:** New events have prev_hash? METADATA-only voice-audit?
- [ ] **L34 Data-Classification:** Pre-spawn gate? No prompt-text in audit `details`?
- [ ] **L35 Egress:** New HTTP-calls in allowlist? No URL-paths in audit?
- [ ] **L38 A2A:** ResponseEnvelope signed? HMAC-SHA256 constant-time?
- [ ] **L33 Artifacts:** PII-redacted description? Not in system-prompt?
- [ ] **L28 User-Recall:** PII-redacted before INSERT? `<user_context>` LAST in system-prompt?

### 2c. Tenant Multi-Tenant (ADR-0007)
- [ ] Keyword-only `tenant_id` params?
- [ ] `validate_tenant_id()` before file-ops?
- [ ] Backward-compat symlinks intact?
- [ ] Audit-chain not migrated cross-tenant?

### 2d. Engine-Agnostic (if not ClaudeCode)
- [ ] `_run_pre_dispatch_gates()` runs before spawn? (L30.1b/L34/L35)
- [ ] L10 path-gate via TEB for Forge/Skill-Forge?
- [ ] L16 audit events registered?
- [ ] L33 artifact registration active?

---

## DECISION MATRIX

| Finding | Action |
|---|---|
| Compliance red-line | REJECT |
| Licensing red-line | REJECT |
| Security red-line | REJECT |
| ADR needed, not present | REJECT |
| Audit-chain broken | REJECT |
| All gates green | → Continue to Compliance Deep-Dive |

