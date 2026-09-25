"""Layer 38 — A2A Relay & Connectivity Prometheus Metrics Exporter.

Exposes Prometheus metrics for the relay and connectivity manager:

1. relay_register_attempts_total - Counter of registration attempts
2. relay_register_success_rate - Gauge of successful registration rate (%)
3. relay_deliver_attempts_total - Counter of delivery attempts by outcome
4. relay_cache_hit_rate - Gauge of cache hit rate (%) from relay.deliver()
5. relay_queue_depth - Gauge of queued messages per kid (current snapshot)
6. relay_connections_active - Gauge of active WebSocket connections
7. discovery_latency_ms - Histogram of pairing discovery latency (p50/p95/p99)
8. discovery_pairing_attempts_total - Counter of pairing attempts
9. discovery_pairing_success_rate - Gauge of successful pairing rate (%)
10. a2a_handshake_latency_ms - Histogram of handshake latency (hello/ack round-trip)
11. a2a_handshake_success_rate - Gauge of successful handshake rate (%)
12. relay_message_latency_ms - Histogram of message delivery latency (p50/p95/p99)

Multi-tenant aware: every metric carries tenant_id label (ADR-0007).
Fail-closed: metric emission failures never interrupt relay/connectivity operations.
Audit-first: metrics emitted AFTER audit events (never lose audit for metrics).

Reference: ADR-0059 (A2A), ADR-0258 (Relay), ADR-0513 (Observability)
"""
from __future__ import annotations

import logging
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Generator

