"""Tests for P0 critical bug fixes (adversarial testing).

- Bug #1: Privilege Escalation via Thread Escape
- Bug #3: Audit Event Loss on Failure
- Bug #4: Wedged Health Check Thread Leak
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corvin_plugins.bootstrap import _register_instance, build_context
from corvin_plugins.health import HealthCollector, HealthCheckTimeout
from corvin_plugins.manifest import BootLayer, PluginRecord
from corvin_plugins.protocol import CorvinPlugin, HealthStatus, PluginContext
from corvin_plugins.registry import (
    PluginRegistry,
    PluginAlreadyRegistered,
    PluginNotFound,
    get_registry,
)


# ─────────────────────────────────────────────────────────────────────────────
# BUG #1: Privilege Escalation via Thread Escape
# ─────────────────────────────────────────────────────────────────────────────


class AdversarialThreadEscapePlugin(CorvinPlugin):
    """Plugin that spawns a thread during load to re-register as CORE."""

    plugin_id = "adversarial-thread-escape"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Adversarial Thread Escape"
    _registry = None
    _ctx = None

    def on_load(self, ctx: PluginContext) -> None:
        """Spawn a thread that tries to unregister and re-register as CORE."""
        self._ctx = ctx
        self._registry = ctx.audit_registry

        # Spawn thread that escapes the loading context
        thread = threading.Thread(
            target=self._escape_thread,
            daemon=True,
            name=f"escape-{self.plugin_id}",
        )
        thread.start()

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)

    def _escape_thread(self) -> None:
        """Attempt privilege escalation via unregister + re-register."""
        # Give the main thread time to finish on_load()
        time.sleep(0.1)

        # Try to unregister and re-register as CORE
        try:
            registry = get_registry()
            registry.unregister(self.plugin_id)

            # Now try to re-register on privileged layer
            # This should be blocked by epoch checking
            registry.register(
                self,
                self._ctx,
                boot_layer=BootLayer.CORE,
            )
        except Exception:
            # Expected — the privilege escalation should fail
            pass


class SameEpochThreadEscapePlugin(CorvinPlugin):
    """Plugin that re-registers in same epoch after unload."""

    plugin_id = "same-epoch-escape"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Same-Epoch Escape"
    _ctx = None

    def on_load(self, ctx: PluginContext) -> None:
        self._ctx = ctx

        # Spawn thread that tries re-escalation in same epoch
        thread = threading.Thread(
            target=self._same_epoch_escape,
            daemon=True,
            name=f"same-epoch-{self.plugin_id}",
        )
        thread.start()

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True)

    def _same_epoch_escape(self) -> None:
        """Attempt to re-register as CORE in same epoch after unload."""
        time.sleep(0.1)

        try:
            registry = get_registry()
            registry.unregister(self.plugin_id)

            # Immediately re-register with privilege before epoch increments
            registry.register(
                self,
                self._ctx,
                boot_layer=BootLayer.CORE,
            )
        except Exception:
            pass


@pytest.mark.asyncio
async def test_bug_1_privilege_escalation_thread_escape():
    """Plugin cannot escalate from installed → CORE via thread escape."""
    registry = get_registry()
    registry._advance_epoch()  # Simulate boot

    plugin = AdversarialThreadEscapePlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
    )

    # Register as installed (unprivileged)
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

    # Verify it's installed, not CORE
    assert registry._boot_layers[plugin.plugin_id] == BootLayer.INSTALLED

    # Give escape thread time to run
    await asyncio.sleep(0.2)

    # After escape attempt, it should still be installed
    assert registry._boot_layers[plugin.plugin_id] == BootLayer.INSTALLED

    registry.unregister(plugin.plugin_id)


@pytest.mark.asyncio
async def test_bug_1_same_epoch_re_escalation_blocked():
    """Plugin cannot re-escalate in same epoch after unload."""
    registry = get_registry()
    registry._advance_epoch()  # Simulate boot

    plugin = SameEpochThreadEscapePlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
    )

    # Register as installed initially
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)
    assert registry._boot_layers[plugin.plugin_id] == BootLayer.INSTALLED

    # Give escape thread time to run
    await asyncio.sleep(0.2)

    # Should still be installed, not CORE
    assert registry._boot_layers[plugin.plugin_id] == BootLayer.INSTALLED

    registry.unregister(plugin.plugin_id)


@pytest.mark.asyncio
async def test_bug_1_cross_epoch_re_escalation_blocked():
    """Plugin cannot re-escalate across boot epochs."""
    registry = get_registry()
    current_epoch = registry._registration_epoch
    registry._advance_epoch()  # Boot 1

    class CrossEpochPlugin(CorvinPlugin):
        plugin_id = "cross-epoch"
        plugin_type = "audit_backend"
        version = "1.0.0"
        display_name = "Cross-Epoch"

        def on_load(self, ctx: PluginContext) -> None:
            pass

        def on_unload(self) -> None:
            pass

        def health_check(self) -> HealthStatus:
            return HealthStatus(ok=True)

    plugin = CrossEpochPlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
    )

    # Register as INSTALLED (privileged_registration_epoch NOT set)
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)
    assert registry._boot_layers[plugin.plugin_id] == BootLayer.INSTALLED

    # Now simulate next boot - advance epoch
    registry._advance_epoch()
    new_epoch = registry._registration_epoch
    assert new_epoch != current_epoch

    # Try to re-register on privileged layer in new epoch
    # This should be allowed IF the plugin was never privileged before
    # But if it WAS privileged, it should be blocked
    registry.unregister(plugin.plugin_id)

    # Simulate plugin from old boot trying to re-register in new epoch as CORE
    # This should be BLOCKED by cross-epoch check
    try:
        registry.register(
            plugin,
            ctx,
            boot_layer=BootLayer.CORE,
        )
        # If we get here, the check failed
        pytest.fail("Cross-epoch privilege escalation was not blocked!")
    except Exception:
        # Expected - registration should fail or be downgraded
        pass


# ─────────────────────────────────────────────────────────────────────────────
# BUG #3: Audit Event Loss on Failure
# ─────────────────────────────────────────────────────────────────────────────


def test_bug_3_audit_write_failure_blocks_registration():
    """Plugin registration is rolled back if audit write fails."""
    registry = get_registry()
    registry._advance_epoch()

    audit_events = []
    audit_write_error = None

    def failing_audit_emit(event_type: str, details: dict) -> None:
        """Audit emit that fails on specific event."""
        nonlocal audit_write_error
        audit_events.append((event_type, details))

        if event_type == "plugin.loaded" and audit_write_error:
            raise RuntimeError("Simulated audit write failure")

    class SimplePlugin(CorvinPlugin):
        plugin_id = "simple-audit-test"
        plugin_type = "audit_backend"
        version = "1.0.0"
        display_name = "Simple"

        def on_load(self, ctx: PluginContext) -> None:
            pass

        def on_unload(self) -> None:
            pass

        def health_check(self) -> HealthStatus:
            return HealthStatus(ok=True)

    plugin = SimplePlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
        audit_emit=failing_audit_emit,
    )

    # First, register successfully
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)
    assert plugin.plugin_id in registry._plugins

    # Now simulate audit failure on next registration attempt
    plugin2 = SimplePlugin()
    plugin2.plugin_id = "audit-fail-test"

    audit_write_error = True
    ctx2 = build_context(
        plugin_id=plugin2.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
        audit_emit=failing_audit_emit,
    )

    # Registration should fail due to audit write failure
    # and the plugin should NOT be registered
    with pytest.raises(RuntimeError, match="audit write failure"):
        registry.register(plugin2, ctx2, boot_layer=BootLayer.INSTALLED)

    # Verify plugin was NOT registered
    assert plugin2.plugin_id not in registry._plugins

    # Cleanup
    registry.unregister(plugin.plugin_id)


def test_bug_3_audit_event_records_registration():
    """Successful registration records audit event."""
    registry = get_registry()
    registry._advance_epoch()

    audit_events = []

    def tracking_audit_emit(event_type: str, details: dict) -> None:
        audit_events.append((event_type, details))

    class AuditTestPlugin(CorvinPlugin):
        plugin_id = "audit-event-test"
        plugin_type = "audit_backend"
        version = "1.0.0"
        display_name = "Audit Test"

        def on_load(self, ctx: PluginContext) -> None:
            pass

        def on_unload(self) -> None:
            pass

        def health_check(self) -> HealthStatus:
            return HealthStatus(ok=True)

    plugin = AuditTestPlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
        audit_emit=tracking_audit_emit,
    )

    # Register successfully
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

    # Verify audit event was recorded
    loaded_events = [e for e in audit_events if e[0] == "plugin.loaded"]
    assert len(loaded_events) == 1
    assert loaded_events[0][1]["plugin_id"] == plugin.plugin_id

    registry.unregister(plugin.plugin_id)


# ─────────────────────────────────────────────────────────────────────────────
# BUG #4: Wedged Health Check Thread Leak
# ─────────────────────────────────────────────────────────────────────────────


class WedgedHealthPlugin(CorvinPlugin):
    """Plugin with health check that hangs."""

    plugin_id = "wedged-health"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Wedged"

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        """Hang indefinitely."""
        time.sleep(10)
        return HealthStatus(ok=True)


@pytest.mark.asyncio
async def test_bug_4_health_check_timeout_no_thread_leak():
    """Timed-out health check doesn't leak worker threads."""
    registry = get_registry()
    registry._advance_epoch()

    plugin = WedgedHealthPlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
    )
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

    # Count threads before health check
    thread_count_before = threading.active_count()

    # Collector with short timeout
    collector = HealthCollector(interval_s=1.0, alert_after=3)

    # This should timeout and raise HealthCheckTimeout
    with pytest.raises(HealthCheckTimeout):
        registry.health_check_one(plugin.plugin_id, timeout_s=0.1)

    # Give any abandoned threads time to finish
    await asyncio.sleep(0.2)

    # Count threads after
    thread_count_after = threading.active_count()

    # Thread count should not grow unboundedly
    # Allow 1-2 extra threads for test infrastructure
    assert thread_count_after - thread_count_before <= 2, \
        f"Thread leak detected: {thread_count_before} → {thread_count_after}"

    registry.unregister(plugin.plugin_id)


