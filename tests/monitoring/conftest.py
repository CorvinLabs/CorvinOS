"""
Pytest configuration for monitoring tests.

Fixtures:
- metrics_collector: Fresh MetricsCollector for each test
- sample_routing_decision: Pre-configured routing decision data
- sample_slo_metrics: Pre-configured SLO metrics
"""

import pytest
from corvin_console.monitoring import MetricsCollector


@pytest.fixture
def metrics_collector():
    """Provide a fresh metrics collector for each test."""
    return MetricsCollector(namespace="test_metrics")


@pytest.fixture
def sample_routing_decision():
    """Sample routing decision data."""
    return {
        "model_selected": "claude-opus-5",
        "complexity_tier": "complex",
        "cost_estimate": 0.0045,
        "confidence": 0.95,
        "latency_estimate_ms": 2500,
        "tenant_id": "default",
    }


@pytest.fixture
def sample_slo_metrics():
    """Sample SLO metrics."""
    return {
        "endpoint": "marketplace",
        "p99_latency_ms": 450.0,
        "error_rate_pct": 0.08,
        "circuit_breaker_open": True,
    }


@pytest.fixture
def sample_learning_event():
    """Sample learning loop event."""
    return {
        "event_type": "outcome_feedback",
        "skill_id": "os.delegation_router",
        "signal": {"correct": True, "confidence_delta": 0.05},
    }
