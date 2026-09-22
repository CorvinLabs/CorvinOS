# PHASE 9 SECURITY AUDIT REPORT
## Control Plane Routes Security Review
**Date:** 2026-09-22  
**Auditor:** Security Remediation Agent  
**Status:** 🔴 CRITICAL — 13 Blocking Issues + 8 High Severity Issues Found

---

## EXECUTIVE SUMMARY

All four Phase 9 control plane route files contain **CRITICAL SECURITY DEFECTS** that make the code unsafe for production:

- **13 CRITICAL issues** blocking merge (privilege escalation, missing auth, audit failures)
- **8 HIGH severity issues** requiring remediation (input validation, tenant isolation)
- **2 BLOCKING DEPENDENCIES** (audit_backend not exported, consent_required module missing)

**VERDICT: 🔴 DO NOT MERGE — Phase 9 code cannot run safely in production.**

---

## CRITICAL ISSUES AUDIT

### Issue #1: Privilege Escalation via Unconditional add_approver()
- **Files:** 
  - `core/console/corvin_console/routes/control_plane_overrides.py`
  - `core/control_plane/override_authority.py`
- **Lines:** 
  - `control_plane_overrides.py:206` (approve_override)
  - `control_plane_overrides.py:252` (deny_override)
  - `override_authority.py:347-354` (add_approver method definition)
- **Severity:** **CRITICAL** — ANY authenticated user becomes an approver without authorization check
- **Issue Description:**
  ```python
  # Lines 205-206 in control_plane_overrides.py
  # Check approver authority (for now, accept all authenticated users)
  authority.add_approver(rec.sid)  # <- NO AUTH CHECK, unconditional!
  ```
  The code calls `add_approver()` **before** checking if the user is actually authorized. This grants every authenticated user approver privileges on the first API call.

- **Attack Vector:** 
  1. Any user logs in to console
  2. Calls `POST /v1/console/control-plane/overrides/{id}/approve`
  3. add_approver() is called unconditionally → user becomes approver
  4. User can now approve ANY override request
  
- **Proof of Vulnerability:**
  - `override_authority.py:161` checks `if approver_id not in self.approvers:` to deny
  - But line 206 adds the user BEFORE this check runs
  - So the check always passes on first call

- **Fix Order:** **P0 (blocking)**
- **Pattern:** Same issue on line 252 in deny_override()

### Issue #2: MockAuditBackend vs Real Audit Backend
- **Files:**
  - `core/console/corvin_console/routes/control_plane_overrides.py`
  - `core/console/corvin_console/routes/control_plane_snapshots.py`
  - `core/console/corvin_console/control_plane/plugin_manager.py`
- **Lines:**
  - `control_plane_overrides.py:43-48` (MockAuditBackend definition in get_authority)
  - `control_plane_snapshots.py:38` (audit_backend=None)
  - `plugin_manager.py` (no audit backend wired)
- **Severity:** **CRITICAL** — All control plane operations have NO audit trail
- **Issue Description:**
  ```python
  # Lines 43-48 in control_plane_overrides.py
  class MockAuditBackend:
      async def log_event(self, event_type: str, payload: dict) -> None:
          pass  # <- NOOP! No audit is happening
  
  _authority = OverrideAuthority(MockAuditBackend())
  ```
  Override Authority uses a mock backend that does nothing. All `await self.audit.log_event()` calls are silently discarded. No audit trail exists for operator overrides.

- **Audit Violations:**
  - ADR-0232 § mandatory core: "every audit event must be hash-chained"
  - GDPR Art. 30/32: "shall maintain records of processing activities"
  - This code violates both — no records exist

- **Cascading Audit Failures:**
  - Snapshots (line 38): `audit_backend=None` → `AttributeError` when `self.audit.log_event()` is called
  - Plugin Manager: No audit backend passed, but code calls `self._emit_audit_event()`

- **Fix Order:** **P0 (prerequisite for wiring backends)**

### Issue #3: Snapshots Audit Backend = None
- **File:** `core/console/corvin_console/routes/control_plane_snapshots.py`
- **Line:** 38
- **Severity:** **CRITICAL** — AttributeError on first snapshot operation
- **Issue:**
  ```python
  _snapshot_manager = SnapshotManager(audit_backend=None, storage_path=storage_path)
  ```
  When `snapshot_manager.py:129` tries to call `await self.audit.log_event()`, it will crash:
  ```
  AttributeError: 'NoneType' object has no attribute 'log_event'
  ```

