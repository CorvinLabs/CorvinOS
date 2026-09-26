# Phase 9 Integration Implementation Guide (3 Days)

**Time-Boxed by Priority | All Fixes Specified**

---

## TODAY (1h CRITICAL)

### 1. SessionBridgeProducer._persist_snapshot_to_disk()
**File:** `core/infinite_session/session_bridge_producer.py`  
**Location:** After `emit_bridge_event()` method (line ~311)

```python
def _persist_snapshot_to_disk(self, snapshot: SessionContextSnapshot) -> None:
    """FIX #3: Persist snapshot to disk for next session recovery."""
    snapshot_dir = (
        Path.home()
        / ".corvin" / "tenants" / snapshot.tenant_id
        / "infinite_session" / "snapshots" / snapshot.task_id
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    
    snapshot_file = snapshot_dir / "latest.json"
    snapshot_data = {
        "snapshot": snapshot.to_dict(),
        "timestamp": datetime.utcnow().isoformat(),
    }
    
    with open(snapshot_file, "w") as f:
        json.dump(snapshot_data, f, indent=2)
    
    logger.info(f"Snapshot persisted: {snapshot_file}")
```

**Wire into emit_bridge_event():**  
After `bridge_event.hash = bridge_event.compute_hash()` (line ~296), add:
```python
# FIX #3: Persist snapshot before audit event
self._persist_snapshot_to_disk(snapshot)
```

### 2. Update Module Imports for Shared ContextVars
**File:** `core/concurrency/context_loss_sentinel.py`  
**Change Lines 18-22:**

FROM:
```python
_task_id_var = ContextVar("task_id", default=None)
_tenant_id_var = ContextVar("tenant_id", default=None)
# ... etc
```

TO:
```python
from core.concurrency.context_helpers import (
    TASK_ID_VAR, TENANT_ID_VAR, WORKTREE_PATH_VAR,
    BASE_COMMIT_VAR, PHASE_NAME_VAR, SESSION_ID_VAR,
)
```

Replace all `_task_id_var` → `TASK_ID_VAR`, etc. throughout file.

**File:** `core/infinite_session/session_recovery.py`  
Same import pattern + replace all local ContextVar refs.

### 3. FIX #5: Tenant-Scoped Audit Path
**File:** `core/infinite_session/session_bridge_producer.py`  
**Method:** `_write_audit_event()` (line ~313)

Change path from:
```python
self.event_store_path  # ~/.corvin/.../audit.jsonl (shared)
```

To:
```python
event_store_path = (
    Path.home()
    / ".corvin" / "tenants" / event.tenant_id
    / "global" / "forge" / "audit.jsonl"
)
event_store_path.parent.mkdir(parents=True, exist_ok=True)
```

Write to `event_store_path` not `self.event_store_path`.

---

## TODAY+1 (1.5h HIGH)

### 4. KeyManagementConfig (FIX #4)
**File:** `core/infinite_session/key_management.py` (NEW)

```python
"""Key management for cryptographic snapshot verification."""
import os
from pathlib import Path

class KeyManagementConfig:
    @staticmethod
    def get_snapshot_key() -> str:
        """Get cryptographic key from secure location (fail-closed)."""
        # Try env var first (dev)
        key = os.getenv("CORVIN_SNAPSHOT_KEY")
        if key and key != "default-key":
            return key
        
        # Try keyfile (production HSM stub)
        keyfile = Path.home() / ".corvin" / "keys" / "snapshot.key"
        if keyfile.exists():
            key = keyfile.read_text().strip()
            if key and key != "default-key":
                return key
        
        raise ValueError(
            "CORVIN_SNAPSHOT_KEY not set and no keyfile at ~/.corvin/keys/snapshot.key. "
            "Cannot verify snapshots without cryptographic key."
        )

    @staticmethod
    def validate_not_default(key: str) -> None:
        """Fail-closed: reject hardcoded default key."""
        if key == "default-key":
            raise ValueError("Using hardcoded default key — security violation")
```

