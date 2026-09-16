"""Phase 8 Observability: Unit tests for metrics collector."""

import pytest
from core.telemetry.metrics_collector import MetricsCollector, MetricsQuery, SkillMetrics


def test_metrics_collector_record():
    """Record a single metric."""
    collector = MetricsCollector()
    collector.record_execution(
        skill_id="test_skill",
        tenant_id="tenant_1",
        latency_ms=45.5,
        convergence_score=0.85,
        model_cost=0.01,
    )

    query = MetricsQuery(tenant_id="tenant_1", range_hours=1)
    metrics = collector.query_metrics(query)

    assert len(metrics) == 1
    assert metrics[0].skill_id == "test_skill"
    assert metrics[0].latency_ms == 45.5
    assert metrics[0].convergence_score == 0.85


def test_metrics_collector_aggregation():
    """Aggregate multiple metrics."""
    collector = MetricsCollector()

    for i in range(5):
        collector.record_execution(
            skill_id="agg_skill",
            tenant_id="tenant_2",
            latency_ms=100 + i * 10,  # 100, 110, 120, 130, 140
            convergence_score=0.8 + i * 0.02,
        )

    query = MetricsQuery(tenant_id="tenant_2", range_hours=1)
    metrics = collector.query_metrics(query)

    assert len(metrics) == 1
    assert metrics[0].skill_id == "agg_skill"
    assert metrics[0].error_rate == 0.0  # No errors
    assert metrics[0].throughput_per_min > 0


def test_metrics_tenant_isolation():
    """Verify tenant isolation (GDPR Art. 5, 6)."""
    collector = MetricsCollector()

    collector.record_execution(
        skill_id="shared_skill",
        tenant_id="tenant_A",
        latency_ms=50,
    )

    collector.record_execution(
        skill_id="shared_skill",
        tenant_id="tenant_B",
        latency_ms=60,
    )

    # Tenant A should only see its metrics
    query_a = MetricsQuery(tenant_id="tenant_A", range_hours=1)
    metrics_a = collector.query_metrics(query_a)
    assert len(metrics_a) == 1
    assert metrics_a[0].tenant_id == "tenant_A"

    # Tenant B should only see its metrics
    query_b = MetricsQuery(tenant_id="tenant_B", range_hours=1)
    metrics_b = collector.query_metrics(query_b)
    assert len(metrics_b) == 1
    assert metrics_b[0].tenant_id == "tenant_B"


def test_metrics_error_rate():
    """Error rate computation."""
    collector = MetricsCollector()

    for i in range(4):
        collector.record_execution(
            skill_id="error_skill",
            tenant_id="tenant_3",
            latency_ms=100,
            error=None if i < 3 else Exception("Test error"),
        )

    query = MetricsQuery(tenant_id="tenant_3", range_hours=1)
    metrics = collector.query_metrics(query)

    assert len(metrics) == 1
    assert abs(metrics[0].error_rate - 0.25) < 0.01  # 1 error out of 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
