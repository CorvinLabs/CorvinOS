# Bug Fix Checklist — Adversarial Testing Findings

This document provides developers with a step-by-step checklist for fixing each discovered bug.

---

## Bug #1: Privilege Escalation via Thread Escape

**Severity:** HIGH | **Assignee:** [Security Team]  
**Estimated Time:** 4-6 hours  
**ADR:** ADR-0233 D5 (update required)

### Reproduction
```python
# Run: python -m pytest test_adversarial_racing.py::TestSecurityAndValidation::test_privilege_escalation_in_same_epoch -xvs
```

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py:_resolve_boot_layer()` lines 366-469
  - [ ] Read `registry.py:_register_locked()` lines 530-536
  - [ ] Understand `loading.current()` and how `_loading.loading()` context works
  - [ ] Review ADR-0233 D5 for privilege escalation semantics

- [ ] **Design Solution**
  - [ ] Option A: Track spawned threads per plugin and validate re-register calls
  - [ ] Option B: Use thread-local storage to mark "trusted" re-register paths
  - [ ] Option C: Add a 30-second timeout after on_load (threads can't escape after timeout)
  - [ ] Document chosen approach and rationale

- [ ] **Implementation**
  - [ ] Add new field to `PluginRegistry.__init__()` to track spawned threads
  - [ ] Modify `_resolve_boot_layer()` to check spawned thread status
  - [ ] Modify `register()` to record spawned threads (if any)
  - [ ] Add unit test for the fix

- [ ] **Testing**
  - [ ] Run `test_adversarial_racing.py::test_privilege_escalation_in_same_epoch` (should pass)
  - [ ] Run all existing plugin tests (no regressions)
  - [ ] Stress test: 100 malicious plugins attempting to escape
  - [ ] Add regression test to main test suite

- [ ] **Documentation**
  - [ ] Update ADR-0233 D5 with fix rationale
  - [ ] Add code comments explaining the guard
  - [ ] Document thread-spawning limitations for plugin authors

- [ ] **Code Review**
  - [ ] Request security team review
  - [ ] Ensure epoch check is still sound
  - [ ] Verify no new race conditions introduced

### Verification Script
```python
# After fix, run this to verify:
def test_escalation_blocked():
    registry = PluginRegistry()
    plugin = EvilPlugin()
    ctx = MagicMock()
    
    # Should succeed as INSTALLED
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)
    assert registry.boot_layer_of("evil") == BootLayer.INSTALLED
    
    # Unregister (escape thread will run)
    registry.unregister("evil")
    time.sleep(1)  # Let escape thread attempt re-register
    
    # Should be gone (or re-registered as INSTALLED, not CORE)
    try:
        layer = registry.boot_layer_of("evil")
        assert layer != BootLayer.CORE, "Escalation succeeded (BAD)"
    except PluginNotFound:
        pass  # Acceptable (thread lost race)
```

---

## Bug #2: Wedged Health Check Thread Leak

**Severity:** MEDIUM | **Assignee:** [Performance Team]  
**Estimated Time:** 6-8 hours  
**Test:** `test_adversarial_racing.py::TestConcurrentPluginInstalls::test_concurrent_health_check_timeout_wedge`

### Reproduction
```bash
# Confirm thread leak:
python -c "
import threading
from corvin_plugins import PluginRegistry, CorvinPlugin
from unittest.mock import MagicMock
import time

registry = PluginRegistry()
plugin = MagicMock(spec=CorvinPlugin)
plugin.plugin_id = 'slow'
plugin.health_check = lambda: time.sleep(999) or HealthStatus(ok=True)
plugin.on_load = MagicMock()
ctx = MagicMock()

registry.register(plugin, ctx)

initial_threads = threading.active_count()
print(f'Initial threads: {initial_threads}')

# Call health() 10 times (each times out)
for i in range(10):
    try:
        registry.health('slow')
    except:
        pass

time.sleep(3)  # Wait for timeouts

final_threads = threading.active_count()
print(f'Final threads: {final_threads}')
print(f'Leaked threads: {final_threads - initial_threads}')
# Should be 0, but will be ~10 (BUG)
"
```

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py:_call_with_deadline()` lines 118-157
  - [ ] Understand why `pool.shutdown(wait=False)` is used (avoids hang)
  - [ ] Review how health checks are called (line 272-280)

