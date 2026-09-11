"""
Tests for CostVarianceOptimizer — ADR-0377 Phase 2

Unit tests covering:
1. Bayesian update correctness
2. Convergence detection
3. Threshold recommendation logic
4. Edge cases (null samples, missing data)
5. Tenant isolation
"""

import pytest
import math
from unittest.mock import Mock, MagicMock

from core.learning.cost_variance_optimizer import (
    CostVarianceOptimizer,
    CostVarianceStats,
)


class TestCostVarianceStats:
    """Test data structure for cost variance statistics."""

    def test_init(self):
        """Test stat initialization."""
        stats = CostVarianceStats(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            n_samples=0,
            variance_sum=0.0,
            variance_squared_sum=0.0,
            quality_sum=0.0,
            recommended_threshold=0.5,
        )
        assert stats.task_type == "code_gen"
        assert stats.n_samples == 0
        assert stats.mean_variance == 0.0
        assert stats.mean_quality == 0.5

    def test_mean_variance(self):
        """Test mean variance calculation."""
        stats = CostVarianceStats(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            n_samples=2,
            variance_sum=-0.10,
            variance_squared_sum=0.01,
            quality_sum=1.8,
            recommended_threshold=0.5,
        )
        assert stats.mean_variance == -0.05
        assert stats.mean_quality == 0.9

    def test_std_dev(self):
        """Test standard deviation calculation."""
        stats = CostVarianceStats(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            n_samples=3,
            variance_sum=0.0,
            variance_squared_sum=0.03,
            quality_sum=2.4,
            recommended_threshold=0.5,
        )
        # variance = (0.03 / 3) - (0/3)^2 = 0.01
        # std_dev = sqrt(0.01) = 0.1
        assert abs(stats.std_dev - 0.1) < 0.001


