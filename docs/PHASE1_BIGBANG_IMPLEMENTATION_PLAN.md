# Phase 1: Big Bang Feature Flags Refactoring — Detailed Implementation Plan

**Timeline:** Weeks 1–12 (Big Bang approach)  
**Branch:** `feature/phase1-bigbang-feature-flags`  
**ADR:** ADR-0544  
**Compliance:** GDPR Art. 30/32, EU AI Act Art. 5/50, LoM Binding (ADR-0537)  
**Status:** READY FOR IMPLEMENTATION

---

## Overview

**Phase 0 (Weeks 1–4):** Planning, call-site audit, preparation  
**Phase 2a (Weeks 5–10):** Skills infrastructure (independent track)  
**Phase 1 (Weeks 11–12):** Big Bang migration (code deletion + rewrite)

---

## PHASE 0: Planning & Preparation (Weeks 1–4)

### Week 1: Finalize Architecture + Risk Analysis

#### Monday (Day 1–2): Draft Review & Adversarial Challenge

**Deliverable:** ADR-0544 finalized + Adversarial Review #1 completed

**Task 1.1: Architecture Sign-Off**
- [ ] Review ADR-0544 with architecture team
- [ ] Confirm: "Skills infrastructure will be ready Week 10?"
- [ ] Confirm: "No Phase 2a delays expected?"
- [ ] Document assumptions: Skill manifest schema frozen, registry audit-first, LoM binding in place

**Task 1.2: Adversarial Review #1 (Architecture + Risks)**
- [ ] Review team (3–5 engineers + 1 compliance officer)
- [ ] Challenge #1: "What if Phase 2a slips 2 weeks? Still viable?"
  - Expected answer: "Yes, delay Phase 1 start, total timeline becomes 29w (acceptable)"
- [ ] Challenge #2: "What if Big Bang reveals 10+ unknown call-sites?"
  - Expected answer: "Unlikely (audit in Week 3–4), but 1-week buffer in Week 12 covers it"
- [ ] Challenge #3: "What's the audit trail risk if Skill execution doesn't log properly?"
  - Expected answer: "Compliance gate (Week 11 Day 4) catches it; rollback if detected"
- [ ] Challenge #4: "EU AI Act Art. 50: How do we prove bot disclosure?"
  - Expected answer: "LoM binding (lom_hash) proves code identity; audit trail immutable"
- [ ] Gate: ≥3 challenges resolved; architecture approved (no blockers)

#### Tuesday–Wednesday (Day 3–4): Call-Site Audit Begins

**Deliverable:** Feature Flags Audit Report (preliminary)

**Task 1.3: Grep Audit for All Feature Flags**
```bash
# Find all references to feature flags
grep -r "spec\.features\|feature_flag\|flag(" \
  core/console/ operator/ tests/ \
  --include="*.py" --include="*.js" --include="*.ts" \
  > /tmp/feature_flags_audit.log

# Expected: ~20+ files, ~1000+ LOC of flag-dependent code
```

**Task 1.4: Categorize by Risk**
- [ ] **HIGH RISK** (≥3 nested conditions, business-critical):
  - `core/orchestration/context_bridge.py:routing_decision()` — routes tasks to engines
  - `core/vibe_engineering/vibe_manager.py:activate_vibe_mode()` — Vibe panel activation
  - `core/console/corvin_console/app.py:feature_gates` — App startup gates
- [ ] **MEDIUM RISK** (admin-only, or isolated):
  - `operator/context_engineering/pipeline.py:stage_selector()` — context pipeline
  - `core/console/corvin_console/routes/admin.py:feature_enable()` — admin API
- [ ] **LOW RISK** (telemetry, logging):
  - Various analytics / health check points

**Task 1.5: Feature Flags → Skills Mapping**
- [ ] For each flag, identify target Skill:
  ```
  spec.features.vibe_engineering_v0_2
    → os.vibe_engineering (Skill, v0.2+)
  
  spec.features.audit_compliance_mode
    → os.audit_compliance (Skill)
  
  spec.features.tier1_optimization
    → os.tier1_optimization (Skill)
  ```

**Thursday (Day 5): Week 1 Gate**

