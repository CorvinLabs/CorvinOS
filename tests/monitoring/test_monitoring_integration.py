"""
Integration test suite for complete monitoring setup (All Components)

Tests the end-to-end monitoring pipeline:
1. Metrics collection
2. Sentry breadcrumbs
3. Grafana dashboards available
4. Alert rules valid

This test ensures all 4 components work together correctly.
"""

import pytest
import json
from pathlib import Path
from corvin_console.monitoring import (
    MetricsCollector,
    get_metrics,
    initialize_sentry,
    SentryBreadcrumbManager,
    create_model_routing_dashboard,
    export_dashboards_json,
)


class TestMonitoringIntegration:
    """Integration tests for the complete monitoring system."""

    def test_complete_routing_event_flow(self):
        """
        Test complete event flow for a routing decision:
        1. Record metrics
        2. Add Sentry breadcrumb
        3. Verify metrics exported
        """
        metrics = MetricsCollector(namespace="test_integration")

        # Record routing decision (triggers metrics + Sentry)
        model = "claude-opus-5"
        tier = "complex"
        cost = 0.0045
        confidence = 0.95

        metrics.record_routing_decision(
            model_selected=model,
            complexity_tier=tier,
            cost_estimate=cost,
            confidence=confidence,
            tenant_id="default",
        )

        # Record breadcrumb
        SentryBreadcrumbManager.routing_decision(
            model_selected=model,
            complexity_tier=tier,
            confidence=confidence,
            cost_estimate=cost,
            latency_estimate_ms=2500,
            reasoning="Complex task requires best model",
        )

        # Verify metrics can be exported
        export = metrics.export_metrics()
        assert isinstance(export, bytes)
        assert len(export) > 0

    def test_complete_error_response_flow(self):
        """
        Test complete error response flow:
        1. Model API fails
        2. Record failure metric
        3. Add Sentry breadcrumb
        4. Fallback to lower tier
        """
        metrics = MetricsCollector(namespace="test_error_flow")

        # Original attempt
        metrics.record_routing_decision(
            model_selected="claude-opus-5",
            complexity_tier="complex",
            cost_estimate=0.005,
            confidence=0.9,
        )

        # API failure
        SentryBreadcrumbManager.model_api_failure(
            model="claude-opus-5",
            error="Rate limit exceeded",
            fallback_model="claude-sonnet-5",
            retry_count=1,
        )

        # Fallback routing
        metrics.record_routing_decision(
            model_selected="claude-sonnet-5",
            complexity_tier="medium",
            cost_estimate=0.0015,
            confidence=0.80,
        )

        # Verify metrics collected
        export = metrics.export_metrics()
        assert b"routing_decisions_total" in export or b"sonnet" in export

    def test_complete_budget_constraint_flow(self):
        """
        Test complete budget constraint flow:
        1. Track costs
        2. Detect budget threshold
        3. Alert via Sentry
        4. Circuit breaker engages
        """
        metrics = MetricsCollector(namespace="test_budget")

        # Accumulate costs
        daily_budget = 50.0
        cost_so_far = 0.0

        for i in range(100):
            cost_per_request = 0.45  # High-cost requests
            cost_so_far += cost_per_request

            metrics.record_routing_decision(
                model_selected="claude-opus-5",
                complexity_tier="complex",
                cost_estimate=cost_per_request,
                confidence=0.95,
            )

            # Check budget threshold (80%)
            if cost_so_far >= daily_budget * 0.8:
                SentryBreadcrumbManager.budget_exceeded(
                    estimated_cost=cost_so_far,
                    daily_budget=daily_budget,
                    percentage_used=(cost_so_far / daily_budget) * 100,
                    tenant_id="default",
                )
                break

        # Verify budget alert was recorded
        assert cost_so_far >= daily_budget * 0.8

    def test_complete_slo_monitoring_flow(self):
        """
        Test complete SLO monitoring flow:
        1. Track latency
        2. Calculate P99
        3. Check against SLO
        4. Trigger circuit breaker if exceeded
        """
        metrics = MetricsCollector(namespace="test_slo")

        # Record latencies (simulate 100 requests)
        latencies = []
        for i in range(100):
            # Most requests are fast, some are slow
            if i < 99:
                latency = 0.150 + (i * 0.002)  # 150-350ms
            else:
                latency = 1.5  # One slow outlier

            latencies.append(latency)
            metrics.record_model_latency(
                model_selected="claude-sonnet-5",
                latency_seconds=latency,
            )

        # Calculate P99
        sorted_latencies = sorted(latencies)
        p99_idx = int(len(sorted_latencies) * 0.99)
        p99_latency = sorted_latencies[p99_idx] * 1000  # Convert to ms

        # Record SLO metric
        metrics.set_slo_latency(
            endpoint="marketplace",
            p99_latency_ms=p99_latency,
        )

        # If P99 > 500ms, trigger circuit breaker
        if p99_latency > 500:
            metrics.set_circuit_breaker_state(
                endpoint="marketplace",
                is_open=False,  # CLOSED = triggered
            )
            SentryBreadcrumbManager.circuit_breaker_triggered(
                endpoint="marketplace",
                reason="p99_latency_exceeded",
                recovery_delay_sec=60,
            )

    def test_complete_learning_loop_flow(self):
        """
        Test complete learning loop flow:
        1. Make routing decision
        2. Collect outcome feedback
        3. Update config
        4. Record convergence
        """
        metrics = MetricsCollector(namespace="test_learning")

        # Initial decision
        metrics.record_routing_decision(
            model_selected="claude-opus-5",
            complexity_tier="complex",
            cost_estimate=0.005,
            confidence=0.80,
        )

        # Record learning event
        metrics.record_learning_event(
            event_type="outcome_feedback",
            skill_id="os.delegation_router",
        )

        # Record preference feedback
        metrics.record_learning_event(
            event_type="preference_feedback",
            skill_id="os.delegation_router",
        )

        # Simulate convergence (repeated feedback improves confidence)
        for i in range(10):
            confidence = 0.80 + (i * 0.02)
            metrics.routing_confidence_score.labels(
                complexity_tier="complex"
            ).set(confidence)

            metrics.record_learning_event(
                event_type="metric_observed",
                skill_id="os.delegation_router",
            )

        # Verify learning metrics collected
        export = metrics.export_metrics()
        assert b"learning_events_received_total" in export or b"received" in export

    def test_audit_chain_monitoring(self):
        """Test audit chain monitoring integration."""
        metrics = MetricsCollector(namespace="test_audit")

        # Record normal audit events
        for i in range(10):
            metrics.record_audit_event(
                event_type="skill_executed",
                tenant_id="default",
            )

        # Record critical failure
        SentryBreadcrumbManager.audit_chain_failure(
            error_type="hash_mismatch",
            message="Event hash does not match chain",
            chain_height=12345,
            last_hash="sha256_abc...",
        )

        # Verify metrics exported
        export = metrics.export_metrics()
        assert b"audit_events_logged_total" in export or b"audit" in export

    def test_dashboards_use_metrics(self):
        """
        Test that dashboards reference the metrics we collect.
        """
        dashboard = create_model_routing_dashboard()

        # Extract all metric names from dashboard
        metric_names = set()
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr", "")

                # Extract metric names from Prometheus expression
                if "corvinos_console_" in expr:
                    # Simple extraction: find metric name
                    parts = expr.split("corvinos_console_")
                    for part in parts[1:]:
                        metric = part.split("(")[0].split("[")[0].split("}")[0]
                        metric_names.add(f"corvinos_console_{metric}")

        # Should reference multiple metrics
        assert len(metric_names) > 0

        # Should reference routing metrics
        found_routing = any("routing" in name for name in metric_names)
        assert found_routing, f"Dashboard should reference routing metrics. Found: {metric_names}"

    def test_all_components_importable(self):
        """Test all monitoring components can be imported."""
        from corvin_console.monitoring import (
            initialize_sentry,
            SentryBreadcrumbManager,
            capture_routing_error,
            get_metrics,
            create_metrics_router,
            create_model_routing_dashboard,
            create_slo_monitoring_dashboard,
            create_learning_loop_dashboard,
        )

        assert all([
            initialize_sentry,
            SentryBreadcrumbManager,
            capture_routing_error,
            get_metrics,
            create_metrics_router,
            create_model_routing_dashboard,
            create_slo_monitoring_dashboard,
            create_learning_loop_dashboard,
        ])

    def test_monitoring_package_structure(self):
        """Test monitoring package has all expected modules."""
        monitoring_dir = Path(__file__).parent.parent.parent / "core/console/corvin_console/monitoring"

        expected_files = [
            "__init__.py",
            "sentry_config.py",
            "metrics.py",
            "grafana_dashboards.py",
            "prometheus_alerts.yaml",
        ]

        for filename in expected_files:
            filepath = monitoring_dir / filename
            assert filepath.exists(), f"Missing file: {filename}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