Update `session_recovery.py::SnapshotVerifier.verify_snapshot_signature()`:
```python
from core.infinite_session.key_management import KeyManagementConfig

def verify_snapshot_signature(...):
    external_key = KeyManagementConfig.get_snapshot_key()
    KeyManagementConfig.validate_not_default(external_key)
    # ... rest of verification
```

### 5. Session Chain Validation (FIX #6)
**File:** `core/infinite_session/session_recovery.py`  
**Method:** `auto_restore_session_context()` (line ~100)

Add before context restoration:
```python
# FIX #6: Session chain validation
snapshot_age_hours = (
    (datetime.utcnow() - datetime.fromisoformat(snapshot_dict.get("timestamp", "")))
    .total_seconds() / 3600
)

if snapshot_age_hours > 24:  # 24h staleness threshold
    logger.error(f"Snapshot too old: {snapshot_age_hours}h")
    raise SnapshotExpiredError(f"Snapshot stale ({snapshot_age_hours}h old)")

# Validate destination session
if snapshot_dict.get("dest_session_id") and snapshot_dict["dest_session_id"] != session_id:
    raise ContextLossError(
        f"Snapshot for session {snapshot_dict['dest_session_id']}, "
        f"cannot restore to {session_id}"
    )
```

### 6. Tenant-Scoped Snapshot Paths
**File:** `core/infinite_session/session_recovery.py`  
**Method:** `_find_latest_snapshot()` (line ~240)

Already fixed by _persist_snapshot_to_disk() above (uses tenant_id in path).

---

## TODAY+2-3 (1h MEDIUM)

### 7. @require_context Decorator (FIX #7)
**File:** `core/concurrency/context_helpers.py`

Add to end of file:
```python
from functools import wraps
from typing import Callable

def require_context(*var_names: str) -> Callable:
    """Decorator: assert ContextVars are set before function runs (fail-closed)."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            missing = []
            for var_name in var_names:
                var = globals().get(f"{var_name.upper()}_VAR")
                if var and var.get() is None:
                    missing.append(var_name)
            
            if missing:
                raise ContextLossError(
                    f"Missing context: {missing}. "
                    f"Call auto_restore_session_context() first."
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

Apply to critical paths:
```python
# In chat_runtime.py
@require_context("task_id", "tenant_id")
async def process_tool_response(tool_name, tool_input):
    ...

# In plugin manager
@require_context("task_id")
def plugin_execute(plugin, *args):
    ...

# In task manager
@require_context("tenant_id")
def create_task(*args):
    ...
```

### 8. Real E2E Integration Tests (FIX #8)
**File:** `tests/e2e/test_session_continuity_real_integration.py` (NEW)

```python
"""Real integration tests with chat_runtime (not mocks)."""
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_session_n_to_n_plus_1_flow(tmp_path):
    """Session N → finalize → snapshot → Session N+1 → restore."""
    
    # Session N: finalize turn
    from core.console.corvin_console.message_completeness_protocol import MessageCompletenessGate
    from core.infinite_session.session_bridge_producer import SessionBridgeProducer
    from core.infinite_session.session_recovery import SessionRecoveryManager
    
    # Setup
    producer = SessionBridgeProducer(event_store_path=tmp_path / "audit.jsonl")
    gate = MessageCompletenessGate(producer=producer)
    
    # Session N finalize
    envelope = gate.finalize_turn_with_context(
        assistant_response="done",
        user_message="test",
        turn_number=1,
        task_id="test_task",
        session_id="sess_1",
        tenant_id="_default",
        last_message_hash="hash1",
        conversation_turn_count=1,
        worktree_path="/tmp",
        base_commit="abc123",
        phase_name="Phase Test",
    )
    
    assert envelope.session_state is not None
    
    # Verify snapshot persisted
    snapshot_file = tmp_path / "snapshots" / "_default" / "test_task" / "latest.json"
    assert snapshot_file.exists()
    
    # Session N+1: restore
    recovery = SessionRecoveryManager()
    # Mock to use tmp_path
    with patch.object(recovery, 'snapshot_dir', tmp_path / "snapshots"):
        restored = await recovery.auto_restore_session_context("_default", "test_task")
        assert restored is not None
        assert restored["task_id"] == "test_task"
