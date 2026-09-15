---
name: corvinOS_review_backend
description: CorvinOS Code Review: Backend Logic, State Machines, Error Handling, Dependencies
---

# CorvinOS Review: Backend Logic & Testing

## PHASE 9: BACKEND LOGIC

### 9a. Protocol & Wire Format
If API changed:
- [ ] Wire-format version bumped (v3.0.0 → v3.0.1 min)?
- [ ] JSON-Schema updated/validated?
- [ ] Backward-compat tested (old clients)?
- [ ] Examples in docs/diagrams updated?

### 9b. State Machines & Invariants
- [ ] State-transitions fully specified?
- [ ] Impossible states unreachable?
- [ ] Concurrent mutations serialized correctly?
- [ ] Timeout/retry logic has lower-bounds (no infinite loops)?

### 9c. Error Handling (Fail-Closed)
- [ ] Unexpected errors result in fail-closed behavior?
- [ ] No silent catch-all patterns?
- [ ] Audit-event before exception thrown (audit-first)?
- [ ] Error messages reveal no sensitive-info?

### 9d. Dependencies & Vulnerabilities
- [ ] `pip audit` / `npm audit` clean (all transitive)?
- [ ] No deprecated packages?
- [ ] Pinned versions sensible (not too loose)?
- [ ] License compatibility checked?

---

## PHASE 10: TESTING & COVERAGE

### 10a. Unit Tests
- [ ] Critical logic has unit-tests?
- [ ] Happy path + edge cases?
- [ ] Mocks correct? (No mock-vs-prod divergence)?
- [ ] Test-isolation: no shared state?

### 10b. Integration Tests
- [ ] Real DB / Real Filesystem for schema-critical code?
- [ ] Fixtures cleaned up after each test?
- [ ] Cross-module dependencies tested?
- [ ] No DB-connections leaking?

### 10c. E2E Tests (Backend)
- [ ] Critical user flows covered?
- [ ] Security-touching code (forge/policy/audit) tested E2E?
- [ ] Real subprocess, real filesystem, real bwrap?

### 10d. Coverage Metrics
- [ ] `pytest --cov` / `nyc` coverage ≥ 80% for changed lines?
- [ ] No dead code (100% coverage for new code)?
- [ ] Coverage report included in PR?

---

## PHASE 11: DATA FLOW & CORRECTNESS

### 11a. Input Validation
- [ ] All user-input sanitized?
- [ ] Type-checks at boundaries?
- [ ] No SQL-injection vectors?
- [ ] Size-limits enforced (prevent DoS)?

### 11b. State Consistency
- [ ] Transactions atomic?
- [ ] No race-conditions in concurrent updates?
- [ ] Rollback on error correct?

### 11c. Performance Patterns
- [ ] No O(n²) loops on large datasets?
- [ ] SQL-indexes on WHERE clauses?
- [ ] No N+1 query patterns?

---

## DECISION MATRIX

| Finding | Action |
|---|---|
| Coverage < 80% for critical code | REQUEST CHANGES |
| Unit/Integration/E2E failing | REJECT |
| Security code untested | REJECT |
| SQL-injection possible | REJECT |
| Audit-first invariant broken | REJECT |
| `pip audit` / `npm audit` fails | REQUEST CHANGES |
| All tests green, logic sound | → Continue to Docs & Quality |

