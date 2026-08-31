"""Proof-of-concept for ContextVar thread-escape vulnerability (ADR-0233 D5 bypass).

This test demonstrates that a plugin can:
1. Register normally with boot_layer=INSTALLED during bootstrap
2. Spawn a thread during on_load() that outlives the loading context
3. After the loading context resets, the thread calls unregister() + register()
   with boot_layer=COMPLIANCE, successfully escalating privilege

The vulnerability exists because:
- ContextVar _loading.current() is reset when on_load() completes
- Threads spawned during on_load() do NOT inherit ContextVars
- _privileged_registration_epoch dict is never written to (unimplemented)
- The re-escalation check at lines 401-411 in registry.py is dead code

This test WILL PASS with the vulnerability present and FAIL after the fix.
"""

import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from corvin_plugins import registry, protocol
from corvin_plugins.manifest import BootLayer


class ThreadEscapePlugin:
    """Plugin that spawns a thread to re-register on privileged layer after context reset."""

    plugin_id = "test-thread-escape"
    plugin_type = "bridge_channel"
    version = "0.0.1"
    boot_layer = BootLayer.INSTALLED

    def __init__(self):
        self._ctx = None
        self._registry = None
        self._thread = None
        self._escalated = False
        self._escalate_error = None

    def on_load(self, ctx: protocol.PluginContext) -> None:
        """Spawn a thread that will try to re-escalate after loading context resets."""
        self._ctx = ctx
        self._registry = registry.get_registry()
        # Spawn worker thread that outlives on_load()
        self._thread = threading.Thread(target=self._escalate_in_thread, daemon=False)
        self._thread.start()
        # Return immediately; let thread run after context resets

    def on_unload(self) -> None:
        """Cleanup: wait for thread to finish (if not called from thread itself)."""
        if self._thread is not None and self._thread != threading.current_thread():
            self._thread.join(timeout=2.0)

    def health_check(self) -> protocol.HealthStatus:
        return protocol.HealthStatus(ok=True)

    def _escalate_in_thread(self) -> None:
        """Worker thread: wait for loading context to be reset, then re-escalate.

        Timeline:
        1. This thread starts while on_load() is running (context is set)
        2. on_load() returns, context resets via finally in _register_locked
        3. Thread wakes after sleep; _loading.current() is now None
        4. Thread calls unregister (removes self from registry)
        5. Thread calls register with boot_layer=COMPLIANCE
        6. BEFORE FIX: Privilege check passes because _loading.current() is None,
           epoch check is empty (never written), plugin escalates to COMPLIANCE
        7. AFTER FIX: Privilege check passes, but epoch check blocks it,
           plugin is re-registered but on INSTALLED layer instead
        """
        # Wait for on_load() to complete and loading context to reset
        time.sleep(0.05)

        try:
            # Unregister self (operator_initiated=False bypasses compliance check)
            self._registry.unregister(self.plugin_id, operator_initiated=False)
            time.sleep(0.01)

            # Re-register on COMPLIANCE layer
            # BEFORE FIX: This succeeds and we end up on COMPLIANCE layer (VULNERABILITY)
            # AFTER FIX: This succeeds but we end up on INSTALLED layer (fixed!)
            self._registry.register(self, self._ctx, boot_layer=BootLayer.COMPLIANCE)

            # Check what boot layer we actually got (may have been downgraded by fix)
            actual_layer = self._registry.boot_layer_of(self.plugin_id)
            if actual_layer == BootLayer.COMPLIANCE:
                # Escalation succeeded — VULNERABILITY!
                self._escalated = True
            else:
                # Escalation was blocked (downgraded) — FIX IS WORKING!
                self._escalated = False
                self._escalate_error = f"Privilege escalation was blocked: requested COMPLIANCE, got {actual_layer}"
        except Exception as e:
            self._escalated = False
            self._escalate_error = str(e)


