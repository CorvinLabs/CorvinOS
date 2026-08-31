"""Continuous KPI Population Daemon — background task emitting L1–L3 KPIs.

ADR-0XXX Phase 6.3 — replaces on-demand metric collection with continuous
background daemon running every 15–30 seconds. Emits all Prometheus metrics
to an in-memory cache, making them available to the `/metrics` endpoint
without latency or cache-miss penalties.

Design constraints
------------------

* **Non-blocking:** Async task, never blocks the main event loop.
* **Fail-safe:** Daemon errors never crash the gateway (wrapped in try/except).
* **Tenant isolation:** Each tenant gets its own metric collection pass.
* **TTL cache reuse:** Warms audit_metrics.render()'s own per-(tenant,
  since) TTL cache — the same cache the /metrics endpoint reads on scrape.
* **Configurable interval:** CORVIN_METRICS_COLLECTOR_INTERVAL env var
  (default: 15s, min: 1s, max: 300s).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


def _collection_interval_seconds() -> float:
    """Get the collection interval from env, with bounds checking."""
    raw = os.environ.get("CORVIN_METRICS_COLLECTOR_INTERVAL", "15")
    try:
        v = float(raw)
    except ValueError:
        v = 15.0
    # Clamp: too aggressive collects strain I/O; too slow defeats the purpose
    return max(1.0, min(v, 300.0))


class KPICollectorDaemon:
    """Background daemon that continuously collects and caches KPIs."""

    def __init__(self, interval_seconds: Optional[float] = None):
        """Initialize the daemon."""
        self.interval = interval_seconds or _collection_interval_seconds()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def run_collection_loop(self) -> None:
        """Main daemon loop: collect, cache, sleep, repeat."""
        logger.info(
            "KPI collector daemon starting (interval=%.1f seconds)",
            self.interval,
        )
        self._running = True

        while self._running:
            try:
                await self._collect_all_tenants()
            except Exception as exc:
                logger.error(
                    "Error in KPI collection loop (will retry): %s",
                    exc, exc_info=True,
                )

            # Sleep, respecting cancellation
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                self._running = False
                raise

        logger.info("KPI collector daemon stopped")

    async def _collect_all_tenants(self) -> None:
        """Iterate all tenants and collect their metrics."""
        try:
            # Import forge.paths lazily to avoid bootstrap issues in tests
            try:
                from forge import paths as _forge_paths
                corvin_home = _forge_paths.corvin_home()
            except ImportError:
                # Fallback to environment or default
                corvin_home = os.environ.get("CORVIN_HOME", str(Path.home() / ".corvin"))

            tenants_root = Path(corvin_home) / "tenants"

            if not tenants_root.is_dir():
                logger.debug("Tenants directory not found: %s", tenants_root)
                return

            # List all tenant directories
            tenant_ids = [
                d.name
                for d in tenants_root.iterdir()
                if d.is_dir()
            ]

            if not tenant_ids:
                logger.debug("No tenants found")
                return

            # Collect metrics for each tenant
            for tid in tenant_ids:
                try:
                    await self._collect_tenant(tid)
                except Exception as exc:
                    logger.warning(
                        "Error collecting metrics for tenant %s: %s",
                        tid, exc, exc_info=True,
                    )

        except Exception as exc:
            logger.error(
                "Error in tenant enumeration: %s", exc, exc_info=True,
            )

    async def _collect_tenant(self, tenant_id: str) -> None:
        """Collect metrics for a single tenant."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._collect_tenant_sync,
            tenant_id,
        )

    def _collect_tenant_sync(self, tenant_id: str) -> None:
        """Synchronous tenant metric collection (runs in thread pool)."""
        try:
            # Import here to avoid module-level circular deps
            from corvin_gateway import audit_metrics as _audit_metrics

            # Warm the cache the /metrics endpoint reads: render() populates
            # audit_metrics' own per-(tenant, since) TTL cache, which
            # tenant_metrics() in app.py reads on scrape. This call IS the
            # cache-warming — there is no separate cache layer to write to.
            # (The prior _audit_metrics.set_cached_metrics(...) call referenced
            # a function that lives in the unread core/telemetry/metrics_cache.py
            # module, not audit_metrics, and raised AttributeError every pass.)
            metrics_text = _audit_metrics.render(tenant_id, since=None)

            logger.debug(
                "Metrics collected for tenant %s (%d bytes)",
                tenant_id, len(metrics_text),
            )
        except ImportError:
            logger.debug(
                "audit_metrics module not available (gateway not installed?)"
            )
        except Exception as exc:
            logger.error(
                "Failed to collect metrics for tenant %s: %s",
                tenant_id, exc, exc_info=True,
            )

    def start(self) -> asyncio.Task:
        """Start the daemon as an asyncio task."""
        if self._task is not None and not self._task.done():
            raise RuntimeError("Daemon is already running")

        self._task = asyncio.create_task(self.run_collection_loop())
        return self._task

    async def stop(self, timeout: float = 5.0) -> None:
        """Stop the daemon and wait for it to finish."""
        if self._task is None or self._task.done():
            logger.debug("Daemon not running, nothing to stop")
            return

        logger.info("Stopping KPI collector daemon...")
        self._running = False
        self._task.cancel()

        try:
            await asyncio.wait_for(self._task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Daemon shutdown timed out after %.0f seconds, cancelling task",
                timeout,
            )
            self._task.cancel()
        except asyncio.CancelledError:
            logger.debug("Daemon task cancelled")

        logger.info("KPI collector daemon stopped")


# Global daemon instance (shared across the gateway process)
_daemon_instance: Optional[KPICollectorDaemon] = None


def get_daemon() -> KPICollectorDaemon:
    """Get or create the global daemon instance."""
    global _daemon_instance
    if _daemon_instance is None:
        _daemon_instance = KPICollectorDaemon()
    return _daemon_instance


async def start_daemon(interval_seconds: Optional[float] = None) -> asyncio.Task:
    """Start the global daemon and return its task handle."""
    daemon = get_daemon()
    if daemon._task is not None and not daemon._task.done():
        logger.debug("Daemon already running")
        return daemon._task
    if interval_seconds is not None:
        daemon.interval = interval_seconds
    return daemon.start()


async def stop_daemon(timeout: float = 5.0) -> None:
    """Stop the global daemon."""
    daemon = get_daemon()
    await daemon.stop(timeout=timeout)


__all__ = [
    "KPICollectorDaemon",
    "get_daemon",
    "start_daemon",
    "stop_daemon",
]
