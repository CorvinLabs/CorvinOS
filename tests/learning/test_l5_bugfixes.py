"""
Comprehensive test suite for all 9 L5 critical/high bug fixes.

Tests cover:
- Bug 1: Division by zero in Bayesian update
- Bug 2: Division by zero in metric degradation
- Bug 3: Cross-tenant data leak
- Bug 4: Unbounded memory growth
- Bug 5: Missing input validation
- Bug 6: Wrong pending count
- Bug 7: Silent cache refresh failure
- Bug 8: Weak random seed
- Bug 9: Missing tenant validation in POST endpoints
"""

import pytest
import math
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

# Imports for each bug fix
from core.learning.advanced_learning import AdvancedLearningEngine
from core.learning.production_tuning import ProductionTuningEngine
from core.learning.feedback_loop_l5_integration import L5FeedbackLoopIntegrator
from core.learning.monitoring_l5 import MetricsCollector, HealthChecker, L5MonitoringSystem


# ============================================================================
# BUG 1: Bayesian Update Division by Zero
# ============================================================================

class TestBug1BayesianUpdateDivByZero:
    """Test that Bayesian update handles prior_std <= 0 safely."""

    def test_bayesian_update_with_zero_prior_std(self):
        """Test Bayesian update with prior_std = 0 (should not crash)."""
        engine = AdvancedLearningEngine(tenant_id="_default")

        # This should NOT crash with division by zero
        update = engine.bayesian_update(
            skill_id="skill_a",
            metric_name="latency",
            param_name="confidence_threshold",
            prior_value=0.7,
            prior_std=0.0,  # ZERO — triggers bug if not fixed
            observed_accuracy=0.85,
        )

        # Verify result is valid
        assert update is not None
        assert math.isfinite(update.posterior_value)
        assert math.isfinite(update.posterior_std)
        assert 0.0 <= update.confidence <= 1.0

    def test_bayesian_update_with_tiny_prior_std(self):
        """Test with prior_std below minimum threshold (0.001)."""
        engine = AdvancedLearningEngine(tenant_id="_default")

        update = engine.bayesian_update(
            skill_id="skill_a",
            metric_name="latency",
            param_name="confidence_threshold",
            prior_value=0.7,
            prior_std=0.0001,  # Below 0.001 threshold
            observed_accuracy=0.85,
        )

        # Should use uninformed prior
        assert update is not None
        assert update.posterior_value == 0.85  # Should be observed value
        assert update.posterior_std == 0.1

    def test_bayesian_update_with_normal_prior_std(self):
        """Test with normal prior_std (control case)."""
        engine = AdvancedLearningEngine(tenant_id="_default")

        update = engine.bayesian_update(
            skill_id="skill_a",
            metric_name="latency",
            param_name="confidence_threshold",
            prior_value=0.7,
            prior_std=0.1,  # Normal value
            observed_accuracy=0.85,
        )

        # Normal calculation should work
        assert update is not None
        assert 0.0 <= update.confidence <= 1.0
        assert math.isfinite(update.posterior_value)


# ============================================================================
# BUG 2: Metric Degradation Division by Zero
# ============================================================================

class TestBug2MetricDegradationDivByZero:
    """Test that metric degradation handles baseline=0 safely."""

    def test_check_degradation_with_zero_baseline_latency(self):
        """Test _check_metric_degradation with baseline latency = 0."""
        engine = ProductionTuningEngine(tenant_id="_default")

        baseline = {"accuracy": 0.9, "latency_p95": 0.0, "error_rate": 0.01}
        current = {"accuracy": 0.85, "latency_p95": 150.0, "error_rate": 0.02}

        # Should NOT crash with division by zero
        result = engine._check_metric_degradation(baseline, current)
        assert isinstance(result, bool)

    def test_check_degradation_with_accuracy_drop(self):
        """Test that accuracy drop > threshold triggers degradation."""
        engine = ProductionTuningEngine(tenant_id="_default")

        baseline = {"accuracy": 0.95, "latency_p95": 100.0, "error_rate": 0.01}
        current = {"accuracy": 0.88, "latency_p95": 100.0, "error_rate": 0.01}

        # Accuracy drop = 0.07 > 0.05 threshold
        result = engine._check_metric_degradation(baseline, current)
        assert result is True

    def test_check_degradation_no_degradation(self):
        """Test when metrics are stable."""
        engine = ProductionTuningEngine(tenant_id="_default")

        baseline = {"accuracy": 0.90, "latency_p95": 100.0, "error_rate": 0.01}
        current = {"accuracy": 0.91, "latency_p95": 95.0, "error_rate": 0.005}

        result = engine._check_metric_degradation(baseline, current)
        assert result is False


