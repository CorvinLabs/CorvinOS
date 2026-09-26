# ADVERSARIAL REVIEW: Session Context Loss Solution (6 Phases)

**Reviewer:** Claude Haiku 4.5  
**Date:** 2026-09-26  
**Standard:** ADR-0670 + dialectical-reasoning + failure mode hunting  
**Status:** CRITICAL FINDINGS PRESENT

---

## EXECUTIVE SUMMARY

**Severity Breakdown:**
- 🔴 **CRITICAL (Blockers):** 3 findings
- 🟠 **HIGH (Major):** 4 findings
- 🟡 **MEDIUM (Design):** 5 findings
- 🟢 **LOW (Hardening):** 2 findings

**Overall Assessment:** Implementation has correct architecture BUT multiple critical bugs prevent it from working. **REJECT for merge.** Fix bugs, add tests, re-review.

---

## PHASE 1: CODE REVIEW — CRITICAL BUGS

### **CRITICAL BUG #1: FrozenInstanceError on Snapshot Hash**

**Location:** `core/infinite_session/session_bridge_producer.py:228`

```python
snapshot = SessionContextSnapshot(...)  # frozen=True
# ... later ...
snapshot.content_hash = snapshot.compute_content_hash()  # ← BUG
```

**Problem:**
- `SessionContextSnapshot` is `@dataclass(frozen=True)` (Line 29)
- Line 228 tries to SET a field on frozen dataclass
- Python raises `FrozenInstanceError` at runtime
- **Snapshot creation ALWAYS FAILS**

**Failure Scenario:**
```
Session N calls finalize_response()
→ MessageCompletenessGate.finalize_turn_with_context()
→ SessionBridgeProducer.create_snapshot()
→ snapshot.content_hash = ... ← FrozenInstanceError
→ Exception uncaught
→ Turn fails, no bridge event emitted
→ **CONTEXT IS LOST** (exact failure this solution was meant to prevent)
```

**Fix:**
Compute hash BEFORE creating dataclass, pass as constructor arg:
```python
# Pre-compute hash
content_hash = SessionContextSnapshot(
    tenant_id=tenant_id,
    # ... all fields
    content_hash="",  # placeholder
).compute_content_hash()

# Create with computed hash
snapshot = SessionContextSnapshot(
    tenant_id=tenant_id,
    # ... all fields
    content_hash=content_hash,  # ← now immutable
)
```

**Severity:** CRITICAL — Solution completely broken

---

### **CRITICAL BUG #2: ContextVar Mocks, Not Real Implementation**

**Location:** `core/concurrency/context_loss_sentinel.py:18-22`

```python
# Placeholder ContextVars (real implementation in core/concurrency/context_helpers.py)
_task_id_var = ContextVar("task_id", default=None)
_tenant_id_var = ContextVar("tenant_id", default=None)
_worktree_var = ContextVar("worktree_path", default=None)
_base_commit_var = ContextVar("base_commit", default=None)
_phase_var = ContextVar("phase_name", default=None)
```

**Problem:**
- These ContextVars are LOCAL to `context_loss_sentinel.py`
- `SessionRecoveryManager` is in a DIFFERENT MODULE (`session_recovery.py`)
- When recovery manager calls `ContextVar.set()`, it modifies its OWN local copy
- `ContextLossSentinel.assert_*()` checks a DIFFERENT copy
- **Context is NOT actually restored across module boundaries**

**Failure Scenario:**
```
Session N+1 starts:
→ SessionRecoveryManager.auto_restore_session_context() in session_recovery.py
→ Calls ContextVarRestorer.restore_context_vars_from_snapshot()
→ Tries to set: _tenant_id_var.set("_default")  ← BUT which _tenant_id_var?
→ Later, tool calls ContextLossSentinel.assert_tenant_context()
→ Checks ITS OWN _tenant_id_var in context_loss_sentinel.py
→ Returns None (because it was never set in THIS module)
→ ContextLossError raised ← **FALSE POSITIVE**
```

**Real Impact:**
- Every session boundary triggers false "context lost" errors
- Solution defeats its own purpose
- Operator can't use sessions anymore

**Fix:**
Use SHARED ContextVars from canonical location:
```python
# core/concurrency/context_helpers.py (SINGLE SOURCE OF TRUTH)
from contextvars import ContextVar

TASK_ID_VAR = ContextVar("task_id", default=None)
TENANT_ID_VAR = ContextVar("tenant_id", default=None)
# ... etc

# Both modules import from here:
from core.concurrency.context_helpers import TASK_ID_VAR, TENANT_ID_VAR
```

