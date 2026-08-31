"""Unit tests for KPICollectorDaemon."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path to import collector_daemon
_THIS_DIR = Path(__file__).resolve().parent
_PARENT_DIR = _THIS_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

# Add operator/forge to path
_REPO = Path(__file__).resolve().parents[3]
if str(_REPO / "operator" / "forge") not in sys.path:
    sys.path.insert(0, str(_REPO / "operator" / "forge"))

# Now we can import the daemon module
from collector_daemon import (
    KPICollectorDaemon,
    _collection_interval_seconds,
    get_daemon,
)


class IntervalConfigurationTests(unittest.TestCase):
    """Test interval configuration from environment."""

    def test_default_interval_15s(self):
        """Default interval (no env var) is 15 seconds."""
        with patch.dict(os.environ, {}, clear=False):
            if "CORVIN_METRICS_COLLECTOR_INTERVAL" in os.environ:
                del os.environ["CORVIN_METRICS_COLLECTOR_INTERVAL"]
            interval = _collection_interval_seconds()
            self.assertEqual(interval, 15.0)

    def test_interval_from_env(self):
        """Interval can be set via environment variable."""
        with patch.dict(os.environ, {"CORVIN_METRICS_COLLECTOR_INTERVAL": "30"}):
            interval = _collection_interval_seconds()
            self.assertEqual(interval, 30.0)

    def test_interval_clamps_minimum(self):
        """Interval is clamped to minimum of 1 second."""
        with patch.dict(os.environ, {"CORVIN_METRICS_COLLECTOR_INTERVAL": "0.5"}):
            interval = _collection_interval_seconds()
            self.assertEqual(interval, 1.0)

    def test_interval_clamps_maximum(self):
        """Interval is clamped to maximum of 300 seconds."""
        with patch.dict(os.environ, {"CORVIN_METRICS_COLLECTOR_INTERVAL": "600"}):
            interval = _collection_interval_seconds()
            self.assertEqual(interval, 300.0)

    def test_interval_invalid_env(self):
        """Invalid interval env var defaults to 15 seconds."""
        with patch.dict(os.environ, {"CORVIN_METRICS_COLLECTOR_INTERVAL": "invalid"}):
            interval = _collection_interval_seconds()
            self.assertEqual(interval, 15.0)


class DaemonLifecycleTests(unittest.TestCase):
    """Test daemon startup/shutdown."""

    def setUp(self):
        """Create a fresh daemon for each test."""
        self.daemon = KPICollectorDaemon(interval_seconds=0.1)

    async def async_test_daemon_starts(self):
        """Daemon can be started and returns a task."""
        task = self.daemon.start()
        self.assertIsNotNone(task)
        self.assertIsInstance(task, asyncio.Task)
        self.assertFalse(task.done())

        await self.daemon.stop(timeout=1.0)
        self.assertTrue(task.done())

    def test_daemon_starts(self):
        """Daemon.start() returns a task."""
        asyncio.run(self.async_test_daemon_starts())

    async def async_test_daemon_stops_gracefully(self):
        """Daemon stops cleanly without raising."""
        task = self.daemon.start()
        await asyncio.sleep(0.05)

        await self.daemon.stop(timeout=1.0)
        self.assertTrue(task.done())

    def test_daemon_stops_gracefully(self):
        """Daemon.stop() completes without raising."""
        asyncio.run(self.async_test_daemon_stops_gracefully())

    async def async_test_daemon_idempotent_stop(self):
        """Calling stop() multiple times is safe."""
        self.daemon.start()
        await self.daemon.stop(timeout=1.0)
        await self.daemon.stop(timeout=1.0)  # Should not raise

    def test_daemon_idempotent_stop(self):
        """Calling stop() twice is safe."""
        asyncio.run(self.async_test_daemon_idempotent_stop())


class TenantIterationTests(unittest.TestCase):
    """Test tenant discovery and iteration."""

    async def async_test_collect_all_tenants_empty_dir(self):
        """If tenants/ is empty, collection completes without error."""
        daemon = KPICollectorDaemon(interval_seconds=0.1)

        with tempfile.TemporaryDirectory() as td:
            tenants_root = Path(td) / "tenants"
            tenants_root.mkdir()

            with patch.dict(os.environ, {"CORVIN_HOME": td}):
                await daemon._collect_all_tenants()

    def test_collect_all_tenants_empty_dir(self):
        """Empty tenants directory is handled gracefully."""
        asyncio.run(self.async_test_collect_all_tenants_empty_dir())

    async def async_test_collect_all_tenants_missing_dir(self):
        """If tenants/ doesn't exist, collection completes without error."""
        daemon = KPICollectorDaemon(interval_seconds=0.1)

        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {"CORVIN_HOME": td}):
                await daemon._collect_all_tenants()

    def test_collect_all_tenants_missing_dir(self):
        """Missing tenants directory is handled gracefully."""
        asyncio.run(self.async_test_collect_all_tenants_missing_dir())


class ErrorHandlingTests(unittest.TestCase):
    """Test daemon error resilience."""

    async def async_test_collection_error_doesnt_stop_daemon(self):
        """If collection fails, daemon keeps running."""
        daemon = KPICollectorDaemon(interval_seconds=0.05)

        with patch.object(
            daemon, "_collect_all_tenants",
            side_effect=RuntimeError("simulated error")
        ):
            task = daemon.start()
            await asyncio.sleep(0.15)

            self.assertFalse(task.done())

            await daemon.stop(timeout=1.0)

    def test_collection_error_doesnt_stop_daemon(self):
        """Errors in collection don't crash the daemon."""
        asyncio.run(self.async_test_collection_error_doesnt_stop_daemon())


class MetricsCacheRegressionTests(unittest.TestCase):
    """Regression: _collect_tenant_sync must not crash on a missing cache setter.

    Before the fix, the daemon called ``_audit_metrics.set_cached_metrics(...)``
    — a function that lives in the unread ``core/telemetry/metrics_cache.py``
    module, NOT in ``corvin_gateway.audit_metrics`` — so every collection pass
    logged ``Failed to collect metrics ... has no attribute 'set_cached_metrics'``
    while the gateway ran. ``audit_metrics.render()`` already warms the cache the
    ``/metrics`` endpoint reads, so the erroneous call was removed. This test
    drives the real ``audit_metrics`` module (no mock) so the AttributeError
    regression would resurface as an ERROR log.
    """

    def test_collect_tenant_sync_does_not_log_collection_error(self):
        daemon = KPICollectorDaemon(interval_seconds=0.1)
        with tempfile.TemporaryDirectory() as td:
            # A tenant global dir so render() has a valid (empty) chain path.
            (Path(td) / "tenants" / "_default" / "global").mkdir(parents=True)
            with patch.dict(os.environ, {"CORVIN_HOME": td}):
                with self.assertNoLogs("collector_daemon", level="ERROR"):
                    daemon._collect_tenant_sync("_default")


if __name__ == "__main__":
    unittest.main()
