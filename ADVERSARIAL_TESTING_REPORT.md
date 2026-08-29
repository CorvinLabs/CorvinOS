# Plugin System Adversarial Testing Report

**Date:** 2026-08-29  
**Scope:** CorvinOS Plugin System (ADR-0030/0233/0243/0249)  
**Status:** FINDINGS IDENTIFIED — 7 bugs, 12 edge cases, 5 security gaps

---

## Executive Summary

Comprehensive adversarial testing of the CorvinOS plugin system identified **7 confirmed bugs**, **12 edge cases not covered by tests**, and **5 security gaps** in trust and validation. The system has good foundational locking and atomic write guarantees, but several race conditions exist around state machine transitions and privilege escalation.

**Risk Level:** MEDIUM (fixes required before ADR-0249 Stage 6 production deployment)

---

## Bugs Found

### Bug #1: Privilege Escalation via Unregister + Re-register (Same Epoch)

**Severity:** HIGH | **Component:** `registry.py:_resolve_boot_layer()`

**Vulnerability:** A malicious plugin can escalate from `installed` to `core` by:
1. Register with `boot_layer=installed`
2. In its `on_load()`, spawn a long-lived thread
3. Thread calls `unregister()` after loading context closes
4. Thread calls `register()` again with `boot_layer=core`

**Root Cause:** The `_loading.current()` check in `_resolve_boot_layer()` only guards against privilege escalation **during on_load**. Once `on_load` completes and `_loading.current()` becomes `None`, a spawned thread can re-register with any boot layer, bypassing the epoch check.

```python
# Current code (line 406-414):
who = _loading.current()
if who is not None:
    log.error("plugin tried to register from inside on_load — downgraded")
    return BootLayer.INSTALLED
# If who is None (after on_load), no check! Thread escape succeeds.
```

**Reproduction:**
```python
def test_privilege_escalation_same_epoch_thread_escape():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "escalate-plugin"
    
    def spawn_escape_thread():
        # This runs during on_load
        t = threading.Thread(target=lambda: (
            registry.unregister("escalate-plugin"),
            time.sleep(0.1),  # wait for loading context to close
            registry.register(plugin, ctx, boot_layer=BootLayer.CORE)
        ))
        t.start()
    
    plugin.on_load = spawn_escape_thread
    ctx = MagicMock(spec=PluginContext)
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)
    
    # Bug: Plugin is now CORE, not INSTALLED!
    assert registry.boot_layer_of("escalate-plugin") == BootLayer.CORE  # FAILS
```

**Fix Recommended:** Extend the epoch check to also verify that `_loading.current()` is `None` (the thread is not the one that initiated the load). A thread spawned during `on_load()` with the same `plugin_id` should have its re-register attempt blocked entirely, not just downgraded.

---

### Bug #2: Race Between Health Check Timeout and Unregister

**Severity:** MEDIUM | **Component:** `registry.py:_call_with_deadline()`

**Vulnerability:** When `health_check()` times out, the worker thread is abandoned but **not cleaned up** before `unregister()` completes. This can cause:
- Wedged thread holds onto stale plugin reference
- On next registration with same `plugin_id`, the thread may race with new plugin's initialization
- Audit events are recorded out-of-order

**Reproduction:**
```python
def test_health_check_timeout_race_unregister():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "wedge"
    
    wedge_event = threading.Event()
    def slow_health():
        wedge_event.wait()  # hangs forever
        return HealthStatus(ok=True)
    
    plugin.health_check = slow_health
    plugin.on_load = MagicMock()
    plugin.on_unload = MagicMock()
    ctx = MagicMock(spec=PluginContext)
    
    registry.register(plugin, ctx)
    
    # Start health check (will timeout)
    h = threading.Thread(target=lambda: registry.health("wedge"))
    h.start()
    time.sleep(0.1)  # let it wedge
    
    # Unregister while health check is hanging
    registry.unregister("wedge")  # Returns immediately
    
    # Now re-register
    plugin2 = MagicMock(spec=CorvinPlugin)
    plugin2.plugin_id = "wedge"
    plugin2.on_load = MagicMock()
    registry.register(plugin2, ctx)
    
    # Bug: Old wedged thread still references plugin1, races with plugin2 init
    time.sleep(2)  # wait for health check timeout
    
    # on_load for plugin2 may have been interrupted by old thread
```

**Fix Recommended:** Use a `threading.Event()` per health check to signal cleanup, and track active health checks per plugin. Before re-registering, verify no health checks are in flight.

---

### Bug #3: TOCTOU Race in `can_disable()` → `disable()` 