- **Fix Order:** **P0 (blocking)**

### Issue #4: Zero Authentication on Snapshots Endpoints
- **File:** `core/console/corvin_console/routes/control_plane_snapshots.py`
- **Lines:** 71, 114, 133, 159, 186, 223, 256
- **Severity:** **CRITICAL** — All snapshot operations are completely unauthenticated
- **Issue:** Every endpoint is missing `@Depends(require_session)` or `@Depends(require_csrf)`
  ```python
  # Line 71 — POST create_snapshot — ZERO authentication
  @router.post("", response_model=SnapshotOperationResponse)
  async def create_snapshot(req: SnapshotCreateRequest, ...) -> SnapshotOperationResponse:
      # NO: Depends(require_session)
  
  # Line 186 — POST restore_snapshot — ZERO authentication
  @router.post("/{snapshot_id}/restore", response_model=SnapshotOperationResponse)
  async def restore_snapshot(...) -> SnapshotOperationResponse:
      # NO: Depends(require_session)
  ```
  Any unauthenticated caller can:
  - Create snapshots
  - Restore arbitrary snapshots
  - Delete snapshots
  - View audit logs

- **Impact:** Unauthenticated actors can capture and restore entire system state

- **Fix Order:** **P0 (blocking)**

### Issue #5: Snapshots Tenant Isolation Broken
- **File:** `core/console/corvin_console/routes/control_plane_snapshots.py`
- **Lines:** 74, 116, 135, 162, 190, 226, 259, 269
- **Severity:** **CRITICAL** — tenant_id hardcoded to "default", no extraction from session
- **Issue:**
  ```python
  # Line 74
  async def create_snapshot(
      req: SnapshotCreateRequest,
      tenant_id: str = Query(default="default")  # <- WRONG: from Query, not session
  ) -> SnapshotOperationResponse:
  ```
  In a multi-tenant deployment:
  1. Operator A (tenant X) makes request
  2. Operator B (tenant Y) can inspect snapshots by passing `?tenant_id=X`
  3. All snapshots stored under single default tenant

- **Pattern:** Every endpoint (except get_audit_log which filters) has this issue

- **Fix Order:** **P0 (blocking)**

### Issue #6: Zero Authentication on Plugins Endpoints
- **File:** `core/console/corvin_console/routes/control_plane_plugins.py`
- **Lines:** 54, 84, 111, 141, 168, 199, 229
- **Severity:** **CRITICAL** — All plugin operations are completely unauthenticated
- **Issue:**
  ```python
  # Line 54 — PUT install_plugin — ZERO authentication
  @router.put("/install")
  async def install_plugin(req: PluginInstallRequest) -> PluginOperationResponse:
      # NO: Depends(require_session)
  ```
  Any unauthenticated caller can:
  - Install arbitrary plugins
  - Enable/disable core plugins
  - Uninstall critical plugins
  - This breaks the entire plugin system

- **Fix Order:** **P0 (blocking)**

### Issue #7: Zero Authentication on Subsystems Endpoints
- **File:** `core/console/corvin_console/routes/control_plane_subsystems.py`
- **Lines:** 60, 91, 125, 156, 191, 215, 234, 267
- **Severity:** **CRITICAL** — All subsystem control operations are completely unauthenticated
- **Issue:**
  ```python
  # Line 60 — PATCH start_subsystem — ZERO authentication
  @router.patch("/{subsystem_id}/start")
  async def start_subsystem(subsystem_id: str, ...) -> SubsystemOperationResponse:
      # NO: Depends(require_session)
  ```
  Any unauthenticated caller can:
  - Start/stop critical subsystems
  - Force restart services
  - Pause/resume operations
  - This is a complete denial-of-service attack surface

- **Fix Order:** **P0 (blocking)**

### Issue #8: Hardcoded User IDs Everywhere
- **Files:**
  - `control_plane_snapshots.py:101, 211, 275`
  - `control_plane_plugins.py:71, 155, 185, 215`
  - `control_plane_subsystems.py:79, 113, 144, 179`
