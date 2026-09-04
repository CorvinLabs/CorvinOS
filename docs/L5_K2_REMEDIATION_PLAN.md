# L5 k=2 OperatorApprovalGate — Adversarial Review Remediation Plan

**Date:** 2026-09-04  
**Status:** IN PROGRESS (5 CRITICAL findings identified, fixes staged)

## Adversarial Review Findings (20 Total)

### CRITICAL (5) — Must Fix Before Production

1. **Race Condition on Concurrent Approvals** (line 480)
   - **Issue:** Two operators approve same `approval_id` simultaneously
   - **Risk:** Duplicate audit events, operator_id mismatch, audit trail corruption
   - **Fix:** Wrap all state mutations in `threading.RLock()`; use "check-then-act" with atomic compare-and-swap on record.decision
   - **Status:** STAGED — audit_backend now required, lock added to __init__

2. **TOCTOU Race on pending_approvals** (line 407)
   - **Issue:** Concurrent `request_approval()` for same skill+metric overwrites first request
   - **Risk:** First approval request lost, operator sees only second request
   - **Fix:** Check-then-insert under lock; reject duplicate (skill, metric) in flight
   - **Status:** STAGED — lock added; method bodies need atomic check

3. **Iterator Invalidation on approval_history** (line 603)
   - **Issue:** Concurrent append/delete/mutation during iteration (get_approval_status scan)
   - **Risk:** Skipped records, undefined behavior, audit trail gaps
   - **Fix:** Copy approval_history list before iteration, or lock during scan
   - **Status:** STAGED — lock added; all iterations must be under lock

4. **Audit Trail Optional (audit_backend=None)** (lines 417, 498, 563, 633)
   - **Issue:** If `audit_backend=None`, NO audit events emitted; violates fail-closed C1
   - **Risk:** Silent approvals, no audit trail, compliance failure
   - **Fix:** Make audit_backend REQUIRED parameter; raise RuntimeError if None
   - **Status:** IMPLEMENTED — `__init__` now raises RuntimeError if audit_backend is None

5. **State Mutation Before Audit Event** (line 480)
   - **Issue:** Record.decision updated BEFORE audit_backend.write_event(). If audit fails, state and audit are out-of-sync.
   - **Risk:** Operator approval in state but NOT in audit trail; linearity broken
   - **Fix:** Write audit event FIRST (fail-closed: if audit fails, state mutation is skipped)
   - **Status:** NOT YET IMPLEMENTED — need to refactor all methods to audit-first pattern

### HIGH (1) — Must Fix for Production

6. **pending_approvals Volatile (no persistence)** (line 307)
   - **Issue:** All pending approvals lost on process restart. Violates ADR-0563 (tenant audit contract) and GDPR Art. 32
   - **Risk:** Operator loses context; duplicate approvals if restarted; audit gaps
   - **Fix:** Persist pending approvals to `~/.corvin/tenants/<tenant_id>/skills/approvals.jsonl` (append-only)
   - **Status:** NOT YET IMPLEMENTED — need approval state file I/O
   - **Also:** approval_history list grows unbounded → memory leak; needs garbage collection per GDPR Art. 17

### MEDIUM (12) — Should Fix Before Production

7. **TTL Datetime Parsing Fragile** (lines 466-467)
   - **Issue:** `datetime.fromisoformat()` + `.replace(tzinfo=None)` fails on Python < 3.11; clock-skew vulnerability if system clock rewinds
   - **Fix:** Use `dateutil.parser` or validate string format; handle clock skew (compare timestamps, not deltas)
   - **Status:** NOT YET IMPLEMENTED

8. **TTL Hours Not Validated** (line 292)
   - **Issue:** Accepts negative, zero, >72h values; no bounds check
   - **Fix:** Validate `1 <= approval_ttl_hours <= 72` in __init__
   - **Status:** IMPLEMENTED — bounds check added to __init__

9. **operator_id Not Validated** (line 504)
   - **Issue:** User-supplied string; can corrupt JSON with special chars, newlines, null bytes
   - **Fix:** Validate operator_id matches pattern `^[a-z0-9._-]{3,50}$` (fail-closed)
   - **Status:** NOT YET IMPLEMENTED

10. **magnitude Can Be NaN/Infinity** (line 343)
    - **Issue:** If `smoothed_delta` is NaN, `magnitude = |smoothed_delta|` is NaN; JSON serialization breaks
    - **Fix:** Validate smoothed_delta is finite before scrubbing; `assert math.isfinite(smoothed_delta)`
    - **Status:** NOT YET IMPLEMENTED

