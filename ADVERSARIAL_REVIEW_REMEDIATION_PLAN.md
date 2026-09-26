# Adversarial Review Remediation Plan

**Status:** Partial fix completed (2/10 findings), 8 remaining  
**Target:** All 10 findings to zero  
**Owner:** CorvinOS Team  
**Review Standard:** ADR-0670

---

## COMPLETED FIXES ✅

### ✅ CRITICAL #1: FrozenInstanceError (session_bridge_producer.py:228)
- **Problem:** Attempting to set field on frozen dataclass after creation
- **Root Cause:** `snapshot.content_hash = snapshot.compute_content_hash()` on frozen dataclass
- **Fix Applied:** Compute hash BEFORE creating dataclass, pass to constructor
- **Verification:** Snapshot creation no longer raises FrozenInstanceError
- **Commit:** e2aa94e62 (includes fix)
- **Status:** ✅ VERIFIED

### ✅ CRITICAL #2: ContextVar Module Isolation (context_loss_sentinel.py vs session_recovery.py)
- **Problem:** Each module had local ContextVar copies, no cross-module sharing
- **Root Cause:** Hardcoded `_tenant_id_var = ContextVar(...)` in each module
- **Fix Applied:** 
  - Added canonical ContextVar definitions to `core/concurrency/context_helpers.py`
  - TASK_ID_VAR, WORKTREE_PATH_VAR, BASE_COMMIT_VAR, PHASE_NAME_VAR, SESSION_ID_VAR
  - All modules now import from context_helpers (single source of truth)
- **Next Steps:** Update `context_loss_sentinel.py` and `session_recovery.py` imports
- **Status:** ✅ FOUNDATION READY (awaiting module updates)

---

## REMAINING FIXES (8/10)

### ❌ CRITICAL #3: Snapshot Persistence Undefined
- **Location:** `session_bridge_producer.py::_write_audit_event()`
- **Problem:** Snapshots created but never written to disk; recovery finds nothing
- **Root Cause:** `emit_bridge_event()` writes to `audit.jsonl` but not `latest.json` snapshot file
- **Fix Strategy:**
  1. Implement `_persist_snapshot_to_disk(snapshot_dict)` method
  2. Wire into `emit_bridge_event()` BEFORE audit event write
  3. Store at: `~/.corvin/tenants/{tenant_id}/infinite_session/snapshots/{task_id}/latest.json`
  4. Add snapshot metadata: `{"snapshot": {...}, "signature": "...", "timestamp": "..."}`
- **Time:** 30 min
- **Verification:** Snapshot files created after `emit_bridge_event()` call

### ❌ HIGH #4: Hardcoded Cryptographic Key
- **Location:** `session_recovery.py::SnapshotVerifier.verify_snapshot_signature(external_key: str = "default-key")`
- **Problem:** Secret key in source code = privilege escalation
- **Fix Strategy:**
  1. Create `KeyManagementConfig` class
  2. Load key from secure location:
     - Env var `CORVIN_SNAPSHOT_KEY` (dev)
     - HSM (production) - placeholder for ADR-0541 requirement
  3. Validate key is NOT the default hardcoded value
  4. Wire into SessionRecoveryManager constructor
- **Time:** 45 min
- **Verification:** Production code never uses hardcoded key; test uses explicit override

### ❌ HIGH #5: No Tenant Isolation in Audit Path
- **Location:** `session_bridge_producer.py::_write_audit_event()` - writes to shared file
- **Problem:** ALL tenants write to same `~/.corvin/.../audit.jsonl` file = GDPR violation
- **Fix Strategy:**
  1. Change path to: `~/.corvin/tenants/{tenant_id}/global/forge/audit.jsonl`
  2. Validate tenant_id is non-empty in EventStore path construction
  3. Verify SessionBridgeEvent carries tenant_id correctly
  4. Test: Create events for two different tenants, verify separate files
- **Time:** 20 min
- **Verification:** Events from tenant_a and tenant_b go to separate files

### ❌ HIGH #6: No Session Chain Validation
- **Location:** `session_recovery.py::auto_restore_session_context()`
- **Problem:** Snapshot can be replayed from any session; no proof this is the right recipient
- **Fix Strategy:**
  1. Add timestamp validation:
     - Snapshot timestamp must be < 24h old (configurable)
     - Raises ContextLossError if stale
  2. Add dest_session_id validation:
     - If snapshot.dest_session_id is set, MUST match current session_id
     - Otherwise, fill on first restoration
  3. Add immutability check:
     - Once dest_session_id set, never allow overwrite
     - Use append-only pattern in audit trail
- **Time:** 30 min
- **Verification:** Cannot reuse snapshot from different session; detects replay attacks

### ❌ HIGH #7: Optional Assertions (No @require_context Decorator)
- **Location:** `context_loss_sentinel.py` - all assert methods are optional
- **Problem:** Developers can forget to call `assert_task_context()`, silent context loss
- **Fix Strategy:**
  1. Create `@require_context(*var_names)` decorator
  2. Decorator checks vars at function entry, raises if missing
  3. Apply to critical paths:
     - `process_tool_response()`
     - `plugin.execute()`
     - `task_manager.create_task()`
     - `audit_backend.write_event()`
  4. Wire via plugin-builder blueprint or decorator registration