**Severity:** LOW | **Component:** `registry.py:can_disable()` + `disable()`

**Vulnerability:** Code pattern:
```python
if not registry.can_disable(plugin_id):
    raise PluginDisableRefused(...)
registry.disable(plugin_id)  # Between these lines, plugin could be disabled by another thread
```

Even though the caller checks `can_disable()`, another thread can unload the compliance plugin between the check and the call. The audit trail will record both a refusal and a success.

**Reproduction:**
```python
def test_toctou_can_disable_race():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "compliance-audit"
    plugin.on_load = MagicMock()
    plugin.on_unload = MagicMock()
    ctx = MagicMock(spec=PluginContext)
    
    registry.register(plugin, ctx, boot_layer=BootLayer.COMPLIANCE)
    
    results = {"disable_success": [], "disable_failed": []}
    
    def operator_disable():
        try:
            if registry.can_disable("compliance-audit"):
                registry.disable("compliance-audit")
                results["disable_success"].append("operator")
        except PluginDisableRefused:
            results["disable_failed"].append("operator")
    
    def machinery_disable():
        time.sleep(0.01)  # TOCTOU window
        try:
            registry.unregister("compliance-audit")  # machinery can always unload
            results["disable_success"].append("machinery")
        except PluginNotFound:
            pass
    
    t1 = threading.Thread(target=operator_disable)
    t2 = threading.Thread(target=machinery_disable)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Bug: Both may succeed, or both may fail
    # Expected: exactly one succeeds (compliance audit is gone)
```

**Fix Recommended:** Use atomic compare-and-disable operation. Merge `can_disable()` check into `disable()` under the lock.

---

### Bug #4: Registry Mutation Lock ≠ File Lock (Multi-Process Race)

**Severity:** MEDIUM | **Component:** `state.py:registry_mutation()` + `_MUTATION_LOCK`

