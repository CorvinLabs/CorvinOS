# TIER 1 PILOT DOCUMENTATION
## Complete Package for Marketplace Plugin Extraction

**Package Version:** 1.0  
**Status:** READY FOR REVIEW & EXECUTION  
**Last Updated:** 2026-08-29  
**Target Release:** CorvinOS v0.8.0

---

## CONTENTS

This documentation package contains **7 comprehensive guides** covering all aspects of the Tier 1 pilot:

### 📋 Strategic & Planning Documents

1. **[TIER_1_MIGRATION_STRATEGY.md](TIER_1_MIGRATION_STRATEGY.md)** (9,000+ words)
   - Executive summary of the entire pilot
   - Scope: 7 plugins, 2 weeks, 4 phases
   - Detailed migration steps (pre/during/post)
   - Success criteria and timeline
   - Risk assessment with 10 identified risks & mitigations
   - **→ Start here for overall understanding**

2. **[TIER_1_REPOSITORY_STRUCTURE.md](TIER_1_REPOSITORY_STRUCTURE.md)** (3,500+ words)
   - Directory layout for Corvin-Marketplace (new)
   - Changes to CorvinOS core (removals only)
   - Plugin file organization (manifest, code, tests, docs)
   - Registry structure (auto-generated)
   - Version coordination between repos
   - **→ Use this during implementation**

### 🔬 Technical & Operational Documents

3. **[TIER_1_TEST_PLAN.md](TIER_1_TEST_PLAN.md)** (3,000+ words)
   - Unit tests per plugin (85%+ coverage)
   - Integration tests (plugin + CorvinOS)
   - E2E tests (operator workflow)
   - Regression tests (546+ core tests)
   - Test fixtures and execution procedures
   - **→ Use this during testing phase**

4. **[OPERATOR_WORKFLOW.md](OPERATOR_WORKFLOW.md)** (2,500+ words)
   - Quick start (5 minutes)
   - Discovering plugins (`corvin plugin list`)
   - Installation, configuration, enabling
   - Testing, monitoring, troubleshooting
   - Upgrading/downgrading, uninstallation
   - Advanced usage & audit trail
   - **→ Share this with operators**

### ✅ Compliance & Risk Documents

5. **[ADR_COMPLIANCE_CHECKLIST.md](ADR_COMPLIANCE_CHECKLIST.md)** (2,500+ words)
   - Verification against 6 relevant ADRs
   - GDPR Art. 30, 32 compliance check
   - EU AI Act Art. 50 compliance check
   - Compliance baseline guarantees maintained
   - Pre-launch & post-launch audit checklists
   - **→ Use for compliance sign-off**

6. **[DEPENDENCY_MAPPING.md](DEPENDENCY_MAPPING.md)** (3,500+ words)
   - Detailed analysis of all 7 plugins
   - Internal, external, bootstrap, operator dependencies
   - Cross-plugin dependency matrix (zero inter-plugin deps)
   - Core dependency verification (zero core dependencies)
   - Extraction readiness checklist
   - **→ Use for extraction planning**

### 📊 This File (Summary & Navigation)

7. **[README.md](README.md)** (this file)
   - Overview & quick reference
   - Document map
   - Pre-requisites & assumptions
   - Success criteria checklist
   - Decision tree for next steps

---

## QUICK REFERENCE

### 📅 Project Timeline

```
        Week 1          Week 2
Day  1  2  3  4  5  6  7  8  9 10 11 12 13 14
P1   [====]                                       (Extraction Prep)
P2        [====]                                  (Core Changes)
P3             [====]                             (Marketplace Setup)
P4                  [========================================]        (Testing & Docs)
```

- **Phase 1 (Days 1-2):** Extraction Preparation
- **Phase 2 (Days 3-4):** Core Repository Changes
- **Phase 3 (Days 5-6):** Marketplace Repository Setup
- **Phase 4 (Days 7-14):** Testing, Documentation & Launch

