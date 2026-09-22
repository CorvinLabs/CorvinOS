# PHASE 9 CRITICAL ISSUES — MANIFEST FOR FIXING

## Quick Reference: All 13 CRITICAL Issues + 8 HIGH Issues

### CRITICAL ISSUES (13 total)

#### [CRITICAL-1] Privilege Escalation: Unconditional add_approver()
```
File: core/console/corvin_console/routes/control_plane_overrides.py
Lines: 206, 252
Pattern: authority.add_approver(rec.sid) called BEFORE checking if user is actually authorized
Fix: Remove unconditional add_approver() — check authorization BEFORE calling approve/deny
Severity: CRITICAL — ANY user becomes approver on first API call
Test: curl -X POST http://localhost:8765/v1/console/control-plane/overrides/123/approve \
  -H "x-csrf-token: X" \
  && curl -X GET http://localhost:8765/v1/console/control-plane/overrides/audit \
  && grep "123" ~/.corvin/audit.jsonl  # Should show user DID NOT become approver
```

#### [CRITICAL-2] MockAuditBackend in Production
```
File: core/console/corvin_console/routes/control_plane_overrides.py
Lines: 43-48
Pattern: class MockAuditBackend: pass
        _authority = OverrideAuthority(MockAuditBackend())
Fix: Wire real audit_backend from core.plugins.corvin_plugins.providers.audit_backend
Severity: CRITICAL — Zero audit trail for all override operations
Calls: override_authority.py lines 122, 162, 202, 270 all call self.audit.log_event()
Impact: GDPR Art. 30/32 violation — no records of override decisions
```

#### [CRITICAL-3] Snapshots audit_backend = None
```
File: core/console/corvin_console/routes/control_plane_snapshots.py
Line: 38
Code: _snapshot_manager = SnapshotManager(audit_backend=None, storage_path=storage_path)
Fix: Wire real audit_backend from dependency injection
Severity: CRITICAL — AttributeError on first snapshot operation
Stack: snapshot_manager.py:129 → await self.audit.log_event() → NoneType error
Blocks: All snapshot operations (create, restore, delete)
```

#### [CRITICAL-4] Zero Authentication on Snapshots Endpoints
```
File: core/console/corvin_console/routes/control_plane_snapshots.py
Lines: 71, 114, 133, 159, 186, 223, 256
Issue: Missing @Depends(require_session) or @Depends(require_csrf)
Impact: ANYONE can create/restore/delete snapshots + view audit logs
Attack: curl -X POST http://localhost:8765/v1/console/control-plane/snapshots \
  -H "Content-Type: application/json" \
  -d '{"name":"restore_as_admin","description":"hacked"}' \
  # No session cookie needed!
Fixes needed:
  - Line 71: create_snapshot → add @Depends(require_csrf)
  - Line 114: list_snapshots → add @Depends(require_session)
  - Line 133: get_audit_log → add @Depends(require_session)
  - Line 159: get_snapshot_detail → add @Depends(require_session)
  - Line 186: restore_snapshot → add @Depends(require_csrf)
  - Line 223: diff_snapshot → add @Depends(require_csrf)
  - Line 256: delete_snapshot → add @Depends(require_csrf)
```

#### [CRITICAL-5] Snapshots Tenant Isolation Broken
```
File: core/console/corvin_console/routes/control_plane_snapshots.py
Lines: 74, 116, 135, 162, 190, 226, 259, 269
Pattern: tenant_id: str = Query(default="default")
Issue: tenant_id comes from URL query, defaults to "default" (hardcoded)
Attack: Tenant A calls with ?tenant_id=other_tenant → accesses other tenant's snapshots
Impact: Complete cross-tenant data leakage in multi-tenant deployments
Fix: Extract tenant_id from authenticated session (rec.tenant_id), not Query
Endpoint impacts:
  - POST create_snapshot (line 71): hardcodes tenant_id="default" in manager call
  - GET list_snapshots (line 114): query parameter allows cross-tenant access
  - GET get_audit_log (line 133): query parameter allows cross-tenant access
  - GET get_snapshot_detail (line 159): query parameter allows cross-tenant access
  - POST restore_snapshot (line 186): query parameter allows cross-tenant access
  - POST diff_snapshot (line 223): query parameter allows cross-tenant access
  - DELETE delete_snapshot (line 256): query parameter allows cross-tenant access
  - GET get_subsystem_audit_log (line 267): query parameter allows cross-tenant access
```