**Severity:** CRITICAL — Core feature broken by module isolation

---

### **CRITICAL BUG #3: Snapshot Persistence is Undefined**

**Location:** `core/infinite_session/session_recovery.py:240-260`

```python
def _find_latest_snapshot(self, task_id: str, tenant_id: str):
    snapshot_file = (
        self.snapshot_dir / tenant_id / task_id / "latest.json"
    )
    
    if not snapshot_file.exists():
        return None
    
    try:
        with open(snapshot_file) as f:
            data = json.load(f)
            # ...
```

**Problem:**
- `SessionBridgeProducer` writes to audit.jsonl (Line 302-311)
- But snapshot_file is written NOWHERE
- `_find_latest_snapshot()` reads from `snapshot_dir / latest.json`
- **File is never created**

**Failure Scenario:**
```
Session N:
→ SessionBridgeProducer.emit_bridge_event()
→ Writes to audit.jsonl ✓
→ Returns SessionBridgeEvent
→ Nobody calls: save_snapshot_to_disk(snapshot)  ← MISSING!

Session N+1:
→ SessionRecoveryManager.auto_restore_session_context()
→ Calls _find_latest_snapshot()
→ File doesn't exist
→ Returns None
→ No context restored
→ **Context is lost** (solution didn't work)
```

**Fix:**
Implement snapshot persistence in SessionBridgeProducer:
```python
def emit_bridge_event(self, ...):
    # ... create event ...
    self._persist_snapshot_to_disk(snapshot)  # ← ADD THIS
    self._write_audit_event(bridge_event)
```

**Severity:** CRITICAL — Nothing actually persists

---

## PHASE 2: ARCHITECTURAL REVIEW — HIDDEN ASSUMPTIONS

### **HIGH BUG #4: No Cryptographic Key Management**

**Location:** `core/infinite_session/session_recovery.py:60-75`

```python
def verify_snapshot_signature(
    self,
    snapshot_dict: Dict[str, Any],
    signature: Optional[str] = None,
    external_key: str = "default-key",  # ← HARDCODED
) -> SnapshotVerificationResult:
```

**Hidden Assumption:** "We have a secure key management system"

**Reality:** Hardcoded `"default-key"` string

**Implications:**
- Every snapshot signed with SAME key
- Key is in source code (compromised on any repo leak)
- No key rotation
- No HSM integration (ADR-0541 requires this)
- Signature verification is theater, not security

**Failure Scenario:**
```
Attacker obtains source code
→ Finds "default-key"
→ Modifies snapshot: task_id = "admin_task"
→ Recomputes HMAC with "default-key"
→ Signature passes verification ✓
→ Admin context restored ✗
→ **PRIVILEGE ESCALATION**
```

**Severity:** HIGH — Security theater

---

### **HIGH BUG #5: No Tenant Isolation in Snapshots**

**Location:** `core/infinite_session/session_bridge_producer.py:287-310`

```python
def _write_audit_event(self, event: SessionBridgeEvent) -> None:
    """Write bridge event to audit trail (append-only)."""
    
    self.event_store_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Serialize event
    event_dict = {
        "event_type": event.event_type,
        # ... fields ...
    }
    
    # Append to audit trail (append-only, one JSON per line)
    with open(self.event_store_path, "a") as f:
        f.write(json.dumps(event_dict) + "\n")
```

**Problem:**
- ALL tenants write to SAME `audit.jsonl` file
- No tenant_id isolation in file path
- One tenant can read another tenant's snapshots

**Failure Scenario:**
```
Tenant A: task_id="refactor_auth"
→ SessionBridgeProducer writes to ~/.corvin/.../audit.jsonl

Tenant B: task_id="refactor_auth" (same name!)
→ Writes to SAME audit.jsonl
→ Can read Tenant A's events (same file)
→ Tenant isolation BROKEN

Later:
Tenant B's SessionRecoveryManager:
→ Loads Tenant A's snapshot (tenant_id="A" in event)
→ Verification checks: tenant_id == "_default" ✓ (wrong!)
→ **CROSS-TENANT CONTEXT LEAK**
```

**Expected:** `~/.corvin/tenants/_default/global/forge/audit.jsonl` (tenant-scoped)  
**Actual:** Same path for all tenants

**Severity:** HIGH — GDPR violation

---