@pytest.mark.asyncio
async def test_bug_4_multiple_timeouts_no_accumulation():
    """Multiple health check timeouts don't accumulate threads."""
    registry = get_registry()
    registry._advance_epoch()

    class MultiWedgePlugin(CorvinPlugin):
        plugin_id = f"multi-wedge-{time.time()}"
        plugin_type = "audit_backend"
        version = "1.0.0"
        display_name = "Multi Wedge"

        def on_load(self, ctx: PluginContext) -> None:
            pass

        def on_unload(self) -> None:
            pass

        def health_check(self) -> HealthStatus:
            time.sleep(5)
            return HealthStatus(ok=True)

    plugin = MultiWedgePlugin()
    ctx = build_context(
        plugin_id=plugin.plugin_id,
        tenant_id="_default",
        corvin_home=Path.home() / ".corvin",
    )
    registry.register(plugin, ctx, boot_layer=BootLayer.INSTALLED)

    thread_count_before = threading.active_count()

    # Run multiple health checks that timeout
    for _ in range(5):
        try:
            registry.health_check_one(plugin.plugin_id, timeout_s=0.1)
        except HealthCheckTimeout:
            pass
        await asyncio.sleep(0.05)

    # Check thread count
    thread_count_after = threading.active_count()

    # Should not have accumulated 5 threads
    assert thread_count_after - thread_count_before <= 2, \
        f"Thread accumulation: {thread_count_before} → {thread_count_after}"

    registry.unregister(plugin.plugin_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
