# Plugin System — Bug Reproductions & Detailed Analysis

This document provides step-by-step reproduction code for each bug discovered in adversarial testing.

---

## Bug #1: Privilege Escalation via Thread Escape (Same Epoch)

**Severity:** HIGH  
**Component:** `registry.py:_resolve_boot_layer()`  
**CVE-like Risk:** Privilege escalation allows a plugin to become non-disableable

### Root Cause Analysis

The privilege escalation guard at line 406-414 only checks if the thread is currently **inside on_load()**:

```python
who = _loading.current()
if who is not None:
    log.error("plugin tried to register from inside on_load — downgraded")
    return BootLayer.INSTALLED
```

Once `on_load()` completes and the loading context is exited, `_loading.current()` becomes `None`, and **any thread can re-register with any boot layer**.

### Attack Scenario

1. **Plugin Developer (Malicious):**
   ```python
   class EvilPlugin(CorvinPlugin):
       plugin_id = "evil-plugin"
       
       def on_load(self, ctx):
           # Spawn a daemon thread that will outlive this on_load
           t = threading.Thread(target=self.escape_escalate, daemon=True)
           t.start()
       
       def escape_escalate(self):
           # Wait for loading context to close
           time.sleep(0.5)
           
           # Now _loading.current() is None
           # Re-register with CORE privilege
           registry.unregister("evil-plugin")
           
           # Create a new plugin object with same ID
           escalated = EvilPlugin()
           registry.register(escalated, ctx, boot_layer=BootLayer.CORE)
           # Success! Now this plugin cannot be disabled
   ```

2. **Result:**
   - Plugin is now `boot_layer=CORE` (non-disableable)
   - Audit trail shows: `plugin.loaded` (as installed) then later operations run as CORE
   - Cannot be disabled via operator action
   - Silently persists across reboots (if saved to disk)

### Proof of Concept Test

```python
def test_privilege_escalation_via_thread_escape():
    """Thread spawned in on_load re-escalates to CORE after loading context closes."""
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "evil-plugin"
    
    escalated = MagicMock(spec=CorvinPlugin)
    escalated.plugin_id = "evil-plugin"
    escalated.on_load = MagicMock(return_value=None)
    escalated.on_unload = MagicMock(return_value=None)
    
    ctx = MagicMock(spec=PluginContext)
    ctx.tenant_id = "_default"
    
    escape_ready = threading.Event()
    escape_done = threading.Event()
    
    def on_load_with_escape(ctx_arg):
        def escape_thread():
            escape_ready.set()
            # Wait for loading context to close
            time.sleep(0.2)
            
            # Try to re-escalate
            try:
                registry.unregister("evil-plugin")
                registry.register(escalated, ctx_arg, boot_layer=BootLayer.CORE)
                escape_done.set()
            except Exception:
                escape_done.set()
        
        t = threading.Thread(target=escape_thread, daemon=True)
        t.start()
    
    plugin.on_load = on_load_with_escape
    plugin.on_unload = MagicMock(return_value=None)
    
    # Register as INSTALLED
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)
    
    # Wait for escape thread to complete
    escape_ready.wait(timeout=1)
    escape_done.wait(timeout=2)
    
    # BUG: Plugin is now CORE, not INSTALLED!
    try:
        actual = registry.boot_layer_of("evil-plugin")
        assert actual == BootLayer.INSTALLED, (
            f"PRIVILEGE ESCALATION: plugin escalated to {actual} "
            f"(expected INSTALLED). Thread escape succeeded!"
        )
    except PluginNotFound:
        # Plugin was unregistered, which is also wrong
        pytest.fail("Plugin was unregistered during escape attempt")
```

### Impact

- **Compliance:** Violates ADR-0233 D5 (privilege escalation guard)
- **Security:** Plugin can become non-disableable
- **Audit:** Audit chain records escalation, but system is already compromised
- **Recovery:** Manual intervention required (delete registry, restart)

### Fix Strategy

