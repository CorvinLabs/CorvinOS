"""Unit tests for ApprovalMetrics (Task 3).

Tests cover:
1. Metrics collection (queue depth, latency, decision rates)
2. Metric aggregation (avg, percentiles, percentages)
3. JSON export (API response format)
4. Prometheus export
5. Edge cases (empty metrics, small samples)
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

from core.learning.approval_metrics import (
    ApprovalMetricsCollector,
    ApprovalMetricsExporter,
    ApprovalMetrics,
    initialize_approval_metrics,
    get_approval_metrics_exporter,
    get_approval_metrics,
)
from core.skills.feedback_stability import OperatorApprovalGate, ApprovalDecision


@pytest.fixture
def mock_audit_backend():
    """Mock audit backend."""
    backend = Mock()
    backend.write_event = Mock(return_value="event_id")
    return backend


@pytest.fixture
def approval_gate(mock_audit_backend):
    """Create OperatorApprovalGate."""
    return OperatorApprovalGate(
        tenant_id="_default",
        auto_approval_confidence_threshold=0.8,
        audit_backend=mock_audit_backend,
    )


@pytest.fixture
def metrics_collector(approval_gate):
    """Create ApprovalMetricsCollector."""
    return ApprovalMetricsCollector(approval_gate, window_hours=24)


@pytest.fixture
def metrics_exporter(metrics_collector):
    """Create ApprovalMetricsExporter."""
    return ApprovalMetricsExporter(metrics_collector)


# ============================================================================
# Task 3.1: Queue Depth Tracking
# ============================================================================


class TestQueueDepthMetrics:
    """Test approval queue depth metrics."""

    def test_queue_depth_empty(self, metrics_collector):
        """Test metrics when no pending approvals."""
        metrics = metrics_collector.compute_metrics()
        assert metrics.total_pending == 0
        assert len(metrics.pending_count_by_skill) == 0

    def test_queue_depth_tracking(self, approval_gate, metrics_collector):
        """Test queue depth increases with pending approvals."""
        # Manually create pending approval in gate
        from core.skills.feedback_stability import DriftAlert, ScrubbedDriftAlert, ApprovalReasonCode

        drift_alert = DriftAlert(
            skill_id="test.skill_1",
            metric_name="metric_1",
            smoothed_delta=0.25,
            drift_threshold=0.15,
            consecutive_high_deltas=2,
        )

        record, _ = approval_gate.request_approval(
            drift_alert=drift_alert,
            confidence=0.5,  # Pending
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # Compute metrics
        metrics = metrics_collector.compute_metrics()

        # Should show 1 pending for test.skill_1
        assert metrics.total_pending >= 1 or "test.skill_1" in metrics.pending_count_by_skill

    def test_queue_depth_by_skill(self, approval_gate, metrics_collector):
        """Test queue depth breakdown by skill."""
        from core.skills.feedback_stability import DriftAlert

        # Create pending for skill_1
        drift_alert_1 = DriftAlert(
            skill_id="test.skill_1",
            metric_name="metric_1",
            smoothed_delta=0.25,
            drift_threshold=0.15,
            consecutive_high_deltas=2,
        )

        approval_gate.request_approval(
            drift_alert=drift_alert_1,
            confidence=0.5,
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # Create pending for skill_2
        drift_alert_2 = DriftAlert(
            skill_id="test.skill_2",
            metric_name="metric_2",
            smoothed_delta=0.25,
            drift_threshold=0.15,
            consecutive_high_deltas=2,
        )

        approval_gate.request_approval(
            drift_alert=drift_alert_2,
            confidence=0.5,
            prev_config_hash="a" * 64,
            next_config_hash="b" * 64,
        )

        # Metrics should show breakdown
        metrics = metrics_collector.compute_metrics()
        assert metrics.total_pending >= 2


# ============================================================================
# Task 3.2: Latency Tracking
# ============================================================================


class TestLatencyMetrics:
    """Test approval latency metrics."""

    def test_latency_empty(self, metrics_collector):
        """Test latency metrics when no approvals."""
        metrics = metrics_collector.compute_metrics()
        assert metrics.avg_latency_ms is None
        assert len(metrics.approval_latencies_ms) == 0

    def test_latency_collection(self, metrics_collector):
        """Test collecting approval latencies."""
        # Record some latencies
        metrics_collector.record_approval("app_1", "skill_1", latency_ms=100.0)
        metrics_collector.record_approval("app_2", "skill_1", latency_ms=200.0)
        metrics_collector.record_approval("app_3", "skill_1", latency_ms=150.0)

        metrics = metrics_collector.compute_metrics()

        assert metrics.avg_latency_ms == 150.0
        assert metrics.p50_latency_ms == 150.0

    def test_latency_percentiles(self, metrics_collector):
        """Test percentile calculations."""
        # Record latencies
        for i in range(100):
            metrics_collector.record_approval(f"app_{i}", "skill_1", latency_ms=float(i + 1))

        metrics = metrics_collector.compute_metrics()

        assert metrics.avg_latency_ms is not None
        assert metrics.p50_latency_ms is not None
        assert metrics.p95_latency_ms is not None
        assert metrics.p50_latency_ms <= metrics.p95_latency_ms


# ============================================================================
# Task 3.3: Decision Rate Metrics
# ============================================================================


class TestDecisionRateMetrics:
    """Test approval decision rate metrics."""

    def test_auto_approval_tracking(self, metrics_collector):
        """Test auto-approval rate tracking."""
        # Record some auto-approvals
        metrics_collector.record_approval_request("app_1", "skill_1", confidence=0.9, auto_approved=True)
        metrics_collector.record_approval_request("app_2", "skill_1", confidence=0.85, auto_approved=True)
        metrics_collector.record_approval_request("app_3", "skill_1", confidence=0.5, auto_approved=False)

        # Record corresponding manual approvals (for non-auto)
        metrics_collector.record_approval("app_3", "skill_1", latency_ms=50.0)

        metrics = metrics_collector.compute_metrics()

        # 2 auto-approved + 1 manual approval = 3 total decisions
        assert metrics.auto_approved_count == 2
        assert metrics.manual_approved_count == 1
        # 2 / (2 + 1) = 66.67%
        assert metrics.auto_approved_pct == pytest.approx(66.67, rel=0.1)

    def test_rejection_tracking(self, metrics_collector):
        """Test rejection tracking."""
        metrics_collector.record_approval_request("app_1", "skill_1", confidence=0.5, auto_approved=False)
        metrics_collector.record_rejection("app_1", "skill_1")

        metrics = metrics_collector.compute_metrics()

        assert metrics.rejected_count == 1
        assert metrics.rejected_pct == pytest.approx(100.0, rel=0.1)

    def test_revoke_tracking(self, metrics_collector):
        """Test revoke tracking."""
        metrics_collector.record_approval_request("app_1", "skill_1", confidence=0.85, auto_approved=True)
        metrics_collector.record_approval("app_1", "skill_1", latency_ms=50.0)
        metrics_collector.record_revoke("app_1", "skill_1")

        metrics = metrics_collector.compute_metrics()

        assert metrics.revoked_count == 1


# ============================================================================
# Task 3.4: Config Apply Success Rate
# ============================================================================


class TestConfigApplyMetrics:
    """Test config apply success rate metrics."""

    def test_config_apply_success_rate(self, metrics_collector):
        """Test config apply success rate."""
        # Record successes
        metrics_collector.record_config_apply("app_1", "skill_1", success=True)
        metrics_collector.record_config_apply("app_2", "skill_1", success=True)

        # Record failures
        metrics_collector.record_config_apply("app_3", "skill_1", success=False, error="Apply failed")
        metrics_collector.record_config_apply("app_4", "skill_1", success=False, error="Timeout")

        metrics = metrics_collector.compute_metrics()

        assert metrics.config_apply_success_count == 2
        assert metrics.config_apply_failure_count == 2
        assert metrics.config_apply_success_pct == pytest.approx(50.0, rel=0.1)

    def test_config_apply_error_tracking(self, metrics_collector):
        """Test error tracking in config apply."""
        metrics_collector.record_config_apply(
            "app_1", "skill_1", success=False, error="Validation failed"
        )

        # Errors are tracked (could be used for alerting)
        assert len(metrics_collector.config_applies) == 1
        assert "Validation failed" in metrics_collector.config_applies[0]["error"]


# ============================================================================
# Task 3.5: JSON Export (API Response)
# ============================================================================


class TestJSONExport:
    """Test JSON export for API responses."""

    def test_export_basic_structure(self, metrics_exporter):
        """Test JSON export has required structure."""
        json_data = metrics_exporter.export_as_json()

        # Check top-level keys
        assert "snapshot_timestamp" in json_data
        assert "approval_queue" in json_data
        assert "approval_latency" in json_data
        assert "decisions" in json_data
        assert "config_apply" in json_data

    def test_export_with_data(self, metrics_collector, metrics_exporter):
        """Test JSON export with actual data."""
        # Record some metrics
        metrics_collector.record_approval_request("app_1", "skill_1", confidence=0.9, auto_approved=True)
        metrics_collector.record_approval("app_1", "skill_1", latency_ms=100.0)
        metrics_collector.record_config_apply("app_1", "skill_1", success=True)

        json_data = metrics_exporter.export_as_json()

        # Verify structure
        assert json_data["decisions"]["auto_approved"]["count"] == 1
        assert json_data["approval_latency"]["avg_ms"] is not None
        assert json_data["config_apply"]["success"]["percent"] is not None

    def test_export_empty_metrics(self, metrics_exporter):
        """Test JSON export with no data."""
        json_data = metrics_exporter.export_as_json()

        # Should not crash, should have defaults
        assert json_data["approval_queue"]["total_pending"] == 0
        assert json_data["approval_latency"]["avg_ms"] is None


# ============================================================================
# Task 3.6: Prometheus Export
# ============================================================================


class TestPrometheusExport:
    """Test Prometheus format export."""

    def test_prometheus_format(self, metrics_collector, metrics_exporter):
        """Test Prometheus text format export."""
        # Record some metrics
        metrics_collector.record_approval_request("app_1", "skill_1", confidence=0.85, auto_approved=True)
        metrics_collector.record_approval("app_1", "skill_1", latency_ms=100.0)

        prometheus_lines = metrics_exporter.export_to_prometheus()

        # Should be list of metric lines
        assert isinstance(prometheus_lines, list)
        assert len(prometheus_lines) > 0

        # Should have queue metric
        queue_line = [l for l in prometheus_lines if "queue_pending_total" in l]
        assert len(queue_line) > 0

        # Should have latency metric
        latency_line = [l for l in prometheus_lines if "latency_avg_ms" in l]
        assert len(latency_line) > 0

    def test_prometheus_gauge_format(self, metrics_collector, metrics_exporter):
        """Test Prometheus gauge format is valid."""
        metrics_collector.record_approval("app_1", "skill_1", latency_ms=123.45)

        prometheus_lines = metrics_exporter.export_to_prometheus()

        # Check format: metric_name value or metric_name{labels} value
        for line in prometheus_lines:
            parts = line.split()
            assert len(parts) >= 2  # metric_name and value


# ============================================================================
# Task 3.7: Windowing (Old Data Pruning)
# ============================================================================


class TestMetricsWindowing:
    """Test time-window pruning of old data."""

    def test_old_data_pruned(self, metrics_collector):
        """Test that old data is pruned."""
        # Record a metric with old timestamp (manually)
        old_time = datetime.utcnow() - timedelta(hours=25)
        metrics_collector.approvals.append({
            "approval_id": "old_app",
            "skill_id": "skill_1",
            "latency_ms": 100.0,
            "timestamp": old_time,
        })

        # Record recent metric
        metrics_collector.record_approval("recent_app", "skill_1", latency_ms=200.0)

        # Compute metrics (should prune old)
        metrics = metrics_collector.compute_metrics()

        # Old data should be gone
        assert "old_app" not in [e["approval_id"] for e in metrics_collector.approvals]
        assert len(metrics_collector.approvals) == 1

    def test_recent_data_kept(self, metrics_collector):
        """Test that recent data is kept."""
        # Record metric with recent timestamp
        recent_time = datetime.utcnow() - timedelta(hours=1)
        metrics_collector.approvals.append({
            "approval_id": "recent_app",
            "skill_id": "skill_1",
            "latency_ms": 100.0,
            "timestamp": recent_time,
        })

        metrics = metrics_collector.compute_metrics()

        # Recent data should still be there
        assert len(metrics_collector.approvals) == 1


# ============================================================================
# Task 3.8: Global Initialization
# ============================================================================


class TestGlobalInitialization:
    """Test global metrics system initialization."""

    def test_initialize_metrics(self, approval_gate):
        """Test metrics system initialization."""
        exporter = initialize_approval_metrics(approval_gate)

        assert exporter is not None
        assert isinstance(exporter, ApprovalMetricsExporter)

    def test_get_metrics_exporter(self, approval_gate):
        """Test getting global metrics exporter."""
        initialize_approval_metrics(approval_gate)

        exporter = get_approval_metrics_exporter()
        assert exporter is not None

    def test_get_metrics_json(self, approval_gate):
        """Test getting metrics as JSON."""
        initialize_approval_metrics(approval_gate)

        metrics_json = get_approval_metrics()
        assert metrics_json is not None
        assert isinstance(metrics_json, dict)