#### [CRITICAL-6] Zero Authentication on Plugins Endpoints
```
File: core/console/corvin_console/routes/control_plane_plugins.py
Lines: 54, 84, 111, 141, 168, 199, 229
Issue: Missing @Depends(require_session) or @Depends(require_csrf)
Impact: ANYONE can install/enable/disable/uninstall plugins
Attack: curl -X PUT http://localhost:8765/v1/console/control-plane/plugins/install \
  -H "Content-Type: application/json" \
  -d '{"plugin_id":"malicious","name":"evil","version":"1.0","boot_layer":"bundled"}' \
  # No authentication needed!
Fixes:
  - Line 54: install_plugin → add @Depends(require_csrf)
  - Line 84: list_plugins → add @Depends(require_session)
  - Line 111: get_plugin → add @Depends(require_session)
  - Line 141: enable_plugin → add @Depends(require_csrf)
  - Line 168: disable_plugin → add @Depends(require_csrf)
  - Line 199: uninstall_plugin → add @Depends(require_csrf)
  - Line 229: get_audit_log → add @Depends(require_session)
```

#### [CRITICAL-7] Zero Authentication on Subsystems Endpoints
```
File: core/console/corvin_console/routes/control_plane_subsystems.py
Lines: 60, 91, 125, 156, 191, 215, 234, 267
Issue: Missing @Depends(require_session) or @Depends(require_csrf)
Impact: ANYONE can start/stop/pause/resume critical subsystems
Attack: curl -X PATCH http://localhost:8765/v1/console/control-plane/subsystems/worker-1/stop \
  -H "Content-Type: application/json" \
  -d '{"force":true,"timeout_s":1}' \
  # No authentication needed! Subsystem stops!
Fixes:
  - Line 60: start_subsystem → add @Depends(require_csrf)
  - Line 91: pause_subsystem → add @Depends(require_csrf)
  - Line 125: resume_subsystem → add @Depends(require_csrf)
  - Line 156: stop_subsystem → add @Depends(require_csrf)
  - Line 191: get_subsystem_status → add @Depends(require_session)
  - Line 215: list_subsystems → add @Depends(require_session)
  - Line 234: get_subsystem_logs → add @Depends(require_session)
  - Line 267: get_subsystem_audit_log → add @Depends(require_session)
```

#### [CRITICAL-8] Hardcoded User IDs — No Attribution
```
Files: Multiple route files
Lines: control_plane_snapshots.py:101, 211, 275
        control_plane_plugins.py:71, 155, 185, 215
        control_plane_subsystems.py:79, 113, 144, 179
Pattern: creator_id="console-user" / operator_id="console-user"
Issue: All operations credited to "console-user" instead of actual user
Impact: GDPR Art. 30 violation — no attribution to actual operator
Consequence: Audit trail doesn't identify who performed actions
Fix: Extract from session (rec.sid or rec.sid_fingerprint) instead of hardcoding
Example:
  Before: creator_id="console-user"
  After:  creator_id=rec.sid  # From authenticated session
```

#### [CRITICAL-9] Missing CSRF Protection on ALL Mutations
```
Files: All route files
Missing pattern: @Depends(require_csrf)
Issue: Every POST/PATCH/DELETE endpoint needs CSRF token validation
Current: All endpoints missing this decorator
Impact: CSRF attacks can hijack control plane operations
Fix: Add @Depends(require_csrf) to every mutation endpoint:
  - control_plane_snapshots.py: lines 71 (POST), 186 (POST), 223 (POST), 256 (DELETE)
  - control_plane_plugins.py: lines 54 (PUT), 141 (PATCH), 168 (PATCH), 199 (DELETE)
  - control_plane_subsystems.py: lines 60 (PATCH), 91 (PATCH), 125 (PATCH), 156 (PATCH)
  - control_plane_overrides.py: lines 187 (POST approve), 233 (POST deny)
Test: curl -X POST http://localhost:8765/v1/console/control-plane/snapshots \
  -H "Content-Type: application/json" \
  -H "Cookie: corvin_console_sid=valid_sid" \
  -d '{"name":"hack","description":"test"}' \
  # Should return 403 (CSRF token missing)
```

#### [CRITICAL-10] Plugin Boot Layer Not Validated in Route
```
File: core/console/corvin_console/routes/control_plane_plugins.py
Line: 69
Code: boot_layer=req.boot_layer  # NO validation!
Issue: Route passes boot_layer directly to manager without pre-checking
Note: Manager DOES validate (plugin_manager.py:86-103), but route should fail-fast
Fix: Add validation in route BEFORE calling manager:
  try:
      BootLayer(req.boot_layer)  # Will raise ValueError if invalid
  except ValueError:
      raise HTTPException(status_code=400, detail=f"Invalid boot_layer: {req.boot_layer}")
Severity: CRITICAL because malformed input should fail at route layer (defense-in-depth)
```

