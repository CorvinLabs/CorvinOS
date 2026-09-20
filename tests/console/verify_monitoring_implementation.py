#!/usr/bin/env python3
"""Verification script for Production Monitoring Dashboard (ADR-0906).

This script validates the implementation without pytest, testing:
  1. API schema definitions
  2. Route helper functions
  3. Response data validity
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta

# Setup paths
REPO_ROOT = Path(__file__).parents[2]
CONSOLE_DIR = REPO_ROOT / "core/console/corvin_console"
sys.path.insert(0, str(CONSOLE_DIR))

# Import components
try:
    from api_schemas.monitoring import (
        SystemHealthResponse,
        AutonomousStatsResponse,
        SkillPerformanceResponse,
        SkillPerformanceMetric,
        AlertsResponse,
        TimeSeriesResponse,
        TimeSeriesDataPoint,
    )
    from routes import monitoring_routes
    print("✅ Imports successful")
except ImportError as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)


def test_health_score_calculation():
    """Test health score generation."""
    print("\n[1] Testing health score calculation...")
    try:
        health = monitoring_routes._generate_health_score("_default")

        # Validate structure
        assert isinstance(health, SystemHealthResponse)
        assert 0 <= health.health_score <= 100
        assert health.status in ["healthy", "degraded", "critical"]
        assert health.tenant_id == "_default"
        assert health.uptime_hours > 0
        assert health.timestamp is not None

        # Validate status matches score
        if health.health_score >= 67:
            assert health.status == "healthy", f"Score {health.health_score} should be healthy"
        elif health.health_score >= 34:
            assert health.status == "degraded", f"Score {health.health_score} should be degraded"
        else:
            assert health.status == "critical", f"Score {health.health_score} should be critical"

        print(f"  ✅ Health score: {health.health_score} ({health.status})")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_autonomous_stats():
    """Test autonomous statistics."""
    print("\n[2] Testing autonomous stats...")
    try:
        stats = monitoring_routes._generate_autonomous_stats("_default")

        # Validate structure
        assert isinstance(stats, AutonomousStatsResponse)
        assert stats.skills_forged >= 0
        assert stats.skills_approved >= 0
        assert stats.skills_deferred >= 0
        assert 0 <= stats.success_rate <= 1
        assert 0 <= stats.avg_optimization_confidence <= 1
        assert stats.avg_latency_ms >= 0
        assert 0 <= stats.avg_error_rate <= 1
        assert stats.tenant_id == "_default"

        # Validate counts
        assert stats.skills_approved + stats.skills_deferred <= stats.skills_forged

        print(f"  ✅ Skills forged: {stats.skills_forged}, Success rate: {stats.success_rate*100:.1f}%")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_skill_performance():
    """Test per-skill performance metrics."""
    print("\n[3] Testing skill performance...")
    try:
        perf = monitoring_routes._generate_skill_performance("_default")

        # Validate structure
        assert isinstance(perf, SkillPerformanceResponse)
        assert len(perf.skills) > 0
        assert perf.tenant_id == "_default"

        # Validate each skill
        for skill in perf.skills:
            assert 0 <= skill.confidence <= 1
            assert skill.latency_p50_ms <= skill.latency_p95_ms <= skill.latency_p99_ms
            assert 0 <= skill.error_rate <= 1
            assert skill.execution_count >= 0
            assert skill.status in ["healthy", "degraded", "critical", "inactive"]
            assert skill.version

        print(f"  ✅ {len(perf.skills)} skills with valid metrics")

        # Validate status logic
        for skill in perf.skills:
            if skill.status == "healthy":
                assert skill.error_rate < 0.05, f"{skill.skill_id} healthy but error_rate={skill.error_rate}"
                assert skill.confidence > 0.85, f"{skill.skill_id} healthy but confidence={skill.confidence}"

        print(f"  ✅ Status logic validated")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_alerts():
    """Test alert generation."""
    print("\n[4] Testing alerts...")
    try:
        alerts = monitoring_routes._generate_alerts("_default")

        # Validate structure
        assert isinstance(alerts, AlertsResponse)
        assert alerts.total_active == len(alerts.active_alerts)
        assert alerts.tenant_id == "_default"

        # Validate active alerts
        for alert in alerts.active_alerts:
            assert alert.alert_id
            assert alert.severity in ["info", "warning", "critical"]
            assert alert.title
            assert alert.message
            assert alert.created_at is not None
            assert alert.resolved_at is None

        # Validate resolved alerts
        for alert in alerts.recent_alerts:
            assert alert.resolved_at is not None
            assert alert.resolved_at >= alert.created_at

        print(f"  ✅ {alerts.total_active} active alerts, {len(alerts.recent_alerts)} recent")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_timeseries():
    """Test time series data."""
    print("\n[5] Testing time series data...")
    try:
        # Test confidence metric
        ts_conf = monitoring_routes._generate_timeseries("confidence", 7, "_default")
        assert isinstance(ts_conf, TimeSeriesResponse)
        assert ts_conf.metric_name == "confidence"
        assert len(ts_conf.data_points) > 0
        assert ts_conf.tenant_id == "_default"

        # Validate confidence values
        for point in ts_conf.data_points:
            assert 0 <= point.value <= 1
            assert point.timestamp is not None

        # Validate chronological order
        for i in range(len(ts_conf.data_points) - 1):
            assert ts_conf.data_points[i].timestamp <= ts_conf.data_points[i + 1].timestamp

        print(f"  ✅ Confidence: {len(ts_conf.data_points)} points, trend valid")

        # Test latency metric
        ts_lat = monitoring_routes._generate_timeseries("latency_p95_ms", 7, "_default")
        assert ts_lat.metric_name == "latency_p95_ms"
        for point in ts_lat.data_points:
            assert point.value >= 0

        print(f"  ✅ Latency: {len(ts_lat.data_points)} points")

        # Test error rate metric
        ts_err = monitoring_routes._generate_timeseries("error_rate", 7, "_default")
        assert ts_err.metric_name == "error_rate"
        for point in ts_err.data_points:
            assert 0 <= point.value <= 1

        print(f"  ✅ Error rate: {len(ts_err.data_points)} points")

        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_tenant_isolation():
    """Test tenant isolation."""
    print("\n[6] Testing tenant isolation...")
    try:
        custom_tenant = "custom-tenant-xyz"

        health = monitoring_routes._generate_health_score(custom_tenant)
        stats = monitoring_routes._generate_autonomous_stats(custom_tenant)
        perf = monitoring_routes._generate_skill_performance(custom_tenant)
        alerts = monitoring_routes._generate_alerts(custom_tenant)
        ts = monitoring_routes._generate_timeseries("confidence", 7, custom_tenant)

        assert health.tenant_id == custom_tenant
        assert stats.tenant_id == custom_tenant
        assert perf.tenant_id == custom_tenant
        assert alerts.tenant_id == custom_tenant
        assert ts.tenant_id == custom_tenant

        print(f"  ✅ All endpoints respect tenant_id")
        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def test_response_times():
    """Test response times."""
    print("\n[7] Testing response times...")
    try:
        import time

        # Health score should be <50ms
        start = time.time()
        monitoring_routes._generate_health_score("_default")
        elapsed = (time.time() - start) * 1000
        assert elapsed < 50, f"Health score took {elapsed:.1f}ms (expected <50ms)"
        print(f"  ✅ Health score: {elapsed:.1f}ms")

        # Autonomous stats should be <100ms
        start = time.time()
        monitoring_routes._generate_autonomous_stats("_default")
        elapsed = (time.time() - start) * 1000
        assert elapsed < 100, f"Autonomous stats took {elapsed:.1f}ms (expected <100ms)"
        print(f"  ✅ Autonomous stats: {elapsed:.1f}ms")

        # Skill performance should be <150ms
        start = time.time()
        monitoring_routes._generate_skill_performance("_default")
        elapsed = (time.time() - start) * 1000
        assert elapsed < 150, f"Skill performance took {elapsed:.1f}ms (expected <150ms)"
        print(f"  ✅ Skill performance: {elapsed:.1f}ms")

        # Alerts should be <100ms
        start = time.time()
        monitoring_routes._generate_alerts("_default")
        elapsed = (time.time() - start) * 1000
        assert elapsed < 100, f"Alerts took {elapsed:.1f}ms (expected <100ms)"
        print(f"  ✅ Alerts: {elapsed:.1f}ms")

        # Time series should be <200ms
        start = time.time()
        monitoring_routes._generate_timeseries("confidence", 7, "_default")
        elapsed = (time.time() - start) * 1000
        assert elapsed < 200, f"Time series took {elapsed:.1f}ms (expected <200ms)"
        print(f"  ✅ Time series: {elapsed:.1f}ms")

        return True
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def main():
    """Run all verification tests."""
    print("=" * 60)
    print("Production Monitoring Dashboard (ADR-0906) — Verification")
    print("=" * 60)

    tests = [
        test_health_score_calculation,
        test_autonomous_stats,
        test_skill_performance,
        test_alerts,
        test_timeseries,
        test_tenant_isolation,
        test_response_times,
    ]

    results = []
    for test in tests:
        results.append(test())

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")
    print("=" * 60)

    if passed == total:
        print("✅ All verification tests passed!")
        return 0
    else:
        print(f"❌ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