1. **Tighter Epoch Check:** When checking `_privileged_registration_epoch`, also verify that the thread **spawning** the re-register was NOT inside a previous on_load.
2. **Thread-Local Load Context:** Store load context in thread-local storage and verify re-register comes from a thread with no load context.
3. **Timeout-Based:** Invalidate all spawned threads 30 seconds after on_load completes (plugin cannot escape after 30s).

---

## Bug #2: Race Between Health Check Timeout and Unregister

**Severity:** MEDIUM  
**Component:** `registry.py:health()` + `unregister()`  
**Impact:** Resource leak (wedged thread), out-of-order audit events, data race

### Root Cause Analysis

At line 118-147, `_call_with_deadline()` spawns a ThreadPoolExecutor with one worker, submits a future, and returns the result. If the deadline expires, the future is abandoned:

```python
pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, ...)
future = pool.submit(fn)
try:
    return future.result(timeout=deadline_s)
except concurrent.futures.TimeoutError:
    raise HealthCheckTimeout(...) from None
finally:
    # NOT calling pool.join() — thread is abandoned!
    pool.shutdown(wait=False)
```

The comment at line 149-151 even documents this is intentional (to avoid hanging), but it creates a race:

1. Thread A calls `health()`, which starts a worker thread
2. Worker thread is still running (wedged)
3. Thread B calls `unregister()`, which pops the plugin from `_plugins`
4. Worker thread finally times out, but plugin object is already gone
5. Race condition: worker thread may be accessing stale plugin reference

### Attack Scenario

```python
def test_wedged_health_vs_unregister_race():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "wedge-plugin"
    
    # Health check hangs forever
    wedge_event = threading.Event()
    def never_returns():
        wedge_event.wait()  # Blocks until test ends
        return HealthStatus(ok=True)
    
    plugin.health_check = never_returns
    plugin.on_load = MagicMock(return_value=None)
    plugin.on_unload = MagicMock(return_value=None)
    ctx = MagicMock(spec=PluginContext)
    
    registry.register(plugin, ctx)
    
    # Start health check (will wedge)
    def check():
        try:
            registry.health("wedge-plugin")
        except Exception:
            pass
    
    h = threading.Thread(target=check)
    h.start()
    
    time.sleep(0.1)  # Let health check start and wedge
    
    # Unregister while health check is still hanging
    registry.unregister("wedge-plugin")
    
    # Now re-register a NEW plugin with same ID
    plugin2 = MagicMock(spec=CorvinPlugin)
    plugin2.plugin_id = "wedge-plugin"
    plugin2.on_load = MagicMock(return_value=None)
    registry.register(plugin2, ctx)
    
    # Wait for health check to timeout (2s + a bit)
    time.sleep(2.5)
    h.join(timeout=1)
    
    # BUG: Old worker thread may still reference plugin1
    # New plugin2 is now in registry, but old health check thread is still active
    
    # Evidence of bug: wedged thread count increases unbounded
    import threading
    thread_count = threading.active_count()
    # Should be ~3-4 (main + few internals)
    # But will be higher if threads are leaking
```

### Impact

- **Resource Leak:** One thread per timeout, accumulates over time
- **Data Race:** Old thread may access plugin that's been unloaded/replaced
- **Audit:** Audit events from health check may come **after** unload events
- **Memory:** Thread stack (~8MB on Linux) per leaked thread

### Fix Strategy

1. **Track Active Health Checks:** Maintain a set of `(plugin_id, timeout_handle)` tuples
2. **Cancel on Unregister:** When `_unregister_locked()` is called, cancel all active health checks for that plugin
3. **Cleaner Abandonment:** Use a signal handler or future callback to mark the thread as "safe to abandon"
4. **Serialization:** Before re-registering, drain all in-flight health checks

---

## Bug #3: TOCTOU Race in `can_disable()` → `disable()`

**Severity:** LOW  
**Component:** `registry.py:can_disable()` + `disable()`  
**Impact:** Audit inconsistency

### Root Cause Analysis

Callers typically do:
```python
if not registry.can_disable(plugin_id):
    raise PluginDisableRefused(...)
registry.disable(plugin_id)
```

