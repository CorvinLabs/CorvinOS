"""
Prometheus Metrics for CorvinOS Console Production Monitoring

Exposes 8 core metrics:
1. routing_decisions_total - Counter of routing decisions by model/tier/tenant
2. routing_confidence_score - Gauge of confidence scores trending up
3. cost_total - Counter of cost by model/tier
4. token_estimation_error - Histogram of token estimation accuracy
5. model_latency_seconds - Histogram of model response latency
6. audit_events_logged - Counter of audit events
7. circuit_breaker_state - Gauge of circuit breaker status (0=OPEN, 1=CLOSED)
8. learning_events_received_total - Counter of learning loop events
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Registry,
    generate_latest,
    CollectorRegistry,
    CONTENT_TYPE_LATEST,
)

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Central Prometheus metrics registry for CorvinOS."""

    def __init__(self, namespace: str = "corvinos_console"):
        """Initialize metrics registry."""
        self.namespace = namespace
        self.registry = CollectorRegistry()

        # 1. Routing decisions counter
        self.routing_decisions_total = Counter(
            f"{namespace}_routing_decisions_total",
            "Total routing decisions made",
            labelnames=["model_selected", "complexity_tier", "tenant_id"],
            registry=self.registry,
        )

        # 2. Routing confidence gauge (tracks average confidence per tier)
        self.routing_confidence_score = Gauge(
            f"{namespace}_routing_confidence_score",
            "Average confidence score of routing decisions",
            labelnames=["complexity_tier"],
            registry=self.registry,
        )

        # 3. Cost counter (tracks cost by model)
        self.cost_total = Counter(
            f"{namespace}_cost_total",
            "Total cost in USD by model and tier",
            labelnames=["model_selected", "complexity_tier", "task_type"],
            registry=self.registry,
        )

        # 4. Token estimation error histogram
        self.token_estimation_error = Histogram(
            f"{namespace}_token_estimation_error",
            "Token estimation error as percentage (|estimated - actual| / actual * 100)",
            labelnames=["task_type"],
            buckets=(5, 10, 20, 30, 50, 75, 100, 150, 200, 500, 1000),
            registry=self.registry,
        )

        # 5. Model latency histogram (seconds)
        self.model_latency_seconds = Histogram(
            f"{namespace}_model_latency_seconds",
            "Model response latency in seconds",
            labelnames=["model_selected"],
            buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0),
            registry=self.registry,
        )

        # 6. Audit events logged counter
        self.audit_events_logged = Counter(
            f"{namespace}_audit_events_logged_total",
            "Total audit events logged",
            labelnames=["event_type", "tenant_id"],
            registry=self.registry,
        )

        # 7. Circuit breaker state gauge
        self.circuit_breaker_state = Gauge(
            f"{namespace}_circuit_breaker_state",
            "Circuit breaker state (0=OPEN/healthy, 1=CLOSED/triggered)",
            labelnames=["endpoint"],
            registry=self.registry,
        )

        # 8. Learning events counter
        self.learning_events_received_total = Counter(
            f"{namespace}_learning_events_received_total",
            "Total learning events received",
            labelnames=["event_type", "skill_id"],
            registry=self.registry,
        )

        # Additional SLO tracking metrics
        self.slo_latency_p99_ms = Gauge(
            f"{namespace}_slo_latency_p99_ms",
            "P99 latency in milliseconds (SLO threshold: 500ms)",
            labelnames=["endpoint"],
            registry=self.registry,
        )

        self.slo_error_rate_pct = Gauge(
            f"{namespace}_slo_error_rate_pct",
            "Error rate percentage (SLO threshold: 0.1%)",
            labelnames=["endpoint"],
            registry=self.registry,
        )

        # Request counter
        self.requests_total = Counter(
            f"{namespace}_requests_total",
            "Total HTTP requests",
            labelnames=["method", "endpoint", "status"],
            registry=self.registry,
        )

        logger.info(f"✅ Metrics registry initialized (namespace={namespace})")

    def record_routing_decision(
        self,
        model_selected: str,
        complexity_tier: str,
        cost_estimate: float,
        confidence: float,
        tenant_id: str = "default",
    ) -> None:
        """Record a routing decision."""
        self.routing_decisions_total.labels(
            model_selected=model_selected,
            complexity_tier=complexity_tier,
            tenant_id=tenant_id,
        ).inc()

        # Update confidence gauge (sliding average)
        current = self.routing_confidence_score.labels(
            complexity_tier=complexity_tier
        )._value.get()
        # Simple moving average: (old * 0.9) + (new * 0.1)
        new_confidence = (current * 0.9) + (confidence * 0.1) if current else confidence
        self.routing_confidence_score.labels(
            complexity_tier=complexity_tier
        ).set(new_confidence)

        # Record cost
        self.cost_total.labels(
            model_selected=model_selected,
            complexity_tier=complexity_tier,
            task_type="general",
        ).inc(cost_estimate)

    def record_token_estimation(
        self,
        estimated_tokens: int,
        actual_tokens: int,
        task_type: str = "general",
    ) -> None:
        """Record token estimation accuracy."""
        if actual_tokens > 0:
            error_percent = abs(estimated_tokens - actual_tokens) / actual_tokens * 100
            self.token_estimation_error.labels(task_type=task_type).observe(error_percent)

    def record_model_latency(
        self,
        model_selected: str,
        latency_seconds: float,
    ) -> None:
        """Record model response latency."""
        self.model_latency_seconds.labels(model_selected=model_selected).observe(
            latency_seconds
        )

    def record_audit_event(
        self,
        event_type: str,
        tenant_id: str = "default",
    ) -> None:
        """Record an audit event."""
        self.audit_events_logged.labels(
            event_type=event_type,
            tenant_id=tenant_id,
        ).inc()

    def set_circuit_breaker_state(
        self,
        endpoint: str,
        is_open: bool,
    ) -> None:
        """
        Set circuit breaker state.

        Args:
            endpoint: Endpoint name (e.g., "marketplace", "claude-api")
            is_open: True if healthy (OPEN), False if triggered (CLOSED)
        """
        state = 0 if is_open else 1  # 0=OPEN (good), 1=CLOSED (bad)
        self.circuit_breaker_state.labels(endpoint=endpoint).set(state)

    def record_learning_event(
        self,
        event_type: str,
        skill_id: str,
    ) -> None:
        """Record a learning loop event."""
        self.learning_events_received_total.labels(
            event_type=event_type,
            skill_id=skill_id,
        ).inc()

    def set_slo_latency(
        self,
        endpoint: str,
        p99_latency_ms: float,
    ) -> None:
        """Update P99 latency SLO metric."""
        self.slo_latency_p99_ms.labels(endpoint=endpoint).set(p99_latency_ms)

    def set_slo_error_rate(
        self,
        endpoint: str,
        error_rate_pct: float,
    ) -> None:
        """Update error rate SLO metric."""
        self.slo_error_rate_pct.labels(endpoint=endpoint).set(error_rate_pct)

    def record_http_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
    ) -> None:
        """Record an HTTP request."""
        self.requests_total.labels(
            method=method,
            endpoint=endpoint,
            status=str(status_code),
        ).inc()

    def export_metrics(self) -> bytes:
        """Export metrics in Prometheus format."""
        return generate_latest(self.registry)