### **HIGH BUG #6: No Handoff Verification Between Sessions**

**Location:** `core/infinite_session/session_recovery.py:110-140`

The `auto_restore_session_context()` method verifies signature but NOT the session chain:

```python
# Verify snapshot signature (fail-closed)
sig_result = self.verifier.verify_snapshot_signature(...)

# Verify tenant isolation (fail-closed)
tenant_result = self.verifier.verify_tenant_isolation(...)

# Restore ContextVars ACTIVELY
restored_vars = self.restorer.restore_context_vars_from_snapshot(...)

# But: NO VERIFICATION that this is the NEXT session in the chain!
```

**Missing Check:**
- No verification that `dest_session_id` matches current session_id
- No verification that bridge event is RECENT (not replayed from days ago)
- No verification that THIS is the intended recipient

**Failure Scenario:**
```
Attacker obtains snapshot from Session 1:
→ Signature is valid (wrong key management)
→ Tenant_id matches (wrong file structure)

Session 999 (different user):
→ Manually loads old snapshot
→ Calls auto_restore_session_context()
→ Context from Session 1 restored in Session 999
→ **CROSS-SESSION HIJACK**
```

**Severity:** HIGH — Replayable contexts

---

### **HIGH BUG #7: Assertions Are Optional, Not Mandatory**

**Location:** `core/concurrency/context_loss_sentinel.py:13-33` (all assert methods)

```python
@staticmethod
def assert_task_context() -> str:
    """Assert task_id is set, raise if missing."""
    task_id = _task_id_var.get(None)
    if task_id is None:
        logger.error("CONTEXT LOSS DETECTED: ...")
        raise ContextLossError(...)
    return task_id
```

**Problem:**
- Method EXISTS but is OPTIONAL to call
- No enforcement that critical paths use it
- Code review can miss places where assertions are missing

**Failure Scenario:**
```
Developer writes:
    def process_tool_response(tool_name, tool_input):
        # Forgot to call assert_task_context()
        logger.info(f"Processing {tool_name} for task={task_id}")
        # task_id is None, but no error raised yet
        # Audit event written without task_id
        emit_audit_event({"task_id": None, ...})
        
        # Later...
        return result
        
No error! Silent context loss!
```

**Real Impact:**
- Audit trail has NULL task_ids
- GDPR records incomplete
- Next session doesn't know which task it came from
- **Defeats the entire solution**

**Fix:**
Make assertions MANDATORY via linter rules or decorator:
```python
@require_context("task_id", "tenant_id")
def process_tool_response(tool_name, tool_input):
    # If context missing, raises before function runs
    ...
```

**Severity:** HIGH — Design flaw, not just code

---

## PHASE 3: DESIGN REVIEW — MISSING PIECES

### **MEDIUM #8: No E2E Integration Test Actual Wiring**

**Location:** `tests/e2e/test_session_continuity_e2e.py`

The test file EXISTS but is mock-based:
```python
def test_full_session_continuity_flow(tmp_path):
    producer = SessionBridgeProducer(event_store_path=tmp_path / "audit.jsonl")
    
    # ... creates snapshot ...
    
    recovery = SessionRecoveryManager(...)
    
    # ... reads snapshot ...
    
    assert (tmp_path / "audit.jsonl").exists()
```

**Problem:**
- Tests use `tmp_path` (temporary directory)
- No test of REAL integration with `chat_runtime.py`
- No test that `finalize_response()` actually calls bridge producer
- No test that `initialize_chat_session()` actually calls recovery manager

**Severity:** MEDIUM — Tests don't prove integration works

---

### **MEDIUM #9: Message Completeness Envelope Has No Consumer**

**Location:** `core/console/corvin_console/message_completeness_protocol.py:71-88`

```python
def finalize_turn_with_context(...) -> SessionMessageEnvelope:
    """Finalize turn: create snapshot + emit bridge event + return message envelope."""
    
    # ... creates envelope ...
    
    return envelope
```

**Problem:**
- This envelope is created but never sent to client
- `chat_runtime.py` integration guide shows HOW but doesn't DO it
- No code that yields the envelope to the WebSocket

**Severity:** MEDIUM — Dead code, no consumer

---

### **MEDIUM #10: Context Recovery Has No Error Handling for Real Failure Cases**

**Location:** `core/infinite_session/session_recovery.py:100-115`

Missing error scenarios:
- Snapshot file is corrupted JSON
- Signature is cryptographically invalid (not just string mismatch)
- Tenant_id check passes but user doesn't have permission to this task
- Snapshot is from future (timestamp in the future)

