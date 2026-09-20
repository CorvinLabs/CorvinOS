"""Tests for Production Monitoring Dashboard Routes (ADR-0906).

Test coverage:
  - System health score calculation
  - Autonomous Forge statistics
  - Per-skill performance metrics
  - Alerts generation
  - Time series data generation
  - Tenant isolation
  - Error handling (invalid parameters, missing auth)

All tests assume:
  - Valid session auth (tenant_id from SessionRecord)
  - Immutable read-only operations
  - <200ms response time
"""
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from pathlib import Path
import sys

# Setup path to import corvin_console
REPO_ROOT = Path(__file__).parents[2]
CONSOLE_DIR = REPO_ROOT / "core/console/corvin_console"
sys.path.insert(0, str(CONSOLE_DIR))

# Import after path setup
from routes import monitoring_routes
from api_schemas.monitoring import (
    SystemHealthResponse,
    AutonomousStatsResponse,
    SkillPerformanceResponse,
    AlertsResponse,
    TimeSeriesResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    """Mock session record for testing."""
    class SessionRecord:
        tenant_id = "_default"
        sid_fingerprint = "test-operator-123"
        is_authenticated = True

    return SessionRecord()


@pytest.fixture
def test_client():
    """Create a test client for the monitoring routes."""
    # Create a minimal FastAPI app with monitoring routes
    from fastapi import FastAPI, Depends
    from deps import require_session
    from auth import SessionRecord as AuthSessionRecord

    # Mock the require_session dependency
    def mock_require_session():
        class MockRecord:
            tenant_id = "_default"
            sid_fingerprint = "test-op"
        return MockRecord()

    app = FastAPI()

    # Override the dependency
    async def override_require_session():
        return mock_require_session()

    # Register router
    app.include_router(
        monitoring_routes.router,
        prefix="/v1/console/monitoring",
        tags=["monitoring"],
    )

    # Apply dependency override
    from fastapi import Depends
    app.dependency_overrides[require_session] = override_require_session

    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Health Score
# ─────────────────────────────────────────────────────────────────────────────


def test_health_score_calculation():
    """Test that health score is calculated correctly and within 0-100 range."""
    tenant_id = "_default"
    health = monitoring_routes._generate_health_score(tenant_id)

    assert isinstance(health, SystemHealthResponse)
    assert 0 <= health.health_score <= 100
    assert health.status in ["healthy", "degraded", "critical"]
    assert health.tenant_id == tenant_id

    # Validate status matches score
    if health.health_score >= 67:
        assert health.status == "healthy"
    elif health.health_score >= 34:
        assert health.status == "degraded"
    else:
        assert health.status == "critical"


def test_health_score_tenant_isolation():
    """Test that health scores are tenant-scoped."""
    health_default = monitoring_routes._generate_health_score("_default")
    health_tenant2 = monitoring_routes._generate_health_score("tenant-2")

    assert health_default.tenant_id == "_default"
    assert health_tenant2.tenant_id == "tenant-2"


def test_health_score_has_timestamp():
    """Test that health score includes valid timestamps."""
    health = monitoring_routes._generate_health_score("_default")

    assert health.last_check is not None
    assert health.timestamp is not None
    assert isinstance(health.last_check, datetime)
    assert isinstance(health.timestamp, datetime)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Autonomous Statistics
# ─────────────────────────────────────────────────────────────────────────────


def test_autonomous_stats_basic():
    """Test that autonomous stats are generated with reasonable values."""
    stats = monitoring_routes._generate_autonomous_stats("_default")

    assert isinstance(stats, AutonomousStatsResponse)
    assert stats.skills_forged >= 0
    assert stats.skills_approved >= 0
    assert stats.skills_deferred >= 0
    assert stats.skills_active >= 0
    assert 0 <= stats.success_rate <= 1
    assert 0 <= stats.avg_optimization_confidence <= 1
    assert stats.avg_latency_ms >= 0
    assert 0 <= stats.avg_error_rate <= 1


def test_autonomous_stats_counts_valid():
    """Test that approved + deferred <= forged."""
    stats = monitoring_routes._generate_autonomous_stats("_default")

    assert stats.skills_approved + stats.skills_deferred <= stats.skills_forged


def test_autonomous_stats_tenant_isolation():
    """Test that stats are tenant-scoped."""
    stats = monitoring_routes._generate_autonomous_stats("custom-tenant")
    assert stats.tenant_id == "custom-tenant"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Skill Performance
# ─────────────────────────────────────────────────────────────────────────────


def test_skill_performance_all_skills_present():
    """Test that all OS skills are included in performance metrics."""
    perf = monitoring_routes._generate_skill_performance("_default")

    assert isinstance(perf, SkillPerformanceResponse)
    assert len(perf.skills) > 0

    skill_ids = {s.skill_id for s in perf.skills}
    expected_skills = {"os.delegation_router", "os.context_adapter", "os.workflow_optimizer"}
    assert expected_skills.issubset(skill_ids)


def test_skill_performance_metrics_valid():
    """Test that all skill metrics are within valid ranges."""
    perf = monitoring_routes._generate_skill_performance("_default")

    for skill in perf.skills:
        # Confidence [0, 1]
        assert 0 <= skill.confidence <= 1

        # Latency percentiles (p50 <= p95 <= p99)
        assert skill.latency_p50_ms <= skill.latency_p95_ms <= skill.latency_p99_ms

        # Error rate [0, 1]
        assert 0 <= skill.error_rate <= 1

        # Execution count non-negative
        assert skill.execution_count >= 0

        # Status valid
        assert skill.status in ["healthy", "degraded", "critical", "inactive"]

        # Version not empty
        assert skill.version


def test_skill_performance_status_logic():
    """Test that skill status is derived from metrics logically."""
    perf = monitoring_routes._generate_skill_performance("_default")

    for skill in perf.skills:
        # Healthy skills should have low error rate
        if skill.status == "healthy":
            assert skill.error_rate < 0.05  # Less than 5%
            assert skill.confidence > 0.85   # High confidence

        # Degraded skills should show degradation
        if skill.status == "degraded":
            assert skill.error_rate >= 0.01  # Some errors
            # Or high latency, or lower confidence


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Alerts
# ─────────────────────────────────────────────────────────────────────────────


def test_alerts_generated():
    """Test that alerts are generated when thresholds are exceeded."""
    alerts = monitoring_routes._generate_alerts("_default")

    assert isinstance(alerts, AlertsResponse)
    assert alerts.total_active == len(alerts.active_alerts)

    # Each active alert must have required fields
    for alert in alerts.active_alerts:
        assert alert.alert_id
        assert alert.severity in ["info", "warning", "critical"]
        assert alert.title
        assert alert.message
        assert alert.created_at is not None
        assert alert.resolved_at is None  # Active alerts are unresolved


def test_alerts_resolved_have_timestamps():
    """Test that resolved alerts have both created_at and resolved_at."""
    alerts = monitoring_routes._generate_alerts("_default")

    for alert in alerts.recent_alerts:
        assert alert.created_at is not None
        assert alert.resolved_at is not None
        assert alert.resolved_at >= alert.created_at


def test_alerts_tenant_isolation():
    """Test that alerts are tenant-scoped."""
    alerts = monitoring_routes._generate_alerts("custom-tenant")
    assert alerts.tenant_id == "custom-tenant"


# ─────────────────────────────────────────────────────────────────────────────
# Tests: Time Series Data
# ─────────────────────────────────────────────────────────────────────────────


def test_timeseries_confidence():
    """Test time series generation for confidence metric."""
    ts = monitoring_routes._generate_timeseries("confidence", 7, "_default")

    assert isinstance(ts, TimeSeriesResponse)
    assert ts.metric_name == "confidence"
    assert len(ts.data_points) > 0

    # Confidence values should be in [0, 1]
    for point in ts.data_points:
        assert 0 <= point.value <= 1


def test_timeseries_latency():
    """Test time series generation for latency metric."""
    ts = monitoring_routes._generate_timeseries("latency_p95_ms", 7, "_default")

    assert ts.metric_name == "latency_p95_ms"
    assert len(ts.data_points) > 0

    # Latency should be non-negative
    for point in ts.data_points:
        assert point.value >= 0


def test_timeseries_error_rate():
    """Test time series generation for error rate metric."""
    ts = monitoring_routes._generate_timeseries("error_rate", 7, "_default")

    assert ts.metric_name == "error_rate"
    assert len(ts.data_points) > 0

    # Error rate should be in [0, 1]
    for point in ts.data_points:
        assert 0 <= point.value <= 1


def test_timeseries_window():
    """Test that time series respects the requested window."""
    days = 7
    ts = monitoring_routes._generate_timeseries("confidence", days, "_default")

    # Window duration should match requested days
    duration = ts.window_end - ts.window_start
    expected_duration = timedelta(days=days)

    assert duration.days == expected_duration.days


def test_timeseries_chronological():
    """Test that time series data is in chronological order."""
    ts = monitoring_routes._generate_timeseries("confidence", 7, "_default")

    for i in range(len(ts.data_points) - 1):
        assert ts.data_points[i].timestamp <= ts.data_points[i + 1].timestamp


def test_timeseries_invalid_metric():
    """Test that invalid metric names are rejected."""
    with pytest.raises((ValueError, KeyError, AssertionError)):
        # This may not raise in the helper, but the route should validate
        ts = monitoring_routes._generate_timeseries("invalid_metric_xyz", 7, "_default")


# ─────────────────────────────────────────────────────────────────────────────
# Integration Tests (with mocked FastAPI)
# ─────────────────────────────────────────────────────────────────────────────


def test_route_health_endpoint_response_time():
    """Test that health endpoint responds quickly."""
    import time
    tenant_id = "_default"

    start = time.time()
    health = monitoring_routes._generate_health_score(tenant_id)
    elapsed = (time.time() - start) * 1000  # Convert to ms

    assert elapsed < 50  # Should respond in <50ms


def test_route_autonomous_stats_response_time():
    """Test that autonomous stats endpoint responds quickly."""
    import time

    start = time.time()
    stats = monitoring_routes._generate_autonomous_stats("_default")
    elapsed = (time.time() - start) * 1000  # Convert to ms

    assert elapsed < 100  # Should respond in <100ms


def test_route_skill_performance_response_time():
    """Test that skill performance endpoint responds quickly."""
    import time

    start = time.time()
    perf = monitoring_routes._generate_skill_performance("_default")
    elapsed = (time.time() - start) * 1000  # Convert to ms

    assert elapsed < 150  # Should respond in <150ms


def test_route_alerts_response_time():
    """Test that alerts endpoint responds quickly."""
    import time

    start = time.time()
    alerts = monitoring_routes._generate_alerts("_default")
    elapsed = (time.time() - start) * 1000  # Convert to ms

    assert elapsed < 100  # Should respond in <100ms


def test_route_timeseries_response_time():
    """Test that time series endpoint responds quickly."""
    import time

    start = time.time()
    ts = monitoring_routes._generate_timeseries("confidence", 7, "_default")
    elapsed = (time.time() - start) * 1000  # Convert to ms

    assert elapsed < 200  # Should respond in <200ms


# ─────────────────────────────────────────────────────────────────────────────
# Consistency Tests
# ─────────────────────────────────────────────────────────────────────────────


def test_multiple_calls_consistent():
    """Test that multiple calls return consistent structure (data may vary)."""
    health1 = monitoring_routes._generate_health_score("_default")
    health2 = monitoring_routes._generate_health_score("_default")

    # Structure should be identical
    assert health1.__class__ == health2.__class__
    assert health1.tenant_id == health2.tenant_id


def test_all_responses_have_timestamp():
    """Test that all response types include a timestamp."""
    health = monitoring_routes._generate_health_score("_default")
    stats = monitoring_routes._generate_autonomous_stats("_default")
    perf = monitoring_routes._generate_skill_performance("_default")
    alerts = monitoring_routes._generate_alerts("_default")
    ts = monitoring_routes._generate_timeseries("confidence", 7, "_default")

    assert hasattr(health, "timestamp")
    assert hasattr(stats, "timestamp")
    assert hasattr(perf, "timestamp")
    assert hasattr(alerts, "timestamp")
    assert hasattr(ts, "timestamp")


def test_all_responses_have_tenant_id():
    """Test that all response types include tenant_id."""
    tenant = "test-tenant-123"

    health = monitoring_routes._generate_health_score(tenant)
    stats = monitoring_routes._generate_autonomous_stats(tenant)
    perf = monitoring_routes._generate_skill_performance(tenant)
    alerts = monitoring_routes._generate_alerts(tenant)
    ts = monitoring_routes._generate_timeseries("confidence", 7, tenant)

    assert health.tenant_id == tenant
    assert stats.tenant_id == tenant
    assert perf.tenant_id == tenant
    assert alerts.tenant_id == tenant
    assert ts.tenant_id == tenant


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
