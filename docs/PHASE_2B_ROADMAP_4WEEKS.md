# Phase 2b Roadmap — 23 Deferred Bugs (4 Weeks, Target: 0 Findings)

**Date:** 2026-09-02  
**Status:** Planning  
**Duration:** 4 weeks (2026-09-09 → 2026-09-30)  
**Target:** Re-audit with 0 findings → Phase 2 re-implementation

---

## Executive Summary

Phase 2 adversarial review (2026-09-02) discovered **31 bugs**, of which **8 CRITICAL** were fixed immediately (ADR-0552). Remaining **23 bugs** (15 HIGH + 8 MEDIUM) are deferred to Phase 2b for systematic hardening.

| Priority | Count | Fixes Needed | Timeline |
|----------|-------|--------------|----------|
| **CRITICAL** | 8 | ✅ DONE (ADR-0552) | Done |
| **HIGH** | 15 | Phase 2b Week 1–3 | 2026-09-09 → 2026-09-23 |
| **MEDIUM** | 8 | Phase 2b Week 3–4 | 2026-09-16 → 2026-09-30 |

**Total effort:** ~40 hours (8h/week × 4 weeks + review overhead)

---

## Phase 2b Execution Plan

### Week 1: Query Safety + Audit Trail (5 bugs)

**Focus:** Crash prevention, validation, audit completeness

| Bug # | Issue | Severity | Files | Effort | Target |
|-------|-------|----------|-------|--------|--------|
| 5 | Path traversal in tenant_id | HIGH | feature_flags_skill.py | 2h | Validate alphanumeric only |
| 6 | Missing tenant ID validation in query | HIGH | event_store.py | 1h | Check tenant_id before loop |
| 7 | No tenant scope in EventEmitter | HIGH | event_emitter.py | 1h | Add tenant_id validation on emit() |
| 12 | Silent data loss on corrupted JSON | HIGH | event_store.py | 2h | Log warning + raise exception |
| 13 | KeyError not caught in reconstruction | HIGH | event_store.py | 1h | Validate required fields exist |

**Deliverable:** Query layer crash-safe, tenant isolation complete  
**Test:** Adversarial review on query layer (0 findings target)

### Week 2: Thread Safety + Durability (5 bugs)

**Focus:** Exception handling, shutdown guarantees, queue contracts

| Bug # | Issue | Severity | Files | Effort | Est. |
|-------|-------|----------|-------|--------|------|
| 9 | Exception in worker thread crashes loop | HIGH | event_emitter.py | 2h | Add exception recovery, restart loop |
| 10 | join(timeout) doesn't guarantee shutdown | HIGH | event_emitter.py | 2h | Check is_alive(), retry or raise |
| 11 | emit() silent loss on queue full | HIGH | event_emitter.py | 1h | Raise exception instead of False |
| 15 | Silent pass when audit log missing | HIGH | test_*.py | 2h | Assert file exists upfront |
| 16 | Wrong hash-chain logic in tests | HIGH | test_*.py | 1h | Change `or` to equality check |

**Deliverable:** EventEmitter thread-safe, tests fail loudly  
**Test:** Adversarial review on threading (0 findings target)

### Week 3: Resource Gates + Schema (7 bugs)

**Focus:** Memory safety, version tracking, field validation

| Bug # | Issue | Severity | Files | Effort | Target |
|-------|-------|----------|-------|--------|--------|
| 21 | Unbounded query result → OOM | MEDIUM | event_store.py | 3h | Add limit + offset parameters |
| 22 | count_events() materializes all | MEDIUM | event_store.py | 3h | Stream-count O(n) events |
| 14 | Schema version field lost | MEDIUM | event_store.py | 1h | Include version in reconstruction |
| 26 | No schema validation on load | MEDIUM | event_store.py | 2h | Validate required fields schema |
| 27 | No audit event for file corruption | MEDIUM | event_store.py | 1h | Log corruption timestamp + file |
| 17 | No test coverage for exception path | MEDIUM | test_*.py | 2h | Add test for execute() exception |
| 25 | Schema version not preserved | MEDIUM | event_store.py | 1h | Pass version in reconstruction |

**Deliverable:** Memory-safe queries, schema versioning, comprehensive tests  
**Test:** Adversarial review on resource/schema (0 findings target)

### Week 4: Integration + Re-audit (6 bugs)

**Focus:** Validation completeness, integration testing, final sweep

