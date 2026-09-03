"""Phase 2a.2: Confidence Drift Detection — Identifies when Skill quality diverges.

Drift = |baseline_confidence - feedback_confidence| > threshold
Triggers optimizer loop when drift detected.
"""

from dataclasses import dataclass
from typing import Optional, List, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceDriftReport:
    """Report: Skill confidence metrics + drift signal."""
    skill_id: str
    baseline_confidence: float  # Hardcoded Phase 1 value (0.8 for success)
    feedback_confidence: float  # % feedback that was "good"
    drift_magnitude: float      # |baseline - feedback|
    drift_detected: bool        # drift_magnitude > threshold
    sample_count: int           # Number of feedback samples used
    recommendation: str         # Optimizer action


class ConfidenceDriftDetector:
    """Detects when Skill decisions diverge from user feedback (confidence drift)."""

    DRIFT_THRESHOLD = 0.2  # Trigger optimizer if drift > 0.2
    MIN_SAMPLES = 10       # Need ≥10 feedback samples to trigger (avoid noise)

    def __init__(self, event_store, feedback_store):
        """Initialize drift detector.

        Args:
            event_store: EventStore for reading learning events
            feedback_store: FeedbackStore for reading ingested feedback
        """
        self.event_store = event_store
        self.feedback_store = feedback_store

    def detect_drift(self, skill_id: str, tenant_id: str, look_back_hours: int = 24) -> ConfidenceDriftReport:
        """Detect confidence drift for a Skill over last N hours.

        Args:
            skill_id: Skill to analyze
            tenant_id: Tenant scope
            look_back_hours: Look back window (default 24h)

        Returns:
            ConfidenceDriftReport with metrics + recommendation
        """
        # Step 1: Get all feedback for this Skill in time window
        feedback_samples = self.feedback_store.get_feedback(
            skill_id=skill_id,
            tenant_id=tenant_id,
            hours=look_back_hours
        )

        # Step 2: Calculate feedback confidence (% "good" out of total)
        if len(feedback_samples) == 0:
            # No feedback yet; can't detect drift
            return ConfidenceDriftReport(
                skill_id=skill_id,
                baseline_confidence=0.8,
                feedback_confidence=0.0,
                drift_magnitude=0.0,
                drift_detected=False,
                sample_count=0,
                recommendation="No feedback; check Skill execution volume"
            )

        good_count = sum(1 for f in feedback_samples if f.feedback_type.value == "good")
        feedback_confidence = good_count / len(feedback_samples)

        # Step 3: Calculate drift
        baseline_confidence = 0.8  # Phase 1 hardcoded value
        drift_magnitude = abs(baseline_confidence - feedback_confidence)
        drift_detected = (
            drift_magnitude > self.DRIFT_THRESHOLD and
            len(feedback_samples) >= self.MIN_SAMPLES
        )

        # Step 4: Generate recommendation
        if not drift_detected:
            recommendation = f"Drift {drift_magnitude:.2f} below threshold; no action"
        elif feedback_confidence > baseline_confidence:
            recommendation = f"Skill better than expected! Feedback confidence {feedback_confidence:.2f} > baseline {baseline_confidence:.2f}; consider tightening routing threshold"
        else:
            recommendation = f"Skill degraded! Feedback confidence {feedback_confidence:.2f} < baseline {baseline_confidence:.2f}; optimizer should tune config"

        return ConfidenceDriftReport(
            skill_id=skill_id,
            baseline_confidence=baseline_confidence,
            feedback_confidence=feedback_confidence,
            drift_magnitude=drift_magnitude,
            drift_detected=drift_detected,
            sample_count=len(feedback_samples),
            recommendation=recommendation
        )

    def detect_drift_batch(self, tenant_id: str) -> List[ConfidenceDriftReport]:
        """Detect drift across ALL Skills in a tenant (runs every 24h).

        Args:
            tenant_id: Tenant scope

        Returns:
            List of drift reports for all Skills with feedback
        """
        reports = []
        skill_ids = self.feedback_store.get_unique_skill_ids(tenant_id)

        for skill_id in skill_ids:
            report = self.detect_drift(skill_id, tenant_id)
            reports.append(report)
            if report.drift_detected:
                logger.warning(f"DRIFT DETECTED: {skill_id} feedback_conf={report.feedback_confidence:.2f} vs baseline={report.baseline_confidence:.2f}")

        return reports


# ============================================================================
# Tests
# ============================================================================

def test_confidence_drift():
    """Unit test: Drift detection logic."""

    class MockFeedback:
        def __init__(self, feedback_type):
            self.feedback_type = type('obj', (object,), {'value': feedback_type})()

    class MockFeedbackStore:
        def __init__(self, feedback_samples):
            self.samples = feedback_samples

        def get_feedback(self, skill_id, tenant_id, hours):
            return self.samples

        def get_unique_skill_ids(self, tenant_id):
            return ["test_skill"]

    class MockEventStore:
        pass

    # Test 1: No feedback (no drift yet)
    store = MockFeedbackStore([])
    detector = ConfidenceDriftDetector(MockEventStore(), store)
    report = detector.detect_drift("test_skill", "_default")

    assert not report.drift_detected, "No feedback should not trigger drift"
    assert report.sample_count == 0
    print("✅ Test 1: No feedback (no drift)")

    # Test 2: All feedback "good" (better than baseline)
    good_feedbacks = [MockFeedback("good") for _ in range(20)]
    store = MockFeedbackStore(good_feedbacks)
    detector = ConfidenceDriftDetector(MockEventStore(), store)
    report = detector.detect_drift("test_skill", "_default")

    assert report.feedback_confidence == 1.0, "All good feedback should be 1.0"
    assert report.drift_magnitude == 0.2, "Drift = |0.8 - 1.0| = 0.2"
    assert report.drift_detected, "Magnitude == threshold should trigger"
    assert "better than expected" in report.recommendation.lower()
    print("✅ Test 2: All feedback 'good' (drift detected, skill better)")

    # Test 3: Mixed feedback (50% good, 50% bad) = drift detected
    mixed_feedbacks = [
        MockFeedback("good") if i % 2 == 0 else MockFeedback("bad")
        for i in range(20)
    ]
    store = MockFeedbackStore(mixed_feedbacks)
    detector = ConfidenceDriftDetector(MockEventStore(), store)
    report = detector.detect_drift("test_skill", "_default")

    assert report.feedback_confidence == 0.5, "50/50 should be 0.5"
    assert report.drift_magnitude == 0.3, "Drift = |0.8 - 0.5| = 0.3"
    assert report.drift_detected, "Magnitude 0.3 > threshold 0.2"
    assert "degraded" in report.recommendation.lower()
    print("✅ Test 3: Mixed feedback (drift detected, skill degraded)")

    # Test 4: Few samples (noise) — no drift despite high magnitude
    few_feedbacks = [MockFeedback("bad") for _ in range(5)]  # Only 5 samples
    store = MockFeedbackStore(few_feedbacks)
    detector = ConfidenceDriftDetector(MockEventStore(), store)
    report = detector.detect_drift("test_skill", "_default")

    assert not report.drift_detected, "Samples < MIN_SAMPLES should not trigger"
    assert report.sample_count == 5, "Should have 5 samples"
    print("✅ Test 4: Few samples (noise suppression — no drift)")

    print("\n✅ All confidence drift tests pass!")


if __name__ == "__main__":
    print("Running Phase 2a.2 Confidence Drift Tests...\n")
    test_confidence_drift()
    print("\n🎉 Drift detection ready!")