Between the check and the call, another thread (or the plugin itself in machinery) can unload the plugin. The audit trail will then show inconsistent states.

### Proof of Concept

```python
def test_toctou_can_disable_disable():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "audit-plugin"
    plugin.on_load = MagicMock(return_value=None)
    plugin.on_unload = MagicMock(return_value=None)
    ctx = MagicMock(spec=PluginContext)
    
    # Register as COMPLIANCE (non-disableable by operator)
    registry.register(plugin, ctx, boot_layer=BootLayer.COMPLIANCE)
    
    audit_events = []
    
    def mock_audit(event_type, data):
        audit_events.append((event_type, data["plugin_id"], data.get("operator_initiated")))
    
    ctx.audit_emit = mock_audit
    
    results = {"operator": None, "machinery": None}
    
    def operator_action():
        # Operator tries to disable
        try:
            if registry.can_disable("audit-plugin"):
                # <-- TOCTOU window here
                time.sleep(0.01)  # Simulate processing
                registry.disable("audit-plugin")
                results["operator"] = "disabled"
            else:
                results["operator"] = "refused"
        except PluginDisableRefused:
            results["operator"] = "refused"
        except PluginNotFound:
            results["operator"] = "not_found"
    
    def machinery_action():
        # Machinery unloads after operator checks
        time.sleep(0.005)  # Shorter delay to hit TOCTOU window
        try:
            registry.unregister("audit-plugin")  # Machinery can always unload
            results["machinery"] = "unloaded"
        except PluginNotFound:
            results["machinery"] = "already_gone"
    
    t1 = threading.Thread(target=operator_action)
    t2 = threading.Thread(target=machinery_action)
    
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    
    # Expected: one succeeds, other gets PluginNotFound or PluginDisableRefused
    # BUG: Both can succeed, or audit trail is inconsistent
    
    print(f"Operator result: {results['operator']}")
    print(f"Machinery result: {results['machinery']}")
    print(f"Audit events: {audit_events}")
    
    # Possible bad outcomes:
    # 1. Both report success (should be impossible for COMPLIANCE)
    # 2. operator="refused", machinery="unloaded" (audit shows bypass)
```

### Fix Strategy

Atomic check-and-set:
```python
def disable(self, plugin_id: str) -> None:
    """Operator-facing unload. Atomic check + unload."""
    with self._op_lock(plugin_id):
        with self._lock:
            if plugin_id not in self._plugins:
                raise PluginNotFound(plugin_id)
            if self._boot_layers.get(plugin_id, BootLayer.INSTALLED) is BootLayer.COMPLIANCE:
                raise PluginDisableRefused(f"{plugin_id!r} is on compliance layer")
        # Now call on_unload, etc.
        self._unregister_locked(plugin_id, operator_initiated=True)
```

---

## Bug #4: Multi-Process Registry File Write Race

**Severity:** MEDIUM  
**Component:** `state.py:registry_mutation()` + fcntl locking  
**Impact:** Silent data loss (plugins disappear from registry)

### Root Cause Analysis

The `registry_mutation()` context manager uses `_MUTATION_LOCK` (thread-local) and fcntl (process-level). But the sequence is:

```python
@contextlib.contextmanager
def registry_mutation(*, tenant_id=None, ...):
    with _MUTATION_LOCK:
        reg = TenantRegistry.load(...)
        yield reg
        reg.save()
```

The load happens inside `TenantRegistry.load()`, which might acquire an fcntl lock. But if the lock is released **before** save completes, a race can occur:

1. Process A: fcntl lock acquired
2. Process A: load registry (10 plugins)
3. Process A: fcntl lock released (inside load?)
4. Process B: fcntl lock acquired
5. Process B: load registry (10 plugins)
6. Process B: add plugin #11, save (11 plugins on disk)
7. Process A: add plugin #12, save (overwrites B's changes, only 11 plugins total)
8. Result: Plugin #11 is lost!

### Proof of Concept