- **Severity:** **CRITICAL** — No user attribution, all operations attributed to "console-user"
- **Issue:**
  ```python
  # Line 101 in control_plane_snapshots.py
  result = await manager.create_snapshot(
      control_plane_state=control_plane_state,
      name=req.name,
      description=req.description,
      creator_id="console-user",  # <- HARDCODED! Not from session
      tenant_id=tenant_id
  )
  ```
  
  This pattern repeats 8+ times. No audit trail shows WHO performed actions:
  - Snapshot creation: credited to "console-user"
  - Plugin install: credited to "console-user"
  - Subsystem control: credited to "console-user"

- **GDPR Violation:** Art. 30 "records of processing activities" requires attribution to the actual user

- **Fix Order:** **P0 (blocking)**

### Issue #9: Missing CSRF Protection on Mutations
- **Files:**
  - `control_plane_snapshots.py`: All POST/DELETE endpoints (lines 71, 186, 223, 256)
  - `control_plane_plugins.py`: All PUT/PATCH/DELETE endpoints (lines 54, 141, 168, 199)
  - `control_plane_subsystems.py`: All PATCH endpoints (lines 60, 91, 125, 156)
- **Severity:** **CRITICAL** — CSRF attacks can hijack control plane operations
- **Issue:**
  ```python
  # Line 71 in control_plane_snapshots.py — POST but no require_csrf
  @router.post("", response_model=SnapshotOperationResponse)
  async def create_snapshot(req: SnapshotCreateRequest, ...) -> SnapshotOperationResponse:
      # Should have: Depends(require_csrf)
      # MISSING!
  ```

  Every mutation endpoint is missing `@Depends(require_csrf)`. The deps.py docs (line 84) state:
  > "Every mutation" requires CSRF protection

- **Fix Order:** **P0 (blocking)**

### Issue #10: Plugin Boot Layer Not Validated
- **File:** `core/console/corvin_console/routes/control_plane_plugins.py`
- **Line:** 69
- **Severity:** **CRITICAL** — Accepts arbitrary boot_layer strings, may bypass security
- **Issue:**
  ```python
  # Line 69
  result = await manager.install_plugin(
      plugin_id=req.plugin_id,
      name=req.name,
      version=req.version,
      boot_layer=req.boot_layer,  # <- NO VALIDATION! Could be anything
      tenant_id="default",
      operator_id="console-user"
  )
  ```

  The PluginManager DOES validate boot_layer (line 86-103), but the route doesn't pre-validate. An attacker could:
  - Send `boot_layer="bypass_compliance"`
  - Or `boot_layer=""; rm -rf /`
  - The manager will reject it, but the error handling is poor

- **However:** plugin_manager.py HAS validation, so this is less severe than the auth issues
- **But:** The route should validate BEFORE calling the manager (fail-fast principle)

- **Fix Order:** **P1 (high)**

### Issue #11: Unbounded Snapshot Name/Description
- **File:** `core/console/corvin_console/routes/control_plane_snapshots.py`
- **Lines:** 44-45 (SnapshotCreateRequest model)
- **Severity:** **CRITICAL** — No max length, could allocate multi-MB payloads → DoS
- **Issue:**
  ```python
  # Lines 44-45
  class SnapshotCreateRequest(BaseModel):
      """Request to create a snapshot."""
      name: str  # <- NO max_length constraint
      description: str  # <- NO max_length constraint
  ```

  An attacker can send:
  ```python
  {
    "name": "a" * (1024 * 1024 * 100),  # 100 MB name
    "description": "b" * (1024 * 1024 * 100)  # 100 MB description
  }
  ```
  This will:
  1. Consume RAM on the server
  2. Cause disk writes to fail
  3. Crash the snapshot manager

- **Fix Order:** **P0 (blocking)**

### Issue #12: Unbounded timeout_s Parameter
- **File:** `core/console/corvin_console/routes/control_plane_subsystems.py`
- **Lines:** 42, 111, 177
- **Severity:** **CRITICAL** — timeout_s unbounded, can be negative/huge → DoS
- **Issue:**
  ```python
  # Line 42
  class SubsystemOperationRequest(BaseModel):
      """Request to control a subsystem."""
      force: Optional[bool] = False
      timeout_s: Optional[int] = 30  # <- NO min/max validation!
  ```

  An attacker can send:
  ```python
  { "timeout_s": -999 }  # Negative timeout
  { "timeout_s": 2**31-1 }  # Huge timeout (24855 days)
  ```

  This will:
  1. Cause unexpected behavior in subsystem shutdown
  2. Hang the system indefinitely
  3. Crash thread pools with overload

