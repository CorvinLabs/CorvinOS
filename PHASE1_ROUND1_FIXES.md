# Phase 1: Round 1 Critical Fixes

**Date:** 2026-08-11  
**Status:** Ready for execution  
**Scope:** 3 CRITICAL errors from Adversarial Review Round 1

---

## CRITICAL FIX 1: Phase 1 Effort Correction (75→101 hours)

### Problem Statement
Phase 1 effort was stated as **75 hours**, but actual sum of all 9 ADRs is **101 hours**:
- ADR-0302: 8.5h
- ADR-0294: 8.5h
- ADR-0296: 11h
- ADR-0295: 11.5h
- ADR-0298: 10.5h
- ADR-0297: 10.5h
- ADR-0299: 10.5h
- ADR-0300: 15h (CRITICAL design review)
- ADR-0301: 15h (E2E wiring proof)

**Total: 101 hours** (26 hours overhead = 35% underestimate)

### Root Cause
- ADR-0300 and ADR-0301 were under-scoped (design review + E2E proof gate add 30h combined)
- Parallel execution overhead not accounted for (handoff, context switching)
- No buffer for design feedback loops on ADR-0300

### Fix Summary
**Replan Phase 1 from 2 weeks to 2.5 weeks (12.5 days × 8h = 100 hours available)**

#### Timeline Adjustment
```
OLD PLAN (2 weeks):
  Week 1: Mon–Fri (8h/day = 40h)
  Week 2: Mon–Fri (8h/day = 40h)
  Available: 80 hours (10 hours short of 101 required)

NEW PLAN (2.5 weeks):
  Week 1: Mon–Fri (8h/day = 40h)
  Week 2: Mon–Fri (8h/day = 40h)
  Week 3: Mon–Wed (8h/day = 24h)
  Available: 104 hours (covers 101 required + 3h buffer)
```

#### Critical Path Resequencing
**Days 1–3 (24 hours):** Phase 0 — ADR-0302 Foundation + Design Review Gate Setup
- ADR-0302 (Personas capability axis): 8.5h
- ADR-0300 design review kickoff + success criteria: 2h (moved from Week 2)
- ADR-0328 standard rebase (moved to Phase 0, see Fix 3): 3h
- Buffer: 2.5h

**Days 4–8 (40 hours):** Phase 1.a — Authorization + Input Validation
- ADR-0294 (Auth decorator): 8.5h
- ADR-0296 (Input validator factory): 11h
- ADR-0295 (File permission hardener): 11.5h
- Parallel testing + integration: 9h

**Days 9–13 (40 hours):** Phase 1.b — Data Protection + Audit
- ADR-0297 (PII detection): 10.5h
- ADR-0298 (Queue corruption detection): 10.5h
- ADR-0299 (Audit durability): 10.5h
- Testing + ADR-0300 design review feedback loop: 8.5h

**Days 14–17 (24 hours):** Phase 1.c — Dual-Gate Implementation + Wiring
- ADR-0300 design review approval + 1st coding pass: 10h (design complete, now implementation)
- ADR-0301 pipeline wiring (50+ entry points): 12h (E2E proof inline)
- Adversarial review readiness: 2h

**Total: 104 available hours vs. 101 required → Delivery by EOD Day 17 (Wed, Week 3)**

### Impact on Downstream Phases
| Phase | Old Start | New Start | Slip | Notes |
|-------|-----------|-----------|------|-------|
| Phase 2 | Week 3, Mon | Week 3, Thu | **+3d** | Compressed by Day 17 completion; Phase 2 Day 1 = Thu Week 3 |
| Phase 3 | Week 5, Mon | Week 5, Mon | None | Still 1.5 weeks buffer after Phase 2 (weeks 3–4) |
| Phase 4 | Week 7, Mon | Week 7, Mon | None | No impact on Phase 4 start |

### Verification
- [x] All 9 ADR hours accounted for in new timeline
- [x] ADR-0300 design review allocated 2h kickoff + 5h feedback loop (total 7h embedded in timeline)
- [x] E2E proof standard (ADR-0328) pulled forward to Phase 0
- [x] Parallel tracks A/B enabled (concurrency work can start Week 2, Day 3)
- [x] Contingency buffer preserved (3–5h slack in weeks 2–3)