Requires multi-process test:
```python
def test_multiprocess_registry_race():
    """Two processes mutate registry concurrently."""
    import subprocess
    import tempfile
    import os
    
    tmpdir = tempfile.mkdtemp()
    registry_file = os.path.join(tmpdir, "tenants", "_default", "plugins", "registry.yaml")
    os.makedirs(os.path.dirname(registry_file), exist_ok=True)
    
    # Initialize empty registry
    with open(registry_file, "w") as f:
        f.write("plugins: {}\n")
    
    script = f"""
import sys
sys.path.insert(0, '{repo_path}')
from corvin_plugins.state import registry_mutation
import time

with registry_mutation(tenant_id="_default", corvin_home_path="{tmpdir}") as tr:
    plugin_id = "plugin-{{sys.argv[1]}}"
    tr.add_record(
        PluginRecord(
            plugin_id=plugin_id,
            origin=PluginOrigin.COMMUNITY,
            enabled=True,
            ...
        )
    )
    time.sleep(0.1)  # Simulate processing
    tr.save()
"""
    
    # Run two processes concurrently
    p1 = subprocess.Popen([sys.executable, "-c", script.format(..., "1")])
    p2 = subprocess.Popen([sys.executable, "-c", script.format(..., "2")])
    
    p1.wait()
    p2.wait()
    
    # Load final registry
    with registry_mutation(tenant_id="_default", corvin_home_path=tmpdir) as tr:
        plugins = tr.records()
    
    # Expected: 2 plugins
    # BUG: Only 1 plugin present (data loss)
    assert len(plugins) == 2, f"Expected 2 plugins, got {len(plugins)}"
```

### Impact

- **Data Loss:** Plugins silently disappear from registry
- **Consistency:** Two calls to the same API return different results
- **Audit Gap:** Plugin removal not recorded in audit trail
- **Recovery:** Manual registry inspection required

### Fix Strategy

1. **fcntl Lock Spans Entire Mutation:** Hold lock from load through save
2. **File Locking Test:** Add explicit test that verifies lock is held across save
3. **Read-Modify-Write Atomic:** Ensure entire load → modify → save is atomic under lock

---

## Bug #5: Audit Event Lost if Emitter Raises

**Severity:** MEDIUM  
**Component:** `registry.py:_register_locked()` line 553-559  
**Impact:** Audit trail gap, system state mismatch

### Root Cause Analysis

```python
# Line 488-559 (simplified):
def _register_locked(self, plugin, ctx, resolved):
    with self._lock:
        self._plugins[plugin.plugin_id] = plugin  # <-- committed now
        self._contexts[plugin.plugin_id] = ctx
        self._boot_layers[plugin.plugin_id] = resolved
    
    try:
        with _loading.loading(...):
            plugin.on_load(ctx)
    except Exception:
        # Rollback if on_load fails
        self._plugins.pop(plugin.plugin_id, None)
        ...
        raise
    
    # If we reach here, plugin is FULLY REGISTERED in the system
    ctx.audit_emit("plugin.loaded", {...})  # <-- If this raises, NO rollback
```

If `ctx.audit_emit()` raises (queue full, disk full, permission denied), the plugin is already loaded and running, but the audit event was never recorded. Worse, the caller doesn't know the registration succeeded (they might see an exception).

### Proof of Concept

```python
def test_audit_emit_failure_no_rollback():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "test"
    plugin.on_load = MagicMock(return_value=None)
    ctx = MagicMock(spec=PluginContext)
    
    # Simulate audit emit raising
    ctx.audit_emit = MagicMock(side_effect=RuntimeError("Disk full"))
    
    # Register — will raise from audit_emit
    with pytest.raises(RuntimeError, match="Disk full"):
        registry.register(plugin, ctx)
    
    # BUG: Plugin is actually registered in the system!
    assert registry.lookup("test") is plugin, (
        "Plugin is registered but registration() raised! "
        "Audit trail is incomplete."
    )
    
    # Caller thinks registration failed and might retry, but plugin is already loaded
```

### Impact

- **Audit Gap:** Hash chain is broken (event missing)
- **Duplicate Loading:** Caller might retry and load plugin twice
- **Resource Leak:** Plugin holds resources but caller doesn't know it succeeded
- **GDPR Violation:** Audit trail is incomplete (Art. 30/32)