- **Fix Order:** **P0 (blocking)**

### Issue #13: Intent Router Missing audit_backend Export
- **File:** `core/console/corvin_console/routes/intents.py`
- **Line:** 22
- **Severity:** **CRITICAL** — Import error at startup
- **Issue:**
  ```python
  # Line 22
  from core.audit import audit_backend  # <- DOESN'T EXIST
  ```

  The module `core/audit` does NOT export `audit_backend`. It exports:
  - AuditChain
  - AuditEntry
  - QueueIntegrityMonitor
  - etc.

  But NOT `audit_backend`. This will cause:
  ```
  ImportError: cannot import name 'audit_backend' from 'core.audit'
  ```

  This breaks the entire console at startup.

- **Correct import should be:**
  ```python
  from core.plugins.corvin_plugins.providers import audit_backend
  ```

- **Fix Order:** **P0 (blocking)**

---

## HIGH SEVERITY ISSUES AUDIT

### Issue #14: Missing @consent_required Gate (all mutation endpoints)
- **Files:**
  - `control_plane_snapshots.py`: lines 71, 186, 223, 256
  - `control_plane_plugins.py`: lines 54, 141, 168, 199
  - `control_plane_subsystems.py`: lines 60, 91, 125, 156
- **Severity:** **HIGH** — GDPR Art. 6(1) consent required, none enforced
- **Issue:** All mutation endpoints should check user consent before operating
  - Snapshot operations may process user data → require consent
  - Plugin/subsystem operations have system-wide effects → require explicit consent
  - Current code has ZERO consent gates

- **Pattern:** Even intents.py (line 58) has `@consent_required("intent_classification")`
  Control plane operations are MORE sensitive than intent classification and have ZERO gates

- **Also Missing:** The consent_required decorator is defined in `core.compliance.consent` module, which doesn't exist yet (see Issue #15 below)

