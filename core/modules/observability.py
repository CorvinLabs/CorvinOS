"""Observability Dashboard (ADR-0327).

Real-time metrics dashboard with WebSocket streaming support.
Cross-tenant isolation enforced. Fail-closed on invalid queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from core.telemetry.source_of_truth import MetricValue, TelemetryRegistry

logger = logging.getLogger(__name__)


@dataclass
class DashboardConfig:
    """Configuration for observability dashboard."""

    refresh_interval_seconds: int = 5
    max_metrics_per_request: int = 1000
    include_historical: bool = False


class SubscriberConnection:
    """Represents a WebSocket subscriber connection."""

    def __init__(self, connection_id: str, tenant_id: str, send_fn: Callable):
        """Initialize connection.

        Args:
            connection_id: Unique connection identifier
            tenant_id: Tenant ID for isolation
            send_fn: Async function to send messages
        """
        self.connection_id = connection_id
        self.tenant_id = tenant_id
        self.send_fn = send_fn
        self.created_at = datetime.utcnow()
        self.last_update_at = datetime.utcnow()

    def __repr__(self) -> str:
        return f"SubscriberConnection(id={self.connection_id}, tenant={self.tenant_id})"


class ObservabilityDashboard:
    """Real-time metrics dashboard."""

    def __init__(self, config: Optional[DashboardConfig] = None):
        """Initialize dashboard.

        Args:
            config: Dashboard configuration
        """
        self.config = config or DashboardConfig()
        self.registry = TelemetryRegistry()
        self._subscribers: dict[str, SubscriberConnection] = {}
        self._update_counter = 0

    def render_metrics_dashboard(self, tenant_id: str) -> dict[str, Any]:
        """Render current metrics as JSON for tenant.

        Args:
            tenant_id: Tenant ID (isolation)

        Returns:
            Dict with metrics snapshot

        Raises:
            ValueError: If tenant_id invalid
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        # Get metrics for tenant
        try:
            metrics_snapshot = self.registry.get_metrics_snapshot(tenant_id)
        except ValueError as e:
            raise ValueError(f"Failed to get metrics snapshot: {e}")

        # Build dashboard response
        metrics_data = []

        for metric_value in metrics_snapshot.values():
            metrics_data.append({
                "name": metric_value.name,
                "value": metric_value.value,
                "labels": metric_value.labels,
                "timestamp": metric_value.timestamp_utc.isoformat() + "Z",
            })

        # Enforce max metrics limit (fail-closed: cap at max)
        if len(metrics_data) > self.config.max_metrics_per_request:
            logger.warning(
                f"Dashboard for tenant {tenant_id} has {len(metrics_data)} metrics, "
                f"capping at {self.config.max_metrics_per_request}"
            )
            metrics_data = metrics_data[:self.config.max_metrics_per_request]

        dashboard = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "tenant_id": tenant_id,
            "metric_count": len(metrics_data),
            "refresh_interval_seconds": self.config.refresh_interval_seconds,
            "metrics": metrics_data,
        }

        logger.debug(f"Rendered dashboard for tenant {tenant_id}: {len(metrics_data)} metrics")

        return dashboard

    def subscribe(
        self,
        connection_id: str,
        tenant_id: str,
        send_fn: Callable,
    ) -> SubscriberConnection:
        """Subscribe to live metric updates (WebSocket).

        Args:
            connection_id: Unique connection identifier
            tenant_id: Tenant ID (isolation)
            send_fn: Async function to send messages

        Returns:
            SubscriberConnection

        Raises:
            ValueError: If inputs invalid
        """
        if not connection_id or not isinstance(connection_id, str):
            raise ValueError(f"Invalid connection_id: {connection_id}")

        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id}")

        if not callable(send_fn):
            raise ValueError("send_fn must be callable")

        connection = SubscriberConnection(connection_id, tenant_id, send_fn)
        self._subscribers[connection_id] = connection

        logger.info(f"Subscriber connected: {connection}")

        return connection

    def unsubscribe(self, connection_id: str) -> None:
        """Unsubscribe from live updates.

        Args:
            connection_id: Connection to remove

        Raises:
            ValueError: If connection not found
        """
        if connection_id not in self._subscribers:
            raise ValueError(f"Connection '{connection_id}' not registered")

        connection = self._subscribers.pop(connection_id)
        logger.info(f"Subscriber disconnected: {connection}")

    async def stream_live_metrics(
        self,
        connection_id: str,
        duration_seconds: Optional[int] = None,
    ) -> int:
        """Stream live metric updates to subscriber.

        Args:
            connection_id: Subscriber connection ID
            duration_seconds: How long to stream (None = indefinite)

        Returns:
            Number of updates sent

        Raises:
            ValueError: If connection not found
        """
        if connection_id not in self._subscribers:
            raise ValueError(f"Connection '{connection_id}' not registered")

        connection = self._subscribers[connection_id]
        start_time = datetime.utcnow()
        update_count = 0

        while True:
            # Check duration limit
            if duration_seconds:
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                if elapsed > duration_seconds:
                    break

            try:
                # Get current dashboard for tenant
                dashboard = self.render_metrics_dashboard(connection.tenant_id)

                # Send to subscriber
                message = {
                    "type": "metrics_update",
                    "data": dashboard,
                    "update_id": self._update_counter,
                }

                await connection.send_fn(json.dumps(message))
                update_count += 1
                self._update_counter += 1

                logger.debug(
                    f"Sent update #{update_count} to subscriber {connection_id}: "
                    f"{dashboard['metric_count']} metrics"
                )

            except Exception as e:
                logger.error(f"Failed to send update to subscriber {connection_id}: {e}")
                # Broken connection: remove subscriber
                try:
                    self.unsubscribe(connection_id)
                except ValueError:
                    pass  # Already unsubscribed
                break

            # Wait before next update
            await asyncio.sleep(self.config.refresh_interval_seconds)

        logger.info(f"Stream ended for subscriber {connection_id}: {update_count} updates sent")

        return update_count

    def get_subscriber_count(self, tenant_id: Optional[str] = None) -> int:
        """Get current subscriber count.

        Args:
            tenant_id: Filter by tenant (None = all tenants)

        Returns:
            Number of active subscribers
        """
        if tenant_id is None:
            return len(self._subscribers)

        return sum(1 for c in self._subscribers.values() if c.tenant_id == tenant_id)

    def get_dashboard_stats(self) -> dict[str, Any]:
        """Get dashboard usage statistics.

        Returns:
            Dict with stats
        """
        return {
            "total_subscribers": len(self._subscribers),
            "total_updates_sent": self._update_counter,
            "refresh_interval_seconds": self.config.refresh_interval_seconds,
            "max_metrics_per_request": self.config.max_metrics_per_request,
        }

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self._subscribers.clear()
        self._update_counter = 0