### Fix Strategy

1. **Emit Synchronously:** Ensure audit_emit doesn't raise; if it would, catch and log separately
2. **Emit Before Returning:** Emit inside the try/except so failure triggers rollback
3. **Fallback Channel:** If audit_emit fails, log to stderr + syslog as fallback

---

## Bug #6: String `boot_layer` Parameter Not Validated Early

**Severity:** LOW  
**Component:** `registry.py:register()` line 484  
**Impact:** Lock leak, poor error message

### Root Cause Analysis

```python
def register(self, plugin, ctx, *, boot_layer=None):
    # Line 484: This can raise ValueError if boot_layer is invalid
    resolved = self._resolve_boot_layer(plugin, boot_layer, ...)
    
    # Only AFTER validation is lock acquired
    with self._op_lock(plugin.plugin_id):
        self._register_locked(plugin, ctx, resolved)
```

If a caller passes `boot_layer="INVALID"`, the `_resolve_boot_layer()` call converts it via `BootLayer(boot_layer)` which raises `ValueError` **before** the `_op_lock` is acquired. The error is correct, but it's raised from a place the caller might not expect.

### Proof of Concept

```python
def test_invalid_boot_layer_string():
    registry = PluginRegistry()
    plugin = MagicMock(spec=CorvinPlugin)
    plugin.plugin_id = "test"
    ctx = MagicMock(spec=PluginContext)
    
    # Pass invalid boot layer string
    with pytest.raises(ValueError, match="INVALID"):
        registry.register(plugin, ctx, boot_layer="INVALID")
    
    # Issue: Error is clear, but raised from _resolve_boot_layer, not register
```

**Note:** This is a LOW-severity issue; the behavior is correct. The recommendation is to validate earlier (in `register()` docstring) or before `_op_lock`.

---

## Bug #7: Extension Hooks Not Revoked if Audit Emit Fails

**Severity:** MEDIUM  
**Component:** `registry.py:_register_locked()` line 540-541  
**Impact:** Orphaned extension hooks, audit trail inconsistency

### Root Cause Analysis

```python
# Line 540-541:
_verify_hook_ownership(plugin_id, ctx.tenant_id)

# Line 553-559:
ctx.audit_emit("plugin.loaded", {...})  # Can raise
```

If `_verify_hook_ownership()` succeeds but `audit_emit()` raises, the hooks are already revoked but the plugin is still registered. The audit record shows the hooks were revoked, but the event itself never made it to the chain. A later retry might try to revoke non-existent hooks.

### Scenario

1. Plugin registers and claims extension hooks for Tenant A
2. `_verify_hook_ownership()` revokes hooks for foreign tenants
3. `audit_emit("plugin.loaded")` raises (disk full)
4. Caller catches exception, thinks registration failed
5. Plugin is actually registered but audit event is missing
6. Later audit verification finds orphaned hooks

### Fix Strategy

Move audit emit to immediately after plugin is reserved (before on_load), or use a two-phase commit pattern.

---

## Summary Table

| Bug # | Severity | Component | Impact | Fix Complexity |
|-------|----------|-----------|--------|-----------------|
| 1 | HIGH | registry.py | Privilege escalation | Medium |
| 2 | MEDIUM | registry.py | Resource leak, data race | High |
| 3 | LOW | registry.py | Audit inconsistency | Low |
| 4 | MEDIUM | state.py | Silent data loss | Medium |
| 5 | MEDIUM | registry.py | Audit gap | Medium |
| 6 | LOW | registry.py | Poor error handling | Low |
| 7 | MEDIUM | registry.py | Hook orphaning | Medium |

---

## Testing Recommendations

1. **Run test_adversarial_racing.py** after each fix to verify resolution
2. **Add memory profiler** to check for thread leaks in Bug #2
3. **Add audit trail verification** to check all events are recorded
4. **Multi-process stress test** for Bug #4 (1000 concurrent mutations)
5. **Fuzzing** for manifest validation (Bug coverage)