**Current Code:**
```python
# Verify snapshot signature (fail-closed)
sig_result = self.verifier.verify_snapshot_signature(...)
if not sig_result.is_valid:
    logger.error(f"Snapshot signature verification FAILED: {sig_result.reason}")
    return None  # ← Silent failure
```

**Severity:** MEDIUM — Silent failures don't help debugging

---

## PHASE 4: FAILURE MODE MATRIX

| Failure Mode | Trigger | Current Behavior | Should Be |
|--------------|---------|------------------|-----------|
| **Frozen dataclass mutation** | create_snapshot() | FrozenInstanceError | Compute hash before creation |
| **ContextVar module isolation** | Any assertion | False positive | Use shared vars module |
| **Snapshot not persisted** | emit_bridge_event() | File not created | Write to disk |
| **Weak cryptography** | Signature verification | Theater | HSM key management |
| **Tenant isolation broken** | Audit trail path | Single file for all | Tenant-scoped paths |
| **No session chain validation** | Restore context | Replayable contexts | Verify dest_session_id + timestamp |
| **Assertions optional** | Code review | Silent loss | Mandatory decorator |
| **Integration untested** | Merge to main | Works in unit tests, fails live | Real E2E in chat_runtime |
| **Envelope not consumed** | Turn ends | Envelope created but dropped | Wire yield into WebSocket |
| **Silent error handling** | Real failures | Logged but hidden | Raise and escalate |

---

## SYNTHESIS: Fixes Required Before Merge

### **Must Fix (CRITICAL):**

1. ✅ **FrozenInstanceError Fix** (session_bridge_producer.py:228)
   - Compute hash before dataclass construction
   - Time: 10 min
   
2. ✅ **ContextVar Module Isolation** (context_loss_sentinel.py + session_recovery.py)
   - Move to shared context_helpers.py
   - Import both modules from there
   - Time: 15 min

3. ✅ **Snapshot Persistence** (session_bridge_producer.py)
   - Implement `_persist_snapshot_to_disk()`
   - Wire into emit_bridge_event()
   - Time: 20 min

### **Must Fix (HIGH):**

4. ✅ **Key Management** (session_recovery.py:63)
   - Remove hardcoded "default-key"
   - Wire to actual key store / HSM
   - Time: 30 min

5. ✅ **Tenant-Scoped Audit Path** (session_bridge_producer.py:289)
   - Use `~/.corvin/tenants/{tenant_id}/global/forge/audit.jsonl`
   - Not shared path
   - Time: 10 min

6. ✅ **Session Chain Validation** (session_recovery.py:110-115)
   - Verify dest_session_id matches current
   - Verify timestamp is recent (< 24h)
   - Time: 15 min

7. ✅ **Mandatory Assertions Decorator** (context_loss_sentinel.py)
   - Create @require_context decorator
   - Use on critical paths
   - Time: 30 min

### **Should Fix (MEDIUM):**

8. ✅ **Real Integration Tests** (test_session_continuity_e2e.py)
   - Add fixtures for chat_runtime integration
   - Test finalize_response() → bridge producer
   - Test initialize_chat_session() → recovery manager
   - Time: 1 hour

9. ✅ **Wire Envelope to Client** (chat_runtime.py)
   - Yield SessionMessageEnvelope in stream_turn()
   - Test WebSocket receives it
   - Time: 30 min

10. ✅ **Explicit Error Handling** (session_recovery.py)
    - Raise on corrupted JSON
    - Raise on invalid signature
    - Raise on permission denied
    - Time: 20 min

---

## TOTAL REMEDIATION: ~3.5 hours

After fixes, REQUIRE:
- [ ] Full re-test with real chat_runtime integration
- [ ] Adversarial review round 2
- [ ] Code review with security team

---

## VERDICT

**Current Status:** ❌ **REJECT FOR MERGE**

**Reason:** 3 critical bugs prevent solution from functioning. Not integration-ready.

**After Fixes:** ✅ **LIKELY ACCEPTABLE** with conditions

**Conditions:**
1. All 10 findings fixed + verified
2. Integration tests passing
3. Security review of key management
4. Cross-tenant isolation verified
5. Replayability tests added

---

**Reviewer Signature:** Claude Haiku 4.5  
**Review Standard:** ADR-0670 Adversarial Review Methodology  
**Next Steps:** Author addresses findings → Re-review → Merge decision
