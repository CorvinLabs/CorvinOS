# PHASE 9 SECURITY REMEDIATION PLAN

**Status:** 🔴 BLOCKING — 13 Critical + 8 High Severity Security Issues  
**Timeline:** 2–3 day remediation sprint required before Phase 10 kickoff  
**Go Decision:** 🔴 NO-GO FOR PRODUCTION until fixes applied  

---

## EXECUTIVE SUMMARY

Phase 9 control-plane implementation (Intent Router, Plugin Manager, Subsystem Control, Override Authority, Snapshots) has **13 critical security defects** identified in comprehensive security review on 2026-09-22.

**Impact:** Code is not production-deployable in current state. Blocks Phase 10 kickoff until remediated.

**Recommendation:** Execute 2–3 day remediation sprint addressing P0+P1 issues, then re-run security review before merge.

---

## CRITICAL ISSUES (P0 — Blocking)

### 1. Privilege Escalation in Override Authority Routes

**File:** `core/console/corvin_console/routes/control_plane_overrides.py:205-206, 251-252`  
**Severity:** CRITICAL — Privilege Escalation  

**Issue:**
```python
@router.post("/{override_id}/approve")
async def approve_override(..., rec: SessionRecord = ...) -> dict[str, Any]:
    authority = get_authority()
    
    # BUG: add_approver() called BEFORE permission check
    authority.add_approver(rec.sid)  # Any authenticated user becomes approver!
    
    try:
        result = await authority.approve_override(override_id, rec.sid, rec.tenant_id)
        # Now they can approve arbitrarily
```

**Fix:** Check permission BEFORE adding approver. Require admin/approver role from SessionRecord.

```python
# FIXED:
if not has_approver_role(rec):
    raise PermissionError("User is not an approver")
# THEN proceed with approval logic
```

---

### 2. Audit System Not Writing to Core Chain

**Files:** 
- `core/console/corvin_console/control_plane/plugin_manager.py:72` (in-memory only)
- `core/console/corvin_console/control_plane/subsystem_manager.py:157` (in-memory only)
- `core/control_plane/override_authority.py:48` (MockAuditBackend)
- `core/control_plane/snapshot_manager.py:38` (audit_backend=None)

**Severity:** CRITICAL — GDPR Art. 30/32 Violation, ADR-0232/0233 Breach  

**Issue:** Audit events stored in volatile Python lists, never persisted to hash-chained core audit trail.

**Fix:** Wire real audit_backend to all managers.

```python
# BEFORE (plugin_manager.py:72)
def _emit_audit_event(self, event_type: str, plugin_id: str, details: Dict[str, Any]) -> None:
    event = {...}
    self.audit_events.append(event)  # VOLATILE - LOST ON CRASH

# AFTER
def _emit_audit_event(self, event_type: str, plugin_id: str, details: Dict[str, Any]) -> None:
    event = {...}
    # Write to core audit chain (fail-closed if chain write fails)
    audit_backend.write_event(
        event_type=event_type,
        payload=event,
        tenant_id=details.get("tenant_id", "default")
    )
```

---

### 3. Snapshots — No Authentication

**File:** `core/console/corvin_console/routes/control_plane_snapshots.py:71+`  
**Severity:** CRITICAL — Complete Authentication Bypass  

**Issue:** Zero `@Depends(require_session)` on snapshot endpoints. Unauthenticated actors can create/restore/delete system snapshots.

**Fix:** Add authentication to all snapshot endpoints.

```python
# BEFORE
@router.post("", response_model=SnapshotOperationResponse)
async def create_snapshot(
    req: SnapshotCreateRequest,
    tenant_id: str = Query(default="default")
) -> SnapshotOperationResponse:
    # No auth!

# AFTER
@router.post("", response_model=SnapshotOperationResponse)
async def create_snapshot(
    req: SnapshotCreateRequest,
    rec: Annotated[SessionRecord, Depends(require_session)] = ...,
) -> SnapshotOperationResponse:
    tenant_id = rec.tenant_id  # Extract from session
```

