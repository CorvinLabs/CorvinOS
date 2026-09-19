# 🚨 CorvinOS Adversarial Review — CRITICAL FINDINGS
**Date:** 2026-09-20 | **Status:** **DO NOT RELEASE** until CRITICAL findings are resolved

## Executive Summary
4-agent parallel adversarial review (Explore, Security, E2E Wiring, ADR Compliance) identified **26 distinct findings** with **3 CRITICAL blockers** preventing production release:

1. 🔴 **3 CRITICAL Security Vulnerabilities** (GDPR exploitable)
2. 🔴 **3 CRITICAL ADR Compliance Violations** (knowledge graph broken)
3. 🟠 **4 HIGH Severity Issues** (dead code, missing E2E, audit failures)
4. 🟡 **12 MEDIUM Severity Issues** (test coverage, wiring gaps)

**Recommendation:** Halt release. Triage CRITICAL findings (48-72h timeline). Fix details in `/tmp/ADVERSARIAL_REVIEW_COMPLETE.md` + this document.

## CRITICAL FINDINGS

### 🔴 Security: 3 Exploitable Vulnerabilities

**[CRITICAL-1] Audit Chain Verification Timing Window**
- File: `core/learning/event_persistence.py:148-173` (_tail_contains)
- Attack: 65KB window verification allows junk after audit reference
- Impact: GDPR Art. 30, 32 — chain corruption undetectable
- Fix: Verify reference is in final JSON record only

**[CRITICAL-2] EventStore Cross-Tenant Leakage**
- File: `core/learning/event_persistence.py:356-399` (cleanup_old_events)
- Attack: Write `tenant_id=tenant_b` event to `tenant_a`'s directory
- Impact: GDPR Art. 5 — permanent cross-tenant data leak
- Fix: Validate ALL events before processing; reject cross-tenant data

**[CRITICAL-3] Consent Gate Legacy Path Bypass**
- File: `corvin_operator/bridges/shared/consent.py:148-157`
- Attack: Grant consent via `~/.corvin/global/consent/` fallback
- Impact: GDPR Art. 6, 7 — consent isolation breaks
- Fix: Remove `tenant_id=None` fallback entirely

### 🔴 ADR Compliance: 3 Blockers (Knowledge Graph Broken)

**[ADR-CRIT-1] 117 ADR Naming Duplicates**
- Examples: `0009-*.md` + `ADR-0009-*.md`, `0015-*.md` + `ADR-0015-*.md`
- Impact: Query resolver is non-deterministic (ADR-0516 violation)
- Fix: Choose one naming scheme; delete old format

**[ADR-CRIT-2] 30 ADRs in Wrong Locations**
- Locations: `.corvin/sessions/*/outputs/`, `.claude/worktrees/*/docs/`
- Impact: Submodule path resolver fails (ADR-0516 violation)
- Fix: Migrate to `corvin_decisions/decisions/` or discard

**[ADR-CRIT-3] Broken Traceability: 302 Violations**
- Missing: `commits:` field (ADRs-0900, 0901, 0532, 0892, etc.)
- Missing: `paths:` field (27 ADRs)
- Impact: Cannot regenerate decision graph from git history
- Compliance Score: 80.3% (300+ gaps across 5 areas)

### 🟠 High Severity: 4 Issues

**[HIGH-1] SecurityOrchestratorSkill — DEAD CODE**
- Call sites: 0 production code
- E2E test: Actually a unit test (violates E2E standard)
- Impact: Audit events defined but never emitted
- Fix: Wire API endpoint OR deprecate explicitly

**[HIGH-2] Audit Chain OFF by Default**
- File: `core/pipeline/bootstrap.py:161` (dual_gate_pipeline_enabled=False)
- Impact: Fresh install records ZERO audit events (GDPR failure)
- Fix: Change default to True OR auto-enable with operator prompt

**[HIGH-3] PluginManager v2 (Marketplace) — DEAD CODE**
- Implementation: 2000+ LOC fully written
- Call sites: 0 outside tests
- Impact: Marketplace feature non-functional; ship-dark code
- Fix: Wire v2 OR remove entirely

**[HIGH-4] ConfigHypothesis Events — UNAUDITED**
- Generated: `skill_adapter.py:117`
- Audit logs: 0 for hypothesis outcomes
- Impact: Learning optimizer decisions lack traceability
- Fix: Wire `outcome_sink.py` to emit hypothesis events

### 🟡 Medium Severity: 12 Issues

**[MEDIUM-1] 87/97 Console Routes — NO E2E TESTS** (massive gap)
**[MEDIUM-2] Audit Chain Unification Seam — NOT ENFORCED** (chains can split)
**[MEDIUM-3] LOM Spoofing — NO CRYPTOGRAPHIC BINDING** (ADR-0537 incomplete)
**[MEDIUM-4-12]** WorkflowOptimizer dead code, consent fields, learning signature validation, etc.

## What Was Fixed in This Review

✅ **Fix #1:** `bootstrap_global` exported from `core/plugins/corvin_plugins/__init__.py`
- Import added: `from .bootstrap import bootstrap_global`
- Exported in __all__: `"bootstrap_global"`
- E2E test: ✅ PASS (`bootstrap_global` is now callable)

## Next Steps (OPERATOR RESPONSIBLE)

**Immediate (48-72h):**
1. Triage 3 CRITICAL security findings
2. Resolve 3 CRITICAL ADR compliance blockers
3. Implement security fixes (3 CRITICAL-1,2,3)
4. Decide: wire or deprecate SecurityOrchestratorSkill + WorkflowOptimizer

**Before Release (1 week):**
5. Fix Audit Chain (OFF→ON default or auto-enable)
6. Add ConfigHypothesis audit wiring
7. Resolve PluginManager v2 (integrate or remove)

**Post-Launch:**
8. Implement E2E tests for 87/97 routes
9. Add LOM cryptographic binding (ADR-0537)
10. Enforce audit chain unification seaming

---

**Full Review:** See `/tmp/ADVERSARIAL_REVIEW_COMPLETE.md` + individual agent reports.