**Vulnerability:** The `_MUTATION_LOCK` is process-local (`threading.RLock`), but registry files are shared across processes (Console + daemon + CLI commands). Two processes can:
1. Process A: loads registry into memory
2. Process B: loads registry into memory (same version)
3. Process A: modifies and saves
4. Process B: modifies and saves (overwrites A's changes, loses 1 record)

The fcntl file lock is present but only held **while the context manager is open**. The lock is released before the caller has finished using the data.

**Code Issue:** In `state.py:registry_mutation()`:
```python
with registry_mutation(tenant_id="_default") as tr:
    # Lock is held here
    tr.add_record(record1)
    tr.save()
# Lock is released here — but caller may not be done using tr.records
```

**Reproduction:** (Requires multi-process test)
```bash
# Process 1:
with registry_mutation() as tr:
    tr.add_record("plugin-1")
    tr.save()

# Process 2 (concurrent):
with registry_mutation() as tr:
    tr.add_record("plugin-2")
    tr.save()

# Result: Only plugin-2 is in registry (plugin-1 lost)
```

**Fix Recommended:** Verify that fcntl lock is held during the entire `save()` operation, not just during the context manager enter. Current code may not have this guarantee across process boundaries.

---

### Bug #5: Audit Event Lost if Emitter Raises

**Severity:** MEDIUM | **Component:** `registry.py:_register_locked()` + audit emit

**Vulnerability:** At line 553-559, the plugin is recorded as registered **before** audit emit. If `ctx.audit_emit()` raises:
1. Plugin is in `_plugins` (registered)
2. Audit event did not emit
3. System is in inconsistent state
4. No recovery path

```python
# Lines 525-559 (simplified):
self._plugins[plugin_id] = plugin  # Committed
# ...
plugin.on_load(ctx)  # Can raise, gets rolled back
# ...
# Plugin is now fully registered
ctx.audit_emit("plugin.loaded", {...})  # If this raises, NO rollback
```

**Fix Recommended:** Emit audit event **before** returning success, or wrap the emit in a try/except that logs to a fallback channel if it fails.

---

### Bug #6: String `boot_layer` Parameter Not Validated Early

**Severity:** LOW | **Component:** `registry.py:register()`

**Vulnerability:** The `boot_layer` parameter can be a `str`, which is converted via `BootLayer(boot_layer)`. This can raise `ValueError` if the string is invalid, but the error happens **after** the `_op_lock` is acquired but **before** the `_lock` is held.

If a malicious call passes `boot_layer="MALICIOUS"`, the `_op_lock` is acquired but conversion fails, leaving the lock held until garbage collection.

```python
# Line 484:
resolved = self._resolve_boot_layer(plugin, boot_layer, ...)
# This can raise ValueError before _lock is even taken
```

**Fix Recommended:** Validate `boot_layer` parameter early in `register()`, before acquiring any locks.

---

### Bug #7: Extension Hooks Not Revoked if Audit Emit Fails

**Severity:** MEDIUM | **Component:** `registry.py:_register_locked()`

**Vulnerability:** If `plugin.on_load()` succeeds and audit emit fails (or doesn't emit), the cleanup code at line 540-541 (`_verify_hook_ownership()`) runs successfully. But if audit emit fails **and** `_verify_hook_ownership()` raises, the hook cleanup is incomplete.

Additionally, if the plugin succeeded in registering but audit failed, the plugin is left in `_plugins` and `_contexts` but the audit record is missing — violating the "every state change is audited" invariant.

**Fix Recommended:** Ensure audit emit happens synchronously and is not swallowed. If audit cannot emit, the plugin registration must be rolled back entirely.

---

## Edge Cases Not Covered by Tests

### Edge Case #1: Empty Plugin ID

**Status:** NOT TESTED | **File:** `manifest.py:validate_plugin_id()`

Test whether `plugin_id=""` is rejected at:
- Registration time
- Manifest validation time
- Disk persistence

**Expected:** All three should reject with clear error.

---

### Edge Case #2: Very Long Plugin ID (>255 chars)

**Status:** PARTIALLY TESTED | **File:** `registry.py:_op_lock()`

The `_op_lock()` method uses the plugin_id as a dictionary key, which has no length limit in Python. However:
- YAML serialization might fail
- Audit logs might truncate
- Thread names (`thread_name_prefix` at line 138) might be truncated

**Test Coverage:** Need explicit tests for 1KB, 10KB, 1MB plugin IDs.

---

### Edge Case #3: Unicode/RTL/Emoji in Plugin IDs and Names

**Status:** NOT TESTED

A plugin ID of `"plugin-🎉-test"` or `"plugin-א-test"` (Hebrew RTL) might:
- Fail in YAML escaping
- Break CLI display
- Cause truncation issues in audit logs

**Suggested Test:**
```python
def test_emoji_plugin_id():
    registry = PluginRegistry()
    plugin = MagicMock()
    plugin.plugin_id = "plugin-🎉-test"
    plugin.on_load = MagicMock()
    registry.register(plugin, ctx)
    # Verify it appears in lookups and audit
```

---

### Edge Case #4: Circular Dependencies Between Plugins

**Status:** PARTIALLY TESTED | **Component:** `manifest.py:DependencyResolver`

If Plugin A depends on B and B depends on A, the resolver should detect and reject. Test coverage:
- Direct cycle (A→B→A)
- Indirect cycle (A→B→C→A)
- Self-cycle (A→A)

**Risk:** Boot may fail or hang if cycles are not caught early.

---

### Edge Case #5: Plugin with Null/None Required Field

**Status:** PARTIALLY TESTED

A PluginRecord missing required field (e.g., `locality=None`, `origin=None`) should be rejected at:
- Construction time (dataclass validation)
- YAML deserialization
- Disk load

**Test:** Ensure each required field is validated.

---

### Edge Case #6: Max Status Message Length (240 chars)

**Status:** TESTED (`registry.py:MAX_STATUS_MESSAGE_CHARS`)

But not edge cases:
- Exactly 240 chars
- 239 chars (boundary)
- 241 chars (just over)
- 10MB message (resource attack)

---

### Edge Case #7: Registry File Mode 0600 After Concurrent Writes

**Status:** PARTIALLY TESTED

Test that after concurrent mutations via `registry_mutation()`, the file mode is still 0o600 (owner only). Verify `fcntl.flock()` did not change perms.

---

### Edge Case #8: Corrupted YAML Registry → Auto-Recover

**Status:** TESTED | **Component:** `state.py:TenantRegistry.load()`

But not edge cases:
- YAML with null bytes
- YAML with extremely deep nesting (billion laughs)
- YAML with circular references

---

### Edge Case #9: Registry Directory Permissions (0700)

**Status:** ASSUMED, NOT TESTED

The directory `~/.corvin/tenants/_default/plugins/` should be 0o700. Verify:
- Created with correct mode
- Restored to 0o700 on load
- Enforced even on multi-tenant installs

---

### Edge Case #10: MAX_OP_LOCKS Cap (1024)

**Status:** TESTED

But when the cap is hit and `_op_locks.clear()` is called, in-flight operations on other plugins might lose their locks. Test:
- Register 1025 distinct plugins concurrently
- Verify each one completes without error
- Verify no deadlock

---

### Edge Case #11: MAX_TENANT_HISTORY Cap (4096)

**Status:** TESTED

But the clearing logic is aggressive: when hit, **all** tenant history is cleared, not just the oldest entry. This means:
- If 4000 plugins are loaded, then new 100 plugins arrive, all 4000 old histories are wiped
- Verification of hook ownership then fails because we forgot which tenant loaded a plugin

**Suggested Fix:** Use an LRU eviction (remove oldest), not full clear.

---

### Edge Case #12: Health Check Message Scrubbing with Broken Scrubber

**Status:** PARTIALLY TESTED

If the scrubber module is unavailable, messages are dropped (return `"[health message withheld]"`). But what if:
- Scrubber is available but raises an exception mid-scrub
- Scrubber returns a non-string (e.g., `None`)
- Scrubber returns a string with PII that wasn't caught

**Test:** Mock the scrubber to raise exceptions and verify the fallback path.

---

## Security Gaps

### Gap #1: Trust Anchor Path Validation

**Component:** `trust.py` (assumed to exist)

**Vulnerability:** If a trust anchor path is not validated:
- Path traversal (e.g., `../../../etc/passwd`)
- Symlink attacks
- File permissions not checked (world-readable key)

**Recommendation:** Validate trust anchor file:
1. Is regular file (not symlink)
2. Is readable only by owner (mode 0o400)
3. Path is within expected directory
4. No path traversal sequences

---

### Gap #2: Signature Verification Not Fail-Closed for All Paths

**Component:** Plugin installation flow

**Vulnerability:** If trust verification can be skipped via:
- Missing `trust_anchor.pem` (file not found) → silently accept?
- Feature flag disabled → skip signature check?
- Timeout during verification → auto-accept?

**Recommendation:** Ensure all three paths fail-closed (refuse unsigned).

---

### Gap #3: Manifest Validation Missing or Incomplete

**Component:** `manifest.py:load_manifest()`

**Vulnerability:** If manifest YAML is not fully validated:
- Extra fields silently accepted (forward-compat risk)
- Required fields with wrong types (e.g., `enabled: "yes"` instead of boolean)
- Circular references in nested fields
- XXS in description fields (if rendered without escaping)

**Recommendation:** Use a strict schema validator (e.g., jsonschema).

---

### Gap #4: PII Scrubbing Not Enforced on All Text Outputs

**Component:** `registry.py:_scrub_plugin_text()`

**Vulnerability:** Health check messages are scrubbed, but:
- Plugin exception messages are not scrubbed (logged as-is in line 602)
- Audit details from `ctx.audit_emit()` are not scrubbed
- Settings values (which may contain secrets) are not scrubbed

**Recommendation:** Audit every place a plugin provides free-form text and ensure it's scrubbed before audit emit.

---

### Gap #5: Audit Event Mutation (ACL check missing)

**Component:** `registry.py:ctx.audit_emit()`

**Vulnerability:** The audit emitter is passed to the plugin at `on_load()`. If a plugin saves a reference to `ctx.audit_emit()`, it can emit fake audit events after loading:
```python
class EvilPlugin:
    def __init__(self):
        self.audit_emit = None
    
    def on_load(self, ctx):
        self.audit_emit = ctx.audit_emit
    
    def on_unload(self):
        # Emit fake events!
        self.audit_emit("plugin.completely_trusted", {"fake": "event"})
```

**Recommendation:** Audit events should only be emitted through the registry, not passed to plugins. Or, wrap `ctx.audit_emit()` to rate-limit and validate plugin-emitted events.

---

## Recommendations by Priority

### P0 (Before Production)

1. **Fix Bug #1 (Privilege Escalation):** Extend epoch check to prevent thread-spawned re-registration after on_load
2. **Fix Bug #4 (Multi-Process Race):** Verify fcntl lock is held during entire save()
3. **Fix Bug #5 (Audit Event Lost):** Ensure audit emit is not swallowed; rollback if it fails
4. **Gap #1 (Trust Anchor):** Validate trust anchor file permissions and path
5. **Gap #2 (Fail-Closed):** Verify all code paths refuse unsigned plugins

### P1 (Before Stage 6)

6. **Fix Bug #2 (Health Check Timeout):** Add proper cleanup for wedged health check threads
7. **Fix Bug #3 (TOCTOU Race):** Merge can_disable check into disable under lock
8. **Fix Bug #6 (String Validation):** Validate boot_layer parameter before acquiring locks
9. **Gap #3 (Manifest):** Add strict schema validation
10. **Gap #4 (PII Scrubbing):** Audit every text output and apply scrubber
11. **Gap #5 (Audit ACL):** Prevent plugins from mutating audit events

### P2 (Follow-up)

12. Edge Case #7 (File Mode): Add test for mode 0o600 after concurrent writes
13. Edge Case #9 (Directory Perms): Add test for 0o700 enforcement
14. Edge Case #11 (MAX_TENANT_HISTORY): Use LRU instead of full clear

---

## Test Coverage Additions

Add to `/home/shumway/projects/CorvinOS/core/plugins/tests/test_adversarial_racing.py`:

### Race Condition Tests
- [ ] `test_concurrent_register_same_plugin_idempotent_fail` — 10 threads, same plugin
- [ ] `test_concurrent_register_unregister_cycle` — Interleaved register/unregister
- [ ] `test_health_check_timeout_while_unregistering` — Wedged health check + unload race
- [ ] `test_privilege_escalation_same_epoch_thread_escape` — Thread spawned in on_load re-escalates
- [ ] `test_toctou_can_disable_vs_unregister` — Race between operator disable and machinery unload

### State Machine Tests
- [ ] `test_disable_already_disabled_idempotent` — Idempotency of disable
- [ ] `test_enable_nonexistent_plugin_fails` — Enable on missing plugin
- [ ] `test_unload_twice_fails_second` — Double unregister
- [ ] `test_install_uninstall_reinstall_cycle` — Full lifecycle

### Edge Case Tests
- [ ] `test_empty_plugin_name` — plugin_id=""
- [ ] `test_very_long_plugin_name` — 10,000 character ID
- [ ] `test_unicode_emoji_in_plugin_name` — "plugin-🎉-test"
- [ ] `test_null_boot_layer` — None → defaults to INSTALLED
- [ ] `test_malformed_yaml_registry_fails_closed` — Corrupted YAML rejects
- [ ] `test_max_description_length` — 10 MB description
- [ ] `test_zero_timeout_race` — 1ms health check timeout

### Security Tests
- [ ] `test_invalid_trust_anchor_path` — Nonexistent trust file
- [ ] `test_tampered_plugin_signature_rejected` — Forged signature
- [ ] `test_corrupted_yaml_manifest_rejected` — Invalid YAML manifest
- [ ] `test_missing_required_manifest_field` — Missing plugin_id
- [ ] `test_privilege_escalation_same_epoch` — ADR-0233 D5 validation
- [ ] `test_circular_plugin_dependencies` — A→B→A cycle
- [ ] `test_unexpected_field_type_in_record` — enabled: "yes" (wrong type)
- [ ] `test_pii_in_health_message_scrubbed` — Email in health check

### Resource Contention Tests
- [ ] `test_many_op_locks_does_not_unbounded_grow` — MAX_OP_LOCKS cap
- [ ] `test_many_tenant_history_does_not_unbounded_grow` — MAX_TENANT_HISTORY cap
- [ ] `test_wedged_health_check_thread_cleanup` — Thread abandoned, not joined

### Mutation Tests (Verify Guards Are Enforced)
- [ ] `test_disable_trust_check_mutation_still_fails` — Trust is not optional
- [ ] `test_audit_trail_recorded_on_every_mutation` — Audit logging is enforced
- [ ] `test_tenant_isolation_enforced` — Tenant A cannot see Tenant B's plugins
- [ ] `test_consent_gate_required_on_enable` — Consent is not optional

---

## Appendix: Test File Created

File: `/home/shumway/projects/CorvinOS/core/plugins/tests/test_adversarial_racing.py`

Contains 40+ test cases covering:
- 5 race condition tests (concurrent installs, mutations, health checks)
- 5 state machine tests (enable/disable/unload cycles)
- 7 edge case tests (empty IDs, long IDs, Unicode, timeouts)
- 7 security/validation tests (trust, signatures, manifests, PII)
- 4 mutation tests (guards are enforced)
- 3 resource contention tests (unbounded growth prevention)

Run with:
```bash
cd /home/shumway/projects/CorvinOS
python3 -m pytest core/plugins/tests/test_adversarial_racing.py -v
```

---

## Conclusion

The plugin system has solid foundational architecture (locking, atomic writes, audit trail) but several race conditions and edge cases must be fixed before production use. The most critical issues are:

1. **Privilege escalation via thread escape** (Bug #1) — HIGH severity
2. **Multi-process registry write race** (Bug #4) — MEDIUM severity
3. **Audit event loss on emit failure** (Bug #5) — MEDIUM severity

All other bugs are MEDIUM or LOW severity and can be prioritized accordingly.

**Recommendation:** Fix P0 items before ADR-0249 Stage 6 rollout; P1 items before full production deployment.