**Gate 1: Planning Complete**
- [ ] ADR-0544 approved
- [ ] Adversarial Review #1 passed (0 unresolved challenges)
- [ ] Feature Flags Audit Report (preliminary, ~20+ files identified)
- [ ] Skills mapping drafted
- **Status:** ✅ READY FOR WEEK 2

---

### Week 2: Detailed Audit + High-Risk Call-Site Analysis

#### Monday–Tuesday: Feature Flags Audit Complete

**Deliverable:** Feature Flags Audit Report (final)

**Task 2.1: Final Grep Audit**
- [ ] Execute comprehensive grep (all languages)
- [ ] Create `FEATURE_FLAGS_AUDIT_FINAL.md`:
  ```markdown
  # Feature Flags Audit Report (2026-09-01)

  ## Summary
  - Total references: 23 files
  - Estimate: ~1,200 LOC of flag-dependent code
  - HIGH RISK: 3 files
  - MEDIUM RISK: 7 files
  - LOW RISK: 13 files

  ## HIGH RISK Files
  1. core/orchestration/context_bridge.py:180–220 (routing logic)
  2. core/vibe_engineering/vibe_manager.py:95–150 (mode activation)
  3. core/console/corvin_console/app.py:1–50 (startup gates)

  ## MEDIUM RISK Files
  [... details ...]

  ## LOW RISK Files
  [... details ...]
  ```

**Task 2.2: High-Risk Code Review**
- [ ] For each HIGH RISK call-site:
  - [ ] Understand current behavior (with flag ON vs. OFF)
  - [ ] Sketch equivalent Skill behavior
  - [ ] Identify dependencies (other flags? config? environment?)
  - [ ] Document corner cases (error handling, timeouts, retries)

**Example (routing_decision):**
```python
# CURRENT (with flag)
if config.features.vibe_engineering_v0_2:
    route = smart_route(task)  # LLM-based routing
else:
    route = fallback_route(task)  # heuristic routing

# FUTURE (Skill-based)
if skills.is_enabled("os.vibe_engineering", version="0.2"):
    route = smart_route(task)  # Same logic, now via Skill
else:
    route = fallback_route(task)
```

#### Wednesday–Thursday: Skills Mapping Finalized

**Deliverable:** Skills Equivalence Mapping Document

**Task 2.3: Skills Equivalence Mapping**
- [ ] For each feature flag, create mapping:
  ```markdown
  ## vibe_engineering_v0_2
  
  **Current:** spec.features.vibe_engineering_v0_2 (boolean toggle)
  **Future:** skills.is_enabled("os.vibe_engineering", version="0.2")
  
  **Behavior equivalence:** 
  - ON: LLM-based routing enabled
  - OFF: fallback heuristic routing
  - Error case: If Skill unavailable, fall back to heuristic
  
  **Config migration:**
  - Old: spec.features.vibe_engineering_v0_2: true
  - New: skills.enabled: ["os.vibe_engineering:v0.2"]
  
  **Testing strategy:**
  - A/B test: both enabled simultaneously, compare output
  - Expected: identical routing decisions
  - Tolerance: ≤1% variance (randomness OK)
  ```

#### Friday: Week 2 Gate

**Gate 2: Audit + Mapping Complete**
- [ ] Feature Flags Audit Report (final, 0 unknowns)
- [ ] Skills Equivalence Mapping (all 20+ flags mapped)
- [ ] High-risk files documented (3 files, behavior understood)
- [ ] No surprises in audit (all references expected)
- **Status:** ✅ READY FOR WEEK 3

---

### Week 3: Test Suite Preparation + Config Converter

#### Monday–Tuesday: Config Migration Script

**Deliverable:** `scripts/migrate_flags_to_skills.py` (dry-run functional)