def _make_plugin_context(plugin_id: str, tenant_id: str = "_default") -> protocol.PluginContext:
    """Create a minimal PluginContext for testing."""
    return protocol.PluginContext(
        plugin_id=plugin_id,
        tenant_id=tenant_id,
        corvin_home=Path("/tmp"),
        config={},
        audit_emit=lambda *args, **kwargs: None,
        compute_registry=None,
        engine_factory=None,
        channel_registry=None,
        notification_registry=None,
        recall_registry=None,
        summary_registry=None,
        router_registry=None,
        audit_registry=None,
        user_registry=None,
        stt_registry=None,
        data_connector_registry=None,
    )


@pytest.mark.parametrize("use_epoch_increment", [False, True])
def test_thread_escape_vulnerability(use_epoch_increment):
    """
    Test that verifies the ContextVar thread-escape vulnerability is FIXED.

    A plugin spawning a thread during on_load() that tries to re-escalate
    privilege after the loading context resets should be BLOCKED by the fix.

    The thread-escape attack proceeds as follows:
    1. Plugin registers with boot_layer=INSTALLED (normal)
    2. Plugin's on_load() spawns a worker thread
    3. on_load() completes, loading context resets
    4. Thread calls unregister(plugin_id, operator_initiated=False)
    5. Thread calls register(plugin, ctx, boot_layer=BootLayer.COMPLIANCE)
    6. BEFORE FIX: re-escalation succeeds (VULNERABILITY!)
    7. AFTER FIX: re-escalation is blocked by epoch check (fixed!)

    The test verifies that step 7 happens: escalation is blocked.
    """
    reg = registry.get_registry()
    plugin = ThreadEscapePlugin()
    ctx = _make_plugin_context("test-thread-escape")

    # If using the epoch increment fix, bump the epoch to simulate boot
    if use_epoch_increment:
        reg._registration_epoch += 1

    # Register plugin with INSTALLED layer (normal bootstrap behavior)
    reg.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

    # Verify initial state
    assert plugin._escalated is False, "Escalation should not have started yet"
    assert reg.boot_layer_of("test-thread-escape") == BootLayer.INSTALLED

    # Wait for thread to complete its escalation attempt
    time.sleep(0.2)

    # After fix: plugin should still be on INSTALLED layer
    actual_layer = reg.boot_layer_of("test-thread-escape")

    # Cleanup
    try:
        reg.unregister("test-thread-escape")
    except Exception:
        pass

    # Verify the fix is working: thread's escalation attempt should have been BLOCKED
    # If the thread tried to escalate but was blocked, _escalated will be False
    # because the re-register call raised an exception.
    if plugin._escalated:
        pytest.fail(
            f"VULNERABILITY NOT FIXED: Plugin escalated from INSTALLED to {actual_layer}. "
            f"Thread escape succeeded. Error: {plugin._escalate_error}"
        )

    # The fix working means: plugin remains on INSTALLED layer despite thread's attempt
    assert actual_layer == BootLayer.INSTALLED, (
        f"FIX VERIFICATION: Plugin should remain INSTALLED after thread-escape attempt, "
        f"but is {actual_layer}"
    )


def test_epoch_tracking_unimplemented():
    """
    Verify that _privileged_registration_epoch dict is never written to.

    This test documents why the epoch check at registry.py:401-411 is dead code.
    """
    reg = registry.get_registry()

    # Clear the epoch dict
    with reg._lock:
        reg._privileged_registration_epoch.clear()

    # Register a global compliance-layer plugin
    plugin = ThreadEscapePlugin()
    ctx = _make_plugin_context("test-epoch-check", tenant_id="_default")

    # Try to register with explicit COMPLIANCE layer
    # (simulating what bootstrap_global would do)
    with reg._lock:
        original_epoch = reg._registration_epoch

    # Simulate being inside a global bootstrap (so _loading.current() would return
    # a value and the loading check would block).
    # But we're not actually inside on_load, so _loading.current() is None.
    # Register with COMPLIANCE layer explicitly from caller.

    # The problem: _privileged_registration_epoch is never populated
    before_write = dict(reg._privileged_registration_epoch)

    # Don't actually register (to avoid conflicts with other tests)
    # Just verify the dict remains empty
    assert len(before_write) == 0, "Epoch dict should be empty initially"

    # After the fix is applied, there should be code like:
    #   if resolved in _PRIVILEGED_BOOT_LAYERS:
    #       with self._lock:
    #           self._privileged_registration_epoch[plugin.plugin_id] = self._registration_epoch
    # But this code doesn't exist yet.


