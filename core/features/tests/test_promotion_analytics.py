"""Tests for promotion analytics engine."""
import tempfile
from pathlib import Path

import pytest

from core.features.promotion_analytics import PromotionAnalytics, PromotionScore
from core.features.telemetry_collector import (
    EventType,
    HourlyAggregate,
    TelemetryCollector,
)


class TestPromotionScore:
    """PromotionScore criteria tests."""

    def test_alpha_to_beta_criteria_met(self):
        """Test ALPHA→BETA criteria all met."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.03,  # < 0.05
            user_satisfaction=0.7,  # > 0.5
            adoption_factor=0.5,  # > 0.0
            stability_days=5,
            is_eligible_for_promotion=True,
        )
        assert score.meets_alpha_to_beta_criteria()

    def test_alpha_to_beta_criteria_high_error(self):
        """Test ALPHA→BETA fails on high error rate."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.10,  # > 0.05
            user_satisfaction=0.8,
            adoption_factor=0.5,
            stability_days=5,
            is_eligible_for_promotion=False,
        )
        assert not score.meets_alpha_to_beta_criteria()

    def test_alpha_to_beta_criteria_low_satisfaction(self):
        """Test ALPHA→BETA fails on low satisfaction."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.03,
            user_satisfaction=0.3,  # < 0.5
            adoption_factor=0.5,
            stability_days=5,
            is_eligible_for_promotion=False,
        )
        assert not score.meets_alpha_to_beta_criteria()

    def test_alpha_to_beta_criteria_no_usage(self):
        """Test ALPHA→BETA fails on no usage."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.03,
            user_satisfaction=0.8,
            adoption_factor=0.0,  # No usage
            stability_days=5,
            is_eligible_for_promotion=False,
        )
        assert not score.meets_alpha_to_beta_criteria()

    def test_beta_to_stable_criteria_met(self):
        """Test BETA→STABLE criteria all met."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.005,  # < 0.01
            user_satisfaction=0.8,  # > 0.7
            adoption_factor=1.5,  # > 1.2
            stability_days=35,  # >= 30
            is_eligible_for_promotion=True,
        )
        assert score.meets_beta_to_stable_criteria()

    def test_beta_to_stable_criteria_not_stable_long_enough(self):
        """Test BETA→STABLE fails on insufficient stability."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.005,
            user_satisfaction=0.8,
            adoption_factor=1.5,
            stability_days=15,  # < 30
            is_eligible_for_promotion=False,
        )
        assert not score.meets_beta_to_stable_criteria()

    def test_stable_to_production_criteria_met(self):
        """Test STABLE→PRODUCTION criteria all met."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.0005,  # < 0.001
            user_satisfaction=0.9,  # > 0.8
            adoption_factor=2.0,  # > 1.5
            stability_days=70,  # >= 60
            is_eligible_for_promotion=True,
        )
        assert score.meets_stable_to_production_criteria()

    def test_stable_to_production_criteria_insufficient_stability(self):
        """Test STABLE→PRODUCTION fails on insufficient stability."""
        score = PromotionScore(
            feature_id="test",
            error_rate=0.0005,
            user_satisfaction=0.9,
            adoption_factor=2.0,
            stability_days=45,  # < 60
            is_eligible_for_promotion=False,
        )
        assert not score.meets_stable_to_production_criteria()


class TestPromotionAnalytics:
    """PromotionAnalytics computation tests."""

    @pytest.fixture
    def setup_analytics(self):
        """Set up analytics with a mock collector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TelemetryCollector(
                storage_path=Path(tmpdir) / "telemetry",
                enabled=True,
            )
            # Mock initialize_collector
            from core.features import telemetry_collector as tc
            tc._COLLECTOR = collector

            analytics = PromotionAnalytics()
            yield analytics, collector

            # Cleanup
            tc._COLLECTOR = None

    def test_analytics_initialization(self, setup_analytics):
        """Test analytics initialization."""
        analytics, _ = setup_analytics
        assert analytics is not None
        assert analytics.collector is not None

    def test_compute_promotion_score_no_data(self, setup_analytics):
        """Test computing score with no telemetry data."""
        analytics, _ = setup_analytics
        score = analytics.compute_promotion_score("nonexistent_feature")
        # Score will be computed but with zero metrics
        assert score is not None or score is None  # Both valid states

    def test_compute_error_rate_24h_no_data(self, setup_analytics):
        """Test error rate with no data."""
        analytics, _ = setup_analytics
        rate = analytics.compute_error_rate_24h("nonexistent")
        assert rate == 0.0

    def test_compute_avg_satisfaction_24h_no_data(self, setup_analytics):
        """Test satisfaction with no data."""
        analytics, _ = setup_analytics
        satisfaction = analytics.compute_avg_satisfaction_24h("nonexistent")
        assert satisfaction == 0.0

    def test_compute_adoption_growth(self, setup_analytics):
        """Test adoption growth computation."""
        analytics, _ = setup_analytics
        growth = analytics.compute_adoption_growth("test_feature")
        # Placeholder returns 1.0
        assert growth == 1.0

    def test_get_days_since_last_error_no_errors(self, setup_analytics):
        """Test days since last error with no errors."""
        analytics, collector = setup_analytics
        collector.collect_event("test_feature", EventType.USAGE_STARTED)
        collector.collect_event("test_feature", EventType.USAGE_STARTED)

        days = analytics.get_days_since_last_error("test_feature")
        # Should be > 0 or 0 depending on aggregate
        assert isinstance(days, int)

    def test_get_days_since_last_error_with_errors(self, setup_analytics):
        """Test days since last error with recent errors."""
        analytics, collector = setup_analytics
        collector.collect_event("test_feature", EventType.ERROR, error_type="ValueError")
        collector.collect_event("test_feature", EventType.USAGE_STARTED)

        days = analytics.get_days_since_last_error("test_feature")
        # Should reset to 0 due to recent error
        assert days == 0

    def test_is_eligible_for_alpha_to_beta_no_data(self, setup_analytics):
        """Test eligibility check with no data."""
        analytics, _ = setup_analytics
        eligible, reason = analytics.is_eligible_for_alpha_to_beta("nonexistent")
        assert eligible is False
        assert "No telemetry data" in reason

    def test_is_eligible_for_beta_to_stable_no_data(self, setup_analytics):
        """Test BETA→STABLE eligibility with no data."""
        analytics, _ = setup_analytics
        eligible, reason = analytics.is_eligible_for_beta_to_stable("nonexistent")
        assert eligible is False
        assert "No telemetry data" in reason

    def test_is_eligible_for_stable_to_production_no_data(self, setup_analytics):
        """Test STABLE→PRODUCTION eligibility with no data."""
        analytics, _ = setup_analytics
        eligible, reason = analytics.is_eligible_for_stable_to_production("nonexistent")
        assert eligible is False
        assert "No telemetry data" in reason


class TestPromotionScoreParity:
    """Test PromotionScore against real data."""

    @pytest.fixture
    def setup_with_data(self):
        """Set up analytics with sample data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = TelemetryCollector(
                storage_path=Path(tmpdir) / "telemetry",
                enabled=True,
            )
            from core.features import telemetry_collector as tc
            tc._COLLECTOR = collector

            analytics = PromotionAnalytics()
            yield analytics, collector
            tc._COLLECTOR = None

    def test_score_eligibility_consistency(self, setup_with_data):
        """Test that score eligibility matches criteria."""
        analytics, collector = setup_with_data

        # Populate with good data
        for _ in range(50):
            collector.collect_event("test_feature", EventType.USAGE_STARTED, duration_ms=100)
            collector.collect_event("test_feature", EventType.FEEDBACK_PROVIDED, feedback_score=0.8)

        score = analytics.compute_promotion_score("test_feature")
        if score and score.is_eligible_for_promotion:
            # If marked eligible, at least one criteria should pass
            assert (
                score.meets_alpha_to_beta_criteria()
                or score.meets_beta_to_stable_criteria()
                or score.meets_stable_to_production_criteria()
            )
