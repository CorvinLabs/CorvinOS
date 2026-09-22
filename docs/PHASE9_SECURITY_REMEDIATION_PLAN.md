# 🔴 Phase 9 Security Remediation Plan

**Status:** Active  
**Date:** 2026-09-22  
**Target Completion:** 2026-09-26 (4 days)  
**Execution:** 4 Parallel Autonomous Streams

---

## 📋 REMEDIATION SCOPE: 21 Issues (13 Critical + 8 High)

### **Stream 1: Audit Backend Fixes (P0 Critical)**

**4 issues to fix:**

1. **Intent Router** — Audit failure not fail-closed
   - File: `core/control_plane/intent_router.py`
   - Fix: Import real `audit_backend`, wire to all audit calls
   - Lines: 45-50 (imports), 120-130 (audit write)

2. **Plugin Manager** — Audit in volatile memory only
   - File: `core/control_plane/plugins.py`
   - Fix: Wire to core audit chain, not local list
   - Lines: 70-80, 150-160

3. **Subsystem Manager** — Same memory issue
   - File: `core/control_plane/subsystems.py`
   - Fix: Persist to core chain
   - Lines: 60-70

4. **Override Authority** — MockAuditBackend in production
   - File: `core/control_plane/override_authority.py`
   - Fix: Remove MockAuditBackend, wire real backend
   - Lines: 44-48

**Exit Criteria:**
- [ ] All 4 modules use real audit_backend
- [ ] Audit events persisted to core chain (not memory)
- [ ] Tests verify audit persistence
- [ ] Merged to main

---

### **Stream 2: Auth & CSRF Fixes (P0 Critical)**

**4 issues to fix:**

5. **Privilege Escalation** — add_approver() before auth
   - File: `core/console/routes/control_plane_overrides.py`
   - Fix: Move auth check BEFORE add_approver call
   - Lines: 206, 252
   - Change: `@router.post()` → `@router.post(@Depends(require_session))`

6. **Snapshots Zero Auth** — Unauthenticated endpoints
   - File: `core/console/routes/control_plane_snapshots.py`
   - Fix: Add `@Depends(require_session)` to all 6 endpoints
   - Lines: 71-150

7. **Missing CSRF** — Plugin Manager mutations
   - File: `core/console/routes/control_plane_plugins.py`
   - Fix: Add `@Depends(require_csrf)` to POST/DELETE routes
   - Lines: 53-107

8. **Missing CSRF** — Subsystem Manager mutations
   - File: `core/console/routes/control_plane_subsystems.py`
   - Fix: Add `@Depends(require_csrf)` to POST/DELETE routes
   - Lines: Similar pattern

**Exit Criteria:**
- [ ] Privilege escalation fixed (auth checked first)
- [ ] All endpoints require session auth
- [ ] All mutations require CSRF token
- [ ] Tests verify auth failures properly rejected
- [ ] Merged to main

---

### **Stream 3: Tenant Isolation Fixes (P0 Critical)**

**3 issues to fix:**

9. **Hardcoded tenant_id="default"** — Plugin Manager
   - File: `core/control_plane/plugins.py`
   - Fix: Extract from session (get_current_tenant())
   - Lines: 70

10. **Hardcoded operator_id** — Plugin Manager
    - File: `core/control_plane/plugins.py`
    - Fix: Extract from session (get_current_user())
    - Lines: 71

11. **Unfiltered audit log** — Plugin Manager
    - File: `core/control_plane/plugins.py`
    - Fix: Filter audit queries by tenant_id
    - Lines: Audit query methods

**Exit Criteria:**
- [ ] No hardcoded tenant_id/operator_id
- [ ] All tenant operations filtered by session tenant
- [ ] Multi-tenant tests verify isolation
- [ ] Merged to main

---

### **Stream 4: Input Validation & Gates (P1 High)**

**6 issues to fix:**

12. **Missing @consent_required** — Plugin operations
    - File: `core/console/routes/control_plane_plugins.py`
    - Fix: Add `@Depends(consent_required)` decorator
    - Lines: Installation/enable/disable routes

13. **Missing @consent_required** — Subsystem operations
    - File: `core/console/routes/control_plane_subsystems.py`
    - Fix: Add `@Depends(consent_required)` decorator

14. **Missing @consent_required** — Snapshot operations
    - File: `core/console/routes/control_plane_snapshots.py`
    - Fix: Add `@Depends(consent_required)` decorator

15. **boot_layer validation** — Accept arbitrary strings
    - File: `core/control_plane/plugins.py`
    - Fix: Validate against BootLayer enum
    - Lines: Install endpoint

16. **Snapshot restore not implemented** — Returns content but doesn't restore
    - File: `core/control_plane/snapshots.py`
    - Fix: Implement actual state restore logic
    - Lines: restore_snapshot() method

17. **timeout_s unbounded** — Negative/huge values
    - File: `core/control_plane/subsystems.py`
    - Fix: Validate 1 <= timeout_s <= 3600
    - Lines: Subsystem update route

18. **Error message leakage** — Raw exceptions to client
    - File: `core/control_plane/intent_router.py`
    - Fix: Sanitize errors, return generic messages + log_id
    - Lines: Error handlers

**Exit Criteria:**
- [ ] All major state-change routes protected by @consent_required
- [ ] All inputs validated (enums, bounds)
- [ ] Snapshot restore actually works
- [ ] Errors sanitized (no stack traces)
- [ ] Merged to main

---

## ⏰ TIMELINE

| Stream | Issues | Duration | Due |
|--------|--------|----------|-----|
| **Stream 1** | 4 (audit) | 6-8h | 2026-09-24 |
| **Stream 2** | 4 (auth/csrf) | 4-6h | 2026-09-24 |
| **Stream 3** | 3 (tenant) | 3-4h | 2026-09-24 |
| **Stream 4** | 6 (validation) | 6-8h | 2026-09-25 |
| **Total** | 17 fixes | ~20-30h | 2026-09-26 |

---

## 🎯 SUCCESS CRITERIA (ALL MUST ✅)

- [ ] All 13 Critical issues fixed + tested
- [ ] All 8 High issues fixed + tested
- [ ] Zero new security findings (re-review)
- [ ] All changes committed to main
- [ ] Phase 9 Go/No-Go: ✅ **GO FOR PRODUCTION**
- [ ] Phase 1 Session 3 UAT can proceed

---

## 🚀 NEXT: IMMEDIATE EXECUTION

4 autonomous agents spawning NOW for parallel remediation.

---

**Remediation Plan Complete. Execution starting.**