### 🎯 Success Criteria

All of the following must be **✅ TRUE** before launch:

```
Code Changes
  ✅ All 7 Tier 1 plugins extracted cleanly
  ✅ CorvinOS core boots without plugins
  ✅ Corvin-Marketplace directory structure created
  ✅ registry.json auto-generated and valid

Testing
  ✅ 546+ core tests pass (zero failures)
  ✅ 87+ plugin unit tests pass (all tiers)
  ✅ 18+ integration tests pass
  ✅ 3+ E2E operator workflow tests pass
  ✅ >85% code coverage per plugin

Security & Compliance
  ✅ Zero compliance baseline regressions
  ✅ GDPR Art. 30, 32 maintained
  ✅ EU AI Act Art. 50 maintained
  ✅ Audit chain integrity verified
  ✅ Boot tripwire still functional
  ✅ Zero ADR violations

Documentation
  ✅ Operator workflow docs complete
  ✅ Migration strategy documented
  ✅ ADR compliance verified
  ✅ Dependency mapping verified
  ✅ Repository structure documented

Operator Readiness
  ✅ Operator can list Tier 1 plugins
  ✅ Operator can install a plugin
  ✅ Operator can configure a plugin
  ✅ Operator can enable/disable plugins
  ✅ Operator can test a plugin
  ✅ Operator can upgrade/downgrade
  ✅ Operator can uninstall a plugin
```

### 🔑 Key Decisions Already Made

| Decision | Status | Reference |
|---|---|---|
| Tier 1 scope (7 plugins) | ✅ FINAL | TIER_1_MIGRATION_STRATEGY.md §1.2 |
| No inter-plugin dependencies | ✅ VERIFIED | DEPENDENCY_MAPPING.md |
| Compliance layer stays in core | ✅ FINAL | ADR_COMPLIANCE_CHECKLIST.md |
| boot_layer=bundled for Tier 1 | ✅ FINAL | TIER_1_REPOSITORY_STRUCTURE.md |
| registry.json auto-generated | ✅ FINAL | TIER_1_REPOSITORY_STRUCTURE.md §2.3 |
| E2E workflow testing required | ✅ FINAL | TIER_1_TEST_PLAN.md §III |
| Rollback < 30 minutes | ✅ FINAL | TIER_1_MIGRATION_STRATEGY.md §V |
| ADR compliance verified | ✅ FINAL | ADR_COMPLIANCE_CHECKLIST.md |

---

## IMPLEMENTATION CHECKLIST

### Pre-Launch (Before Day 1)

- [ ] All 7 documents reviewed by:
  - [ ] Architecture team
  - [ ] Security team
  - [ ] Operations team
  - [ ] QA team
- [ ] Project kickoff meeting scheduled
- [ ] Team assigned to each phase
- [ ] Dev/staging environments ready
- [ ] GitHub repositories created (or access verified)
- [ ] Backup procedures tested
- [ ] Rollback scripts written & tested

### Phase 1 Execution (Days 1-2)

- [ ] Dependency map completed for each plugin (use DEPENDENCY_MAPPING.md)
- [ ] Extraction tests written (verify each plugin isolates)
- [ ] Pre-extraction baseline established (git tag backup/tier-1-pre-extraction)
- [ ] Extract-readiness verified (all checks in DEPENDENCY_MAPPING.md)
- [ ] Sign-off: "Phase 1 complete, ready for Phase 2"

### Phase 2 Execution (Days 3-4)

- [ ] Created Corvin-Marketplace repository structure
- [ ] Copied 7 plugins to marketplace
- [ ] Updated CorvinOS core imports/registrations
- [ ] Ran core test suite (546+ tests pass)
- [ ] Verified no dangling references to extracted plugins
- [ ] Sign-off: "Phase 2 complete, core stable"

### Phase 3 Execution (Days 5-6)

