"""Tests for L5 k=3: Quality Gate (ADR-0580)."""

import pytest
from datetime import datetime
from core.learning.quality_gate import QualityGate, QualityLevel, QualityScore


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []

    def write_event(self, event):
        self.events.append(event)
        return len(self.events)


@pytest.fixture
def quality_gate():
    """Create a QualityGate with mock audit backend."""
    audit = MockAuditBackend()
    return QualityGate(tenant_id="_default", audit_backend=audit)


class TestQualityMetricComputation:
    """Test individual metric computation methods."""

    def test_overfitting_risk_safe(self, quality_gate):
        """Test overfitting detection when deltas align with EMA."""
        # Recent deltas close to EMA, high confidence → low overfitting
        recent_deltas = [0.05, 0.06, 0.04]
        ema_smoothed = 0.05
        ema_confidence = 0.9

        risk = quality_gate._compute_overfitting_risk(
            recent_deltas, ema_smoothed, ema_confidence
        )

        assert risk < 0.3, "Aligned deltas should have low overfitting risk"

    def test_overfitting_risk_severe(self, quality_gate):
        """Test overfitting detection when deltas diverge from EMA."""
        # Recent deltas diverge from EMA, high confidence → high overfitting
        recent_deltas = [0.5, 0.6, 0.55]
        ema_smoothed = 0.05
        ema_confidence = 0.9

        risk = quality_gate._compute_overfitting_risk(
            recent_deltas, ema_smoothed, ema_confidence
        )

        assert risk > 0.7, "Divergent deltas should have high overfitting risk"

    def test_noise_ratio_clean(self, quality_gate):
        """Test noise detection when deltas are consistent."""
        recent_deltas = [0.01, 0.02, 0.015, 0.01]

        noise = quality_gate._compute_noise_ratio(recent_deltas)

        assert noise < 0.3, "Consistent deltas should have low noise"

    def test_noise_ratio_noisy(self, quality_gate):
        """Test noise detection when deltas are outliers."""
        recent_deltas = [0.5, 0.01, 0.6, 0.02]

        noise = quality_gate._compute_noise_ratio(recent_deltas)

        assert noise > 0.5, "Outlier deltas should indicate high noise"

    def test_convergence_rate_converged(self, quality_gate):
        """Test convergence when recent deltas stabilize."""
        recent_deltas = [0.1, 0.095, 0.098, 0.1, 0.099]

        convergence = quality_gate._compute_convergence_rate(recent_deltas)

        assert convergence > 0.8, "Stabilized deltas should show high convergence"

    def test_convergence_rate_diverging(self, quality_gate):
        """Test divergence when recent deltas vary wildly."""
        recent_deltas = [0.01, 0.5, 0.02, 0.6, 0.03]

        convergence = quality_gate._compute_convergence_rate(recent_deltas)

        assert convergence < 0.3, "Varied deltas should show low convergence"

    def test_stability_score_stable(self, quality_gate):
        """Test stability when config values are consistent."""
        config_history = [0.7, 0.70, 0.70, 0.701, 0.70]

        stability = quality_gate._compute_stability_score(config_history)

        assert stability > 0.9, "Consistent config should be stable"

    def test_stability_score_unstable(self, quality_gate):
        """Test instability when config values vary widely."""
        config_history = [0.5, 0.9, 0.3, 0.8, 0.4]

        stability = quality_gate._compute_stability_score(config_history)

        assert stability < 0.5, "Varied config should be unstable"