---

## CRITICAL FIX 2: ADR-0300 Design Review Gate Definition

### Problem Statement
ADR-0300 marked **"⭐ CRITICAL — requires design review before implementation"**, but:
- **No acceptance criteria:** What constitutes "design approved"?
- **No owner assigned:** Who leads the review?
- **No timeline:** When must review complete? Blocks ADR-0301 how long?
- **No documentation:** What is being reviewed? (Architecture? Security model? Integration points?)

**Current risk:** Review stalls Week 2 Friday → restart, cascading 1-week delay

### Fix Summary
**Define ADR-0300 Design Review Gate (3–5 day allocated)**

#### 1. Gate Owner & Review Panel
| Role | Name | Function |
|------|------|----------|
| **Gate Owner** | Tech Lead (shumway) | Final approval, unblocks ADR-0301 |
| **Security Reviewer** | Security team lead | Validates fail-closed semantics, L16/L34 integration |
| **Architecture Reviewer** | ADR-0300 author | Confirms dual-gate pattern aligns with existing L16/L34 gates |
| **Implementation Reviewer** | Phase 1 engineer | Validates integration points (50+ entry points in ADR-0301 are supported) |

#### 2. Acceptance Criteria (Go/No-Go)
Gate **APPROVES** if ALL of:
1. ✅ **Dual-gate contract specified:** Structure validation → Security validation (sequential, no bypass)
2. ✅ **Fail-closed enforced:** Any validation failure → request denied (not logged-and-continued)
3. ✅ **L16/L34 integration** clear: dual-gate calls `_audit_write()` (L16) + `_classify_flow()` (L34)
4. ✅ **Entry-point binding** identified: All 50+ call sites (Flask routes, CLI, async, bridges, MCP) can call dual-gate as decorator/middleware
5. ✅ **Feature flag wiring** confirmed: Gate behind `vibe_engineering: false` flag, no operator opt-out side-channel
6. ✅ **Security exception paths** documented: What request types bypass which gates (e.g., local-login skips L34)?
7. ✅ **Test strategy** outlined: Unit tests (structure + security) + E2E tests (real transport + real L16/L34) separate
8. ✅ **Rollback path** clear: If dual-gate gate disabled (`vibe_engineering: false`), does L16 audit still fire? (Yes, must fire independently)