- [ ] Vendored base classes to _corvin_plugins/
- [ ] Generated registry.json
- [ ] Wrote installation scripts (install_plugin.sh, etc.)
- [ ] Wrote marketplace CI/CD (.github/workflows/)
- [ ] Drafted operator docs (OPERATOR_WORKFLOW.md)
- [ ] Sign-off: "Phase 3 complete, marketplace ready"

### Phase 4 Execution (Days 7-14)

- [ ] Unit tests pass for all 7 plugins (87+)
- [ ] Integration tests pass (18+)
- [ ] E2E tests pass (3+ operator workflows)
- [ ] Core regression tests pass (546+)
- [ ] Code review passed (zero code-review findings)
- [ ] Security audit passed
- [ ] Operator testing completed
- [ ] Sign-off: "All tests pass, ready for launch"

### Launch Preparation (Day 14)

- [ ] Final compliance audit
- [ ] Team sign-off on all metrics
- [ ] Release notes written
- [ ] Announcement prepared
- [ ] Rollback plan rehearsed
- [ ] Go/no-go decision made

---

## DOCUMENT USAGE BY ROLE

### 👨‍💼 Project Manager / Team Lead

**Read:**
1. TIER_1_MIGRATION_STRATEGY.md (§I-III for overview)
2. README.md (this file)

**Use for:**
- Project planning & scheduling
- Risk tracking
- Stakeholder communication

### 🏗️ Architecture Team

**Read:**
1. TIER_1_MIGRATION_STRATEGY.md (full)
2. TIER_1_REPOSITORY_STRUCTURE.md (full)
3. ADR_COMPLIANCE_CHECKLIST.md (full)
4. DEPENDENCY_MAPPING.md (full)

**Use for:**
- Design verification
- Compliance sign-off
- Dependency review

### 👨‍💻 Implementation Team

**Read:**
1. TIER_1_REPOSITORY_STRUCTURE.md (§II-III)
2. DEPENDENCY_MAPPING.md (§Extraction Readiness)
3. TIER_1_MIGRATION_STRATEGY.md (§II)

**Use for:**
- Code changes
- Git workflows
- Build scripts

### 🧪 QA / Testing Team

**Read:**
1. TIER_1_TEST_PLAN.md (full)
2. TIER_1_MIGRATION_STRATEGY.md (§IV)

**Use for:**
- Test writing
- Test execution
- Regression verification

### 🛡️ Security / Compliance Team

**Read:**
1. ADR_COMPLIANCE_CHECKLIST.md (full)
2. TIER_1_MIGRATION_STRATEGY.md (§VI-VII)
3. DEPENDENCY_MAPPING.md (§Bootstrap-Time Analysis)

**Use for:**
- Compliance verification
- Risk assessment sign-off
- Security audit

### 👤 Operations / Operators

**Read:**
1. OPERATOR_WORKFLOW.md (full)
2. TIER_1_MIGRATION_STRATEGY.md (§III)

**Use for:**
- Learning new workflow
- Troubleshooting
- Providing feedback

---

## ASSUMPTIONS & PRE-REQUISITES

### Team Assumptions

- [ ] Team has read CLAUDE.md (CorvinOS compliance baseline)
- [ ] Team understands ADR-0243 (plugin boot layers)
- [ ] Team understands ADR-0233 (plugin consolidation)
- [ ] Team is familiar with CorvinOS plugin architecture

### Infrastructure Assumptions

- [ ] GitHub repos available (CorvinOS + Corvin-Marketplace)
- [ ] Python 3.9+ available for development
- [ ] pytest installed and working
- [ ] git workflows enforced (pre-commit hooks available)
- [ ] CI/CD pipeline configured

### Compliance Assumptions

- [ ] No changes to GDPR Art. 30, 32 enforcement
- [ ] No changes to EU AI Act Art. 50 enforcement
- [ ] Audit chain integrity is non-negotiable
- [ ] Compliance baseline is read-only (cannot weaken)