class TestCompositeScoreComputation:
    """Test composite score computation and classification."""

    def test_excellent_quality(self, quality_gate):
        """Test classification of excellent quality."""
        recent_deltas = [0.01, 0.015, 0.01]
        ema_smoothed = 0.012
        ema_confidence = 0.95
        config_history = [0.7, 0.701, 0.700, 0.701]

        score = quality_gate.compute_quality(
            "test_skill",
            "test_metric",
            recent_deltas,
            ema_smoothed,
            ema_confidence,
            config_history,
        )

        assert score.quality_level == QualityLevel.EXCELLENT
        assert score.composite_score >= 0.85

    def test_poor_quality(self, quality_gate):
        """Test classification of poor quality."""
        recent_deltas = [0.5, 0.01, 0.6, 0.02]
        ema_smoothed = 0.05
        ema_confidence = 0.5
        config_history = [0.3, 0.8, 0.2, 0.9, 0.1]

        score = quality_gate.compute_quality(
            "test_skill",
            "test_metric",
            recent_deltas,
            ema_smoothed,
            ema_confidence,
            config_history,
        )

        assert score.quality_level == QualityLevel.POOR
        assert score.composite_score < 0.55

    def test_no_data_quality(self, quality_gate):
        """Test handling of no data (empty deltas)."""
        score = quality_gate.compute_quality(
            "test_skill", "test_metric", [], 0.0, 0.0, []
        )

        assert score.composite_score == 0.5
        assert score.quality_level == QualityLevel.FAIR


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_score_audited_before_storage(self, quality_gate):
        """Test that scores are audited before being stored."""
        recent_deltas = [0.01, 0.02]
        ema_smoothed = 0.015
        ema_confidence = 0.8
        config_history = [0.7, 0.71]

        quality_gate.compute_quality(
            "test_skill",
            "test_metric",
            recent_deltas,
            ema_smoothed,
            ema_confidence,
            config_history,
        )

        # Check audit was called
        assert len(quality_gate.audit_backend.events) > 0
        event = quality_gate.audit_backend.events[0]
        assert event["event_type"] == "learning_quality_score_computed"
        assert event["skill_id"] == "test_skill"
        assert event["metric_name"] == "test_metric"

    def test_audit_failure_blocks_storage(self):
        """Test that audit failure blocks storage (fail-closed)."""

        class FailingAudit:
            def write_event(self, event):
                raise RuntimeError("Audit backend failed")

        quality_gate = QualityGate(
            tenant_id="_default", audit_backend=FailingAudit()
        )

        with pytest.raises(RuntimeError, match="audit_backend.write_event"):
            quality_gate.compute_quality(
                "test_skill",
                "test_metric",
                [0.01, 0.02],
                0.015,
                0.8,
                [0.7, 0.71],
            )

        # Score should not be stored
        assert quality_gate.get_score("test_skill", "test_metric") is None


class TestScoreRetrieval:
    """Test score retrieval and storage."""

    def test_get_score(self, quality_gate):
        """Test retrieving a stored score."""
        quality_gate.compute_quality(
            "skill_a",
            "metric_x",
            [0.01, 0.02],
            0.015,
            0.8,
            [0.7, 0.71],
        )

        score = quality_gate.get_score("skill_a", "metric_x")

        assert score is not None
        assert score.skill_id == "skill_a"
        assert score.metric_name == "metric_x"

    def test_get_score_not_found(self, quality_gate):
        """Test retrieving a non-existent score."""
        score = quality_gate.get_score("nonexistent", "metric")

        assert score is None

    def test_get_scores_by_skill(self, quality_gate):
        """Test retrieving all scores for a Skill."""
        quality_gate.compute_quality("skill_a", "metric_x", [0.01], 0.01, 0.8, [0.7])
        quality_gate.compute_quality("skill_a", "metric_y", [0.02], 0.02, 0.7, [0.71])
        quality_gate.compute_quality("skill_b", "metric_x", [0.03], 0.03, 0.6, [0.72])

        scores = quality_gate.get_scores_by_skill("skill_a")

        assert len(scores) == 2
        assert "metric_x" in scores
        assert "metric_y" in scores


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_single_delta(self, quality_gate):
        """Test with single delta."""
        score = quality_gate.compute_quality(
            "test", "test", [0.05], 0.05, 0.5, [0.7]
        )

        assert 0.0 <= score.composite_score <= 1.0

    def test_high_ema_confidence(self, quality_gate):
        """Test with high EMA confidence."""
        score = quality_gate.compute_quality(
            "test",
            "test",
            [0.01, 0.02, 0.015],
            0.015,
            0.99,
            [0.7, 0.71],
        )

        assert score.quality_level in [
            QualityLevel.GOOD,
            QualityLevel.EXCELLENT,
        ]

    def test_zero_ema_confidence(self, quality_gate):
        """Test with zero EMA confidence."""
        score = quality_gate.compute_quality(
            "test", "test", [0.01, 0.02], 0.015, 0.0, [0.7, 0.71]
        )

        # Should not crash, score should be reasonable
        assert 0.0 <= score.composite_score <= 1.0

    def test_very_large_deltas(self, quality_gate):
        """Test with very large deltas."""
        score = quality_gate.compute_quality(
            "test", "test", [1000.0, 2000.0], 1500.0, 0.5, [0.7, 0.71]
        )

        # Should clamp to [0, 1]
        assert 0.0 <= score.composite_score <= 1.0