- [ ] **Design Solution**
  - [ ] Create `HealthCheckTracker` class with:
    - [ ] `start_check(plugin_id) -> check_id`
    - [ ] `end_check(plugin_id, check_id)`
    - [ ] `cancel_all_for_plugin(plugin_id)`
  - [ ] Store active checks in registry
  - [ ] Cancel all checks before unregister

- [ ] **Implementation**
  - [ ] Add `_health_checks: dict[str, list[futures.Future]]` to `PluginRegistry.__init__()`
  - [ ] Modify `_call_with_deadline()` to register future in tracker
  - [ ] Add `cancel_health_checks(plugin_id)` method
  - [ ] Call `cancel_health_checks()` in `_unregister_locked()`
  - [ ] Add unit test

- [ ] **Testing**
  - [ ] Run reproduction script (thread count should stabilize)
  - [ ] Run `test_adversarial_racing.py::test_concurrent_health_check_timeout_wedge` (should pass)
  - [ ] Stress test: 100 concurrent health checks with timeouts
  - [ ] Memory profiler: confirm thread count doesn't grow

- [ ] **Documentation**
  - [ ] Update code comments explaining thread cleanup
  - [ ] Document health check cancellation behavior

- [ ] **Code Review**
  - [ ] Verify no thread joins in timeout path
  - [ ] Verify cancellation doesn't break other health checks

### Verification Script
```python
# After fix, run this:
def test_no_thread_leak():
    import threading
    registry = PluginRegistry()
    plugin = MagicMock()
    plugin.plugin_id = 'test'
    plugin.health_check = lambda: time.sleep(999)
    plugin.on_load = MagicMock()
    ctx = MagicMock()
    
    registry.register(plugin, ctx)
    
    initial = threading.active_count()
    
    # Timeout 10 times
    for _ in range(10):
        try:
            registry.health('test')
        except:
            pass
    
    # Unregister (should cancel all)
    registry.unregister('test')
    
    time.sleep(3)  # Wait for timeouts and cleanup
    
    final = threading.active_count()
    assert final <= initial + 2, f"Leaked threads: {final - initial}"
```

---

## Bug #3: TOCTOU Race in can_disable()

**Severity:** LOW | **Assignee:** [Core Team]  
**Estimated Time:** 2-3 hours  
**Test:** `test_adversarial_racing.py::TestPluginStateTransitions` (add new test)

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py:can_disable()` lines 679-689
  - [ ] Read `registry.py:disable()` lines 691-698
  - [ ] Review callers of `disable()` to understand usage patterns

- [ ] **Design Solution**
  - [ ] Merge check into `disable()` under a single lock
  - [ ] New implementation:
    ```python
    def disable(self, plugin_id):
        with self._op_lock(plugin_id):
            with self._lock:
                if plugin_id not in self._plugins:
                    raise PluginNotFound(...)
                if self._boot_layers.get(...) is BootLayer.COMPLIANCE:
                    raise PluginDisableRefused(...)
            self._unregister_locked(plugin_id, operator_initiated=True)
    ```

- [ ] **Implementation**
  - [ ] Modify `disable()` to do atomic check-and-set
  - [ ] Remove the TOCTOU window
  - [ ] Update docstrings

- [ ] **Testing**
  - [ ] Run `test_adversarial_racing.py::test_toctou_can_disable_vs_unregister`
  - [ ] Concurrent disable + unregister stress test
  - [ ] Verify audit trail is consistent

- [ ] **Code Review**
  - [ ] Verify atomic operation
  - [ ] Check for new deadlock opportunities

---

## Bug #4: Multi-Process Registry Write Race

**Severity:** MEDIUM | **Assignee:** [File I/O Team]  
**Estimated Time:** 2-3 hours  
**Test:** `test_adversarial_racing.py::TestConcurrentPluginInstalls::test_concurrent_registry_mutations_file_corruption`

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `state.py:registry_mutation()` lines 92-120
  - [ ] Read `state.py:TenantRegistry.load()` and `.save()` methods
  - [ ] Understand fcntl locking on current platform

- [ ] **Verify the Bug**
  - [ ] Check current fcntl lock acquisition point
  - [ ] Verify lock is held during entire `save()` operation
  - [ ] Test with multi-process benchmark

- [ ] **Design Solution**
  - [ ] Extend fcntl lock to span load→modify→save
  - [ ] OR: Use a separate `.lock` file with long-lived lock
  - [ ] Document locking semantics

- [ ] **Implementation**
  - [ ] Modify `registry_mutation()` context manager
  - [ ] Ensure `fcntl.flock()` is held for entire duration
  - [ ] Add unit test for lock correctness

- [ ] **Testing**
  - [ ] Run multi-process stress test (20 concurrent processes, 100 iterations each)
  - [ ] Verify no data loss (all plugins appear in registry)
  - [ ] Run `test_concurrent_registry_mutations_file_corruption`

- [ ] **Documentation**
  - [ ] Document the locking strategy
  - [ ] Add code comments

---

## Bug #5: Audit Event Lost on Emit Failure

**Severity:** MEDIUM | **Assignee:** [Audit Team]  
**Estimated Time:** 2-3 hours  
**Test:** `test_adversarial_racing.py::TestMutationResistance::test_audit_trail_recorded_on_every_mutation`

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py:_register_locked()` lines 488-560
  - [ ] Understand what happens if `ctx.audit_emit()` raises
  - [ ] Review rollback logic