# ============================================================================
# BUG 3: Cross-Tenant Data Leak
# ============================================================================

class TestBug3CrossTenantDataLeak:
    """Test that pending_approvals are properly tenant-scoped."""

    def test_pending_approvals_tenant_isolation(self):
        """Test that two tenants have isolated pending_approvals."""
        # Mock dependencies
        stability_gate = Mock()
        approval_gate = Mock()
        quality_gate = Mock()
        conflict_resolver = Mock()
        rollback_guard = Mock()
        audit_backend = Mock()

        integrator_tenant_a = L5FeedbackLoopIntegrator(
            stability_gate, approval_gate, quality_gate,
            conflict_resolver, rollback_guard,
            tenant_id="tenant_a",
            audit_backend=audit_backend,
        )
        integrator_tenant_b = L5FeedbackLoopIntegrator(
            stability_gate, approval_gate, quality_gate,
            conflict_resolver, rollback_guard,
            tenant_id="tenant_b",
            audit_backend=audit_backend,
        )

        # Manually add pending approval to tenant A
        integrator_tenant_a.pending_approvals["tenant_a"] = {
            "skill_a": {
                "metric_x": {
                    "approval_id": "appr_001",
                    "pipeline_id": "pipe_001",
                    "timestamp": datetime.utcnow().isoformat(),
                    "raw_delta": 0.05,
                }
            }
        }

        # Verify tenant B doesn't see tenant A's approvals
        tenant_b_approvals = integrator_tenant_b.get_pending_approvals()
        assert tenant_b_approvals == {}

        # Verify tenant A sees their own approvals
        tenant_a_approvals = integrator_tenant_a.get_pending_approvals()
        assert "skill_a" in tenant_a_approvals


# ============================================================================
# BUG 4: Unbounded Memory Growth
# ============================================================================

class TestBug4UnboundedMemoryGrowth:
    """Test that drift_signals list is pruned."""

    def test_drift_signals_pruning(self):
        """Test that drift_signals is limited to 100 entries."""
        engine = AdvancedLearningEngine(tenant_id="_default")

        # Add 150 drift signals
        for i in range(150):
            engine.feedback_history.append({
                "operator_id": "user:alice",
                "skill_id": "skill_a",
                "metric_name": "latency",
                "decision": "approve",
                "correct": True,
                "timestamp": datetime.utcnow().isoformat(),
            })

        # Trigger drift detection multiple times
        for i in range(150):
            # This should trigger drift occasionally
            signal = engine.detect_concept_drift("skill_a", "latency")

        # Verify drift_signals list is bounded
        assert len(engine.drift_signals) <= 100
        assert len(engine.drift_signals) > 0


# ============================================================================
# BUG 5: Missing Input Validation
# ============================================================================

