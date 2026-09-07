"""Tests for ADR-0324 — Confidence Intervals (Bayesian Beta-Binomial).

Coverage:
- Confidence interval calculation
- Windowed trend detection

The aggregation pipeline itself is ``core.learning.performance_aggregation``
(covered by ``tests/unit/test_performance_aggregation.py``). The former
sibling ``performance_aggregator.py`` was a dead duplicate — zero non-test
callers, and it called ``EventStore.read_events_by_type`` which no store
defines — and was deleted on 2026-09-07 together with its tests here.
"""

from core.learning.confidence_intervals import (
    ConfidenceIntervalCalculator,
    WindowedConfidenceCalculator,
)


# ============================================================================
# Confidence Interval Tests
# ============================================================================


class TestConfidenceIntervalCalculator:
    """Test Bayesian confidence interval calculations."""

    def test_compute_interval_high_success_rate(self):
        """Test confidence interval for high success rate (8 successes, 2 failures)."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=8, failures=2)

        # Posterior: Beta(8+2, 2+2) = Beta(10, 4)
        assert 0.5 < ci.mean < 0.9, "Mean should be between 50-90%"
        assert ci.lower < ci.mean < ci.upper, "Lower < Mean < Upper"
        assert ci.samples == 10
        # At 80% success rate, 95% CI should be roughly [55%, 95%]
        assert 0.4 < ci.lower < 0.7
        assert 0.8 < ci.upper < 1.0

    def test_compute_interval_low_success_rate(self):
        """Test confidence interval for low success rate (2 successes, 8 failures)."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=2, failures=8)

        # Posterior: Beta(2+2, 8+2) = Beta(4, 10)
        assert 0.1 < ci.mean < 0.5, "Mean should be between 10-50%"
        assert ci.lower < ci.mean < ci.upper
        assert ci.samples == 10

    def test_compute_interval_zero_samples(self):
        """Test confidence interval with zero samples."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=0, failures=0)

        # Posterior: Beta(0+2, 0+2) = Beta(2, 2) (uniform)
        assert 0.4 < ci.mean < 0.6, "Mean of Beta(2,2) should be 0.5"
        assert ci.samples == 0

    def test_compute_interval_cold_start(self):
        """Test that cold-start (1 success) is regularized, not overconfident."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=1, failures=0)

        # Posterior: Beta(1+2, 0+2) = Beta(3, 2); the 95% credible interval of
        # Beta(3, 2) is [0.194, 0.932] — NOT [1.0, 1.0].
        assert 0.15 < ci.lower < 0.5, "Cold-start should have a regularized lower bound"
        assert 0.7 < ci.upper < 0.95, "Cold-start should have upper bound < 0.95"
        assert ci.lower < ci.mean < ci.upper

    def test_compute_interval_width(self):
        """Test that confidence interval width decreases with sample size."""
        ci_10 = ConfidenceIntervalCalculator.compute_interval(successes=5, failures=5)
        ci_100 = ConfidenceIntervalCalculator.compute_interval(successes=50, failures=50)

        width_10 = ci_10.width()
        width_100 = ci_100.width()

        # More samples → narrower interval
        assert width_100 < width_10, "Larger sample should have narrower interval"

    def test_compute_interval_credible_level(self):
        """Test different credible levels."""
        ci_90 = ConfidenceIntervalCalculator.compute_interval(
            successes=8, failures=2, credible_level=0.90
        )
        ci_99 = ConfidenceIntervalCalculator.compute_interval(
            successes=8, failures=2, credible_level=0.99
        )

        # Higher credible level → wider interval
        assert ci_99.width() > ci_90.width()

    def test_confidence_interval_str(self):
        """Test string representation."""
        ci = ConfidenceIntervalCalculator.compute_interval(successes=8, failures=2)
        s = str(ci)
        assert "%" in s and "[" in s and "]" in s and "n=" in s


class TestWindowedConfidenceCalculator:
    """Test windowed confidence calculations."""

    def test_compute_for_window(self):
        """Test confidence intervals across multiple time windows."""
        successes = {"7d": 7, "30d": 20, "all": 25}
        failures = {"7d": 3, "30d": 10, "all": 15}

        intervals = WindowedConfidenceCalculator.compute_for_window(successes, failures)

        assert len(intervals) == 3
        assert "7d" in intervals and "30d" in intervals and "all" in intervals
        assert intervals["7d"].samples == 10
        assert intervals["30d"].samples == 30
        assert intervals["all"].samples == 40

    def test_trend_improving(self):
        """Test trend detection: improving."""
        intervals = {
            "7d": ConfidenceIntervalCalculator.compute_interval(5, 15),  # 25%
            "30d": ConfidenceIntervalCalculator.compute_interval(15, 35),  # 30%
            "all": ConfidenceIntervalCalculator.compute_interval(20, 30),  # 40%
        }

        trend = WindowedConfidenceCalculator.trend(
            intervals, window_order=["7d", "30d", "all"]
        )
        assert trend == "improving"

    def test_trend_declining(self):
        """Test trend detection: declining."""
        intervals = {
            "7d": ConfidenceIntervalCalculator.compute_interval(20, 30),  # 40%
            "30d": ConfidenceIntervalCalculator.compute_interval(15, 35),  # 30%
            "all": ConfidenceIntervalCalculator.compute_interval(5, 15),  # 25%
        }

        trend = WindowedConfidenceCalculator.trend(
            intervals, window_order=["7d", "30d", "all"]
        )
        assert trend == "declining"

    def test_trend_stable(self):
        """Test trend detection: stable."""
        intervals = {
            "7d": ConfidenceIntervalCalculator.compute_interval(50, 50),  # 50%
            "30d": ConfidenceIntervalCalculator.compute_interval(100, 100),  # 50%
            "all": ConfidenceIntervalCalculator.compute_interval(150, 150),  # 50%
        }

        trend = WindowedConfidenceCalculator.trend(
            intervals, window_order=["7d", "30d", "all"]
        )
        assert trend == "stable"
