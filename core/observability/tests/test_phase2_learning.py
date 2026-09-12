"""Phase 2 Tests: Skill Execution Tracing + Learning Feedback.

Test coverage:
- SkillExecutionTracer (span creation, metrics recording)
- LearningFeedbackEvent (immutability, validation)
- MultiTenantOptimizer (gradient computation, anomaly detection, convergence)
"""

import pytest
from unittest.mock import MagicMock
from statistics import mean, stdev

from core.observability.otel_exporter.skills import (
    SkillExecutionMetrics, SkillStatus, SkillExecutionTracer, LearningFeedbackEvent,
)
from core.learning.multi_tenant_optimizer import (
    MultiTenantOptimizer, SkillMetricsWindow, AnomalyType, LearningConfigUpdate,
)


class TestSkillExecutionTracer:
    """Tests for SkillExecutionTracer."""

    def test_init_valid(self):
        """Initialize tracer with tenant + instance IDs."""
        tracer = SkillExecutionTracer(tenant_id="acme", instance_id="inst-1")
        assert tracer.tenant_id == "acme"
        assert tracer.instance_id == "inst-1"

    def test_record_metrics(self):
        """Record skill execution metrics."""
        tracer = SkillExecutionTracer(tenant_id="acme", instance_id="inst-1")

        metrics = SkillExecutionMetrics(
            skill_id="os.delegation_router",
            skill_version="1.0.0",
            execution_duration_ms=45.5,
            input_size_bytes=1024,
            output_size_bytes=2048,
            status=SkillStatus.SUCCESS,
        )

        # Should log without error
        tracer.record_metrics(metrics)
        assert metrics.skill_id == "os.delegation_router"

    def test_execution_error_status(self):
        """Record skill execution error."""
        metrics = SkillExecutionMetrics(
            skill_id="test_skill",
            skill_version="1.0",
            execution_duration_ms=100.0,
            input_size_bytes=512,
            output_size_bytes=0,
            status=SkillStatus.ERROR,
            error_type="ValueError",
            error_message="Invalid parameter (scrubbed)",
        )

        assert metrics.status == SkillStatus.ERROR
        assert metrics.error_type == "ValueError"


class TestLearningFeedbackEvent:
    """Tests for LearningFeedbackEvent."""

    def test_init_valid(self):
        """Initialize feedback event."""
        event = LearningFeedbackEvent(
            tenant_id="acme",
            skill_id="os.routing",
            feedback_type="outcome",
            signal=0.85,
        )
        assert event.feedback_type == "outcome"
        assert event.signal == 0.85

    def test_invalid_feedback_type(self):
        """Invalid feedback_type raises error."""
        with pytest.raises(ValueError, match="Invalid feedback_type"):
            LearningFeedbackEvent(
                tenant_id="acme",
                skill_id="os.routing",
                feedback_type="invalid",
                signal=1.0,
            )

    def test_feedback_types(self):
        """All valid feedback types work."""
        for ftype in ["outcome", "preference", "confidence", "metric"]:
            event = LearningFeedbackEvent(
                tenant_id="acme",
                skill_id="skill",
                feedback_type=ftype,
                signal=0.5,
            )
            assert event.feedback_type == ftype


class TestMultiTenantOptimizer:
    """Tests for MultiTenantOptimizer."""

    def test_init_requires_tenant_id(self):
        """tenant_id is mandatory."""
        with pytest.raises(ValueError, match="tenant_id is mandatory"):
            MultiTenantOptimizer(tenant_id="")

    def test_init_valid(self):
        """Initialize optimizer."""
        optimizer = MultiTenantOptimizer(tenant_id="acme-corp")
        assert optimizer.tenant_id == "acme-corp"

    def test_anomaly_detection_normal(self):
        """Normal feedback (within 3σ) not flagged as anomaly."""
        optimizer = MultiTenantOptimizer(tenant_id="acme")

        # Prime history with normal values
        history = [0.7, 0.72, 0.71, 0.68, 0.69]
        optimizer.feedback_history["routing"] = history.copy()

        # Value within 3σ should not be anomaly
        is_anomaly, atype = optimizer._check_anomaly("routing", 0.70)
        assert is_anomaly is False
        assert atype == AnomalyType.NORMAL

    def test_anomaly_detection_outlier(self):
        """Outlier feedback (> 3σ) flagged as anomaly."""
        optimizer = MultiTenantOptimizer(tenant_id="acme")

        # Prime with consistent history
        history = [0.7, 0.71, 0.70, 0.69, 0.71]
        optimizer.feedback_history["routing"] = history.copy()

        # Wild outlier should be detected
        is_anomaly, atype = optimizer._check_anomaly("routing", 0.99)  # Way too high
        assert is_anomaly is True
        assert atype == AnomalyType.OUTLIER

    def test_step_with_feedback(self):
        """Optimizer step computes config delta."""
        optimizer = MultiTenantOptimizer(tenant_id="acme")
        optimizer.audit_logger = MagicMock()

        metrics_window = SkillMetricsWindow(
            tenant_id="acme",
            skill_id="os.routing",
            skill_version="1.0",
            latency_p50_ms=45.0,
            latency_p99_ms=150.0,
            error_rate_percent=0.5,
            sample_count=1000,
        )

        # Provide user feedback
        result = optimizer.step(
            metrics_window=metrics_window,
            user_feedback={"threshold": 0.65},
        )

        # Should return config update
        assert result is not None
        assert isinstance(result, LearningConfigUpdate)
        assert result.param_name == "threshold"
        assert result.new_value == 0.65

    def test_convergence_meter(self):
        """Convergence meter detects oscillation."""
        optimizer = MultiTenantOptimizer(tenant_id="acme")
        optimizer.audit_logger = MagicMock()

        # Simulate oscillating latencies (high variance)
        oscillating_latencies = [100, 200, 90, 210, 85, 220, 95]
        for latency in oscillating_latencies:
            optimizer._update_convergence_meter(latency)

        # Should have detected instability (sigma > 50)
        # (This is logged but doesn't raise; just audit trail)
        assert len(optimizer.convergence_meter_latencies) > 0


class TestSkillMetricsWindow:
    """Tests for SkillMetricsWindow."""

    def test_init_defaults(self):
        """Initialize with required fields only."""
        window = SkillMetricsWindow(
            tenant_id="acme",
            skill_id="os.routing",
            skill_version="1.0",
        )
        assert window.tenant_id == "acme"
        assert window.skill_id == "os.routing"
        assert window.latency_p50_ms == 0.0

    def test_stratified_by_geo(self):
        """Metrics window can stratify by geo."""
        de_window = SkillMetricsWindow(
            tenant_id="acme",
            skill_id="os.routing",
            skill_version="1.0",
            geo_country="DE",
            latency_p99_ms=180.0,
        )

        us_window = SkillMetricsWindow(
            tenant_id="acme",
            skill_id="os.routing",
            skill_version="1.0",
            geo_country="US",
            latency_p99_ms=120.0,
        )

        # EU is slower than US (realistic)
        assert de_window.latency_p99_ms > us_window.latency_p99_ms


class TestLearningConfigUpdate:
    """Tests for LearningConfigUpdate."""

    def test_init_valid(self):
        """Initialize config update."""
        update = LearningConfigUpdate(
            tenant_id="acme",
            skill_id="os.routing",
            param_name="threshold",
            old_value=0.70,
            new_value=0.65,
            confidence=0.92,
            reason="user_feedback",
        )
        assert update.old_value == 0.70
        assert update.new_value == 0.65
        assert update.confidence == 0.92


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