---

### 4. Missing CSRF Protection on All Mutations

**Files:**
- `core/console/corvin_console/routes/control_plane_plugins.py` (PUT /install, PATCH /enable, /disable, DELETE)
- `core/console/corvin_console/routes/control_plane_subsystems.py` (PATCH /start, /pause, /resume, /stop)
- `core/console/corvin_console/routes/control_plane_snapshots.py` (POST, DELETE)

**Severity:** CRITICAL — CSRF attacks can disable systems, delete snapshots  

**Fix:** Add `@require_csrf` to all state-changing endpoints.

```python
# BEFORE
@router.put("/install")
async def install_plugin(req: PluginInstallRequest):
    # No CSRF protection!

# AFTER
from ..deps import require_csrf

@router.put("/install")
async def install_plugin(
    req: PluginInstallRequest,
    rec: Annotated[SessionRecord, Depends(require_csrf)] = ...,
):
    # Now CSRF protected
```

---

### 5. Missing Consent Gates

**Files:**
- `core/console/corvin_console/routes/control_plane_plugins.py` (all endpoints)
- `core/console/corvin_console/routes/control_plane_subsystems.py` (all endpoints)
- `core/console/corvin_console/routes/control_plane_snapshots.py` (all endpoints)

**Severity:** CRITICAL — Operates on behalf of user without consent  

**Fix:** Add `@consent_required` decorator.

```python
from core.compliance.consent import consent_required

# BEFORE
@router.put("/install")
async def install_plugin(req: PluginInstallRequest):

# AFTER
@router.put("/install")
@consent_required("control_plane_plugin_install")
async def install_plugin(req: PluginInstallRequest):
```

---

### 6. Cross-Tenant Isolation Failures

**File:** `core/console/corvin_console/routes/control_plane_plugins.py:70, 154`  

**Severity:** CRITICAL — Multi-tenant data leak  

**Issue:** Hardcoded `tenant_id="default"` in all operations. Multi-tenant deployments share single plugin registry.

**Fix:** Extract tenant_id from SessionRecord, pass to all operations.

```python
# BEFORE
result = await manager.install_plugin(
    plugin_id=req.plugin_id,
    name=req.name,
    version=req.version,
    boot_layer=req.boot_layer,
    tenant_id="default",  # BUG: hardcoded
    operator_id="console-user"
)

# AFTER
result = await manager.install_plugin(
    plugin_id=req.plugin_id,
    name=req.name,
    version=req.version,
    boot_layer=req.boot_layer,
    tenant_id=rec.tenant_id,  # From authenticated session
    operator_id=rec.sid
)
```

---

### 7. Audit Log Unfiltered (Cross-Tenant Leak)

**File:** `core/console/corvin_console/control_plane/plugin_manager.py:240`  

**Severity:** CRITICAL — Audit trail leakage  

**Issue:** GET /audit-log returns all events without tenant_id filtering.

**Fix:** Filter audit log by tenant_id before returning.

```python
# BEFORE
def get_audit_log(self) -> List[Dict[str, Any]]:
    return self.audit_events  # No filtering!

# AFTER
def get_audit_log(self, tenant_id: str) -> List[Dict[str, Any]]:
    return [e for e in self.audit_events if e.get("tenant_id") == tenant_id]
```

---

### 8. Intent Router — Import Error at Startup

**File:** `core/console/corvin_console/routes/intents.py:22`  

**Severity:** CRITICAL — Service startup failure  

**Issue:** `from core.audit import audit_backend` fails — audit_backend not exported.

**Fix:** Either export audit_backend from core.audit, or import the module that provides it.

```python
# BEFORE
from core.audit import audit_backend  # ImportError!

# AFTER (option 1)
from core.audit.backend import audit_backend

# AFTER (option 2 - in core/audit/__init__.py)
from .backend import audit_backend
```

---

## HIGH SEVERITY ISSUES (P1 — Critical Before Merge)