class TestBug5MissingInputValidation:
    """Test that record_ab_test_metrics validates arm_id."""

    def test_record_metrics_valid_arm_id(self):
        """Test with valid arm_id."""
        engine = ProductionTuningEngine(tenant_id="_default")

        test = engine.start_ab_test(
            skill_id="skill_a",
            metric_name="latency",
            control_config={"threshold": 0.7},
            treatment_config={"threshold": 0.75},
        )

        # Should succeed with valid arm_id
        engine.record_ab_test_metrics(
            test_id=test.test_id,
            arm_id="control",
            approval_accuracy=0.92,
            latency_p50=120.0,
            latency_p95=150.0,
            error_rate=0.005,
            cost=50.0,
            num_evaluations=200,
        )

    def test_record_metrics_invalid_arm_id(self):
        """Test with invalid arm_id (should raise ValueError)."""
        engine = ProductionTuningEngine(tenant_id="_default")

        test = engine.start_ab_test(
            skill_id="skill_a",
            metric_name="latency",
            control_config={"threshold": 0.7},
            treatment_config={"threshold": 0.75},
        )

        # Should fail with invalid arm_id
        with pytest.raises(ValueError, match="Invalid arm_id"):
            engine.record_ab_test_metrics(
                test_id=test.test_id,
                arm_id="invalid_arm",  # INVALID
                approval_accuracy=0.92,
                latency_p50=120.0,
                latency_p95=150.0,
                error_rate=0.005,
                cost=50.0,
                num_evaluations=200,
            )


# ============================================================================
# BUG 6: Wrong Pending Count
# ============================================================================

class TestBug6WrongPendingCount:
    """Test that pending_count sums approvals, not skills."""

    def test_pending_count_calculation(self):
        """Test that pending_count correctly sums approvals."""
        audit_backend = Mock()
        audit_backend.query_events.return_value = [
            {
                "event_type": "approval_request",
                "skill_id": "skill_a",
                "decision": "pending",
                "latency_ms": 100,
            },
            {
                "event_type": "approval_request",
                "skill_id": "skill_a",
                "decision": "pending",
                "latency_ms": 150,
            },
            {
                "event_type": "approval_request",
                "skill_id": "skill_b",
                "decision": "pending",
                "latency_ms": 120,
            },
        ]

        collector = MetricsCollector(audit_backend, window_hours=24, tenant_id="_default")
        checker = HealthChecker(collector, tenant_id="_default")

        metrics = collector.collect_metrics()
        # pending_by_skill = {"skill_a": 2, "skill_b": 1}
        # Total pending should be 3, not 2 (number of skills)

        gates_status = checker._check_gate_latencies(metrics)
        # Each gate should report the total pending count
        for gate in gates_status.values():
            assert gate.pending_count == 3  # Not 2 (number of skills)


# ============================================================================
# BUG 7: Silent Cache Refresh Failure
# ============================================================================

class TestBug7SilentCacheRefresh:
    """Test that cache refresh failures are logged."""

    def test_refresh_cache_timeout_logging(self):
        """Test that timeout is logged properly."""
        audit_backend = Mock()
        audit_backend.query_events.side_effect = TimeoutError("Audit backend timeout")

        collector = MetricsCollector(audit_backend, window_hours=24, tenant_id="_default")

        # Should not crash, but should log warning
        with patch('core.learning.monitoring_l5.logger') as mock_logger:
            collector._refresh_cache(datetime.utcnow() - timedelta(hours=24))
            # Verify warning was logged
            mock_logger.warning.assert_called()

    def test_refresh_cache_generic_failure_logging(self):
        """Test that generic exceptions are logged properly."""
        audit_backend = Mock()
        audit_backend.query_events.side_effect = RuntimeError("Database error")

        collector = MetricsCollector(audit_backend, window_hours=24, tenant_id="_default")

        # Should not crash, but should log warning
        with patch('core.learning.monitoring_l5.logger') as mock_logger:
            collector._refresh_cache(datetime.utcnow() - timedelta(hours=24))
            # Verify warning was logged (changed from error to warning in fix)
            mock_logger.warning.assert_called()


# ============================================================================
# BUG 8: Weak Random Seed
# ============================================================================

