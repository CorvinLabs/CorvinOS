# Phase 4 Autonomous Execution Dispatch Guide

**Status:** Ready to Execute  
**Date:** 2026-09-19  
**Target Completion:** 2026-09-27 (8-day sprint)

---

## Executive Summary

**Phase 2B:** ✅ COMPLETE (ModelTier migration)  
**Phase 4:** 🟡 READY (110h, 4 parallel streams, dependency-ordered)

This document provides autonomous agents with clear execution instructions for Phase 4.

---

## Phase 4 Structure: 4 Parallel Streams

### Stream A: Boot Layer Foundation (35h, Priority 1)
**Dependencies:** None (foundational)  
**ADRs:** ADR-0303, ADR-0304, ADR-0305, ADR-0307

| ADR | Task | Est. Hours | Quality Gates |
|-----|------|-----------|-----------------|
| ADR-0303 | ParallelExecutor (work dispatch) | 9h | k=1-5 LDD, E2E proof, 18 unit tests |
| ADR-0304 | AsyncQueue (work serialization) | 8h | k=1-5 LDD, E2E proof, 15 unit tests |
| ADR-0305 | WorkerPool (thread/async management) | 9h | k=1-5 LDD, E2E proof, 20 unit tests |
| ADR-0307 | SkillExecutor (run & monitor) | 9h | k=1-5 LDD, E2E proof, 25 unit tests |

**Execution Order:** ADR-0303 → ADR-0304 → ADR-0305 → ADR-0307  
**Checkpoint:** All 4 ADRs ACCEPTED, 78 unit tests passing, E2E proof for each

**Per-ADR Workflow:**
1. Read ADR from `/home/shumway/projects/Corvin-ADR/decisions/`
2. Run k=1 Dialectical Reasoning (surface design choices)
3. Implement code (create files in `core/executor/` per ADR paths)
4. Write unit tests (tests/unit/ per ADR test specs)
5. Run E2E wiring proof (verify real call sites, not just unit tests)
6. Emit audit events (ADR-0232 compliance)
7. Commit with ADR reference: `feat(adrs): add ADR-XXXX — [title] [ADR-XXXX]`
8. Verify: `git log --oneline -1` shows ADR reference

---

### Stream B: Registry Unification (35h, Priority 2)
**Dependencies:** Stream A (needs executor + worker pool running)  
**ADRs:** ADR-0301, ADR-0306, ADR-0308/309 (if exists)

| ADR | Task | Est. Hours | Quality Gates |
|-----|------|-----------|-----------------|
| ADR-0301 | Pipeline entry point wiring | 8h | k=1-5 LDD, E2E proof, 12 unit tests |
| ADR-0306 | Skill Selector (tool selection logic) | 12h | k=1-5 LDD, E2E proof, 20 unit tests |
| ADR-0308 | Plugin Registry Unification* | 15h | k=1-5 LDD, E2E proof, 25 unit tests |

*Check if ADR-0308/309 exist in Corvin-ADR; if not, skip or create per ADR Gate

**Execution Order:** ADR-0301 → ADR-0306 → ADR-0308  
**Checkpoint:** All ADRs ACCEPTED, registry tests green, wiring verified

---

### Stream C: Protective Layers (40h, Priority 3)
**Dependencies:** Stream A (needs executor running)  
**ADRs:** ADR-0310, ADR-0311, ADR-0312, ADR-0313

| ADR | Task | Est. Hours | Quality Gates |
|-----|------|-----------|-----------------|
| ADR-0310 | Circuit Breaker (cascading failure protection) | 10h | k=1-5 LDD, E2E proof, adversarial tests |
| ADR-0311 | Rate Limiter (resource exhaustion) | 10h | k=1-5 LDD, E2E proof, load tests |
| ADR-0312 | Metrics Pipeline (observability) | 10h | k=1-5 LDD, E2E proof, 30+ unit tests |
| ADR-0313 | Skill Persistence (storage) | 10h | k=1-5 LDD, E2E proof, ACID tests |

**Execution Order:** ADR-0310 (parallel-safe) → ADR-0311 (parallel-safe) → ADR-0312 → ADR-0313  
**Checkpoint:** All ADRs ACCEPTED, load tests passed, no performance regressions

---

### Stream D: E2E Testing (20h, Priority 4)
**Dependencies:** Streams A, B, C (tests all layers)  
**Goal:** Expand tests from 231 → 400+, no regressions

