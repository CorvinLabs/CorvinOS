# Stream B: T09 Audit Phase 2b Wiring (3 days)

**Status:** Ready to execute  
**Assigned:** Claude or team  
**Dependencies:** T05 complete ✓ (ADR dedup done)

---

## Overview

Register + emit 4 missing audit events for Phase 2b:
1. `compute.worker_terminated` → L22 Worker Lifecycle
2. `a2a.genesis_block_created` → L38 NBAC Init  
3. `a2a.offline_pair_initiated` → L38 Offline Pairing
4. `plugin.execution_timeout` → L4 Plugin Lifecycle

**Status:** Events already in `EVENT_SEVERITY` + `_EVENT_ALLOWLIST` ✓  
**Missing:** Emit calls (no `write_event(...)` found in prod code)

---

## Audit Event Registration ✓ (Already done)

**Location:** `corvin_operator/forge/forge/security_events.py`

| Event | Line | Status | Severity |
|-------|------|--------|----------|
| `compute.worker_terminated` | 861 | Registered ✓ | INFO |
| `a2a.genesis_block_created` | 877 | Registered ✓ | INFO |
| `a2a.offline_pair_initiated` | 878 | Registered ✓ | INFO |
| `plugin.execution_timeout` | 883 | Registered ✓ | WARNING |

All events also in `_EVENT_ALLOWLIST` (lines 3248, 3267, 3270, 3282) ✓

---

## Wiring: Emit Locations (To Do)

### 1. `compute.worker_terminated` (L22)

**What:** Worker process exits normally or crashes  
**Where:** `core/compute/corvin_compute/worker.py::<Worker.terminate()>`

**Pseudocode:**
```python
def terminate(self, reason: str = "normal"):
    """Shut down the worker process."""
    # ... cleanup code ...
    
    # NEW: Emit audit event
    from corvin_operator.forge.forge.security_events import write_event
    write_event({
        "event_type": "compute.worker_terminated",
        "worker_id": self.worker_id,
        "worker_type": self.worker_type,
        "cpu_cores": self.cpu_cores,
        "memory_mb": self.memory_mb,
        "termination_reason": reason,  # "normal", "timeout", "crash", etc.
        "duration_seconds": time.time() - self.start_time,
    })
```

**Test:**
```bash
pytest tests/security/test_worker_termination_audit.py
# Must verify: every .terminate() call lands audit event with correct reason
```

---

### 2. `a2a.genesis_block_created` (L38)

**What:** A2A NBAC chain initialized (first block created)  
**Where:** `ops/launcher/a2a_entry.py::create_genesis_block()` or equivalent init

**Pseudocode:**
```python
def create_genesis_block(config: A2AConfig) -> GenesisBlock:
    """Initialize NBAC chain with genesis block."""
    block = GenesisBlock(
        nonce_prefix=config.nonce_prefix,
        instance_id=config.instance_id,
        timestamp=time.time()
    )
    
    # NEW: Emit audit event
    from corvin_operator.forge.forge.security_events import write_event
    write_event({
        "event_type": "a2a.genesis_block_created",
        "instance_id": config.instance_id,
        "nonce_prefix": config.nonce_prefix[:8],  # First 8 hex only, not full
        "block_height": block.height,
    })
    
    return block
```

**Test:**
```bash
pytest tests/a2a/test_genesis_block_audit.py
# Must verify: genesis block creation emits event + hash-chain intact
```

---

### 3. `a2a.offline_pair_initiated` (L38)

**What:** A2A offline pairing process starts (establishing trust without online handshake)  
**Where:** `core/bridges/shared/a2a_token.py::OfflinePairingSession.initiate()`

**Pseudocode:**
```python
class OfflinePairingSession:
    def initiate(self, peer_id: str, pairing_code: str):
        """Start offline pairing with peer."""
        session = {
            "peer_id": peer_id,
            "pairing_code_hash": hashlib.sha256(pairing_code.encode()).hexdigest(),
            "initiated_at": time.time(),
        }
        
        # NEW: Emit audit event
        from corvin_operator.forge.forge.security_events import write_event
        write_event({
            "event_type": "a2a.offline_pair_initiated",
            "peer_id": peer_id,
            "pairing_session_id": session["session_id"],
        })
        
        return session
```