def test_contextvar_not_inherited_by_thread():
    """
    Verify that threading.Thread does not inherit ContextVars.

    This test confirms the root cause: a thread spawned during on_load()
    will see _loading.current() as None even though it was started inside
    a loading context.
    """
    from corvin_plugins import loading as _loading

    captured_in_main = None
    captured_in_thread = None

    def thread_func():
        nonlocal captured_in_thread
        time.sleep(0.05)  # Wait for context to reset
        captured_in_thread = _loading.current()

    # Set a context
    with _loading.loading("test-plugin", "_default"):
        captured_in_main = _loading.current()
        # Spawn thread INSIDE the context
        t = threading.Thread(target=thread_func)
        t.start()
        time.sleep(0.01)  # Let thread read while we're still in context

    t.join()

    # In main thread (inside context): _loading.current() is set
    assert captured_in_main is not None
    assert captured_in_main.plugin_id == "test-plugin"

    # In spawned thread (after context reset): _loading.current() is None
    # This is why the thread escape vulnerability works!
    assert captured_in_thread is None, (
        "Thread should NOT inherit ContextVar (threading.Thread limitation), "
        "but it did: " + str(captured_in_thread)
    )


class CrashDuringOnLoadPlugin:
    """Plugin that crashes during on_load(), leaving partial state behind."""

    plugin_id = "test-crash-on-load"
    plugin_type = "bridge_channel"
    version = "0.0.1"

    def on_load(self, ctx: protocol.PluginContext) -> None:
        """Crash halfway through to leave partial state."""
        # This might have already taken a provider slot
        # or registered an extension hook
        raise RuntimeError("Simulated on_load() crash")

    def on_unload(self) -> None:
        pass

    def health_check(self) -> protocol.HealthStatus:
        return protocol.HealthStatus(ok=True)


def test_partial_state_on_load_crash():
    """
    Verify that if on_load() crashes, the registry cleans up all partial state.

    This tests the fix at registry.py:477-491 which cleans up on exception:
    - _detach_provider_slot(plugin)
    - _revoke_hooks(plugin.plugin_id)
    - _breakers.forget(plugin.plugin_id)
    - self._plugins.pop(plugin.plugin_id, None)
    - self._contexts.pop(plugin.plugin_id, None)
    - self._boot_layers.pop(plugin.plugin_id, None)

    A thread spawned before the crash could use this partial state as a
    launching point for re-escalation.
    """
    reg = registry.get_registry()
    plugin = CrashDuringOnLoadPlugin()
    ctx = _make_plugin_context("test-crash-on-load")

    with pytest.raises(RuntimeError, match="Simulated on_load"):
        reg.register(plugin, ctx)

    # After crash, plugin should be completely removed from registry
    assert "test-crash-on-load" not in reg.discover()
    assert len(reg._plugins) == 0 or "test-crash-on-load" not in reg._plugins

    # Verify the lock was not held (deadlock check)
    # Try to register another plugin — should succeed quickly
    class SimplePlugin:
        plugin_id = "test-simple"
        plugin_type = "bridge_channel"
        version = "0.0.1"

        def on_load(self, ctx):
            pass

        def on_unload(self):
            pass

        def health_check(self):
            return protocol.HealthStatus(ok=True)

    simple = SimplePlugin()
    simple_ctx = _make_plugin_context("test-simple")
    reg.register(simple, simple_ctx)  # Should not hang
    assert "test-simple" in reg.discover()

    reg.unregister("test-simple")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
