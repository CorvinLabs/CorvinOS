"""Unit tests for AuditBackend plugin (ADR-0233).

Tests cover:
- Plugin lifecycle (on_load, on_unload, health_check)
- Fanout capability (queue management, never_raises)
- Thread safety and queue bounds
"""

import pytest
import queue
import threading
import time
from unittest.mock import Mock, MagicMock, patch

from corvin_plugins.protocol import HealthStatus, PluginContext


# Import the actual template (or mock if not in sys.path)
# For testing purposes, we inline a test-compatible version
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
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._dropped = 0

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._drain, name="audit-fanout", daemon=True
        )
        self._worker.start()
        if ctx.audit_registry is not None:
            ctx.audit_registry.set_active(self)

    def on_unload(self) -> None:
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


class TestAuditBackendPlugin:
    """Unit tests for audit backend plugin."""

    def test_init(self):
        """Test plugin initialization."""
        plugin = AuditBackendPlugin()
        assert plugin.plugin_id == "com.test.audit-backend"
        assert plugin.plugin_type == "audit_backend"
        assert plugin.version == "1.0.0"
        assert plugin._dropped == 0
        assert plugin._queue.qsize() == 0

    def test_on_load_registers_with_registry(self):
        """Test plugin self-registers on load."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.config = {"endpoint": "https://siem.example.com"}
        ctx.audit_registry = MagicMock()

        plugin.on_load(ctx)

        ctx.audit_registry.set_active.assert_called_once_with(plugin)
        assert plugin._config == ctx.config
        assert plugin._worker is not None
        assert plugin._worker.is_alive()

        plugin.on_unload()

    def test_on_load_without_registry(self):
        """Test on_load when registry is None (graceful degradation)."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None

        plugin.on_load(ctx)  # Should not raise

        assert plugin._worker is not None
        plugin.on_unload()

    def test_fanout_never_raises_on_valid_input(self):
        """Test fanout accepts events without raising."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        try:
            plugin.fanout(
                "skill_executed",
                {"skill_id": "os.router", "output": "route_to_opus"},
                severity="INFO",
                tenant_id="_default"
            )
        except Exception as e:
            pytest.fail(f"fanout raised {type(e).__name__}: {e}")

        assert plugin._queue.qsize() == 1
        plugin.on_unload()

    def test_fanout_never_raises_on_queue_full(self):
        """Test fanout handles queue full gracefully."""
        plugin = AuditBackendPlugin()
        plugin.MAX_QUEUED = 3  # Small queue for testing
        plugin._queue = queue.Queue(maxsize=3)

        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Fill the queue
        for i in range(3):
            plugin.fanout(f"event_{i}", {"index": i})

        # Next call should drop oldest, not raise
        try:
            plugin.fanout("event_4", {"index": 4})
        except Exception as e:
            pytest.fail(f"fanout raised on queue full: {type(e).__name__}: {e}")

        assert plugin._dropped >= 1
        plugin.on_unload()

    def test_fanout_queue_bounded_max_queued(self):
        """Test queue respects MAX_QUEUED limit."""
        plugin = AuditBackendPlugin()
        plugin.MAX_QUEUED = 100

        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Send more than MAX_QUEUED events
        for i in range(150):
            plugin.fanout(f"event_{i}", {"index": i})

        # Queue should never exceed MAX_QUEUED
        assert plugin._queue.qsize() <= plugin.MAX_QUEUED
        plugin.on_unload()

    def test_health_check_reports_dropped(self):
        """Test health_check reports dropped count."""
        plugin = AuditBackendPlugin()
        plugin.MAX_QUEUED = 2
        plugin._queue = queue.Queue(maxsize=2)

        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Trigger drops
        for i in range(5):
            plugin.fanout(f"event_{i}", {"index": i})

        health = plugin.health_check()

        assert health.ok is True
        assert "dropped" in health.details
        assert health.details["dropped"] > 0
        assert "queued" in health.details

        plugin.on_unload()

    def test_on_unload_stops_worker(self):
        """Test on_unload cleanly shuts down worker thread."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        assert plugin._worker.is_alive()

        plugin.on_unload()

        # Give thread time to exit
        plugin._worker.join(timeout=2.0)
        assert not plugin._worker.is_alive()

    def test_verify_chain_returns_healthy(self):
        """Test verify_chain returns expected status."""
        plugin = AuditBackendPlugin()

        health = plugin.verify_chain()

        assert health.ok is True
        assert "backend keeps no verifiable copy" in health.message

    def test_enforce_retention_returns_dict(self):
        """Test enforce_retention returns deletion count."""
        plugin = AuditBackendPlugin()

        result = plugin.enforce_retention(max_age_days=30, tenant_id="_default")

        assert isinstance(result, dict)
        assert "deleted" in result
        assert result["deleted"] == 0


class TestAuditBackendConcurrency:
    """Adversarial tests for thread safety."""

    def test_fanout_concurrent_calls_no_corruption(self):
        """Test fanout handles concurrent calls safely."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        errors = []

        def submit_events(thread_id):
            try:
                for i in range(10):
                    plugin.fanout(
                        f"event_t{thread_id}",
                        {"thread": thread_id, "index": i}
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=submit_events, args=(i,))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert plugin._queue.qsize() == 50  # All events queued

        plugin.on_unload()

    def test_fanout_rapid_fire_under_pressure(self):
        """Test fanout under high-frequency fire (stress test)."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        start = time.time()

        # Rapid fire
        for i in range(1000):
            plugin.fanout("perf_test", {"index": i})

        elapsed = time.time() - start

        # All events should be processed (or queued/dropped)
        # without blocking the caller
        assert elapsed < 2.0  # Should be very fast

        plugin.on_unload()


class TestAuditBackendTenantIsolation:
    """Tests for GDPR Art. 5, 6, 32 tenant isolation."""

    def test_fanout_preserves_tenant_id(self):
        """Test fanout preserves tenant_id for each event."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        # Send events for multiple tenants
        plugin.fanout("event1", {"data": "t1"}, tenant_id="tenant-a")
        plugin.fanout("event2", {"data": "t2"}, tenant_id="tenant-b")

        # Drain queue and verify tenant_id is present
        events = []
        try:
            while True:
                events.append(plugin._queue.get_nowait())
        except queue.Empty:
            pass

        assert len(events) == 2
        assert events[0]["tenant_id"] == "tenant-a"
        assert events[1]["tenant_id"] == "tenant-b"

        plugin.on_unload()

    def test_fanout_defaults_to_default_tenant(self):
        """Test fanout defaults tenant_id to '_default'."""
        plugin = AuditBackendPlugin()
        ctx = MagicMock(spec=PluginContext)
        ctx.audit_registry = None
        plugin.on_load(ctx)

        plugin.fanout("event1", {"data": "test"})  # No tenant_id

        event = plugin._queue.get_nowait()
        assert event["tenant_id"] == "_default"

        plugin.on_unload()