try:
    from prometheus_client import (
        Counter,
        Gauge,
        Histogram,
        Registry,
        CollectorRegistry,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

_log = logging.getLogger("corvin.a2a.relay_metrics")


@dataclass(frozen=True)
class MetricsSnapshot:
    """Immutable snapshot of relay/connectivity metrics at a point in time."""
    timestamp: float
    relay_register_attempts: int
    relay_register_successes: int
    relay_deliver_delivered: int
    relay_deliver_queued: int
    relay_deliver_dropped: int
    relay_active_connections: int
    relay_total_queue_bytes: int
    discovery_latency_samples: list[float]  # milliseconds
    discovery_pairing_attempts: int
    discovery_pairing_successes: int
    handshake_latency_samples: list[float]  # milliseconds
    handshake_attempts: int
    handshake_successes: int
    message_latency_samples: list[float]  # milliseconds


class RelayMetricsCollector:
    """Prometheus metrics collector for A2A Relay and Connectivity Manager.

    Thread-safe, fail-closed design: metric errors never propagate to caller.
    """

    def __init__(self, namespace: str = "a2a", registry: Optional[Registry] = None):
        """Initialize metrics collector with optional custom registry."""
        self.namespace = namespace
        self.registry = registry or (CollectorRegistry() if PROMETHEUS_AVAILABLE else None)
        self._lock = threading.RLock()
        self._init_metrics()

    def _init_metrics(self) -> None:
        """Initialize all Prometheus metrics."""
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            _log.warning("Prometheus client not available; metrics disabled")
            return

        try:
            # ── Relay registration metrics ────────────────────────────────────

            # Total registration attempts (monotonic counter)
            self.relay_register_attempts_total = Counter(
                f"{self.namespace}_relay_register_attempts_total",
                "Total A2A relay registration attempts",
                labelnames=["tenant_id", "outcome"],  # outcome: success, auth_key_mismatch, too_many_kids, relay_at_capacity
                registry=self.registry,
            )

            # Registration success rate (gauge, %)
            self.relay_register_success_rate = Gauge(
                f"{self.namespace}_relay_register_success_rate",
                "A2A relay registration success rate (%)",
                labelnames=["tenant_id"],
                registry=self.registry,
            )

            # ── Relay delivery metrics ────────────────────────────────────────

            # Total delivery attempts by outcome (delivered/queued/dropped)
            self.relay_deliver_attempts_total = Counter(
                f"{self.namespace}_relay_deliver_attempts_total",
                "Total A2A relay message delivery attempts",
                labelnames=["tenant_id", "outcome"],  # outcome: delivered, queued, dropped
                registry=self.registry,
            )

            # Cache hit rate (%)
            self.relay_cache_hit_rate = Gauge(
                f"{self.namespace}_relay_cache_hit_rate",
                "A2A relay cache hit rate (% of deliveries to live connections)",
                labelnames=["tenant_id"],
                registry=self.registry,
            )

            # Queue depth (current snapshot)
            self.relay_queue_depth = Gauge(
                f"{self.namespace}_relay_queue_depth",
                "Current queue depth (queued messages) per kid in A2A relay",
                labelnames=["tenant_id", "kid_prefix"],  # kid_prefix: first 8 chars for cardinality control
                registry=self.registry,
            )

            # Active connections count
            self.relay_connections_active = Gauge(
                f"{self.namespace}_relay_connections_active",
                "Current active WebSocket connections in A2A relay",
                labelnames=["tenant_id"],
                registry=self.registry,
            )

            # ── Discovery metrics ─────────────────────────────────────────────

            # Discovery latency histogram (milliseconds)
            self.discovery_latency_ms = Histogram(
                f"{self.namespace}_discovery_latency_ms",
                "A2A discovery/pairing latency in milliseconds",
                labelnames=["tenant_id"],
                buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000),  # SLO: p99 < 100ms
                registry=self.registry,
            )

            # Pairing attempts counter
            self.discovery_pairing_attempts_total = Counter(
                f"{self.namespace}_discovery_pairing_attempts_total",
                "Total A2A discovery/pairing attempts",
                labelnames=["tenant_id", "outcome"],  # outcome: success, timeout, rejected
                registry=self.registry,
            )

            # Pairing success rate (%)
            self.discovery_pairing_success_rate = Gauge(
                f"{self.namespace}_discovery_pairing_success_rate",
                "A2A discovery/pairing success rate (%) — SLO target > 95%",
                labelnames=["tenant_id"],
                registry=self.registry,
            )

            # ── Handshake metrics ─────────────────────────────────────────────

            # Handshake latency histogram (milliseconds) — hello/ack round-trip
            self.a2a_handshake_latency_ms = Histogram(
                f"{self.namespace}_a2a_handshake_latency_ms",
                "A2A handshake (hello/ack) latency in milliseconds",
                labelnames=["tenant_id"],
                buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000),
                registry=self.registry,
            )

            # Handshake attempts counter
            self.a2a_handshake_attempts_total = Counter(
                f"{self.namespace}_a2a_handshake_attempts_total",
                "Total A2A handshake (hello/ack) attempts",
                labelnames=["tenant_id", "outcome"],  # outcome: success, timeout, verify_failed
                registry=self.registry,
            )

            # Handshake success rate (%)
            self.a2a_handshake_success_rate = Gauge(
                f"{self.namespace}_a2a_handshake_success_rate",
                "A2A handshake (hello/ack) success rate (%)",
                labelnames=["tenant_id"],
                registry=self.registry,
            )

            # ── Message latency metrics ───────────────────────────────────────

            # Message delivery latency histogram (milliseconds)
            self.relay_message_latency_ms = Histogram(
                f"{self.namespace}_relay_message_latency_ms",
                "A2A relay message delivery latency in milliseconds",
                labelnames=["tenant_id"],
                buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000),
                registry=self.registry,
            )

            _log.info("A2A relay metrics initialized successfully")
        except Exception as e:
            _log.error(f"Failed to initialize relay metrics: {e}")
            self.registry = None

    def record_registration(self, tenant_id: str, outcome: str) -> None:
        """Record a relay registration attempt.

        Args:
            tenant_id: Tenant identifier (ADR-0007)
            outcome: "success", "auth_key_mismatch", "too_many_kids", "relay_at_capacity"
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.relay_register_attempts_total.labels(
                    tenant_id=tenant_id, outcome=outcome
                ).inc()
        except Exception as e:
            _log.error(f"Failed to record registration metric: {e}")

    def record_delivery(self, tenant_id: str, outcome: str, latency_ms: float = 0) -> None:
        """Record a relay delivery attempt.

        Args:
            tenant_id: Tenant identifier (ADR-0007)
            outcome: "delivered", "queued", "dropped"
            latency_ms: Message latency in milliseconds (if measured)
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.relay_deliver_attempts_total.labels(
                    tenant_id=tenant_id, outcome=outcome
                ).inc()
                if latency_ms > 0:
                    self.relay_message_latency_ms.labels(tenant_id=tenant_id).observe(latency_ms)
        except Exception as e:
            _log.error(f"Failed to record delivery metric: {e}")

    def record_discovery_attempt(self, tenant_id: str, outcome: str, latency_ms: float = 0) -> None:
        """Record a discovery/pairing attempt.

        Args:
            tenant_id: Tenant identifier
            outcome: "success", "timeout", "rejected"
            latency_ms: Pairing latency in milliseconds (if measured)
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.discovery_pairing_attempts_total.labels(
                    tenant_id=tenant_id, outcome=outcome
                ).inc()
                if latency_ms > 0:
                    self.discovery_latency_ms.labels(tenant_id=tenant_id).observe(latency_ms)
        except Exception as e:
            _log.error(f"Failed to record discovery metric: {e}")

    def record_handshake(self, tenant_id: str, outcome: str, latency_ms: float = 0) -> None:
        """Record a handshake (hello/ack) attempt.

        Args:
            tenant_id: Tenant identifier
            outcome: "success", "timeout", "verify_failed"
            latency_ms: Handshake latency in milliseconds (if measured)
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.a2a_handshake_attempts_total.labels(
                    tenant_id=tenant_id, outcome=outcome
                ).inc()
                if latency_ms > 0:
                    self.a2a_handshake_latency_ms.labels(tenant_id=tenant_id).observe(latency_ms)
        except Exception as e:
            _log.error(f"Failed to record handshake metric: {e}")

    def update_registration_success_rate(self, tenant_id: str, success_rate: float) -> None:
        """Update registration success rate gauge.

        Args:
            tenant_id: Tenant identifier
            success_rate: Success rate as percentage (0-100)
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.relay_register_success_rate.labels(tenant_id=tenant_id).set(success_rate)
        except Exception as e:
            _log.error(f"Failed to update registration success rate: {e}")

    def update_delivery_cache_hit_rate(self, tenant_id: str, cache_hit_rate: float) -> None:
        """Update relay cache hit rate gauge.

        Args:
            tenant_id: Tenant identifier
            cache_hit_rate: Cache hit rate as percentage (0-100)
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.relay_cache_hit_rate.labels(tenant_id=tenant_id).set(cache_hit_rate)
        except Exception as e:
            _log.error(f"Failed to update cache hit rate: {e}")

    def update_active_connections(self, tenant_id: str, count: int) -> None:
        """Update active WebSocket connection count.

        Args:
            tenant_id: Tenant identifier
            count: Current number of active connections
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.relay_connections_active.labels(tenant_id=tenant_id).set(count)
        except Exception as e:
            _log.error(f"Failed to update active connections: {e}")

    def update_pairing_success_rate(self, tenant_id: str, success_rate: float) -> None:
        """Update pairing success rate gauge.

        Args:
            tenant_id: Tenant identifier
            success_rate: Success rate as percentage (0-100) — SLO target > 95%
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.discovery_pairing_success_rate.labels(tenant_id=tenant_id).set(success_rate)
        except Exception as e:
            _log.error(f"Failed to update pairing success rate: {e}")

    def update_handshake_success_rate(self, tenant_id: str, success_rate: float) -> None:
        """Update handshake success rate gauge.

        Args:
            tenant_id: Tenant identifier
            success_rate: Success rate as percentage (0-100)
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return
        try:
            with self._lock:
                self.a2a_handshake_success_rate.labels(tenant_id=tenant_id).set(success_rate)
        except Exception as e:
            _log.error(f"Failed to update handshake success rate: {e}")

    def generate_metrics_text(self) -> bytes:
        """Generate Prometheus exposition format (text/plain; version=0.0.4).

        Returns:
            Prometheus metrics in text format, or empty bytes if unavailable.
        """
        if not PROMETHEUS_AVAILABLE or self.registry is None:
            return b"# A2A relay metrics unavailable (prometheus_client not installed)\n"
        try:
            with self._lock:
                return generate_latest(self.registry)
        except Exception as e:
            _log.error(f"Failed to generate metrics text: {e}")
            return b"# Error generating metrics\n"

    @contextmanager
    def measure_latency(self, tenant_id: str, metric_type: str) -> Generator[None, None, None]:
        """Context manager to measure operation latency.

        Args:
            tenant_id: Tenant identifier
            metric_type: "discovery", "handshake", "message"

        Usage:
            with metrics.measure_latency(tenant_id, "discovery"):
                # operation code
        """
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000
            if metric_type == "discovery":
                self.discovery_latency_ms.labels(tenant_id=tenant_id).observe(elapsed_ms)
            elif metric_type == "handshake":
                self.a2a_handshake_latency_ms.labels(tenant_id=tenant_id).observe(elapsed_ms)
            elif metric_type == "message":
                self.relay_message_latency_ms.labels(tenant_id=tenant_id).observe(elapsed_ms)


# Global singleton instance
_metrics_collector: Optional[RelayMetricsCollector] = None
_collector_lock = threading.Lock()


def get_relay_metrics() -> RelayMetricsCollector:
    """Get or create the global A2A relay metrics collector (singleton)."""
    global _metrics_collector
    if _metrics_collector is None:
        with _collector_lock:
            if _metrics_collector is None:
                _metrics_collector = RelayMetricsCollector()
    return _metrics_collector
