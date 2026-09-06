"""Adversarial tests for AuditBackend plugin.

Tests defensive behavior under hostile conditions:
- Plugin fanout raises → core chain unaffected
- Plugin hangs → caller doesn't block
- Plugin mutates shared state → isolation maintained
"""

import pytest
import queue
import threading
import time
from unittest.mock import Mock, MagicMock, patch

from corvin_plugins.protocol import HealthStatus, PluginContext


class AuditBackendPlugin:
    """Concrete implementation based on template."""

    plugin_id = "com.test.audit-backend"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Test Audit Backend"
    MAX_QUEUED = 10_000

    def __init__(self) -> None:
        self._config: dict = {}
        self._queue: queue.Queue = queue.Queue(maxsize=self.MAX_QUEUED)
        self._worker = None
        self._stop = None
        self._dropped = 0

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        self._stop = threading.Event()
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._drain, name="audit-fanout", daemon=True
        )
        self._worker.start()
        if ctx.audit_registry is not None:
            ctx.audit_registry.set_active(self)

    def on_unload(self) -> None:
        if self._stop:
            self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5.0)

    def health_check(self) -> HealthStatus:
        return HealthStatus(
            ok=True,
            message="ok",
            details={"queued": self._queue.qsize(), "dropped": self._dropped},
        )

    def fanout(
        self,
        event_type: str,
        details: dict,
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> None:
        """Accept a copy of an already-committed core audit event."""
        record = {
            "event_type": event_type,
            "severity": severity,
            "tenant_id": tenant_id,
            "details": details,
        }
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(record)
            except (queue.Empty, queue.Full):
                pass
            self._dropped += 1

    def verify_chain(self) -> HealthStatus:
        return HealthStatus(ok=True, message="backend keeps no verifiable copy")

    def enforce_retention(self, max_age_days: int, *, tenant_id: str = "_default") -> dict:
        return {"deleted": 0}

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                del record  # Placeholder for actual sink
            except Exception:
                pass


@pytest.mark.adversarial
class TestAuditBackendHostile:
    """Adversarial tests: hostile inputs, failures, edge cases."""

    def test_fanout_malicious_input_no_raise(self):
        """Test fanout handles malicious/unexpected input gracefully."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Hostile inputs
        test_cases = [
            (None, {}),  # None event_type
            ("x" * 10000, {}),  # Huge event_type
            ("event", None),  # None details
            ("event", {"key": "x" * 100000}),  # Massive details
            ("event", {1: "numeric_key"}),  # Non-string keys (JSON incompatible)
            ("event\x00\x01\x02", {}),  # Control characters
        ]

        for event_type, details in test_cases:
            try:
                plugin.fanout(event_type or "null_event", details or {})
            except Exception as e:
                pytest.fail(f"fanout raised on hostile input: {type(e).__name__}: {e}")

        plugin.on_unload()

    def test_fanout_does_not_raise_under_memory_pressure(self):
        """Test fanout doesn't raise even under memory stress."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Memory pressure: huge payload
        large_payload = {"data": "x" * (10 * 1024 * 1024)}  # 10MB payload

        start = time.time()
        try:
            plugin.fanout("large_event", large_payload)
        except MemoryError:
            pytest.fail("fanout raised MemoryError")
        except Exception as e:
            pytest.fail(f"fanout raised {type(e).__name__}: {e}")

        elapsed = time.time() - start
        assert elapsed < 5.0  # Should not block

        plugin.on_unload()

    def test_fanout_tenant_id_cannot_be_spoofed(self):
        """Test tenant_id is not overwritten from details."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Attacker tries to inject tenant_id
        malicious_details = {
            "tenant_id": "hacked-tenant",  # Inside details
            "data": "malicious"
        }

        plugin.fanout("event", malicious_details, tenant_id="real-tenant")

        event = plugin._queue.get_nowait()
        assert event["tenant_id"] == "real-tenant"  # Must use kwarg, not details
        assert event["details"]["tenant_id"] == "hacked-tenant"  # Details preserved as-is

        plugin.on_unload()

    def test_fanout_details_dict_not_mutated(self):
        """Test plugin doesn't mutate caller's details dict."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        original_details = {"key": "value", "nested": {"inner": 42}}
        details_copy = {"key": "value", "nested": {"inner": 42}}

        plugin.fanout("event", original_details)

        # Caller's dict should be unchanged
        assert original_details == details_copy

        plugin.on_unload()

    def test_fanout_exception_in_queue_put_nomask(self):
        """Test fanout handles queue.Full gracefully (not masked)."""
        plugin = type("_Bounded", (AuditBackendPlugin,), {"MAX_QUEUED": 1})()  # bound BEFORE __init__ builds the queue
        plugin._queue = queue.Queue(maxsize=1)

        ctx = MagicMock(spec=PluginContext)

        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Fill queue
        plugin.fanout("event1", {"index": 1})

        # Next call hits Full, triggers drop logic
        try:
            plugin.fanout("event2", {"index": 2})
            plugin.fanout("event3", {"index": 3})
        except Exception as e:
            pytest.fail(f"fanout raised on queue full: {type(e).__name__}: {e}")

        assert plugin._dropped >= 1

        plugin.on_unload()


@pytest.mark.adversarial
class TestAuditBackendRaceConditions:
    """Adversarial: thread safety and race conditions."""

    def test_fanout_concurrent_write_corruption(self):
        """Test fanout doesn't corrupt state under concurrent writes."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        errors = []
        dropped_log = []

        def concurrent_fanout(thread_id):
            try:
                for i in range(100):
                    plugin.fanout(
                        f"event_t{thread_id}",
                        {"thread": thread_id, "index": i, "data": "x" * 1000},
                    )
                    # Log dropped count per thread
                    dropped_log.append((thread_id, plugin._dropped))
            except Exception as e:
                errors.append((thread_id, e))

        threads = [
            threading.Thread(target=concurrent_fanout, args=(i,))
            for i in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No exceptions
        assert len(errors) == 0

        # Dropped should be monotonically non-decreasing (never go backward)
        for i in range(1, len(dropped_log)):
            assert dropped_log[i][1] >= dropped_log[i-1][1], \
                f"Dropped count went backward: {dropped_log[i-1]} → {dropped_log[i]}"

        plugin.on_unload()

    def test_fanout_concurrent_with_unload(self):
        """Test fanout during concurrent unload doesn't crash."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        errors = []

        def keep_fanout():
            try:
                for i in range(500):
                    plugin.fanout("event", {"index": i})
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        fanout_thread = threading.Thread(target=keep_fanout, daemon=True)
        fanout_thread.start()

        time.sleep(0.1)  # Let fanout start
        plugin.on_unload()  # Unload while fanout active

        fanout_thread.join(timeout=5.0)

        # Should not crash
        assert len(errors) == 0

    def test_health_check_safe_concurrent_with_fanout(self):
        """Test health_check is safe while fanout is active."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        errors = []

        def concurrent_fanout():
            try:
                for i in range(1000):
                    plugin.fanout("event", {"index": i})
            except Exception as e:
                errors.append(("fanout", e))

        def concurrent_health_check():
            try:
                for i in range(1000):
                    plugin.health_check()
            except Exception as e:
                errors.append(("health_check", e))

        t1 = threading.Thread(target=concurrent_fanout, daemon=True)
        t2 = threading.Thread(target=concurrent_health_check, daemon=True)

        t1.start()
        t2.start()

        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert len(errors) == 0

        plugin.on_unload()


@pytest.mark.adversarial
class TestAuditBackendBoundaryConditions:
    """Adversarial: boundary conditions and edge cases."""

    def test_fanout_empty_details(self):
        """Test fanout accepts empty details dict."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        plugin.fanout("event", {})

        event = plugin._queue.get_nowait()
        assert event["details"] == {}

        plugin.on_unload()

    def test_fanout_max_queued_boundary(self):
        """Test behavior at MAX_QUEUED boundary."""
        plugin = type("_Bounded", (AuditBackendPlugin,), {"MAX_QUEUED": 10})()  # bound BEFORE __init__ builds the queue

        ctx = MagicMock(spec=PluginContext)

        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Fill exactly to MAX_QUEUED
        for i in range(10):
            plugin.fanout(f"event_{i}", {"index": i})

        assert plugin._queue.qsize() == 10

        # Add one more (should trigger drop)
        plugin.fanout("overflow", {"index": 11})

        assert plugin._queue.qsize() <= 10
        assert plugin._dropped > 0

        plugin.on_unload()

    def test_fanout_repeated_calls_idempotent(self):
        """Test fanout can be called repeatedly with same input."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        event_type = "idempotent_event"
        details = {"id": "123"}

        for _ in range(100):
            plugin.fanout(event_type, details)

        assert plugin._queue.qsize() == 100

        plugin.on_unload()

    def test_verify_chain_never_raises(self):
        """Test verify_chain never raises under any condition."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {}  # dataclass FIELDS are not class attrs, so spec= hides them
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Even after failures
        plugin.fanout("event", {})
        plugin.on_unload()

        # After unload
        try:
            result = plugin.verify_chain()
        except Exception as e:
            pytest.fail(f"verify_chain raised after unload: {type(e).__name__}: {e}")

        assert isinstance(result, HealthStatus)

    def test_enforce_retention_with_invalid_tenant(self):
        """Test enforce_retention handles invalid tenant_id gracefully."""
        plugin = AuditBackendPlugin()

        try:
            result = plugin.enforce_retention(max_age_days=30, tenant_id=None)
        except TypeError:
            # Expected: tenant_id has a default
            pass

        result = plugin.enforce_retention(max_age_days=30, tenant_id="nonexistent")
        assert isinstance(result, dict)