```

### 9. Envelope Consumer Wiring (FIX #9)
**File:** `core/console/corvin_console/chat_runtime.py`  
**Method:** `_stream_turn_impl()` (end of generator)

Before `yield {"type": "done"}`, add:
```python
# FIX #9: Wire envelope consumer
from core.console.corvin_console.message_completeness_protocol import MessageCompletenessGate

gate = MessageCompletenessGate()
envelope = gate.finalize_turn_with_context(
    assistant_response=final_response_text,
    user_message=prompt,
    turn_number=sess.turn_count,
    task_id=task_id,
    session_id=sess.chat_key,
    tenant_id=sess.tenant_id,
    last_message_hash=hash(final_response_text),
    conversation_turn_count=len(turns),
    worktree_path=str(sess.workdir),
    base_commit=current_git_commit(),
    phase_name="current_phase",
    # ... fill in from sess context
)

yield envelope.to_dict()  # ← Send envelope to WebSocket
yield {"type": "done"}
```

### 10. Explicit Error Handling (FIX #10)
**File:** `core/infinite_session/session_recovery.py`

Create exception classes:
```python
class SnapshotVerificationError(Exception): pass
class SnapshotExpiredError(Exception): pass

def auto_restore_session_context(...):
    # FIX #10: Explicit errors instead of silent returns
    try:
        snapshot = self._find_latest_snapshot(...)
        if not snapshot:
            return None  # OK: no prior context
        
        # Verify (raises on failure)
        sig_result = self.verifier.verify_snapshot_signature(...)
        if not sig_result.is_valid:
            raise SnapshotVerificationError(sig_result.reason)
        
        # Tenant check (raises on mismatch)
        tenant_result = self.verifier.verify_tenant_isolation(...)
        if not tenant_result.is_valid:
            raise ContextLossError(tenant_result.reason)
        
        # ... rest of restoration
    except SnapshotVerificationError as e:
        logger.warning(f"Snapshot verification failed: {e}")
        raise  # Don't swallow
    except SnapshotExpiredError as e:
        logger.warning(f"Snapshot expired: {e}")
        return None  # Fall back to fresh context
```

Caller (`initialize_chat_session()`) must:
```python
try:
    restored = await recovery.auto_restore_session_context(...)
except ContextLossError:
    logger.error("Cross-tenant context leak detected")
    return fresh_context()
except SnapshotVerificationError:
    logger.error("Snapshot tampered")
    return fresh_context()
```

---

## VERIFICATION CHECKLIST

- [ ] Snapshot files created at: `~/.corvin/tenants/{tid}/infinite_session/snapshots/{taskid}/latest.json`
- [ ] All ContextVars imported from `context_helpers` (no local copies)
- [ ] Audit trail at: `~/.corvin/tenants/{tid}/global/forge/audit.jsonl` (tenant-scoped)
- [ ] KeyManagementConfig rejects hardcoded key
- [ ] Session chain validation: timestamp < 24h, dest_session_id matches
- [ ] @require_context decorator applied to 3+ critical paths
- [ ] E2E test passes: Session N → snapshot → Session N+1 → restore
- [ ] SessionMessageEnvelope received on WebSocket
- [ ] All errors explicit (SnapshotVerificationError, SnapshotExpiredError, ContextLossError)

---

## COMMITS

**TODAY:** `fix(phase-9): FIX #3 snapshot persistence + FIX #5 tenant audit paths + FIX #2 context vars`

**TODAY+1:** `fix(phase-9): FIX #4 key management + FIX #6 session chain validation`

**TODAY+2-3:** `fix(phase-9): FIX #7 decorator + FIX #8 e2e tests + FIX #9 envelope + FIX #10 errors`

---

**Total Time: 3.5 hours | All 10 findings → 0 by Day 3 EOD**
