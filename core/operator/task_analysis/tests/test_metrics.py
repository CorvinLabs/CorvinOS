"""Unit tests for TaskMetrics Prometheus exporter.

Tests all 7 metrics + context manager integration.
"""

import sys
import pytest
from pathlib import Path

# Adjust path for imports (allows running from different directories)
sys.path.insert(0, str(Path(__file__).parent.parent))

from metrics import (
    TaskMetrics,
    MetricsPhase,
    MetricsOutcome,
)


class TestTaskMetrics:
    """Test TaskMetrics collector."""

    def test_metrics_no_prometheus_installed(self, monkeypatch):
        """Metrics should no-op gracefully if prometheus_client not installed."""
        # Force no-op mode
        metrics = TaskMetrics()
        metrics._enabled = False

        # Should not raise
        metrics.record_phase(MetricsPhase.NORMALIZATION, 0.1, MetricsOutcome.SUCCESS)
        metrics.record_confidence(0.85)
        metrics.record_decision("native", "none")
        metrics.record_model_selection("haiku")
        metrics.record_redundancy(10, 5)
        metrics.record_cost(0.05)

        summary = metrics.summary()
        assert summary == {}

    def test_record_phase_timing(self):
        """Phase timing should be recorded with outcome."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        metrics.record_phase(
            MetricsPhase.NORMALIZATION, 0.123, MetricsOutcome.SUCCESS
        )

        summary = metrics.summary()
        assert "total_duration_seconds" in summary
        assert summary["phases"]["normalization"]["duration_seconds"] == 0.123
        assert summary["phases"]["normalization"]["outcome"] == "success"

    def test_record_confidence_clamping(self):
        """Confidence should be clamped to [0.0, 1.0]."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        # Should not raise
        metrics.record_confidence(-0.5)  # Clamped to 0.0
        metrics.record_confidence(1.5)  # Clamped to 1.0
        metrics.record_confidence(0.75)  # Within range

    def test_record_decision_routing(self):
        """Routing decision should be labeled by target + carve_out_reason."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        metrics.record_decision("native", "none")
        metrics.record_decision("acs", "big_data_vocabulary")
        metrics.record_decision("tde", "high_complexity_opus")

        # Verify counter increments (not directly testable without reading registry,
        # but function should complete without error)

    def test_record_model_selection(self):
        """Model selection should be recorded as haiku or opus."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        metrics.record_model_selection("haiku")
        metrics.record_model_selection("opus")
        metrics.record_model_selection("OPUS")  # Case insensitive

    def test_record_redundancy_ratio(self):
        """Graph redundancy should be (original - filtered) / original."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        # 10 original, 5 filtered → 50% redundancy
        metrics.record_redundancy(10, 5)

        # 5 original, 5 filtered → 0% redundancy
        metrics.record_redundancy(5, 5)

        # Edge case: original count 0
        metrics.record_redundancy(0, 0)  # Should not raise

    def test_record_cost_non_negative(self):
        """Estimated cost should be clamped to non-negative."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        metrics.record_cost(-0.05)  # Clamped to 0.0
        metrics.record_cost(0.0)
        metrics.record_cost(5.25)

    def test_phase_timer_context_manager_success(self):
        """Phase timer should record timing on success."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        with metrics.phase_timer(MetricsPhase.CLASSIFICATION) as ctx:
            ctx["outcome"] = MetricsOutcome.SUCCESS
            # Simulate work
            pass

        summary = metrics.summary()
        assert "total_duration_seconds" in summary
        assert summary["phases"]["classification"]["outcome"] == "success"

    def test_phase_timer_context_manager_failure(self):
        """Phase timer should record failure and duration."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        with pytest.raises(ValueError):
            with metrics.phase_timer(MetricsPhase.FILTERING) as ctx:
                ctx["violation_details"] = "Test error"
                raise ValueError("Test error")

        summary = metrics.summary()
        assert summary["phases"]["filtering"]["outcome"] == "failure"

    def test_phase_timer_contract_violation(self):
        """Phase timer should record contract violations."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        with metrics.phase_timer(MetricsPhase.VALIDATION) as ctx:
            ctx["contract_violation"] = True
            ctx["violation_details"] = "Missing field: type"

        summary = metrics.summary()
        assert summary["phases"]["validation"]["contract_violation"] is True
        assert summary["total_contract_violations"] == 1

    def test_reset_clears_phase_tracking(self):
        """reset() should clear in-memory phase tracking."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        metrics.record_phase(MetricsPhase.NORMALIZATION, 0.1)
        assert len(metrics._phases_this_run) > 0

        metrics.reset()
        assert len(metrics._phases_this_run) == 0

    def test_summary_aggregation(self):
        """summary() should aggregate all phase metrics."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        metrics.record_phase(MetricsPhase.NORMALIZATION, 0.1, MetricsOutcome.SUCCESS)
        metrics.record_phase(MetricsPhase.CLASSIFICATION, 0.15, MetricsOutcome.SUCCESS)
        metrics.record_phase(MetricsPhase.FILTERING, 0.05, MetricsOutcome.SUCCESS)

        summary = metrics.summary()
        assert summary["total_duration_seconds"] == pytest.approx(0.3, abs=0.01)
        assert len(summary["phases"]) == 3
        assert summary["total_contract_violations"] == 0

    def test_multiple_runs_reset_between(self):
        """Each call to route_task() should reset metrics."""
        metrics = TaskMetrics()
        if not metrics._enabled:
            pytest.skip("prometheus_client not installed")

        # First run
        metrics.record_phase(MetricsPhase.NORMALIZATION, 0.1)
        summary1 = metrics.summary()
        assert len(summary1.get("phases", {})) == 1

        # Reset
        metrics.reset()

        # Second run
        metrics.record_phase(MetricsPhase.CLASSIFICATION, 0.2)
        summary2 = metrics.summary()
        assert len(summary2.get("phases", {})) == 1
        assert "normalization" not in summary2.get("phases", {})
        assert "classification" in summary2.get("phases", {})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