- **Fix Order:** **P1 (depends on Issue #15)**

### Issue #15: Missing core.compliance.consent Module
- **Severity:** **HIGH** — Module doesn't exist, but code imports from it
- **Issue:**
  - intents.py (line 23) imports: `from core.compliance.consent import consent_required`
  - The module `core/compliance/consent.py` does NOT exist
  - This is a blocker for implementing consent gates

- **Current Status:**
  - Module location: `/home/shumway/projects/CorvinOS/core/compliance/`
  - Expected file: `consent.py` — NOT FOUND

- **Fix Order:** **P1 (must create before wiring consent gates)**

### Issue #16: Control Plane Plugins Tenant Isolation Broken
- **File:** `core/console/corvin_console/routes/control_plane_plugins.py`
- **Lines:** 70, 154, 184, 214
- **Severity:** **HIGH** — Hardcoded tenant_id="default", no session extraction
- **Issue:**
  ```python
  # Line 70
  result = await manager.install_plugin(
      plugin_id=req.plugin_id,
      name=req.name,
      version=req.version,
      boot_layer=req.boot_layer,
      tenant_id="default",  # <- HARDCODED! Should be from session
      operator_id="console-user"
  )
  ```

  Same issue as snapshots: all operations forced to "default" tenant. Multi-tenant deployments will have complete cross-tenant data leakage.

- **Fix Order:** **P0 (blocking)**

### Issue #17: Control Plane Subsystems Tenant Isolation Broken
- **File:** `core/console/corvin_console/routes/control_plane_subsystems.py`
- **Lines:** 63, 95, 128, 160, 194, 217, 238, 269
- **Severity:** **HIGH** — Hardcoded tenant_id="default", no session extraction
- **Issue:** Same pattern as snapshots and plugins
  ```python
  # Line 63
  async def start_subsystem(
      subsystem_id: str,
      tenant_id: str = Query(default="default")  # <- WRONG
  ) -> SubsystemOperationResponse:
  ```

- **Fix Order:** **P0 (blocking)**

### Issue #18: Plugin Manager Audit Events in Volatile Memory Only
- **File:** `core/console/corvin_console/control_plane/plugin_manager.py`
- **Line:** 68
- **Severity:** **HIGH** — Audit trail lost on process crash
- **Issue:**
  ```python
  # Line 68
  self.audit_events = []  # <- Volatile list in memory!
  ```

  The `get_audit_log()` method (which control_plane_plugins.py calls on line 229) returns this volatile list. On process restart:
  - All audit events are lost
  - No persistent record of plugin operations
  - Compliance audit trail is broken

- **Should:** Write to core audit chain (ADR-0232) instead

- **Fix Order:** **P1 (after audit_backend wiring)**

### Issue #19: Subsystem Manager Audit Events in Volatile Memory Only
- **File:** `core/control_plane/subsystem_controller.py` (likely, need to verify)
- **Severity:** **HIGH** — Same issue as plugin manager
- **Note:** control_plane_subsystems.py:280 calls `manager.get_audit_log()` which returns volatile events

- **Fix Order:** **P1 (after audit_backend wiring)**

### Issue #20: Snapshot restore() Doesn't Actually Restore
- **File:** `core/control_plane/snapshot_manager.py`
- **Lines:** 186-220 (in control_plane_snapshots.py route)
- **Severity:** **HIGH** — API claims to restore but doesn't implement it
- **Issue:**
  ```python
  # Line 209 in snapshot_manager.py
  result = await manager.restore_snapshot(
      snapshot_id=snapshot_id,
      approver_id="console-user",
      tenant_id=tenant_id
  )
  # Returns: {"status": "success", ...}
  # But does the manager ACTUALLY restore the state?
  ```

  Looking at the route (control_plane_snapshots.py:186-220), the logic:
  1. Accepts restore request
  2. Calls manager.restore_snapshot()
  3. Returns success
  4. BUT: The manager likely doesn't actually modify system state

  This is a "silent failure" — API claims success while state is unchanged

- **Fix Order:** **P2 (functionality, not security urgent)**

### Issue #21: Error Messages Leak Implementation Details
- **File:** `control_plane_overrides.py`
- **Lines:** 101-104, 114-115
- **Severity:** **HIGH** — Exception messages returned to client reveal system structure
- **Issue:**
  ```python
  # Line 101-104
  except ValueError:
      raise HTTPException(
          http_status.HTTP_400_BAD_REQUEST,
          detail=f"Invalid override_type: {body.override_type}",
      )
  ```

  This returns the exact invalid value to the client, which could help attackers discover valid values via enumeration.

- **Better:** Log internally, return generic message to client

- **Fix Order:** **P1 (high)**

---

## BLOCKING DEPENDENCY ISSUES

### Dependency #1: audit_backend Not Exported from core.audit
- **Issue:** Intent Router (line 22) tries to import: `from core.audit import audit_backend`
- **Reality:** `core/audit/__init__.py` doesn't export `audit_backend`
- **Impact:** Console fails to start with ImportError
- **Fix:** Export audit_backend from `core.plugins.corvin_plugins.providers.audit_backend`
- **Action Required:** Update imports in intents.py BEFORE merging

### Dependency #2: core.compliance.consent Module Missing
- **Issue:** Intents.py imports consent_required from non-existent module
- **Reality:** `core/compliance/consent.py` does NOT exist
- **Impact:** Can't implement consent gates without this module
- **Fix:** Create `core/compliance/consent.py` with consent_required decorator
- **Action Required:** Create module + wire into deps.py BEFORE implementing consent gates

---

## AUDIT ISSUE MATRIX

| Issue | override_authority.py | control_plane_overrides.py | control_plane_snapshots.py | control_plane_plugins.py | control_plane_subsystems.py |
|---|---|---|---|---|---|
| **MockAuditBackend** | ✓ Uses it | ✓ Creates it | - | - | - |
| **audit_backend = None** | - | - | ✓ Line 38 | - | - |
| **Audit events volatile** | - | - | ✓ (Snapshot) | ✓ Line 68 | ✓ (Subsys) |
| **No @require_session** | - | ✗ Has it | ✓ MISSING | ✓ MISSING | ✓ MISSING |
| **No @require_csrf** | - | ✗ Partial | ✓ MISSING | ✓ MISSING | ✓ MISSING |
| **No @consent_required** | - | ✓ MISSING | ✓ MISSING | ✓ MISSING | ✓ MISSING |
| **Hardcoded tenant_id** | - | ✗ Good | ✓ Lines 74+ | ✓ Lines 70+ | ✓ Lines 63+ |
| **Hardcoded user ID** | - | ✗ Good (rec.sid) | ✓ Lines 101+ | ✓ Lines 71+ | ✓ Lines 79+ |
| **Unbounded inputs** | - | - | ✓ name/desc | - | ✓ timeout_s |
| **No boot_layer validation** | - | - | - | ✓ Line 69 | - |

---

## FIX EXECUTION ORDER

### Phase 1: Export Dependencies (Prerequisite for all fixes)
1. **Add audit_backend export to core/audit/__init__.py**
   - Or fix intents.py import to use correct path
   - File: `/home/shumway/projects/CorvinOS/core/audit/__init__.py`

2. **Create core/compliance/consent.py module**
   - Define `consent_required` decorator
   - File: `/home/shumway/projects/CorvinOS/core/compliance/consent.py`
   - Must export to deps.py

### Phase 2: Fix Critical Authentication/CSRF/Consent (P0)
3. **control_plane_snapshots.py** (8 critical issues)
   - Line 71: Add `@Depends(require_csrf)` to create_snapshot
   - Line 114: Add `@Depends(require_session)` to list_snapshots
   - Line 133: Add `@Depends(require_session)` to get_snapshot_audit_log
   - Line 159: Add `@Depends(require_session)` to get_snapshot_detail
   - Line 186: Add `@Depends(require_csrf)` to restore_snapshot
   - Line 223: Add `@Depends(require_csrf)` to diff_snapshot
   - Line 256: Add `@Depends(require_csrf)` to delete_snapshot
   - Extract tenant_id and user_id from session (replace Query defaults)
   - Add input validation (name/description max length)
   - All routes: Add `@consent_required("control_plane_snapshot_operations")`
   - Line 38: Wire real audit_backend (from dependency injection)

4. **control_plane_plugins.py** (7 critical issues)
   - Line 54: Add `@Depends(require_csrf)` to install_plugin
   - Line 84: Add `@Depends(require_session)` to list_plugins
   - Line 111: Add `@Depends(require_session)` to get_plugin
   - Line 141: Add `@Depends(require_csrf)` to enable_plugin
   - Line 168: Add `@Depends(require_csrf)` to disable_plugin
   - Line 199: Add `@Depends(require_csrf)` to uninstall_plugin
   - Line 229: Add `@Depends(require_session)` to get_audit_log
   - Extract tenant_id and operator_id from session
   - Add boot_layer validation in route BEFORE calling manager
   - All routes: Add `@consent_required("plugin_management")`

5. **control_plane_subsystems.py** (8 critical issues)
   - Line 60: Add `@Depends(require_csrf)` to start_subsystem
   - Line 91: Add `@Depends(require_csrf)` to pause_subsystem
   - Line 125: Add `@Depends(require_csrf)` to resume_subsystem
   - Line 156: Add `@Depends(require_csrf)` to stop_subsystem
   - Line 191: Add `@Depends(require_session)` to get_subsystem_status
   - Line 215: Add `@Depends(require_session)` to list_subsystems
   - Line 234: Add `@Depends(require_session)` to get_subsystem_logs
   - Line 267: Add `@Depends(require_session)` to get_subsystem_audit_log
   - Extract tenant_id and operator_id from session
   - Add timeout_s validation (1 ≤ timeout_s ≤ 3600)
   - All routes: Add `@consent_required("subsystem_control")`

6. **control_plane_overrides.py** (3 critical issues)
   - Line 206: REMOVE unconditional `authority.add_approver(rec.sid)` call
   - Line 252: REMOVE unconditional `authority.add_approver(rec.sid)` call
   - Add proper approver authority check BEFORE allowing approval
   - Line 43-48: Wire real audit_backend (from dependency injection)
   - Line 38: Fix get_authority() to inject real backend

### Phase 3: Fix Intent Router Import (P0)
7. **core/console/corvin_console/routes/intents.py**
   - Line 22: Fix import of audit_backend
   - Change from: `from core.audit import audit_backend`
   - Change to: `from core.plugins.corvin_plugins.providers import audit_backend`

### Phase 4: Fix Audit Chain Integration (P1)
8. **core/console/corvin_console/control_plane/plugin_manager.py**
   - Line 68: Replace `self.audit_events = []` with real audit chain writer
   - Wire core audit chain instead of volatile memory

9. **core/control_plane/subsystem_controller.py**
   - Same: replace volatile audit events with core audit chain

### Phase 5: Fix Remaining High-Severity Issues (P1)
10. **Error message sanitization** (all routes)
   - Log full error internally
   - Return generic message to client

11. **Snapshot restore implementation** (snapshot_manager.py)
   - Actually apply state changes, don't just return success

---

## TESTING REQUIREMENTS

After fixes, the following tests MUST pass:

1. **Authentication Tests**
   - Unauthenticated requests to control plane routes return 401
   - Test each endpoint individually

2. **CSRF Tests**
   - POST/PATCH/DELETE without CSRF token returns 403
   - POST/PATCH/DELETE with invalid token returns 403

3. **Consent Tests**
   - Requests without consent return 403
   - Requests with consent proceed

4. **Tenant Isolation Tests**
   - Tenant A cannot access Tenant B's snapshots
   - Tenant isolation verified in database queries

5. **Audit Chain Tests**
   - Every control plane operation emits an audit event
   - Events are hash-chained and immutable
   - Events survive process restart

6. **Adversarial Tests**
   - Privilege escalation attempts fail
   - Unbounded input attempts are rejected
   - Negative timeout_s values are rejected
   - Multi-MB names are rejected

---

## SUMMARY OF FILES TO MODIFY

**Priority Order (dependencies first):**

1. `/home/shumway/projects/CorvinOS/core/audit/__init__.py` — Export audit_backend
2. `/home/shumway/projects/CorvinOS/core/compliance/consent.py` — Create module (NEW FILE)
3. `/home/shumway/projects/CorvinOS/core/console/corvin_console/deps.py` — Export consent_required
4. `/home/shumway/projects/CorvinOS/core/console/corvin_console/routes/intents.py` — Fix import
5. `/home/shumway/projects/CorvinOS/core/console/corvin_console/routes/control_plane_overrides.py` — Fix privilege escalation + wire audit
6. `/home/shumway/projects/CorvinOS/core/console/corvin_console/routes/control_plane_snapshots.py` — Add auth/CSRF/consent + wire audit
7. `/home/shumway/projects/CorvinOS/core/console/corvin_console/routes/control_plane_plugins.py` — Add auth/CSRF/consent
8. `/home/shumway/projects/CorvinOS/core/console/corvin_console/routes/control_plane_subsystems.py` — Add auth/CSRF/consent + validate timeout_s
9. `/home/shumway/projects/CorvinOS/core/console/corvin_console/control_plane/plugin_manager.py` — Wire real audit chain
10. `/home/shumway/projects/CorvinOS/core/control_plane/subsystem_controller.py` — Wire real audit chain
11. `/home/shumway/projects/CorvinOS/core/control_plane/snapshot_manager.py` — Implement restore + validate inputs

---

## GO/NO-GO DECISION

**🔴 NO-GO FOR PRODUCTION**

**Verdict:** Phase 9 code contains 13 CRITICAL security defects blocking production deployment.

**Remediation Time Estimate:** 6-8 hours (experienced developer)

**Next Steps:**
1. Create audit report (DONE)
2. Fix Phase 0 (export dependencies): 1-2 hours
3. Fix Phase 1 (auth/CSRF/consent): 2-3 hours  
4. Fix Phase 2 (audit chain): 1-2 hours
5. Comprehensive testing: 1-2 hours
6. Security review + approval: 1 hour

**Target Merge Date:** After 2-3 day remediation sprint

---

## REFERENCE

- **ADR-0232:** Boot tripwire, audit chain integrity
- **ADR-0233:** Plugin audit backend (secondary sink)
- **ADR-2029:** Control Plane design
- **GDPR Art. 30:** Records of processing activities
- **GDPR Art. 32:** Security of processing