---

## DECISION TREE

Use this to decide what to read/do next:

```
Are you responsible for...?

[Project Planning]
  → Read: TIER_1_MIGRATION_STRATEGY.md §I-III
  → Then: TIER_1_MIGRATION_STRATEGY.md §VIII-IX

[Architecture Design]
  → Read: TIER_1_REPOSITORY_STRUCTURE.md (full)
  → Read: DEPENDENCY_MAPPING.md (full)
  → Then: ADR_COMPLIANCE_CHECKLIST.md

[Implementation]
  → Read: TIER_1_REPOSITORY_STRUCTURE.md (full)
  → Read: DEPENDENCY_MAPPING.md §Extraction Readiness
  → Then: TIER_1_MIGRATION_STRATEGY.md §II

[Testing & QA]
  → Read: TIER_1_TEST_PLAN.md (full)
  → Read: TIER_1_MIGRATION_STRATEGY.md §IV
  → Then: Start writing tests

[Security & Compliance]
  → Read: ADR_COMPLIANCE_CHECKLIST.md (full)
  → Read: TIER_1_MIGRATION_STRATEGY.md §VII
  → Then: Schedule compliance audit

[Operations & Operators]
  → Read: OPERATOR_WORKFLOW.md (full)
  → Read: TIER_1_MIGRATION_STRATEGY.md §III
  → Then: Test with real workflow

[Risk Management]
  → Read: TIER_1_MIGRATION_STRATEGY.md §VII (Risk Register)
  → Read: TIER_1_MIGRATION_STRATEGY.md §V (Rollback)
  → Then: Plan mitigations
```

---

## KEY METRICS TO TRACK

### During Implementation

Track these daily:

```
Phase: ____________  Date: ____________

Code Completion:
  Phase 1 (Prep): ___% (target: 100% by EOD Day 2)
  Phase 2 (Core): ___% (target: 100% by EOD Day 4)
  Phase 3 (Mkt):  ___% (target: 100% by EOD Day 6)
  Phase 4 (Test): ___% (target: 100% by EOD Day 14)

Testing:
  Core tests:     ___/546 passed (target: 546/546)
  Plugin tests:   ___/87 passed (target: 87/87)
  Integration:    ___/18 passed (target: 18/18)
  E2E:            ___/3 passed (target: 3/3)

Risk Status:
  Blockers:       ___ (target: 0)
  At-risk items:  ___ (target: 0)
  Mitigated:      ___ (target: 10/10)
```

---

## GETTING HELP

### If you get stuck on...

| Issue | Document Section |
|---|---|
| Understanding the overall strategy | TIER_1_MIGRATION_STRATEGY.md §I |
| Repository structure questions | TIER_1_REPOSITORY_STRUCTURE.md |
| Dependency questions | DEPENDENCY_MAPPING.md |
| Testing strategy | TIER_1_TEST_PLAN.md |
| Operator workflow | OPERATOR_WORKFLOW.md |
| Compliance questions | ADR_COMPLIANCE_CHECKLIST.md |
| Risk mitigation | TIER_1_MIGRATION_STRATEGY.md §VII |
| Rollback procedures | TIER_1_MIGRATION_STRATEGY.md §V |

### FAQ

**Q: Can we extract Tier 1 plugins in a different order?**  
A: Yes! All plugins have zero inter-plugin dependencies (verified in DEPENDENCY_MAPPING.md). Extract in any order or in parallel.

**Q: Do we need to run all 4 phases?**  
A: Yes. They're sequential and each gate builds on the previous. No shortcuts.

**Q: What if a test fails?**  
A: Reference TIER_1_TEST_PLAN.md for expected coverage. Debug the failing test, fix the code, re-run. No rollback unless it's a Phase 2 (core) failure.

**Q: How long is rollback?**  
A: <30 minutes. See TIER_1_MIGRATION_STRATEGY.md §V for procedures. Test it during Phase 1.