### 9. Intent Router — Error Message Leakage

**File:** `core/console/corvin_console/routes/intents.py:135`  

```python
raise HTTPException(status_code=500, detail=logger_msg)  # Leaks internal errors
```

**Fix:** Sanitize error messages.

```python
# AFTER
detail = "Intent classification failed. Please try again."
# Log full error internally; return generic message to client
logger.error(f"Intent classification error: {str(e)}", exc_info=True)
raise HTTPException(status_code=500, detail=detail)
```

---

### 10. Plugin Manager — boot_layer Not Validated

**File:** `core/console/corvin_console/routes/control_plane_plugins.py:42`  

**Issue:** Accepts arbitrary strings for boot_layer. Should validate against enum (bundled/installed/community).

**Fix:** Add Pydantic validation.

```python
from enum import Enum

class BootLayer(str, Enum):
    BUNDLED = "bundled"
    INSTALLED = "installed"
    COMMUNITY = "community"

class PluginInstallRequest(BaseModel):
    boot_layer: BootLayer  # Now validated
```

---

### 11–13. Additional High-Severity Issues

14. **Subsystem Manager — timeout_s unbounded** → Validate bounds (1–3600)
15. **Intent Router — Empty tenant_id silently becomes "default"** → Raise error if tenant_id empty or invalid
16. **Plugin Manager — Dependency checking dead code** → Populate dependents list or remove check
17. **Snapshots — restore_snapshot() doesn't restore** → Implement actual restore logic
18. **Snapshots — name/description unbounded** → Add max_length=500

---

## REMEDIATION TASKS

### Day 1: Priority 0 Issues (Blocking)

- [ ] **Task 1:** Fix privilege escalation in override_authority (add auth check before add_approver)
- [ ] **Task 2:** Wire audit_backend to all 5 managers (not in-memory, not Mock, not None)
- [ ] **Task 3:** Add @Depends(require_session) to all snapshot routes
- [ ] **Task 4:** Fix import error (audit_backend export)
- [ ] **Task 5:** Add @require_csrf to plugins/subsystems/snapshots mutations
- [ ] **Task 6:** Add @consent_required to control-plane endpoints
- [ ] **Task 7:** Extract tenant_id from SessionRecord; remove hardcoding

### Day 2: Priority 1 Issues (Critical Before Merge)

- [ ] **Task 8:** Filter audit logs by tenant_id
- [ ] **Task 9:** Validate boot_layer (enum)
- [ ] **Task 10:** Validate timeout_s (bounds)
- [ ] **Task 11:** Validate override_type (enum)
- [ ] **Task 12:** Sanitize error messages

### Day 3: Priority 2 Issues (Before Release)

- [ ] **Task 13:** Implement actual snapshot restore
- [ ] **Task 14:** Bound snapshot name/description
- [ ] **Task 15:** Implement fail-closed audit backend errors

---

## RE-RUN SECURITY REVIEW

After fixes are applied:

```bash
# Run unit tests
pytest core/console/corvin_console/tests/test_control_plane_*.py -v

# Run security-specific tests
pytest tests/security/test_control_plane_csrf.py -v
pytest tests/security/test_control_plane_auth.py -v
pytest tests/security/test_control_plane_tenant_isolation.py -v

# Run comprehensive review
python3 scripts/security_review_phase9.py
```

---

## PHASE 10 BLOCKER

Phase 10 (Advanced Skills) **CANNOT START** until Phase 9 security fixes are applied and re-reviewed.

**Estimated Timeline:**
- Day 1: P0 fixes + unit tests (6–8h)
- Day 2: P1 fixes + security re-review (4–6h)
- Day 3: P2 fixes + full test suite (4–6h)
- Total: 2–3 days

**After remediation:** Phase 10 kickoff (2026-09-25)

---

## APPENDIX: Code Examples

See inline in sections above for before/after code examples.

---

**Document Version:** 1.0  
**Date:** 2026-09-22  
**Status:** ACTIVE — Remediation in progress