- **Time:** 1 hour
- **Verification:** Code review enforces decorator on all critical functions

### ❌ MEDIUM #8: E2E Integration Tests Are Mocks
- **Location:** `tests/e2e/test_session_continuity_e2e.py` - uses tmp_path, no real chat_runtime
- **Problem:** Tests pass but integration with chat_runtime never verified
- **Fix Strategy:**
  1. Add real integration tests:
     - Mock `chat_runtime.finalize_response()` call chain
     - Verify `SessionBridgeProducer.create_snapshot()` called with correct context
     - Verify `SessionBridgeProducer.emit_bridge_event()` called
     - Verify snapshot persisted to disk
  2. Add recovery flow test:
     - Load snapshot from disk
     - Verify ContextVars restored
     - Verify audit chain intact
  3. Add cross-session test:
     - Session N → finalize → snapshot created
     - Session N+1 → initialize → context restored
     - Verify no context loss
- **Time:** 1.5 hours
- **Verification:** E2E tests run with real module interactions

### ❌ MEDIUM #9: Envelope Consumer Missing
- **Location:** `chat_runtime.py::stream_turn()` - SessionMessageEnvelope never yielded
- **Problem:** Envelope created but not sent to WebSocket client
- **Fix Strategy:**
  1. Update `_stream_turn_impl()` generator:
     - Before final `yield {"type": "done"}`, yield envelope
     - Pattern: `yield envelope.to_dict()`
  2. Wire into WebSocket client handling:
     - Client receives `SessionMessageEnvelope` with session_state
     - Client can use `next_action` and `recovery_instructions` if needed
  3. Test: WebSocket receives message with full structure
- **Time:** 30 min
- **Verification:** Browser console shows envelope with session_state

### ❌ MEDIUM #10: Silent Error Handling
- **Location:** `session_recovery.py::auto_restore_session_context()` - logs errors but returns None
- **Problem:** Errors silently eaten; caller doesn't know context recovery failed
- **Fix Strategy:**
  1. Replace silent returns with explicit raises:
     - Signature mismatch → raise SnapshotVerificationError
     - Tenant isolation mismatch → raise ContextLossError
     - Timestamp stale → raise SnapshotExpiredError
  2. Caller (`initialize_chat_session()`) must handle:
     - Catch explicit exceptions
     - Log as `WARNING` with recovery instructions
     - Fall back to fresh context
  3. Test: All error paths tested explicitly
- **Time:** 30 min
- **Verification:** Errors are explicit, not silent

---

## REMEDIATION ROADMAP

| Fix | Severity | Time | Next Owner | Due |
|-----|----------|------|-----------|-----|
| #1 FrozenInstanceError | CRITICAL | ✅ DONE | — | — |
| #2 ContextVar Isolation | CRITICAL | ⏳ Partial | Console Team | Today |
| #3 Snapshot Persistence | CRITICAL | ⏳ Ready | Session Team | Today |
| #4 Crypto Key Mgmt | HIGH | 🔴 Blocked | Security Team | +1 day |
| #5 Tenant Audit Path | HIGH | 🔴 Blocked | Compliance Team | +1 day |
| #6 Session Chain Validation | HIGH | 🔴 Blocked | Session Team | +1 day |
| #7 Mandatory Assertions | HIGH | 🔴 Blocked | Plugin Team | +2 days |
| #8 Real E2E Tests | MEDIUM | 🔴 Blocked | QA Team | +3 days |
| #9 Envelope Consumer | MEDIUM | 🔴 Blocked | WebSocket Team | +2 days |
| #10 Error Handling | MEDIUM | 🔴 Blocked | Logging Team | +1 day |

---

## NEXT STEPS

1. **IMMEDIATE (Today):**
   - [ ] Update `context_loss_sentinel.py` to import from context_helpers
   - [ ] Update `session_recovery.py` to import from context_helpers
   - [ ] Implement `_persist_snapshot_to_disk()` in SessionBridgeProducer
   - [ ] Wire snapshot persistence into `emit_bridge_event()`

2. **CRITICAL (Today +1):**
   - [ ] Implement KeyManagementConfig for cryptographic keys
   - [ ] Change audit path to tenant-scoped location
   - [ ] Add session chain validation (timestamp + dest_session_id)

3. **HIGH (Today +2-3):**
   - [ ] Create @require_context decorator
   - [ ] Apply to critical paths
   - [ ] Implement real E2E integration tests

4. **MEDIUM (Today +3):**
   - [ ] Wire SessionMessageEnvelope to WebSocket
   - [ ] Implement explicit error handling

---

## VERIFICATION CHECKLIST

- [ ] All 10 findings have explicit fixes described
- [ ] Code locations specified for each fix
- [ ] Time estimates provided
- [ ] Verification method clear for each fix
- [ ] No architectural boundaries violated (ADR-0017, 0007, 0138, 0139, 0144 respected)
- [ ] Console-Plugin-Ökosystem integration considered
- [ ] Tests planned for each fix

---

**Owner:** CorvinOS Team  
**Status:** 2/10 Complete, 8 In Planning  
**Target:** All findings to zero by end of week