- [ ] **Design Solution**
  - [ ] Option A: Emit before returning (earlier in function)
  - [ ] Option B: Wrap emit in try/except, fallback to stderr if it fails
  - [ ] Option C: Rollback registration if emit fails
  - [ ] Choose one and document rationale

- [ ] **Implementation**
  - [ ] Modify `_register_locked()` to emit synchronously
  - [ ] Add exception handling with fallback
  - [ ] Add unit test

- [ ] **Testing**
  - [ ] Mock `ctx.audit_emit` to raise exception
  - [ ] Verify plugin is either fully registered or fully rolled back
  - [ ] Run `test_audit_trail_recorded_on_every_mutation`

---

## Bug #6: String boot_layer Not Validated Early

**Severity:** LOW | **Assignee:** [Core Team]  
**Estimated Time:** 1-2 hours

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py:register()` line 484

- [ ] **Design Solution**
  - [ ] Validate `boot_layer` parameter before acquiring locks
  - [ ] Move `BootLayer(boot_layer)` conversion early

- [ ] **Implementation**
  - [ ] Add validation at entry to `register()`
  - [ ] Clearer error messages

- [ ] **Testing**
  - [ ] Test with invalid boot_layer strings
  - [ ] Verify lock is not leaked

---

## Bug #7: Hooks Not Revoked if Audit Emit Fails

**Severity:** MEDIUM | **Assignee:** [Audit Team]  
**Estimated Time:** 2-3 hours

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py:_register_locked()` lines 540-559
  - [ ] Understand `_verify_hook_ownership()` behavior

- [ ] **Design Solution**
  - [ ] Ensure `_verify_hook_ownership()` happens after audit emit
  - [ ] OR: Wrap both in try/except with rollback

- [ ] **Implementation**
  - [ ] Reorder operations if needed
  - [ ] Add exception handling

---

## Edge Case #11: MAX_TENANT_HISTORY Full Clear

**Severity:** MEDIUM | **Assignee:** [Core Team]  
**Estimated Time:** 2-3 hours

### Fix Steps

- [ ] **Understand Current Code**
  - [ ] Read `registry.py` lines 324-326 (MAX_TENANT_HISTORY)
  - [ ] Read line 502-503 (clear behavior)

- [ ] **Design Solution**
  - [ ] Use LRU eviction instead of full clear
  - [ ] Keep newest 4096 entries, evict oldest

- [ ] **Implementation**
  - [ ] Replace `dict` with `collections.OrderedDict` or `functools.lru_cache`
  - [ ] Add LRU eviction logic

- [ ] **Testing**
  - [ ] Register 5000+ plugins, verify oldest entries are evicted
  - [ ] Verify hook ownership verification still works

---

## Security Gap #1-2: Trust Anchor Validation

**Severity:** MEDIUM | **Assignee:** [Security Team]  
**Estimated Time:** 3-4 hours

### Fix Steps

- [ ] **Understand Trust Module**
  - [ ] Locate `trust.py` module
  - [ ] Review `verify_plugin_signature()` function
  - [ ] Understand how trust anchor is loaded

- [ ] **Design Solution**
  - [ ] Validate trust anchor file:
    1. Is regular file (not symlink)
    2. Mode 0o400 (owner-read only)
    3. No path traversal in path
    4. Within expected directory