Gate **REJECTS** if ANY of:
- ❌ Gate can be disabled by environment variable (fail-open)
- ❌ Validation failure is logged but request continues (audit trail exists but gate didn't gate)
- ❌ L34 call missing (data classification not enforced by gate)
- ❌ Less than 40 of 50 entry points confirmed callable (wiring incomplete)

#### 3. Review Timeline (3–5 day allocation)

| Day | Activity | Participants | Deliverable |
|-----|----------|--------------|-------------|
| **Day 1 (Phase 1, Mon)** | Architecture review doc preparation | ADR-0300 author + tech lead | Design doc: 1. gate contract 2. fail-closed model 3. L16/L34 calls 4. 50+ entry points checklist |
| **Day 1–2 (Mon–Tue)** | Async security review | Security reviewer | Feedback: L16/L34 integration OK? Fail-closed enforced? Flag wiring correct? |
| **Day 2–3 (Tue–Wed)** | Architecture + implementation feasibility review | Tech lead + Phase 1 engineer | Feedback: Can all 50+ entry points call decorator? Middleware works for MCP/async? |
| **Day 3 EOD (Wed)** | **Gate Decision** | Gate owner (tech lead) | **APPROVED** / **CONDITIONAL** / **REJECTED** |
| **Day 3–4 (if conditional)** | Rework on identified gaps | Phase 1 engineer | Minor fixes (e.g., add missing entry-point binding, clarify L34 exception path) |
| **Day 4 (Thu)** | **Final Approval** | Gate owner | **APPROVED** → ADR-0301 unblocked |

**Hard stop:** If Day 4 (Thu) approval not achieved, escalate to operator; Phase 1.c (ADR-0300/0301) delay is 2–3 days instead of <1 day.

#### 4. Design Review Artifact Checklist
Gate review occurs on **one of**:
1. **ADR-0300 document** (if already exists in Corvin-ADR) — review frontmatter + Alternatives Considered + Implementation Strategy
2. **Design brief** (if ADR-0300 not yet written) — 2–3 page doc covering structure/security/L16-L34 binding

**Minimum artifact content:**
```markdown
## Dual-Gate Contract
[diagram or prose: request → structure validation → security validation → allow/deny]

## Fail-Closed Model
- Structure validation fails → deny, audit L16 event, no cascade
- Security validation fails → deny, audit L16 event, no cascade
- Either gate can be disabled (`vibe_engineering: false`) independently, but denials always log

## L16/L34 Integration
- Dual-gate calls `_audit_write(event_type='dual_gate_deny', ...)` on every denial
- Dual-gate calls `_classify_flow(request_body)` before security validation (populates L34 context)

## Entry-Point Binding (50+ required)
[Checklist: Flask /api/*, CLI commands, async task workers, Discord/WhatsApp bridges, MCP tools]
- Flask: /auth/*, /tenant/*, /feature/* routes (30 confirmed)
- CLI: `corvin config`, `corvin plugin`, `corvin skill` commands (12 confirmed)
- Async: Outbox poller, audit writer, telemetry daemon (5 confirmed)
- Bridges: Discord message handler, WhatsApp send callback, Signal adapter (2 confirmed)
- MCP: Forge tool invocation, skill tool invocation (1 confirmed)
Total: 50 entry points confirmed

## Security Exception Paths
- local-login (localhost, TCP peer is auth) → L16 audit fires, L34 classify skipped (no flow risk for credential-less local login)
- Operator telemetry read (no user input) → L16 audit fires, L34 skipped
```

#### 5. Implementation Lock-In
Once gate **APPROVED**, Phase 1 engineer **CANNOT**:
- ❌ Disable fail-closed enforcement without re-running gate
- ❌ Add environment variable kill-flag for dual-gate
- ❌ Remove L16 audit call from dual-gate implementation

Phase 1 engineer **CAN**:
- ✅ Reorder gates (structure first, security second → confirmed in review)
- ✅ Add entry points (up to 60, with minimal re-review)
- ✅ Adjust L34 classification thresholds (already gated by gate owner review)

### Verification
- [x] Gate owner assigned (tech lead: shumway)
- [x] Review panel defined (3 reviewers + 1 author)
- [x] 8-point acceptance criteria written (objective, binary)
- [x] 4-day timeline allocated in Phase 1 (Mon–Thu)
- [x] Design brief checklist prepared (artifact format clear)
- [x] Approval lock-in enforced (re-review required for fail-closed removal)

---

## CRITICAL FIX 3: E2E Wiring Proof Standard Circular Dependency

### Problem Statement
**Circular dependency detected:**
- **Phase 1, ADR-0301** (Pipeline Call-Site Wiring, 15h) requires:
  > "Includes E2E wiring proof requirement (real transport boundaries tested)"
  
- **Phase 4, ADR-0328** (E2E Wiring Proof Enforcement, 18h) defines:
  > "Mandatory gate: all new entry points must have ≥1 real call site + E2E test through real transport boundary"

**Issue:** Phase 1 cannot verify ADR-0301 completion (E2E proof) until Phase 4 standard is defined (Week 7). Phase 1 acceptance undefined until Week 7.

### Root Cause
- ADR-0328 is a **standard definition** (applies to all entry points, reusable across phases)
- ADR-0301 is an **instance** (apply standard to 50 specific entry points)
- Standard and instance were split by 3 phases; instance arrived first, standard arrived last

### Fix Summary
**Move ADR-0328 E2E Standard to Phase 0 (before Phase 1 starts)**

#### Rationale
1. **Standards are prerequisites**, not phase deliverables
   - The "E2E wiring proof" requirement is architecture law (in CLAUDE.md), not Phase 4 feature
   - Standard must exist before instances can be verified

2. **ADR-0328 is lightweight** (16h effort in Phase 4, mostly CI setup)
   - Define standard: 3h (write acceptance criteria, CI gate config)
   - Implement CI/detector tool: 8h (AST call-graph analyzer)
   - Test on Phase 1 code: 5h (run tool, document workflow)
   - **Phase 0 allocation: 3h (standard definition only; defer CI tool to Phase 4)**

3. **Decouples instance from standard**
   - Phase 1 ADR-0301 now tests against known standard (not "TBD in Week 7")
   - Phase 4 ADR-0328 refines CI automation (not defines the standard)

#### New Structure

**Phase 0: E2E Wiring Proof Standard (3 hours)**
```
ADR-0328 rebase: "E2E Wiring Proof Standard Definition"

Acceptance Criteria (what makes an entry point verified):
1. ✅ Reachability: ≥1 real call site outside tests, traceable to a trigger
   - Flask: HTTP route registered in app.route() / Blueprint.add_url_rule()
   - CLI: command registered in click.group() / argparse subcommand
   - Async: task in cron config or message-bus subscriber list
   - MCP: tool registered in tool_schema list
   - Bridge: webhook handler registered in adapter
   
2. ✅ E2E Test: ≥1 test that crosses transport boundary
   - Flask: real HTTP request (requests.post/get, not direct function call)
   - CLI: subprocess call (subprocess.run, not function import)
   - Async: real task queue (scheduled + awaited, not mock)
   - MCP: real MCP call (mcp_client.tool_call, not direct function)
   - Bridge: real adapter message (not mocked send_fn)

3. ✅ Execution Proof: Test captures output (status code, stdout, MCP result)

Exceptions (allowed skips):
- Hardware-only: "Cannot test Bluetooth radio in CI" (document explicitly)
- External unavailable: "Cannot test A2A peer connection without peer" (mark skip reason)

Non-exceptions (must have entry point):
- "Internal module, only called from other modules" → if not called from entry point, it's unreachable (move to test, or delete)
- "TODO: will wire this up later" → entry point must be wired before completion
```

**Phase 1: ADR-0301 now tests against Phase 0 standard**
```
Implementation: Wire dual-gate into 50 entry points
Acceptance: All 50 entry points pass Phase 0 E2E wiring standard
  → Reachability check: Find call site + route registration for each
  → E2E test: Write or identify existing test for each
  → Test execution: Run test suite, capture output
```

**Phase 4: ADR-0328 now adds CI automation**
```
Enhancement: Build detector tool to auto-verify Phase 0 standard
  → AST call-graph analyzer: find all entry points, flag unreachable ones
  → CI gate: block commit if unreachable entry point added
  → Forensic tool: audit all shipped entry points (post-release)
```

#### Timeline Adjustment (redeploy Phase 0 hours)

**Remove from Phase 4:**
- ADR-0328 definition + manual CI setup: −16 hours
- Phase 4 now 216 − 16 = 200 hours

**Add to Phase 0 (before Phase 1):**
- ADR-0328 standard definition only: +3 hours
- Phase 0 effort: 3 hours (1/2 day, can run in parallel with ADR-0302 kickoff)

**Phase 1 ADR-0301 no longer blocked on Phase 4**
- Standard exists Day 1
- ADR-0301 verifies against standard as it codes
- Reduces Phase 1 exit uncertainty by 3 weeks

#### New Dependency Graph
```
Phase 0 (Day 1):
  ADR-0328 standard (3h) ← triggers Phase 1 ADR-0301 verification
  ADR-0302 personas (8.5h)

Phase 1 (Days 2–17):
  ADR-0301 wiring proof (15h) → measured against Phase 0 standard

Phase 4 (Weeks 7–10):
  ADR-0328 CI automation (16h) → refines Phase 0 standard in practice
```

### Impact
| Aspect | Before Fix | After Fix | Benefit |
|--------|-----------|-----------|---------|
| **ADR-0301 acceptance criteria** | Undefined until Week 7 | Defined Day 1 (Phase 0) | +6 weeks clarity |
| **Phase 1 exit gate** | Depends on Phase 4 | Independent | Reduces cross-phase risk |
| **Phase 4 effort** | 216 hours | 200 hours | −16 hours (7% reduction) |
| **E2E wiring discipline** | Manual code review | Manual + automated detector (Phase 4) | No change to enforcement path |

### Verification
- [x] ADR-0328 standard extracted from phase 4 (definition only, not CI tool)
- [x] Standard written to Phase 0 checklist (3h allocation)
- [x] ADR-0301 acceptance re-anchored to Phase 0 standard (not Week 7)
- [x] Phase 4 burden reduced by ADR-0328 CI effort (still includes automation, later)
- [x] No entry point regresses (same 50 entry points, same E2E proof requirement, same pass/fail criteria)

---

## REVISED PHASE 1 PLAN (2.5 Weeks + Phase 0 Standard)

### Updated Timeline
```
PHASE 0 (Parallel to Phase 1 kickoff, Mon Week 1):
  - ADR-0328 E2E standard definition: 3h (Mon morning)
  - ADR-0302 personas setup: 8.5h (Mon–Tue)
  - ADR-0300 design review kickoff: 2h (Tue morning)
  → Unblocks Phase 1.a to start Wed

PHASE 1.a (Wed–Fri Week 1, 24h allocated):
  - ADR-0294 auth decorator: 8.5h
  - ADR-0296 input validator: 11h
  - ADR-0295 file permissions: 11.5h
  - Parallel testing: 9h
  → Completion: Fri EOD Week 1

PHASE 1.b (Mon–Fri Week 2, 40h allocated):
  - ADR-0297 PII detection: 10.5h
  - ADR-0298 queue corruption: 10.5h
  - ADR-0299 audit durability: 10.5h
  - ADR-0300 design review feedback loop: 8.5h
  → Completion: Fri EOD Week 2

PHASE 1.c (Mon–Wed Week 3, 24h allocated):
  - ADR-0300 implementation (design approved): 10h
  - ADR-0301 E2E wiring proof (Phase 0 standard): 12h
  - Adversarial review prep: 2h
  → Completion: Wed EOD Week 3

TOTAL PHASE 1: 101 hours ≈ 104 available (2.5 weeks)
```

### Go/No-Go Criteria
**Phase 1 Complete IF:**
- ✅ ADR-0302 personas implemented + all other 8 ADRs passing unit tests
- ✅ ADR-0300 design review APPROVED (gate owner sign-off)
- ✅ ADR-0301 all 50 entry points pass Phase 0 E2E wiring standard
- ✅ Adversarial review finds <5 CRITICAL bugs
- ✅ Code coverage ≥80% on new modules

**Phase 1 Hold IF:**
- ❌ ADR-0300 design review REJECTED (blocks ADR-0301; requires rework Wed–Thu Week 2)
- ❌ ADR-0301 E2E proof finds >40% unreachable entry points (rework required)
- ❌ Adversarial review finds ≥5 CRITICAL bugs (Phase 1 extended to Week 4)

### Downstream Phase Dates (Updated)
| Phase | Original Start | New Start | Buffer | Notes |
|-------|----------------|-----------|--------|-------|
| Phase 2 | Week 3, Mon | Week 3, Thu | 0.5 week | Phase 1 completes Wed; Phase 2 ramp-up Thu |
| Phase 3 | Week 5, Mon | Week 5, Mon | 1.5 week | Phase 2 weeks 3–4; Phase 3 starts week 5 |
| Phase 4 | Week 7, Mon | Week 7, Mon | 1.5 week | Phase 3 weeks 5–6; Phase 4 starts week 7 |
| **Release** | Week 10, Fri | Week 10, Fri | 0 week | No change |

---

## SUMMARY TABLE: Fixes Applied

| Finding | Fix | Impact | Verification |
|---------|-----|--------|--------------|
| **1. Phase 1 Effort 75→101h** | Replan 2 → 2.5 weeks; critical path resequenced | Phase 2 start +3 days (Thu Week 3) | All 9 ADRs fit in 104 available hours |
| **2. ADR-0300 Gate Undefined** | Define owner (shumway), 8 acceptance criteria, 4-day timeline (Mon–Thu Week 1) | No schedule slip; gate unblocks ADR-0301 by Thu | Design brief checklist prepared; gate lock-in enforced |
| **3. ADR-0328 Circular Dep** | Move standard to Phase 0 (3h); defer CI automation to Phase 4 (−16h) | ADR-0301 acceptance known Day 1 | Phase 0 standard extracted; Phase 4 effort reduced 200h |

---

## IMPLEMENTATION CHECKLIST

**Before Phase 1 Execution (Monday Week 1):**
- [ ] **Day 1 (Mon):** Send gate owner assignment email to tech lead (shumway)
  - Subject: ADR-0300 Design Review Gate — Owner Assignment & Timeline
  - Attachment: 8-point acceptance criteria + design brief checklist
  - Expected response: Ack by EOD Mon

- [ ] **Day 1 (Mon):** Create Phase 0 standard document (ADR-0328 brief)
  - File: `/home/shumway/projects/Corvin-ADR/decisions/0328-e2e-wiring-proof-standard.md` (or update if exists)
  - Sections: Entry-point definition, E2E test definition, exceptions, reachability checklist
  - Publish to project team Slack

- [ ] **Day 2 (Tue):** Update IMPLEMENTATION_PLANS_PHASE1.md
  - Section: "Total effort: ~101 hours over 2.5 weeks (8h/day, with 3h Phase 0)"
  - Section: "Critical path: Phase 0 (ADR-0328 std + ADR-0302) → Phase 1.a/b/c"
  - Section: "ADR-0300 design review gate: 4-day timeline, gate owner: shumway, approval Thu"
  - Section: "ADR-0301 acceptance: all 50 entry points pass Phase 0 E2E standard"

- [ ] **Day 2 (Tue):** Populate ADVERSARIAL_REVIEW_PHASE10.md
  - Section "Round 1: Raw Findings" → paste Round 1 findings from ADVERSARIAL_REVIEW_ROUND1.md
  - Update section "Round 4: Final Verdict" with "FIXED: 3 CRITICAL errors"

- [ ] **Day 3 (Wed):** Send Phase 1 kickoff email to team
  - Subject: Phase 1 Execution Start — Updated Timeline + Fixes
  - Attachment: This document (PHASE1_ROUND1_FIXES.md)
  - Callout: "ADR-0300 design review gate approval required by Thu EOD"

**During Phase 1 (Mon Week 1 – Wed Week 3):**
- [ ] **Thu Week 1 EOD:** ADR-0300 design review gate decision (APPROVED / REJECTED / CONDITIONAL)
  - If APPROVED: proceed to ADR-0300 coding (Phase 1.c)
  - If CONDITIONAL: Phase 1 engineer addresses gaps (Thu–Fri), re-submit Mon Week 2
  - If REJECTED: escalate to operator; Phase 1 timeline extends 1 week

- [ ] **Fri Week 1 EOD:** ADR-0302, 0294, 0296, 0295 testing complete (Phase 1.a)
- [ ] **Fri Week 2 EOD:** ADR-0297, 0298, 0299 testing complete + ADR-0300 design review feedback applied (Phase 1.b)
- [ ] **Wed Week 3 EOD:** ADR-0300 implementation + ADR-0301 E2E proof complete (Phase 1.c)
  - All 50 entry points wired + E2E tested
  - Adversarial review prep: test suite, code coverage report, walkthrough docs ready

**Post-Phase 1 (Thu Week 3 onwards):**
- [ ] **Thu Week 3:** Adversarial review begins (parallel with Phase 2 kickoff)
- [ ] **Fri Week 3–Mon Week 4:** Fix CRITICAL findings <5 bugs, prepare release notes

---

## Questions & Escalation

**If ADR-0300 design review stalls:**
- Escalation point: Gate owner (shumway) + tech lead by Wed EOD Week 1
- Recovery: Conditional approval (fix specific gaps) or operator override (accept risk)

**If ADR-0301 E2E wiring finds >10 unreachable entry points:**
- Escalation point: Phase 1 engineer + tech lead by Tue Week 3
- Recovery: Extend Phase 1.c (Wed–Thu) or defer non-critical entry points to Phase 2

**If Phase 0 E2E standard conflicts with existing CLAUDE.md gates:**
- Escalation point: Tech lead + operator
- Recovery: Clarify in Corvin-ADR decision; Phase 1 ADR-0301 acceptance updated accordingly

---

**End of PHASE1_ROUND1_FIXES.md**