**Test:**
```bash
pytest tests/a2a/test_offline_pairing_audit.py
# Must verify: pairing initiation emits event + no pairing_code leaked
```

---

### 4. `plugin.execution_timeout` (L4)

**What:** Plugin exceeded max execution time and was forcibly terminated  
**Where:** `core/plugins/corvin_plugins/lifecycle.py::PluginExecutor.run_with_timeout()`

**Pseudocode:**
```python
def run_with_timeout(self, plugin_id: str, func, timeout_seconds: int) -> Any:
    """Execute plugin function with timeout."""
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(func)
        try:
            result = future.result(timeout=timeout_seconds)
            return result
        except concurrent.futures.TimeoutError:
            # NEW: Emit audit event
            from corvin_operator.forge.forge.security_events import write_event
            write_event({
                "event_type": "plugin.execution_timeout",
                "plugin_id": plugin_id,
                "timeout_seconds": timeout_seconds,
                "error_class": "TimeoutError",
            })
            raise
```

**Test:**
```bash
pytest tests/plugins/test_execution_timeout_audit.py
# Must verify: timeout triggers audit event + no plugin output leaked
```

---

## E2E Proof Template

**For each event:**

1. **Unit Test:** Verify emit call lands event in audit stream
2. **Integration Test:** Run full flow (worker spawn → terminate, etc.) and check audit chain
3. **Hash-Chain Verification:** Run `verify_audit_chain()` post-test, confirm 0 hash breaks

**Example for compute.worker_terminated:**

```python
def test_worker_terminated_audit_e2e():
    """E2E: worker lifecycle → audit event + chain integrity."""
    
    # 1. Spawn a worker
    worker = ComputeWorker(job_id="test_123")
    
    # 2. Terminate it
    worker.terminate(reason="test_complete")
    
    # 3. Verify audit event in chain
    audit_records = audit_store.query_events(
        event_type="compute.worker_terminated",
        job_id="test_123"
    )
    assert len(audit_records) == 1, "Event must be emitted"
    
    # 4. Verify chain integrity
    from corvin_operator.forge.forge.audit_validator import verify_chain
    result = verify_chain()
    assert result.is_valid, "Hash chain must remain intact"
    assert result.gap_count == 0, "No gaps allowed"
```

---

## Implementation Checklist

- [ ] **Day 1:** Implement + test event #1 (compute.worker_terminated)
- [ ] **Day 1:** Implement + test event #2 (a2a.genesis_block_created)
- [ ] **Day 2:** Implement + test event #3 (a2a.offline_pair_initiated)
- [ ] **Day 2:** Implement + test event #4 (plugin.execution_timeout)
- [ ] **Day 3:** Full Wave 1 E2E test suite (all 4 events + hash-chain validation)

**Success Criteria:**
- ✅ All 4 events emit on their respective triggers
- ✅ All 4 events land in audit chain (hash-linked)
- ✅ E2E test suite passes (4 × event + 1 × chain integrity test)
- ✅ CI gate confirms zero audit event gaps (EVENT_SEVERITY vs. actual emits)

---

## Quality Gate: E2E Wiring Proof

**Mandatory for T09 completion:**
```bash
# Run full integration test
pytest tests/security/test_audit_phase2_events.py -v

# Verify chain integrity
python3 scripts/verify_audit_chain.py --tenant=_default

# Grep: confirm 4 events in chain
grep -E "compute.worker_terminated|a2a.genesis_block_created|a2a.offline_pair_initiated|plugin.execution_timeout" \
  ~/.corvin/audit.jsonl | wc -l
# Expected: ≥4
```

---

**Start:** T05 complete ✓ → Ready to begin 2026-09-26 morning  
**Duration:** 3 days (parallel with Stream C + D)  
**EOD Target:** 2026-09-27