- [ ] **Implementation**
  - [ ] Add validation function `_validate_trust_anchor_path()`
  - [ ] Call before loading key
  - [ ] Clear error messages

- [ ] **Testing**
  - [ ] Test symlink attack
  - [ ] Test path traversal
  - [ ] Test permission check
  - [ ] Test with world-readable key

---

## Security Gap #3: Manifest Schema Validation

**Severity:** MEDIUM | **Assignee:** [Validation Team]  
**Estimated Time:** 4-5 hours

### Fix Steps

- [ ] **Understand Current Validation**
  - [ ] Read `manifest.py:PluginRecord` dataclass
  - [ ] Review `load_manifest()` function

- [ ] **Design Solution**
  - [ ] Define JSON schema for manifest
  - [ ] Use `jsonschema` library for validation
  - [ ] Strict validation (no extra fields)
  - [ ] Type checking for all fields

- [ ] **Implementation**
  - [ ] Create manifest schema (JSON)
  - [ ] Add validation function
  - [ ] Call during manifest load

- [ ] **Testing**
  - [ ] Test malformed YAML
  - [ ] Test missing required fields
  - [ ] Test wrong field types
  - [ ] Test extra fields (should fail)
  - [ ] Test circular references

---

## Security Gap #4: PII Scrubbing on All Outputs

**Severity:** MEDIUM | **Assignee:** [Audit Team]  
**Estimated Time:** 3-4 hours

### Fix Steps

- [ ] **Audit Text Outputs**
  - [ ] Find all plugin-provided text outputs:
    1. Health check messages (already scrubbed)
    2. Exception messages (NOT scrubbed)
    3. Audit details (NOT scrubbed)
    4. Settings values (NOT scrubbed)

- [ ] **Design Solution**
  - [ ] Create `_scrub_any_text()` utility
  - [ ] Apply to exception messages
  - [ ] Apply to settings values before audit emit
  - [ ] Apply to audit details

- [ ] **Implementation**
  - [ ] Modify exception handling in `_unregister_locked()` line 602
  - [ ] Modify settings scrubbing before audit
  - [ ] Add unit tests

- [ ] **Testing**
  - [ ] Inject PII into each output path
  - [ ] Verify scrubbed or redacted

---

## Cross-Cutting Concerns

### Testing After Each Fix
```bash
# Run full test suite:
python -m pytest core/plugins/tests/ -v

# Run adversarial tests only:
python -m pytest core/plugins/tests/test_adversarial_racing.py -v

# Run with coverage:
python -m pytest core/plugins/tests/test_adversarial_racing.py --cov=corvin_plugins
```

### Regression Testing
- [ ] All existing plugin tests pass
- [ ] No new warnings or deprecations
- [ ] No performance degradation (>10%)

### Documentation
- [ ] Update relevant ADRs
- [ ] Add code comments explaining fixes
- [ ] Update developer guide if needed

### Code Review Checklist
- [ ] Security team reviews
- [ ] Performance team reviews
- [ ] Architecture team reviews
- [ ] Tests have >80% coverage
- [ ] No new technical debt introduced

---

## Timeline Estimate

| Phase | Bugs | Est. Time | Deadline |
|-------|------|-----------|----------|
| P0 (Critical) | #1, #4, #5 | 10-12h | Week 1 |
| P1 (Important) | #2, #3, #6, #7, Gaps | 20-25h | Week 2 |
| P2 (Follow-up) | Edge cases | 15-20h | Weeks 3-4 |
| **Total** | All 12 | **45-57h** | 4 weeks |

**Resource Allocation:**
- Security Team: 15h (Bugs #1, Gaps #1-2, #5)
- File I/O Team: 3h (Bug #4)
- Audit Team: 8h (Bugs #5, #7, Gap #4)
- Core Team: 8h (Bugs #3, #6, Edge case #11)
- Performance Team: 8h (Bug #2)
- Validation Team: 5h (Gap #3)

---

## Sign-Off

| Role | Name | Date | Status |
|------|------|------|--------|
| Security Lead | [ ] | [ ] | |
| Engineering Lead | [ ] | [ ] | |
| QA Lead | [ ] | [ ] | |
| Release Manager | [ ] | [ ] | |

**Note:** All fixes must pass security review before merge to main branch.
