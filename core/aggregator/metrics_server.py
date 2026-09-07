"""Metrics Server — In-Process Aggregator Service (ADR-0638)

Manages a background task that periodically collects and persists
global metrics from all tenant instances.

Responsibilities:
  1. Periodic collection task (configurable interval, default: 60s)
  2. Cache latest metrics for fast API responses
  3. Health monitoring of collector
  4. Error handling and logging
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from core.aggregator.metrics_collector import MetricsCollector, GlobalMetrics, TenantMetrics

logger = logging.getLogger(__name__)


class MetricsServer:
    """In-process aggregator service.

    Usage (in FastAPI lifespan):
        server = MetricsServer()
        await server.start()
        # API handlers use server.get_latest_metrics() etc.
        await server.stop()
    """

    def __init__(self, collection_interval_sec: int = 60):
        """Initialize metrics server.

        Args:
            collection_interval_sec: Interval between collections (seconds)
        """
        self.collector = MetricsCollector()
        self.collection_interval_sec = collection_interval_sec

        # Cache
        self._latest_global_metrics: Optional[GlobalMetrics] = None
        self._latest_tenant_metrics: Dict[str, TenantMetrics] = {}
        self._last_collection_time: Optional[datetime] = None
        self._collection_error: Optional[str] = None

        # Background task
        self._collection_task: Optional[asyncio.Task] = None
        self._running = False

        logger.info(
            f"MetricsServer initialized (collection interval: {collection_interval_sec}s)"
        )

    async def start(self) -> None:
        """Start the background collection task.

        Should be called during application startup (FastAPI lifespan).
        """
        if self._running:
            logger.warning("MetricsServer is already running")
            return

        self._running = True
        self._collection_task = asyncio.create_task(self._collection_loop())
        logger.info("MetricsServer started")

    async def stop(self) -> None:
        """Stop the background collection task.

        Should be called during application shutdown (FastAPI lifespan).
        """
        if not self._running:
            return

        self._running = False
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass

        logger.info("MetricsServer stopped")

    async def _collection_loop(self) -> None:
        """Background loop that periodically collects metrics."""
        while self._running:
            try:
                # Collect metrics from all tenants
                logger.debug("Starting metrics collection cycle")
                global_metrics = self.collector.collect_global_metrics()

                # Collect per-tenant metrics for cache
                tenants = self.collector.discover_tenants()
                tenant_metrics = {}
                for tenant_id in tenants:
                    tenant_metrics[tenant_id] = self.collector.collect_tenant_metrics(tenant_id)

                # Update cache
                self._latest_global_metrics = global_metrics
                self._latest_tenant_metrics = tenant_metrics
                self._last_collection_time = datetime.utcnow()
                self._collection_error = None

                # Persist to disk
                self.collector.persist_global_metrics(global_metrics)

                logger.debug(
                    f"Metrics collection complete: {global_metrics.instance_count} instances, "
                    f"{global_metrics.total_events} events"
                )

            except Exception as e:
                self._collection_error = str(e)
                logger.error(f"Error during metrics collection: {e}", exc_info=True)

            # Wait for next collection
            try:
                await asyncio.sleep(self.collection_interval_sec)
            except asyncio.CancelledError:
                break

    def get_latest_global_metrics(self) -> Optional[GlobalMetrics]:
        """Get the most recently collected global metrics.

        Returns:
            GlobalMetrics object or None if no collection has completed yet
        """
        return self._latest_global_metrics

    def get_latest_tenant_metrics(self, tenant_id: str) -> Optional[TenantMetrics]:
        """Get the most recently collected metrics for a specific tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            TenantMetrics object or None
        """
        return self._latest_tenant_metrics.get(tenant_id)

    def get_all_tenant_metrics(self) -> Dict[str, TenantMetrics]:
        """Get all latest tenant metrics.

        Returns:
            Dictionary mapping tenant_id to TenantMetrics
        """
        return self._latest_tenant_metrics.copy()

    def get_health_status(self) -> Dict[str, Any]:
        """Get server health and collection status.

        Returns:
            Dictionary with health information
        """
        return {
            "running": self._running,
            "last_collection_time": self._last_collection_time.isoformat() if self._last_collection_time else None,
            "collection_interval_sec": self.collection_interval_sec,
            "instances_cached": len(self._latest_tenant_metrics),
            "has_error": self._collection_error is not None,
            "error_message": self._collection_error,
        }


# Global singleton instance (initialized in gateway lifespan)
_metrics_server_instance: Optional[MetricsServer] = None


def get_metrics_server() -> MetricsServer:
    """Get or create the global metrics server instance.

    Note: In production, the gateway's lifespan should create this
    explicitly and call start(). This is a fallback for testing.
    """
    global _metrics_server_instance
    if _metrics_server_instance is None:
        _metrics_server_instance = MetricsServer()
    return _metrics_server_instance


def set_metrics_server(server: MetricsServer) -> None:
    """Set the global metrics server instance.

    Should be called during gateway initialization.

    Args:
        server: MetricsServer instance
    """
    global _metrics_server_instance
    _metrics_server_instance = server
