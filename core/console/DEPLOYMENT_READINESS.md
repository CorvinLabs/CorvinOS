# Session Persistence — Production Deployment Checklist

**Date:** 2026-09-04  
**Target:** Production Go-Live Decision  
**Status:** ✅ Phase 2 Implementation Complete | 🔄 Adversarial Review in Progress

## Phase 2 Completion Summary

### ✅ Implemented Features

| Feature | Status | Tests | Commit |
|---------|--------|-------|--------|
| **CRITICAL #1: Cache Tenant Isolation** | ✅ | 3/3 passing | b0538d60 |
| **CRITICAL #2: Write Failure Failsafe** | ✅ | Existing | 1f70f154 |
| **CRITICAL #3: Audit Backend Integration** | ✅ | Integrated | b0538d60 |
| **HIGH #4: Cleanup Logic Unification** | ✅ | Integrated | 48de1d0e |
| **HIGH #5: Cache Coherency** | ✅ | Integrated | b0538d60 |
| **HIGH #6: Cleanup Validation** | ✅ | Integrated | 48de1d0e |
| **MEDIUM: Tmp File Cleanup** | ✅ | Integrated | 48de1d0e |
| **MEDIUM: Fsync Durability** | ✅ | Integrated | 48de1d0e |

### ✅ Testing Complete

**Unit + Integration Tests:**
- 18/18 tests passing
- 3 new cache isolation tests (CRITICAL #1 verification)
- All existing recovery, cleanup, concurrency tests still passing

**Stress Tests:**
- 1000 concurrent sessions (10 tenants × 100 sessions each)
- Cross-tenant access blocked (✅ isolation verified)
- Cache hit rate: 100%
- No data corruption or lost sessions

**App Verification:**
- Console app creates successfully
- Lifespan context manager ready
- Routes mounted
- Static files configured

### 📋 Pre-Production Checklist

**Code Quality:**
- ✅ Syntax check passed
- ✅ Type checking passed
- ✅ All tests green
- ⏳ Adversarial Review #2 (in progress)

**Security:**
- ✅ Cache tenant isolation implemented
- ✅ Audit events wired to backend
- ✅ Chmod validation (from Phase 1)
- ✅ Thread-safe statistics (from Phase 1)
- ✅ Fail-closed on bootstrap errors (from Phase 1)
- ⏳ Adversarial Review #2 findings

**Deployment Readiness:**
- ✅ Phase 2 code committed
- ✅ Documentation updated (ADR-0566)
- ✅ Backward compatible (cache optional)
- ⏳ Final review approval

## Decision Criteria

### GO Conditions (All Required)
```
[?] Adversarial Review #2 shows 0 findings
[?] All CRITICAL/HIGH fixes verified
[?] Cache isolation tested under concurrent load
[?] Audit events verified in audit_backend
[?] Operator approval (manual sign-off)
```

### NO-GO Conditions (Any One Blocks)
```
[ ] Any unfixed CRITICAL finding
[ ] Audit integration incomplete
[ ] Cache isolation bypass found
[ ] Review shows HIGH-severity issues
[ ] Stress tests fail
```

## Deployment Plan

### Phase 2D: Immediate (Upon Review Approval)

1. **Soak Testing** (1-2 hours)
   - Deploy to staging environment
   - Monitor audit events in audit.jsonl
   - Verify cache hit rates
   - Check for any orphaned tmp files

2. **Production Deployment**
   - Blue-green deploy (keep old console running)
   - Drain connections (max 30s timeout)
   - Start new console with Phase 2 code
   - Verify bootstrap succeeded (check logs)
   - Run quick smoke test (login + session creation)

3. **Monitoring**
   - Watch audit events: `console_session_created/loaded/ended`
   - Monitor cache stats: `cache_hit_rate_percent` (target: >95%)
   - Check cleanup validation failures (should be 0)
   - Alert on write failure counters (should be 0)

### Phase 2E: Post-Deployment (Week 1)

1. **Validation**
   - Verify all sessions persist across restarts
   - Check cache isolation holds under normal load
   - Monitor audit trail for completeness
   - Verify persistent sessions survive 90 days

2. **Optimization**
   - Tune cache size if needed (default 1000)
   - Adjust cleanup max_age if needed (default 86400s)
   - Monitor cleanup_orphaned_tmp_files() impact

3. **Rollback Plan**
   - If critical issues: revert to Phase 1 (commit 1f70f154)
   - Sessions will work but without cache + full audit
   - No data loss (all sessions on disk)

## Operator Runbooks

### Troubleshooting

**Symptom: Cache hit rate low (<50%)**
- Check cache size (default 1000): might be too small
- Verify no cache invalidations triggered
- Check concurrent session count

**Symptom: Cleanup validation failures**
- Check disk space: `df /path/to/.corvin`
- Check permissions: `ls -ld ~/.corvin/global/console/sessions`
- Run manual cleanup: SessionManager.cleanup_expired_sessions()

**Symptom: Audit events missing**
- Verify audit_backend is installed and active
- Check: `audit_backend.get_active()` returns non-None
- Check audit.jsonl for fanout events

**Symptom: Sessions not persisting**
- Verify Phase 2 deployed correctly
- Check SessionManager.cache_stats() (should show cache enabled)
- Check audit trail for session_created events

### Manual Operations

**Force session cleanup:**
```python
from corvin_console.session_manager import get_session_manager
manager = get_session_manager()
await manager.cleanup_orphaned_tmp_files()
await manager.cleanup_expired_sessions(max_age_s=3600)
```

**Check cache stats:**
```python
from corvin_console.session_manager import get_session_manager
manager = get_session_manager()
print(manager.cache_stats())
```

**Verify tenant isolation:**
```python
# Try to access tenant_a session from tenant_b
cached = manager.cache_get(sid, "tenant_b")
assert cached is None, "Isolation bypass!"
```

## Timeline

| Date | Phase | Status |
|------|-------|--------|
| 2026-09-04 | Phase 2 Implementation | ✅ Complete |
| 2026-09-04 | Adversarial Review #2 | 🔄 In Progress |
| 2026-09-05 | GO/NO-GO Decision | ⏳ Pending |
| 2026-09-05 | Staging Soak Test | ⏳ If GO |
| 2026-09-05 | Production Deployment | ⏳ If GO |
| 2026-09-06–2026-09-12 | Post-Deployment Monitoring | ⏳ If deployed |

## References

- **ADR-0565:** Phase 1 Architecture (session storage, recovery, cleanup)
- **ADR-0566:** Phase 2 Security Hardening (this roadmap)
- **SESSION_PERSISTENCE.md:** User-facing documentation
- **Commit 1f70f154:** Phase 1 implementation
- **Commit b0538d60:** Phase 2 CRITICAL fixes
- **Commit 48de1d0e:** Phase 2 HIGH + MEDIUM fixes

## Approval Sign-Off

```
Code Review:    [ ] Approved by: _________________
Adversarial:    [ ] 0 findings | [ ] N findings (documented)
Operations:     [ ] Approved by: _________________
Legal/Compliance: [ ] Approved by: _________________
Product:        [ ] Approved by: _________________
```

---

**Generated:** 2026-09-04 by Phase 2 Implementation  
**Status:** Awaiting Adversarial Review completion and approval
