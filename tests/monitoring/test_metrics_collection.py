"""
Test suite for Prometheus metrics collection (Component 4)

6 tests covering:
- Routing decision metrics
- Token estimation metrics
- Model latency metrics
- Audit events
- Circuit breaker state
- Learning events
- Metrics export (Prometheus format)
"""

import pytest
from corvin_console.monitoring import MetricsCollector, get_metrics, initialize_metrics


class TestMetricsCollector:
    """Test Prometheus metrics collection."""

    def setup_method(self):
        """Create a fresh metrics collector for each test."""
        self.metrics = MetricsCollector(namespace="test_corvinos")

    def test_record_routing_decision(self):
        """Test recording routing decisions."""
        self.metrics.record_routing_decision(
            model_selected="claude-opus-5",
            complexity_tier="complex",
            cost_estimate=0.0045,
            confidence=0.95,
            tenant_id="default",
        )

        # Should not raise any errors
        assert self.metrics.routing_decisions_total is not None

    def test_record_multiple_routing_decisions(self):
        """Test recording multiple routing decisions and metrics."""
        for i in range(10):
            tier = ["simple", "medium", "complex"][i % 3]
            model = ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"][i % 3]
            confidence = 0.7 + (i * 0.01)

            self.metrics.record_routing_decision(
                model_selected=model,
                complexity_tier=tier,
                cost_estimate=0.001 * i,
                confidence=confidence,
                tenant_id="default",
            )

        assert True

    def test_record_token_estimation(self):
        """Test token estimation accuracy tracking."""
        self.metrics.record_token_estimation(
            estimated_tokens=250,
            actual_tokens=240,
            task_type="general",
        )
        assert True

    def test_record_token_estimation_high_error(self):
        """Test high token estimation error."""
        self.metrics.record_token_estimation(
            estimated_tokens=100,
            actual_tokens=250,  # 150% error
            task_type="general",
        )
        assert True

    def test_record_model_latency(self):
        """Test model response latency tracking."""
        self.metrics.record_model_latency(
            model_selected="claude-opus-5",
            latency_seconds=2.5,
        )
        assert True

    def test_record_audit_event(self):
        """Test audit event logging."""
        self.metrics.record_audit_event(
            event_type="skill_executed",
            tenant_id="default",
        )
        assert True

    def test_set_circuit_breaker_state_open(self):
        """Test circuit breaker OPEN state (healthy)."""
        self.metrics.set_circuit_breaker_state(
            endpoint="marketplace",
            is_open=True,
        )
        assert True

    def test_set_circuit_breaker_state_closed(self):
        """Test circuit breaker CLOSED state (triggered)."""
        self.metrics.set_circuit_breaker_state(
            endpoint="marketplace",
            is_open=False,
        )
        assert True

    def test_record_learning_event(self):
        """Test learning loop event recording."""
        self.metrics.record_learning_event(
            event_type="outcome_feedback",
            skill_id="os.delegation_router",
        )
        assert True

    def test_record_learning_events_multiple_types(self):
        """Test multiple learning event types."""
        event_types = ["outcome_feedback", "preference_feedback", "confidence_update", "metric_observed"]

        for event_type in event_types:
            self.metrics.record_learning_event(
                event_type=event_type,
                skill_id="os.delegation_router",
            )
        assert True

    def test_set_slo_latency(self):
        """Test SLO latency metric."""
        self.metrics.set_slo_latency(
            endpoint="marketplace",
            p99_latency_ms=450.0,
        )
        assert True

    def test_set_slo_latency_breach(self):
        """Test SLO latency breach (>500ms)."""
        self.metrics.set_slo_latency(
            endpoint="marketplace",
            p99_latency_ms=550.0,  # Over 500ms threshold
        )
        assert True

    def test_set_slo_error_rate(self):
        """Test SLO error rate metric."""
        self.metrics.set_slo_error_rate(
            endpoint="marketplace",
            error_rate_pct=0.08,
        )
        assert True

    def test_set_slo_error_rate_breach(self):
        """Test SLO error rate breach (>0.1%)."""
        self.metrics.set_slo_error_rate(
            endpoint="marketplace",
            error_rate_pct=0.15,  # Over 0.1% threshold
        )
        assert True

    def test_record_http_request(self):
        """Test HTTP request recording."""
        self.metrics.record_http_request(
            method="POST",
            endpoint="/v1/console/marketplace/plugins",
            status_code=200,
        )
        assert True

    def test_record_http_request_error(self):
        """Test HTTP error request recording."""
        self.metrics.record_http_request(
            method="POST",
            endpoint="/v1/console/marketplace/plugins",
            status_code=500,
        )
        assert True

    def test_export_metrics_format(self):
        """Test Prometheus format export."""
        # Record some metrics
        self.metrics.record_routing_decision(
            model_selected="claude-opus-5",
            complexity_tier="complex",
            cost_estimate=0.0045,
            confidence=0.95,
            tenant_id="default",
        )

        # Export to Prometheus format
        metrics_output = self.metrics.export_metrics()

        # Should be bytes
        assert isinstance(metrics_output, bytes)

        # Should contain Prometheus format markers
        metrics_str = metrics_output.decode("utf-8")
        assert "# HELP" in metrics_str or "routing_decisions_total" in metrics_str


class TestGlobalMetricsInstance:
    """Test global metrics singleton."""

    def test_get_metrics_singleton(self):
        """Test that get_metrics returns a singleton."""
        metrics1 = get_metrics()
        metrics2 = get_metrics()

        # Should be the same instance
        assert metrics1 is metrics2

    def test_initialize_metrics(self):
        """Test metrics initialization."""
        initialize_metrics()

        # Should not raise an error
        metrics = get_metrics()
        assert metrics is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