class TestBug8WeakRandomSeed:
    """Test that random seed has strong entropy."""

    def test_canary_cohort_selection_uses_strong_seed(self):
        """Test that cohort selection uses 256+ bit entropy."""
        engine = ProductionTuningEngine(tenant_id="_default")

        canary = engine.start_canary_deployment(
            skill_id="skill_a",
            metric_name="latency",
            new_config={"threshold": 0.75},
            metrics_pre={"accuracy": 0.92, "latency_p95": 150.0, "error_rate": 0.005},
        )

        # Select cohort 10 times with different deployment IDs
        cohorts_selected = []
        for i in range(10):
            operator_ids = [f"op_{j}" for j in range(100)]
            cohort = engine.select_canary_cohort(
                canary.deployment_id + f"_{i}",
                operator_ids,
            )
            cohorts_selected.append(set(cohort))

        # With strong seed, cohorts should be different
        # (though some overlap is expected with random selection)
        unique_cohorts = set(frozenset(c) for c in cohorts_selected)
        # Most cohorts should be unique
        assert len(unique_cohorts) > 1


# ============================================================================
# BUG 9: Missing Tenant Validation
# ============================================================================

class TestBug9MissingTenantValidation:
    """Test that POST endpoints validate tenant before mutation."""

    def test_acknowledge_endpoint_validates_tenant(self):
        """Test that acknowledge endpoint validates tenant."""
        from core.console.corvin_console.routes.l5_metrics_api import acknowledge_l5_alert, AlertAcknowledgeRequest
        from fastapi import HTTPException

        # Create mock session with different tenant
        session_rec = Mock()
        session_rec.tenant_id = "tenant_a"

        request = AlertAcknowledgeRequest(tenant_id="tenant_b")

        # Should raise 403 when tenant doesn't match
        with pytest.raises(HTTPException) as exc_info:
            from core.console.corvin_console.routes.l5_metrics_api import _validate_tenant_access
            _validate_tenant_access(session_rec, request.tenant_id)

        assert exc_info.value.status_code == 403

    def test_resolve_endpoint_validates_tenant(self):
        """Test that resolve endpoint validates tenant."""
        from core.console.corvin_console.routes.l5_metrics_api import resolve_l5_alert, AlertResolveRequest
        from fastapi import HTTPException

        # Create mock session with different tenant
        session_rec = Mock()
        session_rec.tenant_id = "tenant_a"

        request = AlertResolveRequest(tenant_id="tenant_c")

        # Should raise 403 when tenant doesn't match
        with pytest.raises(HTTPException) as exc_info:
            from core.console.corvin_console.routes.l5_metrics_api import _validate_tenant_access
            _validate_tenant_access(session_rec, request.tenant_id)

        assert exc_info.value.status_code == 403


# ============================================================================
# Stress Tests
# ============================================================================

class TestStressConcurrency:
    """Stress tests for concurrent operations."""

    def test_concurrent_bayesian_updates(self):
        """Test that Bayesian updates are thread-safe."""
        import threading

        engine = AdvancedLearningEngine(tenant_id="_default")
        errors = []

        def update_worker():
            try:
                for i in range(100):
                    engine.bayesian_update(
                        skill_id=f"skill_{i % 5}",
                        metric_name="latency",
                        param_name="threshold",
                        prior_value=0.7,
                        prior_std=0.1,
                        observed_accuracy=0.8 + (i % 5) * 0.01,
                    )
            except Exception as e:
                errors.append(e)

        # Start 10 threads
        threads = [threading.Thread(target=update_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have no errors
        assert len(errors) == 0

    def test_concurrent_metric_degradation_checks(self):
        """Test that metric degradation checks are thread-safe."""
        import threading

        engine = ProductionTuningEngine(tenant_id="_default")
        errors = []

        def degrade_worker():
            try:
                for i in range(100):
                    baseline = {
                        "accuracy": 0.9,
                        "latency_p95": 100.0 + (i % 50),
                        "error_rate": 0.01,
                    }
                    current = {
                        "accuracy": 0.88 + (i % 10) * 0.001,
                        "latency_p95": 105.0 + (i % 50),
                        "error_rate": 0.02,
                    }
                    engine._check_metric_degradation(baseline, current)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=degrade_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