#### [CRITICAL-11] Unbounded Snapshot Name/Description
```
File: core/console/corvin_console/routes/control_plane_snapshots.py
Lines: 44-45 (model definition)
Code: class SnapshotCreateRequest(BaseModel):
          name: str  # NO max_length!
          description: str  # NO max_length!
Attack: POST with name = "a" * (1024 * 1024 * 100)  # 100 MB name
Impact: Memory exhaustion → process crash → denial of service
Fix: Add Pydantic constraints:
  from pydantic import BaseModel, Field
  class SnapshotCreateRequest(BaseModel):
      name: str = Field(max_length=256)
      description: str = Field(max_length=4096)
Test: curl -X POST http://localhost:8765/v1/console/control-plane/snapshots \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$(python3 -c 'print(\"a\"*1000000)')\",\"description\":\"test\"}" \
  # Should return 413 (Payload Too Large) or 400
```

#### [CRITICAL-12] Unbounded timeout_s Parameter
```
File: core/console/corvin_console/routes/control_plane_subsystems.py
Line: 42
Code: class SubsystemOperationRequest(BaseModel):
          timeout_s: Optional[int] = 30  # NO min/max!
Attack: Send timeout_s = -999 or timeout_s = 2147483647 (2^31-1 seconds = 68 years)
Impact: Unexpected behavior, DoS via infinite waits, thread pool exhaustion
Fix: Add Pydantic constraints:
  from pydantic import BaseModel, Field
  class SubsystemOperationRequest(BaseModel):
      force: Optional[bool] = False
      timeout_s: Optional[int] = Field(default=30, ge=1, le=3600)  # 1 sec to 1 hour max
Also update routes at lines 111, 177 that use timeout_s
Test: curl -X PATCH http://localhost:8765/v1/console/control-plane/subsystems/s1/pause \
  -H "Content-Type: application/json" \
  -H "Cookie: corvin_console_sid=valid_sid" \
  -H "x-csrf-token: valid_token" \
  -d '{"timeout_s":-999}' \
  # Should return 422 (Unprocessable Entity - validation error)
```

#### [CRITICAL-13] Intent Router Import Error at Startup
```
File: core/console/corvin_console/routes/intents.py
Line: 22
Current: from core.audit import audit_backend  # DOESN'T EXIST
Error: ImportError: cannot import name 'audit_backend' from 'core.audit'
Reason: core/audit/__init__.py doesn't export audit_backend
Fix: Change import to:
  from core.plugins.corvin_plugins.providers import audit_backend
Verification: core/plugins/corvin_plugins/providers/audit_backend.py exists and has:
  - get_active() function (line 436)
  - fanout() function (line 461)
  - _registry global (line 433)
Impact: BLOCKS console startup — critical path item
Blocker: Must fix BEFORE running any integration tests
```

---

### HIGH SEVERITY ISSUES (8 total)

#### [HIGH-1] Missing @consent_required on Mutation Endpoints
```
Locations: All control plane route files
Pattern: No @consent_required decorator on ANY route
Impact: GDPR Art. 6(1) consent requirement not enforced
Affected operations:
  - Snapshot create/restore/delete (snapshot operations on user data)
  - Plugin install/enable/disable (affects system behavior)
  - Subsystem control (affects critical services)
Reference: intents.py line 58 has @consent_required("intent_classification")
           Control plane operations are MORE sensitive and have ZERO gates
Fix: Add @consent_required("<scope>") to every mutation endpoint
Depends: Must create core/compliance/consent.py first (Issue HIGH-2)
```

#### [HIGH-2] core/compliance/consent Module Missing
```
Location: /home/shumway/projects/CorvinOS/core/compliance/
Status: DOES NOT EXIST
Imports: core/console/corvin_console/routes/intents.py:23
         from core.compliance.consent import consent_required
Error: ModuleNotFoundError: No module named 'core.compliance.consent'
Blocks: Can't implement consent gates until this module exists
Action Required:
  1. Create /home/shumway/projects/CorvinOS/core/compliance/consent.py
  2. Define consent_required decorator
  3. Export from core/compliance/__init__.py
  4. Wire into deps.py for import in routes
Priority: HIGH — blocks consent gate implementation
```

#### [HIGH-3] Plugins Tenant Isolation Broken
```
File: core/console/corvin_console/routes/control_plane_plugins.py
Lines: 70, 154, 184, 214 (in manager calls)
Pattern: tenant_id="default" (hardcoded instead of from session)
Impact: Multi-tenant deployments have complete cross-tenant leakage
Scenario:
  1. Tenant A operator calls install_plugin()
  2. All plugins installed under "default" tenant only
  3. Tenant B can list/install/disable these "shared" plugins
  4. No isolation between tenants
Fix: Extract from session: tenant_id=rec.tenant_id
Affects: Every plugin operation (install, enable, disable, uninstall)
```