**Task 3.1: Config Converter Implementation**
- [ ] Create converter that reads tenant.corvin.yaml
- [ ] Parses `spec.features.*` (old format)
- [ ] Converts to Skill manifests (new format)
- [ ] Outputs preview (what will change?)
- [ ] Supports dry-run (show changes, don't apply)

**Task 3.2: Config Converter Testing**
- [ ] Test on sample configs:
  - Empty features (no changes)
  - Single flag (converts correctly)
  - Multiple flags (all convert)
  - Nested config (preserves other sections)
- [ ] Test error cases:
  - Invalid YAML (caught, helpful error)
  - Unknown flag (warned, skipped)
  - Malformed config (detected, halted)

#### Wednesday: Feature Flags → Skills Test Suite

**Deliverable:** `tests/core/console/test_feature_flags_to_skills_equivalence.py` (skeleton)

**Task 3.3: A/B Equivalence Test Framework**
- [ ] Create test that checks: old behavior == new Skill behavior
- [ ] For HIGH RISK call-sites (3 files):
  - Run code path with feature flag enabled
  - Run same code path with Skill enabled
  - Assert output identical (or within tolerance)
- [ ] Test structure:
  ```python
  def test_vibe_routing_equivalence():
      """Feature flag routing == Skill routing."""
      task = {"project": "test", "complexity": 3}
      
      # Old path (with flag)
      route_old = routing_decision(task, feature_flag_enabled=True)
      
      # New path (with Skill)
      route_new = routing_decision(task, skill_enabled=True)
      
      # Should match
      assert route_old == route_new
  ```

#### Thursday–Friday: Week 3 Gate

**Gate 3: Preparation Complete**
- [ ] Config migration script works (dry-run + full-run tested)
- [ ] Equivalence test framework ready (HIGH RISK call-sites covered)
- [ ] Skills mapping finalized (all conversions validated)
- **Status:** ✅ READY FOR PHASE 2a + WEEKS 5–10

---

### Week 4: Contingency Planning + Team Alignment

#### Monday: Contingency & Rollback Planning

**Deliverable:** Rollback Plan + Contingency Scenarios

**Task 4.1: Prepare Rollback Procedure**
- [ ] Git tag current state: `pre-flags-deletion-2026-09-01`
- [ ] Document: how to restore from tag if needed
- [ ] Test tag restoration (verify it works)

**Task 4.2: Contingency Scenarios**
- [ ] Scenario 1: Phase 2a delays → push Phase 1 start
- [ ] Scenario 2: Unknown call-sites found → halt, wrap, re-test
- [ ] Scenario 3: Compliance audit fails → stop, fix, re-audit
- [ ] Scenario 4: Production rollback → restore tag, investigate

#### Tuesday–Wednesday: Team Alignment + Training

**Deliverable:** Team Ready (all understand plan + responsibilities)

**Task 4.3: Team Briefing**
- [ ] Eng lead: understands code deletion + rewrite plan
- [ ] QA lead: understands A/B equivalence testing
- [ ] Compliance officer: understands GDPR/EU AI Act gates
- [ ] Operator/Support: understands migration guide + rollback

**Task 4.4: Compliance Briefing**
- [ ] Legal/Compliance officer reviews ADR-0544
- [ ] Confirms GDPR Art. 30/32 gates feasible
- [ ] Confirms EU AI Act Art. 5/50 + LoM binding plan
- [ ] Signs off: "Ready to audit at Week 11"

#### Thursday–Friday: Week 4 Gate

**Gate 4: Team + Contingency Ready**
- [ ] Rollback plan tested (tag accessible, verified)
- [ ] All 4 contingency scenarios documented
- [ ] Team briefing complete (0 questions)
- [ ] Compliance officer ready for Week 11 audit
- [ ] **Status:** ✅ PHASE 0 COMPLETE, AWAIT PHASE 2a READINESS

---

## PHASE 2a: Skills Infrastructure (Weeks 5–10)

**INDEPENDENT TRACK** — not blocked by Phase 1 planning

### Week 10 Gate: Skills Ready

**Requirement:** Skill infrastructure must be production-ready by end of Week 10

- [ ] ✅ Skill manifest schema finalized (ADR-0533)
- [ ] ✅ Skill registry implemented (audit-first)
- [ ] ✅ L5 routing Skill (`os.delegation_router`) working
- [ ] ✅ L10 context Skill (`os.context_adapter`) working
- [ ] ✅ Learning loop wired (ADR-0314)
- [ ] ✅ LoM binding in place (ADR-0537, all Skill events include lom_hash)
- [ ] ✅ Boot-tripwire passing (audit chain integrity verified)
- [ ] ✅ Full test suite: ≥100 tests passing, coverage ≥90%

**If this gate FAILS:** Delay Phase 1 big bang by 1–2 weeks (no penalty, Skills wait for Phase 1 to be ready)

---

## PHASE 1: Big Bang Migration (Weeks 11–12)

### WEEK 11: Execution (Code Cutover + Testing)

#### Monday (Day 1): Initial Setup

**Deliverable:** Deletion script ready, no code changed yet

**Task 11.1: Pre-Cutover Verification**
- [ ] Confirm master branch is clean (all Phase 2a work merged)
- [ ] Tag current state: `pre-flags-deletion-2026-09-01` (rollback point)
- [ ] Verify Skills infrastructure ready (all 10 prerequisites met)
- [ ] Pull latest main, merge Phase 2a work

**Task 11.2: Prepare Deletion Script**
- [ ] Create `scripts/delete_feature_flags.sh`:
  ```bash
  #!/bin/bash
  git rm core/console/corvin_core/feature_flags.py
  git rm core/vibe_engineering/feature_flags.py
  git rm core/audit/feature_flags.py
  git rm core/console/corvin_console/promotion_daemon.py
  git rm ops/launcher/corvin/flag_commands.py
  git rm core/console/tests/test_feature_flags.py
  git rm operator/bundle/config-templates/tenant.corvin.yaml
  # ... etc (all feature flag files)
  ```
- [ ] Test script in dry-run mode (git check-rm)

#### Monday–Tuesday (Day 1–3): Code Cutover + Rewriting

**Deliverable:** All call-sites rewritten, code compiles, no crashes

**Task 11.3: Execute Deletion**
- [ ] Run deletion script
- [ ] Commit: "refactor(phase1): Delete feature flag system (4,900 LOC)"

**Task 11.4: Rewrite Call-Sites**
- [ ] For each of 20+ call-sites:
  1. Replace `if config.features.X:` with `if skills.is_enabled("os.Y"):`
  2. Test that specific module compiles
  3. Update related tests
  4. Commit: "refactor(phase1): Migrate {module} to Skills"
- [ ] All 20+ commits should be small, atomic, reviewable

**Example commits:**
```
refactor(phase1): Migrate orchestration/context_bridge to Skills
  - Replace feature_flag("vibe_engineering_v0_2") with skills.is_enabled("os.vibe_engineering")
  - Behavior unchanged
  - Tests passing

refactor(phase1): Migrate vibe_engineering/vibe_manager to Skills
  - Replace feature_flag("vibe_engineering_v0_2") with skills.is_enabled("os.vibe_engineering")
  - Behavior unchanged
  - Tests passing

... (20 more commits, one per file group)
```

#### Wednesday (Day 4): Testing + Equivalence Verification

**Deliverable:** All tests green, A/B equivalence proven

**Task 11.5: Full Test Suite Run**
```bash
pytest core/console/ operator/launcher/ tests/ -v --cov
# Expected: 0 failures, coverage ≥90%
```

**Task 11.6: A/B Equivalence Testing**
- [ ] Run equivalence test suite (from Week 3)
- [ ] For each HIGH RISK call-site:
  - [ ] Manual test: feature flag ON → Skill ON (output identical)
  - [ ] Manual test: feature flag OFF → Skill disabled (output identical)
  - [ ] Manual test: error cases (both fail same way)
- [ ] Document equivalence: "All tested paths match within tolerance"

**Task 11.7: Audit Trail Verification (Pre-Deploy)**
- [ ] Verify Skill registry is logging all decisions
- [ ] Sample 100 Skill executions from audit trail
- [ ] Verify: all 100 have `SKILL_EXECUTED` events with LoM binding
- [ ] Check: hash-chain verified (no gaps, no tampering)

#### Thursday (Day 5): Compliance Audit Gate

**Deliverable:** Compliance sign-off (legal approval to deploy)

**Adversarial Review #2 (Implementation + Risks)**
- [ ] Code review: all 20+ commits reviewed (0 blockers)
- [ ] Challenge: "Did we miss any call-sites?"
  - Mitigation: Grep audit in Week 3–4 was comprehensive; second grep confirms 0 unknowns
- [ ] Challenge: "Are audit events correct?"
  - Verification: Sample audit trail shows 100% of Skill executions logged
- [ ] Challenge: "Is LoM binding present?"
  - Verification: Every audit event includes `lom` + `lom_hash` (sampled + verified)

**Task 11.8: Compliance Audit (GDPR + EU AI Act)**
- [ ] Checklist:
  - [ ] ✅ GDPR Art. 30: Single audit trail, 100% of Skill decisions logged (sample verified)
  - [ ] ✅ GDPR Art. 32: Hash-chain verified at boot; no breaks detected
  - [ ] ✅ EU AI Act Art. 5: Skill manifests public + transparent
  - [ ] ✅ EU AI Act Art. 50: LoM binding verified (lom_hash checks source code)
  - [ ] ✅ Tenant isolation: All audit queries filtered by tenant_id (code review confirms)
  - [ ] ✅ No PII in audit: Verified (audit events carry only metadata, no user data)

**Task 11.9: Compliance Sign-Off**
- [ ] Legal/Compliance officer reviews findings
- [ ] Signs off: "Ready to deploy. GDPR/EU AI Act compliance verified."
- [ ] Document approval in `COMPLIANCE_AUDIT_WEEK11_SIGNOFF.md`

**Gate: Week 11 Compliance Audit**
- [ ] 0 unresolved compliance issues
- [ ] Legal sign-off obtained
- [ ] **Status:** ✅ READY FOR PRODUCTION DEPLOYMENT

---

### WEEK 12: Deployment + Final Verification

#### Monday: Staged Rollout Begins

**Deliverable:** Code deployed to 10% prod, telemetry looks good

**Task 12.1: Code Review + Final Approval**
- [ ] Final code review: all 20+ commits + deletions (0 blockers)
- [ ] Pair programming spot-check: review 3 highest-risk files
- [ ] Approval: CTO/Eng lead signs off

**Task 12.2: Staged Deployment (10%)**
- [ ] Deploy to 10% prod traffic (canary)
- [ ] Monitor: watch for Skill execution errors, audit trail gaps
- [ ] Expected: all Skill decisions logging correctly, no fallbacks needed
- [ ] Duration: 2–4 hours (if good, proceed to 50%)

#### Tuesday: Expand to 50% Prod

**Task 12.3: Staged Deployment (50%)**
- [ ] Deploy to 50% prod traffic
- [ ] Monitor: same as 10% phase
- [ ] Confirm: telemetry looks good, no operator complaints
- [ ] Duration: 4–8 hours (if good, proceed to 100%)

#### Wednesday: Full Rollout

**Task 12.4: Staged Deployment (100%)**
- [ ] Deploy to 100% prod
- [ ] Monitor: 24–48 hours (watch for edge cases)
- [ ] Operator support: on standby for rollback requests
- [ ] Expected: zero rollbacks needed (but ready if issues arise)

#### Thursday: Post-Deploy Verification

**Deliverable:** Production verified, compliance holds

**Adversarial Review #3 (Post-Deploy)**
- [ ] Challenge: "Did production telemetry match expectations?"
  - Verification: All Skill decisions logged correctly
- [ ] Challenge: "Any unexpected Skill behavior?"
  - Verification: No surge in error rates, no timeouts
- [ ] Challenge: "Is audit trail complete?"
  - Verification: Spot-check audit.jsonl (sample 1000 events, verify all valid)
- [ ] Challenge: "Any compliance violations discovered?"
  - Verification: GDPR Art. 30/32, EU AI Act Art. 5/50 all holding

**Task 12.5: Post-Deploy Compliance Verification**
- [ ] Audit trail verification (production):
  - [ ] Hash-chain verified (all 100K+ events, no gaps)
  - [ ] LoM binding verified (sample 100 events, all have lom_hash)
  - [ ] Tenant isolation verified (cross-tenant queries blocked)
- [ ] Compliance sign-off (second round):
  - [ ] "Prod compliance verified. Ready to close Phase 1."

#### Friday: Phase 1 Closure

**Deliverable:** Phase 1 complete, operator documentation shipped

**Task 12.6: Operator Documentation + Support Training**
- [ ] Publish migration guide: `docs/PHASE1_FEATURE_FLAGS_MIGRATION.md`
  - Step-by-step: old config → new Skill manifest
  - Examples: vibe_engineering, audit_compliance
  - Troubleshooting: common errors + fixes
  - Video (optional): screen recording
- [ ] Train support team: "Where are my feature flags?" → "They're now Skills"
- [ ] FAQ published

**Task 12.7: Release Notes**
- [ ] Publish release notes (external + internal)
- [ ] Announce: "Feature flags deprecated, now using Skill registry"
- [ ] Point to migration guide
- [ ] Support contact listed

**Task 12.8: Metrics Collection + Post-Mortems**
- [ ] Collect Phase 1 metrics:
  - Deployment time: X hours (staged rollout)
  - Incidents: 0
  - Audit trail integrity: 100%
  - Compliance violations: 0
- [ ] Document lessons learned (for Phase 2b plugin migration)

**Gate: Week 12 Phase 1 Closure**
- [ ] Code deployed (100% prod)
- [ ] Audit trail verified + compliant
- [ ] Operator manual published
- [ ] Zero rollbacks
- [ ] Zero compliance violations
- **Status:** ✅ PHASE 1 COMPLETE

---

## Week 13: Final Verification + Debrief

### Monday: E2E Wiring Proof

**Deliverable:** Independent verification that feature flags are gone, Skills working

**Task 13.1: E2E Proof**
- [ ] Trigger: User makes request → Skill executes → Audit event logged
- [ ] Verify: Skill decision is in audit trail (SKILL_EXECUTED event)
- [ ] Verify: Zero feature flag code running (grep confirms)
- [ ] Verify: Operator can query audit trail, see Skill decisions

### Tuesday–Wednesday: Compliance Final Audit

**Deliverable:** Independent compliance audit (3rd party or internal audit team)

**Task 13.2: Independent Audit**
- [ ] External auditor (or internal compliance team) reviews:
  - GDPR Art. 30: Processing records (Skill audit trail complete)
  - GDPR Art. 32: Security (hash-chain, boot-tripwire, tenant isolation)
  - EU AI Act Art. 5: Transparency (Skill manifests public)
  - EU AI Act Art. 50: Bot disclosure + LoM binding
- [ ] Audit report published (sanitized for public)

### Thursday–Friday: Debrief + Method Capture

**Deliverable:** Lessons learned, playbook for Phase 2b

**Task 13.3: Retrospective**
- [ ] Team retrospective: what went well? what was hard?
- [ ] Document: "How to Big Bang a subsystem" (playbook for plugin migration)
- [ ] Potential Concept: CONCEPT-XXXX "Big Bang Subsystem Refactoring Pattern"

**Task 13.4: Phase 2b Planning**
- [ ] Plugin migration (weeks 13–18) can now use same playbook
- [ ] Timeline: same 2-week sprint (audit → deletion → rewrite → test → deploy)
- [ ] Compliance: same gates (GDPR/EU AI Act verification before deploy)

**Gate: Phase 1 Verified + Complete**
- [ ] Feature flags: 0 LOC in production
- [ ] Skills: 100% of routing decisions via Skill registry
- [ ] Audit trail: immutable, hash-chained, compliant
- [ ] Compliance: GDPR/EU AI Act verified by independent auditor
- **Status:** ✅ PHASE 1 DONE

---

## Success Metrics (End of Phase 1, Week 13)

| Metric | Target | Actual | Status |
|---|---|---|---|
| **Feature flags deleted** | 0 references in code | TBD | |
| **Call-sites rewritten** | 20/20 migrated | TBD | |
| **Tests passing** | 0 failures, ≥90% coverage | TBD | |
| **Audit trail complete** | 100% of Skill decisions logged | TBD | |
| **Hash-chain verified** | 0 breaks, all events verifiable | TBD | |
| **Compliance audited** | GDPR/EU AI Act + LoM binding OK | TBD | |
| **Production incidents** | 0 (zero rollbacks) | TBD | |
| **Operator manual quality** | ≥5-star rating from support | TBD | |
| **Timeline adherence** | ≤ 2-week slip (acceptable) | TBD | |

---

**Document Version:** 1.0  
**Status:** READY FOR WEEK 1 KICK-OFF  
**Next:** Week 1 Adversarial Review Gate (Architecture + Risks)