**Q: Can we start Phase 3 before Phase 2 is done?**  
A: No. Phase 3 depends on Phase 2 completeness. Follow the sequence.

**Q: Is an operator training session needed?**  
A: Yes. Recommended: 30-minute walkthrough using OPERATOR_WORKFLOW.md. Let operators practice with a staging environment.

---

## APPROVAL & SIGN-OFF

Before executing this plan, get written approval from:

- [ ] **Architecture Lead** — Confirms design & dependencies OK
- [ ] **Security Lead** — Confirms compliance baseline maintained
- [ ] **QA Lead** — Confirms test plan is sufficient
- [ ] **Operations Lead** — Confirms operator workflow is usable
- [ ] **Project Manager** — Confirms timeline & resources

---

## AFTER LAUNCH

### Week 1-2 (Monitoring)

- [ ] Monitor plugin installations via telemetry
- [ ] Track support tickets (target: <5 Tier 1 related)
- [ ] Verify operator feedback is positive
- [ ] Check plugin health (99%+ success rate target)

### Week 3-4 (Follow-up)

- [ ] Post-mortem: what went well? what didn't?
- [ ] Update documentation based on feedback
- [ ] Plan Tier 2 extraction (community vetting)
- [ ] Plan Tier 3 extraction (marketplace governance)

### Month 2+ (Consolidation)

- [ ] Measure adoption of marketplace plugins
- [ ] Identify most-requested features
- [ ] Plan improvements to `corvin plugin` CLI
- [ ] Prepare for plugin pricing/licensing (ADR-0249)

---

## DOCUMENT HISTORY

| Date | Version | Author | Change |
|---|---|---|---|
| 2026-08-29 | 1.0 | Claude Code | Initial complete package |

---

## FEEDBACK & QUESTIONS

**Q: Do we need changes to this plan?**  
A: Yes, feedback is expected. Before Day 1, submit feedback via GitHub issues.

**Q: Are there any time-sensitive items?**  
A: No. This is a well-documented, repeatable process. Execute when ready.

**Q: What's the next step?**  
A: 
1. Share this package with the team
2. Get approvals (section above)
3. Schedule kickoff meeting
4. Assign Phase 1 lead
5. Start Day 1

---

## DOCUMENT MAP

```
README.md (you are here)
  ├── TIER_1_MIGRATION_STRATEGY.md (strategy & planning)
  │   ├── 2-week timeline
  │   ├── 4-phase execution plan
  │   ├── 10 identified risks
  │   └── Rollback procedures
  │
  ├── TIER_1_REPOSITORY_STRUCTURE.md (implementation guide)
  │   ├── Corvin-Marketplace directory layout
  │   ├── CorvinOS core changes
  │   └── Version coordination
  │
  ├── TIER_1_TEST_PLAN.md (testing strategy)
  │   ├── Unit tests (87+ per plugin)
  │   ├── Integration tests (18+)
  │   ├── E2E tests (3+)
  │   └── Regression tests (546+)
  │
  ├── OPERATOR_WORKFLOW.md (user guide)
  │   ├── Quick start (5 min)
  │   ├── Installation
  │   ├── Configuration
  │   ├── Troubleshooting
  │   └── Advanced usage
  │
  ├── ADR_COMPLIANCE_CHECKLIST.md (compliance verification)
  │   ├── 6 ADRs verified
  │   ├── GDPR Art. 30, 32
  │   ├── EU AI Act Art. 50
  │   └── Compliance baseline maintained
  │
  └── DEPENDENCY_MAPPING.md (technical analysis)
      ├── 7 plugins analyzed
      ├── Zero inter-plugin deps
      ├── Zero core deps
      └── Extraction readiness ✅
```

---

**Package Owner:** Architecture Team  
**Status:** ✅ COMPLETE & READY FOR REVIEW  
**Next Action:** Schedule kickoff meeting & start Phase 1