# Global metrics instance
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def initialize_metrics() -> None:
    """Initialize global metrics collector."""
    get_metrics()
    logger.info("✅ Metrics initialized")


# FastAPI endpoint helper
from fastapi import APIRouter


def create_metrics_router() -> APIRouter:
    """Create a FastAPI router that exposes the /metrics endpoint."""
    router = APIRouter()

    @router.get("/metrics", response_class=None)
    async def metrics_endpoint():
        """Prometheus-format metrics endpoint."""
        from fastapi.responses import Response
        metrics = get_metrics()
        return Response(
            content=metrics.export_metrics(),
            media_type=CONTENT_TYPE_LATEST,
        )

    return router


if __name__ == "__main__":
    # Demo: record some metrics and export
    metrics = MetricsCollector()

    # Record routing decision
    metrics.record_routing_decision(
        model_selected="claude-opus-5",
        complexity_tier="complex",
        cost_estimate=0.0045,
        confidence=0.95,
        tenant_id="default",
    )

    # Record token estimation
    metrics.record_token_estimation(
        estimated_tokens=250,
        actual_tokens=240,
        task_type="general",
    )

    # Record model latency
    metrics.record_model_latency(
        model_selected="claude-opus-5",
        latency_seconds=2.5,
    )

    # Record audit event
    metrics.record_audit_event(
        event_type="skill_executed",
        tenant_id="default",
    )

    # Set circuit breaker state
    metrics.set_circuit_breaker_state(endpoint="marketplace", is_open=True)

    # Record learning event
    metrics.record_learning_event(
        event_type="outcome_feedback",
        skill_id="os.delegation_router",
    )

    # Export metrics
    output = metrics.export_metrics().decode("utf-8")
    print("✅ Prometheus metrics:")
    print(output[:500])  # Print first 500 chars
