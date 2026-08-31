"""Anomaly Detection & Fingerprint Poisoning Protection (Phase 3, Week 15).

Detects suspicious operator behavior and protects against adversarial attacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from typing import Optional
from statistics import mean, stdev


@dataclass
class FeedbackAnomaly:
    """Detected anomaly in feedback pattern."""

    anomaly_type: str  # "bias_shift", "threshold_gaming", "noise_spike", "pattern_violation"
    severity: str  # "info", "warning", "critical"
    description: str
    confidence: float  # 0-1
    recommended_action: str


# Alias for backward compatibility
AnomalyAlert = FeedbackAnomaly


class AnomalyDetector:
    """Detects anomalies in operator feedback patterns."""

    def __init__(self, window_size: int = 50):
        self.window_size = window_size
        self.feedback_quality_scores = deque(maxlen=window_size)
        self.feedback_sentiment_sequence = deque(maxlen=window_size)  # +1, 0, -1
        self.confidence_claims = deque(maxlen=window_size)
        self.anomaly_count = 0

    def record_feedback(
        self,
        quality_score: float,
        sentiment: int,  # -1 (bad), 0 (neutral), 1 (good)
        confidence_claim: float,
    ) -> Optional[FeedbackAnomaly]:
        """Record feedback and check for anomalies."""
        self.feedback_quality_scores.append(quality_score)
        self.feedback_sentiment_sequence.append(sentiment)
        self.confidence_claims.append(confidence_claim)

        # Check for anomalies
        return self._detect_anomalies()

    def _detect_anomalies(self) -> Optional[FeedbackAnomaly]:
        """Detect pattern anomalies."""
        if len(self.feedback_quality_scores) < 10:
            return None

        # Check 1: Bias shift (sudden sentiment change)
        recent = list(self.feedback_sentiment_sequence)[-10:]
        older = list(self.feedback_sentiment_sequence)[:-10]

        if older:
            recent_avg = mean(recent) if recent else 0
            older_avg = mean(older) if older else 0
            bias_shift = abs(recent_avg - older_avg)

            if bias_shift > 1.5:  # Massive shift
                self.anomaly_count += 1
                return FeedbackAnomaly(
                    anomaly_type="bias_shift",
                    severity="warning",
                    description=f"Feedback sentiment shifted from {older_avg:.2f} to {recent_avg:.2f}",
                    confidence=min(1.0, bias_shift / 2.0),
                    recommended_action="Review recent decisions; consider resetting fingerprint",
                )

        # Check 2: Threshold gaming (confidence spike)
        recent_conf = list(self.confidence_claims)[-10:]
        older_conf = list(self.confidence_claims)[:-10]

        if older_conf:
            recent_conf_avg = mean(recent_conf)
            older_conf_avg = mean(older_conf)

            if recent_conf_avg > older_conf_avg + 0.3:  # Confidence spike
                self.anomaly_count += 1
                return FeedbackAnomaly(
                    anomaly_type="threshold_gaming",
                    severity="info",
                    description=f"Confidence claims increased from {older_conf_avg:.2f} to {recent_conf_avg:.2f}",
                    confidence=0.6,
                    recommended_action="Monitor for behavior changes",
                )

        # Check 3: Quality variance anomaly
        if len(self.feedback_quality_scores) > 10:
            quality_std = stdev(self.feedback_quality_scores) if len(self.feedback_quality_scores) > 1 else 0

            if quality_std > 0.4:  # High variance
                return FeedbackAnomaly(
                    anomaly_type="noise_spike",
                    severity="info",
                    description=f"High variance in quality scores (std: {quality_std:.2f})",
                    confidence=0.5,
                    recommended_action="Review data quality; ensure consistent measurement",
                )

        return None

    def get_health_status(self) -> dict:
        """Get anomaly detector health status."""
        if len(self.feedback_quality_scores) == 0:
            return {"status": "insufficient_data"}

        return {
            "status": "healthy" if self.anomaly_count < 3 else "suspicious",
            "anomaly_count": self.anomaly_count,
            "avg_sentiment": mean(self.feedback_sentiment_sequence) if self.feedback_sentiment_sequence else 0,
            "avg_quality": mean(self.feedback_quality_scores),
            "confidence_avg": mean(self.confidence_claims) if self.confidence_claims else 0,
        }

    def reset_after_poisoning_detection(self) -> None:
        """Reset if poisoning detected."""
        self.feedback_quality_scores.clear()
        self.feedback_sentiment_sequence.clear()
        self.confidence_claims.clear()
        self.anomaly_count = 0
