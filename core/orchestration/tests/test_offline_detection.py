"""
Tests for offline detection and fallback routing.

Coverage:
- Health status tracking
- Failure counter management
- Offline/online transitions
- Engine selection
- Quality expectations
"""

import pytest
from datetime import datetime, timedelta

from core.orchestration.offline_detection import (
    OfflineDetector,
    OfflineDetectionConfig,
    APIHealthStatus,
    HealthCheckResult,
    OfflineDecision,
)


class TestOfflineDetectionConfig:
    """Test offline detection configuration."""

    def test_default_config(self):
        """Default configuration is valid."""
        config = OfflineDetectionConfig()
        assert config.api_timeout_seconds == 5.0
        assert config.health_check_interval_seconds == 30.0
        assert config.offline_threshold_failures == 3

    def test_custom_config(self):
        """Custom configuration."""
        config = OfflineDetectionConfig(
            api_timeout_seconds=10.0,
            offline_threshold_failures=5,
        )
        assert config.api_timeout_seconds == 10.0
        assert config.offline_threshold_failures == 5


class TestHealthCheckResult:
    """Test health check results."""

    def test_healthy_result(self):
        """Healthy status."""
        result = HealthCheckResult(
            status=APIHealthStatus.HEALTHY,
            latency_ms=100.0,
        )
        assert result.status == APIHealthStatus.HEALTHY

    def test_offline_result(self):
        """Offline status."""
        result = HealthCheckResult(
            status=APIHealthStatus.OFFLINE,
            latency_ms=5000.0,
            error_message="Timeout",
        )
        assert result.status == APIHealthStatus.OFFLINE
        assert result.error_message == "Timeout"


class TestOfflineDetector:
    """Test offline detection logic."""

    def test_detector_creation(self):
        """Detector can be created."""
        detector = OfflineDetector()
        assert detector.status == APIHealthStatus.UNKNOWN
        assert not detector.is_offline()

    def test_success_resets_failures(self):
        """Successful API call resets failure counter."""
        detector = OfflineDetector()
        detector.consecutive_failures = 2

        # Report success
        detector.report_api_response(latency_ms=1000.0, success=True)

        assert detector.consecutive_failures == 0

    def test_failure_increments_counter(self):
        """Failed API call increments failure counter."""
        detector = OfflineDetector()
        detector.report_api_response(latency_ms=1000.0, success=False)

        assert detector.consecutive_failures == 1

    def test_timeout_increments_counter(self):
        """Slow API call (timeout) increments counter."""
        config = OfflineDetectionConfig(api_timeout_seconds=5.0)
        detector = OfflineDetector(config)

        # Report slow response (>5s)
        detector.report_api_response(latency_ms=6000.0, success=True)

        assert detector.consecutive_failures == 1

    def test_offline_transition(self):
        """Detector transitions to offline after threshold failures."""
        config = OfflineDetectionConfig(offline_threshold_failures=3)
        detector = OfflineDetector(config)

        # Report 3 failures
        detector.report_api_response(latency_ms=6000.0, success=True)
        assert not detector.is_offline()

        detector.report_api_response(latency_ms=6000.0, success=True)
        assert not detector.is_offline()

        detector.report_api_response(latency_ms=6000.0, success=True)
        assert detector.is_offline()

    def test_online_transition(self):
        """Detector transitions back to online on recovery."""
        config = OfflineDetectionConfig(offline_threshold_failures=3)
        detector = OfflineDetector(config)

        # Go offline
        for _ in range(3):
            detector.report_api_response(latency_ms=6000.0, success=True)
        assert detector.is_offline()

        # Report success (should transition online)
        detector.report_api_response(latency_ms=1000.0, success=True)
        assert not detector.is_offline()

    def test_offline_duration(self):
        """Offline duration is tracked."""
        detector = OfflineDetector()

        # Manually transition to offline
        detector._transition_to_offline()
        assert detector.is_offline()

        # Check duration
        duration = detector.get_offline_duration_seconds()
        assert duration >= 0.0

        # Duration increases
        detector.offline_since = detector.offline_since - timedelta(seconds=10)
        duration = detector.get_offline_duration_seconds()
        assert duration >= 10.0

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Health check can be performed."""
        detector = OfflineDetector()
        result = await detector.check_health()

        assert result is not None
        assert result.status in [
            APIHealthStatus.HEALTHY,
            APIHealthStatus.DEGRADED,
            APIHealthStatus.OFFLINE,
        ]
        assert result.latency_ms >= 0


class TestOfflineDecision:
    """Test offline routing decisions."""

    def test_online_engine_choice(self):
        """When online, choose Claude."""
        detector = OfflineDetector()
        decision = OfflineDecision(detector)

        assert decision.get_engine_choice() == "claude"

    def test_offline_engine_choice(self):
        """When offline, choose local Llama2."""
        detector = OfflineDetector()
        detector._transition_to_offline()
        decision = OfflineDecision(detector)

        assert decision.get_engine_choice() == "local_llama2"

    def test_quality_expectation_online(self):
        """Online quality is 0.98 (Claude)."""
        detector = OfflineDetector()
        decision = OfflineDecision(detector)

        quality = decision.get_quality_expectation()
        assert quality == 0.98

    def test_quality_expectation_offline(self):
        """Offline quality is 0.85 (Llama2)."""
        detector = OfflineDetector()
        detector._transition_to_offline()
        decision = OfflineDecision(detector)

        quality = decision.get_quality_expectation()
        assert quality == 0.85

    def test_should_use_offline_engine(self):
        """Correctly determine offline mode."""
        detector = OfflineDetector()
        decision = OfflineDecision(detector)

        assert not decision.should_use_offline_engine()

        detector._transition_to_offline()
        assert decision.should_use_offline_engine()