| Bug # | Issue | Severity | Files | Effort | Target |
|-------|-------|----------|-------|--------|--------|
| 18 | No input validation on flag ID | MEDIUM | feature_flags_skill.py | 1h | Validate flag_id format (alphanumeric) |
| 19 | No input validation on tenant ID | MEDIUM | feature_flags_skill.py | 1h | Validate tenant_id (already #5) |
| 20 | Unknown operation string not sanitized | MEDIUM | feature_flags_skill.py | 1h | Whitelist allowed operations |
| 23 | Daemon thread loses events | MEDIUM | event_emitter.py | 1h | Non-daemon thread (done in CRITICAL #7) |
| 24 | Large file allocations on read | MEDIUM | event_store.py | 2h | Implement cursor-based pagination |
| 28 | Latency not on error path | MEDIUM | feature_flags_skill.py | 1h | Include latency in all responses (done in CRITICAL #3) |

**Plus:** Full integration re-run + adversarial review (16h)

**Deliverable:** All 23 bugs fixed, integration tests green, re-audit ready  
**Final:** Merge Phase 2b → Re-implement Phase 2 with clean slate

---

## Quality Gates (Per Week)

### Week 1 Exit Gate
- [ ] Query tests pass (no crashes on malformed JSON)
- [ ] Tenant isolation verified (no cross-tenant leakage in queries)
- [ ] Audit trail complete (all operations logged)
- [ ] Code review: 0 findings on query layer

### Week 2 Exit Gate
- [ ] ThreadSafety tests pass (exception recovery, shutdown timeout)
- [ ] Test suite runs without silent passes
- [ ] Hash-chain integrity verified
- [ ] Code review: 0 findings on threading

### Week 3 Exit Gate
- [ ] Query limits prevent OOM (tested with 10M events)
- [ ] Schema versioning preserved (round-trip test)
- [ ] Exception path fully covered
- [ ] Code review: 0 findings on resource/schema

### Week 4 Exit Gate
- [ ] All input validation present
- [ ] Full integration test suite (end-to-end)
- [ ] **ADVERSARIAL REVIEW: 0 findings**
- [ ] Ready for Phase 2 re-implementation

---

## Cumulative Bug Fix Tracking

| Week | Bugs Fixed | Cumulative | Status |
|------|-----------|-----------|--------|
| CRITICAL (Done) | 8 | 8/31 | ✅ COMPLETE |
| Week 1 | 5 | 13/31 | → In Progress (2026-09-09) |
| Week 2 | 5 | 18/31 | → Planned |
| Week 3 | 7 | 25/31 | → Planned |
| Week 4 | 6 | 31/31 | → Planned |
| **Re-audit** | — | — | → Target: 0 findings |

---

## Risk Mitigation

| Risk | Mitigation | Owner |
|------|-----------|-------|
| Scope creep (more bugs found) | Time-box each week at 12h; defer to Phase 2c if needed | Shumway |
| Test suite incomplete | E2E wiring proof required for every fix (integration tests) | Code review |
| Regression in CRITICAL fixes | Regression test suite (8 CRITICAL tests remain active) | Test framework |
| Re-audit fails again | Adversarial review on HIGH/MEDIUM layer only (not full Phase 2) | Reviewer |

---

## Success Criteria

✅ **Phase 2b is DONE when:**
1. All 23 bugs fixed and committed
2. Adversarial re-review produces 0 findings
3. Integration test suite green (all 48 tests pass)
4. Regression tests confirm CRITICAL fixes still hold
5. Phase 2 ready for re-implementation with clean slate

---

## Appendix: Bug Registry (23 deferred bugs)

### HIGH Severity (15 bugs)

| # | Issue | Location | Severity | Est. Hours |
|---|-------|----------|----------|-----------|
| 5 | Path traversal in tenant_id | feature_flags_skill:55 | HIGH | 2 |
| 6 | Missing tenant validation in query | event_store:80 | HIGH | 1 |
| 7 | No tenant scope in EventEmitter | event_emitter:46 | HIGH | 1 |
| 9 | Exception in worker thread | event_emitter:30 | HIGH | 2 |
| 10 | join(timeout) guarantee missing | event_emitter:52 | HIGH | 2 |
| 11 | emit() silent loss on full queue | event_emitter:46 | HIGH | 1 |
| 12 | Silent data loss on corrupted JSON | event_store:102 | HIGH | 2 |
| 13 | KeyError in reconstruction | event_store:89 | HIGH | 1 |
| 14 | Schema version field omitted | event_store:88 | HIGH | 1 |
| 15 | Silent pass when audit log missing | test_*.py:52 | HIGH | 2 |
| 16 | Wrong hash-chain logic | test_*.py:100 | HIGH | 1 |
| 26 | No schema validation on load | event_store:— | HIGH | 2 |
| 27 | No audit for file corruption | event_store:102 | HIGH | 1 |
| 18 | No validation on flag ID | feature_flags_skill:— | HIGH | 1 |
| 20 | Unknown operation string unsan. | feature_flags_skill:189 | HIGH | 1 |

### MEDIUM Severity (8 bugs)

| # | Issue | Location | Severity | Est. Hours |
|---|-------|----------|----------|-----------|
| 21 | Unbounded query → OOM | event_store:51 | MEDIUM | 3 |
| 22 | count_events() materializes | event_store:107 | MEDIUM | 3 |
| 17 | No exception path test coverage | feature_flags_skill:— | MEDIUM | 2 |
| 25 | Version lost on round-trip | event_store:88 | MEDIUM | 1 |
| 24 | Large allocations on file read | event_store:66 | MEDIUM | 2 |
| 23 | Daemon thread loses events | event_emitter:20 | MEDIUM | 1 |
| 19 | No validation on tenant ID | feature_flags_skill:— | MEDIUM | 1 |
| 28 | Latency missing on error | feature_flags_skill:305 | MEDIUM | 1 |

---

**Report Generated:** 2026-09-02  
**Next Review:** 2026-09-09 (Phase 2b Week 1 Exit Gate)  
**Contact:** Shumway (autonomous planning + execution)