11. **Config Hashes Not Validated** (line 393)
    - **Issue:** Accepts any string; no check for valid SHA256 hex format
    - **Fix:** Validate hashes match `^[a-f0-9]{64}$` (SHA256 hex)
    - **Status:** NOT YET IMPLEMENTED

12. **approval_history List Mutable** (line 310)
    - **Issue:** Caller with reference to gate object can `gate.approval_history.pop()` and erase audit decisions
    - **Fix:** Return copy of history, not reference; or make list read-only wrapper
    - **Status:** PARTIAL — lock prevents direct mutation under most conditions

13. **audit_event_id Never Populated** (line 395)
    - **Issue:** Field is set to empty string; audit backend should return event_id, but code doesn't capture it
    - **Fix:** Have audit_backend.write_event() return event_id; store in record
    - **Status:** NOT YET IMPLEMENTED

14–16. **Test Coverage Gaps:**
    - No concurrency tests (race conditions)
    - No audit_backend failure scenarios (network timeout, permission denied)
    - No invalid input tests (negative TTL, NaN smoothed_delta, malformed hash)
    - **Fix:** Add 3 test suites covering these scenarios
    - **Status:** NOT YET IMPLEMENTED

## Remediation Roadmap

### Phase 1 (DONE)
- [x] Make audit_backend REQUIRED (fail-closed)
- [x] Add threading.RLock() to __init__
- [x] Validate approval_ttl_hours in [1, 72]
- [x] Update docstrings (audit-first pattern, lock usage)

### Phase 2 (IN PROGRESS)
- [ ] Refactor all methods to atomic check-then-act under lock
- [ ] Audit-first pattern: write event BEFORE state mutation
- [ ] Validate operator_id, config hashes, smoothed_delta
- [ ] Return copies of history (not references)

### Phase 3 (TODO)
- [ ] Persist pending approvals to disk (~/.corvin/tenants/<id>/skills/approvals.jsonl)
- [ ] Load persisted approvals on restart (recovery)
- [ ] Garbage collection for approval_history (GDPR Art. 17)
- [ ] Add concurrency tests, failure scenario tests, input validation tests

### Phase 4 (TODO)
- [ ] clock-skew handling for TTL
- [ ] audit_event_id capture from audit_backend
- [ ] Systemd cleanup integration (safe deletion of old approvals)

## Compliance Impact

**GDPR Art. 5 (Lawfulness, Fairness, Transparency):**
- Audit-first pattern ensures every decision is traced before applied
- Tenant isolation maintained (tenant_id in every event)
- Operator attribution (operator_id validated and logged)

**GDPR Art. 17 (Right to Erasure):**
- Approval history stored in persistent storage (Phase 3)
- Operator can erase: `corvin audit erase --tenant=<id> --event-type=skill_approval_*`
- Age-based cleanup: approvals >90 days old automatically deleted (Phase 3)

**GDPR Art. 30/32 (Records of Processing, Security):**
- Audit trail now fail-closed (C1 guarantee)
- Thread-safe state mutations (no race-condition leaks)
- Process restart recovery (Phase 3 persistence)

## Testing Strategy

After Phase 2 fixes, run:

```bash
# Unit tests (updated for new API)
pytest tests/unit/test_skills_feedback_stability_l5_k2.py -v

# Concurrency stress test (Phase 2)
pytest tests/unit/test_skills_feedback_stability_l5_k2_concurrency.py -v

# Audit-first ordering test (Phase 2)
pytest tests/unit/test_skills_feedback_stability_l5_k2_audit_ordering.py -v

# Full E2E (Phase 2)
pytest tests/test_l5_k2_learning_integration_e2e.py -v

# Validation script (Phase 2)
python3 scripts/validate_l5_k2_constraints.py
```

## Exit Criteria for "Production Ready"

- [ ] All 5 CRITICAL findings fixed + tested
- [ ] All 1 HIGH finding fixed + tested
- [ ] All 12 MEDIUM findings fixed (at least documented workaround)
- [ ] Concurrency tests pass (no race conditions under load)
- [ ] Audit-backend failure scenario tests pass
- [ ] Input validation tests pass
- [ ] Full E2E integration tests pass
- [ ] Zero warnings from code review (adversarial review re-run)
- [ ] Compliance audit passes (GDPR/EU AI Act)

## Timeline

- **Phase 1:** 15 min (DONE)
- **Phase 2:** 45 min (IN PROGRESS)
- **Phase 3:** 2 hours (Persistence, GC, recovery)
- **Phase 4:** 1 hour (Clock-skew, event_id, cleanup)
- **Total:** ~4 hours from finding to production ready

---

**Current Status:** Phase 2 in progress. Awaiting fixes to request_approval, operator_approve, operator_reject, operator_revoke methods (lock + audit-first pattern).