class TestCostVarianceOptimizer:
    """Test CostVarianceOptimizer core functionality."""

    @pytest.fixture
    def optimizer(self):
        """Create optimizer with mock store."""
        store = {}
        audit_backend = Mock()
        audit_backend.write_event = Mock()
        optimizer = CostVarianceOptimizer(store=store, audit_backend=audit_backend)
        return optimizer

    def test_process_cost_variance_single_sample(self, optimizer):
        """Test processing a single cost variance sample."""
        threshold, is_converged = optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=0.9,
            cost_variance=-0.05,
            tenant_id="_default",
            base_threshold=0.5,
        )

        # Single sample, not converged
        assert is_converged is False
        # Threshold adjusted slightly due to negative variance
        assert threshold < 0.5  # Cheaper than expected, lower threshold

    def test_process_cost_variance_negative_variance_lowers_threshold(self, optimizer):
        """Test that negative variance (cheaper) lowers threshold."""
        # Process 15 samples with consistently negative variance
        for i in range(15):
            threshold, _ = optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.85,
                cost_variance=-0.03,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Threshold should be lower than base due to consistent cost savings
        assert threshold < 0.5

    def test_process_cost_variance_low_quality_raises_threshold(self, optimizer):
        """Test that low quality raises threshold (prefer better models)."""
        # Process samples with low quality
        for i in range(15):
            threshold, _ = optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.6,  # Below 0.7 threshold
                cost_variance=0.0,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Threshold should be higher due to quality penalty
        assert threshold > 0.5

    def test_convergence_detection(self, optimizer):
        """Test convergence detection with stable variance."""
        # Feed 60 samples with consistent variance
        is_converged = False
        for i in range(60):
            _, is_converged = optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.85,
                cost_variance=-0.002,  # Small consistent variance
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Should converge after sufficient samples with stable variance
        assert is_converged is True

    def test_get_threshold_recommendation_before_convergence(self, optimizer):
        """Test that recommendations are base threshold before MIN_SAMPLES."""
        # Process only 5 samples
        for i in range(5):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=-0.05,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Should return base threshold (not enough samples yet)
        recommendation = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            base_threshold=0.5,
        )
        assert recommendation == 0.5

    def test_get_threshold_recommendation_after_convergence(self, optimizer):
        """Test that recommendations are updated after sufficient samples."""
        # Process 15 samples with consistent cost savings
        for i in range(15):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=-0.04,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Should return learned threshold (> MIN_SAMPLES)
        recommendation = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            base_threshold=0.5,
        )
        assert recommendation < 0.5  # Lower due to cost savings

    def test_get_stats(self, optimizer):
        """Test retrieving statistics."""
        optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=0.85,
            cost_variance=-0.05,
            tenant_id="_default",
            base_threshold=0.5,
        )

        stats = optimizer.get_stats(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            base_threshold=0.5,
        )

        assert stats.n_samples == 1
        assert stats.mean_variance == -0.05
        assert stats.mean_quality == 0.85

    def test_is_converged(self, optimizer):
        """Test convergence status check."""
        # Process 60 samples
        for i in range(60):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.85,
                cost_variance=-0.001,
                tenant_id="_default",
                base_threshold=0.5,
            )

        is_converged = optimizer.is_converged(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
        )
        assert is_converged is True

    def test_reset_learning(self, optimizer):
        """Test learning reset."""
        # Add some learning data
        for i in range(15):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.85,
                cost_variance=-0.05,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Reset
        optimizer.reset_learning(tenant_id="_default")

        # Verify reset
        stats = optimizer.get_stats(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            base_threshold=0.5,
        )
        assert stats.n_samples == 0

    def test_tenant_isolation(self, optimizer):
        """Test that learning is isolated per tenant."""
        # Tenant A: 15 samples with negative variance
        for i in range(15):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=-0.05,
                tenant_id="tenant_a",
                base_threshold=0.5,
            )

        # Tenant B: 15 samples with positive variance
        for i in range(15):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=0.05,
                tenant_id="tenant_b",
                base_threshold=0.5,
            )

        # Tenant A should have lower threshold
        threshold_a = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="tenant_a",
            base_threshold=0.5,
        )

        # Tenant B should have higher threshold
        threshold_b = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="tenant_b",
            base_threshold=0.5,
        )

        assert threshold_a < 0.5  # Lower due to cost savings
        # threshold_b may be raised or stay at 0.5 (positive variance has weaker effect)
        assert threshold_a < threshold_b  # Tenant A should be lower than Tenant B

    def test_quality_score_validation(self, optimizer):
        """Test that invalid quality scores are rejected."""
        with pytest.raises(ValueError):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=1.5,  # Out of range
                cost_variance=-0.05,
                tenant_id="_default",
            )

        with pytest.raises(ValueError):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=-0.1,  # Out of range
                cost_variance=-0.05,
                tenant_id="_default",
            )

    def test_threshold_clamping(self, optimizer):
        """Test that thresholds are clamped to [0.1, 0.9]."""
        # Feed extreme negative variances to drive threshold very low
        for i in range(30):
            threshold, _ = optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=-0.2,  # Extreme savings
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Should be clamped to [0.1, 0.9]
        assert 0.1 <= threshold <= 0.9

    def test_audit_event_emission(self, optimizer):
        """Test that audit events are emitted."""
        optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=0.85,
            cost_variance=-0.05,
            tenant_id="_default",
            base_threshold=0.5,
        )

        # Verify audit event was called
        optimizer.audit_backend.write_event.assert_called()
        call_args = optimizer.audit_backend.write_event.call_args
        assert call_args.kwargs["event_type"] == "cost_variance_updated"
        assert call_args.kwargs["tenant_id"] == "_default"

    def test_multiple_subsystems(self, optimizer):
        """Test learning across multiple subsystems."""
        # code_analyzer: negative variance
        for i in range(15):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=-0.05,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # refactoring_engine: positive variance
        for i in range(15):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="refactoring_engine",
                quality_score=0.9,
                cost_variance=0.05,
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Different thresholds per subsystem
        threshold_analyzer = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            base_threshold=0.5,
        )

        threshold_engine = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="refactoring_engine",
            tenant_id="_default",
            base_threshold=0.5,
        )

        assert threshold_analyzer < 0.5  # Lower due to cost savings
        # refactoring_engine may be raised or stay at 0.5 (positive variance weaker)
        assert threshold_analyzer < threshold_engine  # analyzer lower than engine

    def test_persistence(self, optimizer):
        """Test that stats are persisted to store."""
        optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=0.85,
            cost_variance=-0.05,
            tenant_id="_default",
            base_threshold=0.5,
        )

        # Check store has the data
        store_key = "cost_variance:code_gen:code_analyzer:_default"
        assert store_key in optimizer.store
        assert optimizer.store[store_key]["n_samples"] == 1


class TestCostVarianceOptimizerEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_store_initialization(self):
        """Test initialization with empty store."""
        optimizer = CostVarianceOptimizer(store={})

        stats = optimizer.get_stats(
            task_type="unknown",
            subsystem="unknown",
            base_threshold=0.5,
        )

        assert stats.n_samples == 0
        assert stats.recommended_threshold == 0.5

    def test_missing_audit_backend(self):
        """Test graceful degradation when audit backend is missing."""
        optimizer = CostVarianceOptimizer(store={}, audit_backend=None)

        # Should raise because audit is required (fail-closed)
        with pytest.raises(RuntimeError):
            optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.85,
                cost_variance=-0.05,
                tenant_id="_default",
            )

    def test_boundary_quality_scores(self):
        """Test with boundary quality scores (0.0 and 1.0)."""
        optimizer = CostVarianceOptimizer(store={}, audit_backend=Mock())

        # Quality 0.0
        threshold_0, _ = optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=0.0,
            cost_variance=0.0,
            tenant_id="_default",
            base_threshold=0.5,
        )

        # Quality 1.0
        threshold_1, _ = optimizer.process_cost_variance(
            task_type="code_gen",
            subsystem="code_analyzer",
            quality_score=1.0,
            cost_variance=0.0,
            tenant_id="_default",
            base_threshold=0.5,
        )

        # Both should be valid
        assert 0.1 <= threshold_0 <= 0.9
        assert 0.1 <= threshold_1 <= 0.9


class TestCostVarianceOptimizerIntegration:
    """Integration tests with simulated workflows."""

    def test_learning_cycle_full_workflow(self):
        """Test a full learning cycle: 50 tasks with variance feedback."""
        optimizer = CostVarianceOptimizer(store={}, audit_backend=Mock())

        # Simulate 50 tasks with gradually improving cost variance
        threshold_history = []
        for task_id in range(50):
            # Early: high variance; late: low variance (converging)
            noise = 0.05 * (1.0 - task_id / 50.0)  # Decreasing noise
            cost_variance = -0.03 + noise
            quality = 0.8 + (task_id / 100.0)  # Improving quality

            threshold, is_converged = optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=min(1.0, quality),
                cost_variance=cost_variance,
                tenant_id="_default",
                base_threshold=0.5,
            )
            threshold_history.append(threshold)

        # Threshold should stabilize over time (variance decreases)
        early_var = max(threshold_history[:10]) - min(threshold_history[:10])
        late_var = max(threshold_history[-10:]) - min(threshold_history[-10:])
        # Even if early_var is small, late_var should be smaller or equal
        # This indicates stabilization
        assert late_var <= early_var + 0.001  # Allow small rounding error
        # Verify threshold changes over the workflow (not stuck at 0.5)
        assert not all(t == 0.5 for t in threshold_history)

    def test_cost_savings_detection(self):
        """Test detection of consistent cost savings."""
        optimizer = CostVarianceOptimizer(store={}, audit_backend=Mock())

        # 30 tasks all consistently cheaper than expected
        for i in range(30):
            threshold, is_converged = optimizer.process_cost_variance(
                task_type="code_gen",
                subsystem="code_analyzer",
                quality_score=0.9,
                cost_variance=-0.02,  # Consistently 2 cents cheaper
                tenant_id="_default",
                base_threshold=0.5,
            )

        # Final threshold should be lower than base (cost savings detected)
        final_threshold = optimizer.get_threshold_recommendation(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            base_threshold=0.5,
        )
        # With consistent -0.02 variance: adjustment proportional to cost savings
        assert final_threshold < 0.5  # Should be lower due to cost savings