#### [HIGH-4] Subsystems Tenant Isolation Broken
```
File: core/console/corvin_console/routes/control_plane_subsystems.py
Lines: 63, 95, 128, 160, 194, 217, 238, 269 (Query defaults)
Pattern: tenant_id: str = Query(default="default")
Impact: Same as plugins — cross-tenant access possible
Fix: Extract from session: tenant_id=rec.tenant_id (not from Query)
Pattern repeats 8+ times across all subsystem endpoints
```

#### [HIGH-5] Plugin Manager Audit Events Volatile
```
File: core/console/corvin_console/control_plane/plugin_manager.py
Line: 68
Code: self.audit_events = []  # In-memory list!
Route that uses it: control_plane_plugins.py:229 → manager.get_audit_log()
Impact: On process restart, ALL audit events are lost
Consequence: Compliance audit trail broken for plugin operations
Fix: Wire to core audit chain (ADR-0232) instead of volatile memory
Current: get_audit_log() returns self.audit_events[-limit:]
Should: Return events from hash-chained audit.jsonl
Depends: Must wire real audit_backend first (Issue CRITICAL-2)
```

#### [HIGH-6] Subsystem Manager Audit Events Volatile
```
File: core/control_plane/subsystem_controller.py (likely)
Pattern: Same as plugin manager — audit_events as volatile list
Route that uses it: control_plane_subsystems.py:267 → manager.get_audit_log()
Impact: Subsystem operation audit trail lost on restart
Fix: Wire to core audit chain instead of volatile memory
```

#### [HIGH-7] Snapshot Restore Doesn't Actually Restore
```
File: core/control_plane/snapshot_manager.py
Lines: 186-220 in routes (calls manager.restore_snapshot())
Issue: API returns {"status":"success"} without actually modifying state
Consequence: User thinks system was restored, but state is unchanged
Silent failure: No error reported, user is misled
Severity: HIGH because it's a silent failure (user trusts wrong state)
Fix: Implement actual state restoration in snapshot_manager.py
Verify: After restore, run verification that state matches snapshot
```

#### [HIGH-8] Error Messages Leak Implementation Details
```
File: core/console/corvin_console/routes/control_plane_overrides.py
Lines: 101-104, 114-115, 173-174
Pattern: detail=str(exc)  or  detail=f"Invalid override_type: {body.override_type}"
Issue: Exception messages returned to client reveal system internals
Impact: Helps attackers enumerate valid values
Example:
  Before: "Invalid override_type: invalid_value" (reveals valid ones exist)
  After: "Invalid input"
Fix: Log full error internally, return generic message to client
Pattern:
  # Bad:
  except ValueError as exc:
      raise HTTPException(status_code=400, detail=str(exc))
  
  # Good:
  except ValueError as exc:
      logger.error(f"Override validation failed: {exc}")
      raise HTTPException(status_code=400, detail="Invalid override request")
```

---

## TESTING CHECKLIST

### Authentication Tests
- [ ] GET /v1/console/control-plane/snapshots without session → 401
- [ ] GET /v1/console/control-plane/plugins without session → 401
- [ ] GET /v1/console/control-plane/subsystems without session → 401
- [ ] POST create snapshot without session → 401
- [ ] PUT install plugin without session → 401
- [ ] PATCH start subsystem without session → 401

### CSRF Tests
- [ ] POST without x-csrf-token → 403
- [ ] POST with invalid token → 403
- [ ] DELETE without x-csrf-token → 403
- [ ] Valid token + valid session → 200

### Tenant Isolation Tests
- [ ] Tenant A creates snapshot → stored for Tenant A only
- [ ] Tenant B cannot access Tenant A's snapshots (even with explicit query)
- [ ] Tenant A lists snapshots → sees only own snapshots
- [ ] Audit log filtered by tenant_id

### Audit Chain Tests
- [ ] Every create/restore/delete operation creates audit event
- [ ] Audit events hash-chained (prev_hash links to previous event)
- [ ] Audit events survive process restart (in audit.jsonl)
- [ ] Snapshots, plugins, subsystems all audited

### Adversarial Tests
- [ ] Privilege escalation attempt (add_approver) is blocked
- [ ] Unbounded name input (1 MB) is rejected
- [ ] Negative timeout_s is rejected
- [ ] Invalid boot_layer is rejected

---

## SUMMARY

**Total Issues:** 21 (13 Critical + 8 High)  
**Files to Fix:** 11  
**Estimated Effort:** 6-8 hours  
**Blocking Dependencies:** 2 (audit_backend export, consent module)  
**GO/NO-GO:** 🔴 **NO-GO — Phase 9 is not production-ready**

**Next Step:** Start with Dependency fixes, then proceed through Critical issues in priority order.

