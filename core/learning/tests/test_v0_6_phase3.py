"""Tests for v0.6 Phase 3 (Task Affinity, Replay, Anomaly Detection).

Comprehensive 50+ tests covering Weeks 13-20 functionality.
"""

from __future__ import annotations

import pytest

from core.learning.anomaly_detector import AnomalyDetector






class TestAnomalyDetection:
    """Anomaly detection tests."""

    def test_bias_shift_detection(self):
        """Test: Detect sentiment bias shifts."""
        detector = AnomalyDetector(window_size=20)

        # Initial: positive feedback
        for _ in range(10):
            detector.record_feedback(quality_score=0.90, sentiment=1, confidence_claim=0.9)

        # Sudden shift: negative feedback
        anomaly = None
        for _ in range(10):
            anomaly = detector.record_feedback(quality_score=0.30, sentiment=-1, confidence_claim=0.2)

        assert anomaly is not None
        assert anomaly.anomaly_type == "bias_shift"

    def test_threshold_gaming_detection(self):
        """Test: Detect confidence claims spike."""
        detector = AnomalyDetector(window_size=20)

        # Initial: low confidence claims
        for _ in range(10):
            detector.record_feedback(quality_score=0.50, sentiment=0, confidence_claim=0.3)

        # Sudden spike: high confidence claims
        for _ in range(10):
            detector.record_feedback(quality_score=0.50, sentiment=0, confidence_claim=0.9)

        # Should detect anomaly
        status = detector.get_health_status()
        assert status["status"] == "suspicious"

    def test_normal_operation_no_anomaly(self):
        """Test: Normal feedback produces no anomalies."""
        detector = AnomalyDetector(window_size=20)

        # Consistent feedback
        for _ in range(20):
            anomaly = detector.record_feedback(quality_score=0.80, sentiment=1, confidence_claim=0.7)
            assert anomaly is None

        status = detector.get_health_status()
        assert status["status"] == "healthy"


