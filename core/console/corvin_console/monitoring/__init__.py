"""
CorvinOS Console Monitoring Package

Components:
1. Sentry - Error tracking and performance monitoring
2. Metrics - Prometheus metrics for SLOs, routing, learning
3. Grafana - Pre-built dashboards for visualization
4. Alert Rules - Prometheus alerting rules (YAML format)

Usage:
    from corvin_console.monitoring import initialize_sentry, get_metrics

    # Initialize at app startup
    initialize_sentry()
    metrics = get_metrics()

    # Record metrics
    metrics.record_routing_decision(
        model_selected="claude-opus-5",
        complexity_tier="complex",
        cost_estimate=0.0045,
        confidence=0.95,
    )
"""

from .sentry_config import (
    initialize_sentry,
    SentryBreadcrumbManager,
    capture_routing_error,
    capture_audit_chain_error,
    add_sentry_middleware,
)

from .metrics import (
    MetricsCollector,
    get_metrics,
    initialize_metrics,
    create_metrics_router,
)

from .grafana_dashboards import (
    create_model_routing_dashboard,
    create_slo_monitoring_dashboard,
    create_learning_loop_dashboard,
    export_dashboards_json,
)

__all__ = [
    # Sentry
    "initialize_sentry",
    "SentryBreadcrumbManager",
    "capture_routing_error",
    "capture_audit_chain_error",
    "add_sentry_middleware",
    # Metrics
    "MetricsCollector",
    "get_metrics",
    "initialize_metrics",
    "create_metrics_router",
    # Grafana
    "create_model_routing_dashboard",
    "create_slo_monitoring_dashboard",
    "create_learning_loop_dashboard",
    "export_dashboards_json",
]

__version__ = "1.0.0"