| Task | Est. Hours | Quality Gates |
|------|-----------|-----------------|
| Playwright browser tests | 8h | 100+ new browser tests, all passing |
| Performance baselines | 5h | Capture baseline latency/throughput |
| Regression suite | 4h | 100+ regression tests from prior phases |
| Documentation | 3h | Test coverage report, performance metrics |

**Execution Order:** Parallel across all (tests are independent)  
**Checkpoint:** 400+ tests passing, 0 regressions, performance baseline established

---

## Autonomous Execution Rules

### Quality Gates (Mandatory)

**Every commit must pass:**
1. ✅ k=1 Dialectical Reasoning (surface assumptions)
2. ✅ k=2 Red/Green tests (fail → pass)
3. ✅ k=3 E2E wiring proof (real call sites, not unit tests)
4. ✅ k=4 Refinement (code review, simplification)
5. ✅ k=5 Documentation (update CLAUDE.md, ADR paths)

**No exceptions:** If k=X fails, STOP, diagnose, fix inline.

### Blocker Handling

| Scenario | Action |
|----------|--------|
| **Test fails** | Stop, read test failure, fix code, re-run (max 2 retries) |
| **Import error** | Stop, trace import path, verify path in ADR, fix immediately |
| **Audit event missing** | Stop, add audit.write() call per ADR-0232, verify in audit.jsonl |
| **E2E proof fails** | Stop, find real call site, trace wiring, create test that exercises it |

**After 2 retry attempts on same blocker:** Document the issue, skip to next task, escalate in final report.

### Commit Message Format

```
feat(stream-X): add ADR-XXXX — [title] [ADR-XXXX]

Per-stream implementation of [ADR title]. Implements:
- [Bullet point 1: what was built]
- [Bullet point 2: tests added]
- [Bullet point 3: compliance gates]

Quality gates (k=1-5):
- ✅ k=1 Dialectical Reasoning: [1-line summary]
- ✅ k=2 Red/Green: [test count] unit tests passing
- ✅ k=3 E2E Wiring: [real call site description]
- ✅ k=4 Refinement: [review summary]
- ✅ k=5 Docs: [ADR paths updated]

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
```

---

## Parallel Execution Strategy

**Start simultaneously:**
- **Day 1-3:** Stream A (foundational, unblocks others)

**After Stream A checkpoint (Day 3):**
- **Day 3-5:** Stream B (builds on A)
- **Day 3-6:** Stream C (parallel to B, no dependencies)
- **Day 3-7:** Stream D (parallel to B+C, tests all)

**Final checkpoint (Day 7-8):**
- All ADRs ACCEPTED
- 400+ tests passing
- 0 regressions
- Release validation passes

---

## Success Criteria

| Gate | Criteria |
|------|----------|
| **ADR Completion** | All 11 ADRs marked ACCEPTED in Corvin-ADR/decisions/ |
| **Test Coverage** | 400+ tests passing, 0 failures, 90%+ code coverage |
| **Regressions** | Phase 1-3 tests still passing (0 regressions) |
| **Performance** | <5% latency increase, no memory leaks |
| **Compliance** | All audit events emitted (ADR-0232), GDPR gates functional |
| **Release Readiness** | No HIGH/CRITICAL security findings from adversarial review |

---

## Failure Escalation

If blocked on same issue after 2 retries:

1. Document the blocker (file, line, error message)
2. Provide context (ADR path, test failure, import trace)
3. Skip to next ADR
4. Continue execution
5. Report in final summary under "BLOCKERS"

---

## Autonomous Handoff Checklist

- [ ] Phase 2B completion committed (`196ecda0`)
- [ ] Phase 4 roadmap documented (this file)
- [ ] Stream A ADRs identified (ADR-0303-0307)
- [ ] Execution order verified (dependencies checked)
- [ ] Quality gates understood (k=1-5 LDD)
- [ ] Commit format validated
- [ ] Blocker escalation rules clear

**Launch Command:**

```bash
cd /home/shumway/projects/CorvinOS
git pull origin main

# Start Stream A execution
# Agent dispatches to ADR-0303 (ParallelExecutor)
agent --role stream-a-executor --adr ADR-0303 --ldd-depth 5
```

---

**Ready for autonomous execution. Launch Stream A.**
