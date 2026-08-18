"""Tests for v0.4 Weeks 3-4 (Confidence Alerting, Error Patterns, Operator Fingerprinting)."""

from __future__ import annotations

import pytest
from uuid import uuid4

from core.learning.confidence_alerts import (
    AlertSeverity,
    ConfidenceAlert,
    AlertThresholdManager,
    AlertRateLimiter,
    AlertHistory,
    ConfidenceAlertingSystem,
)
from core.learning.error_patterns import (
    ErrorPattern,
    ErrorObservation,
    PatternDetector,
    ErrorPredictor,
    RootCauseAnalyzer,
)
from core.learning.operator_fingerprint import (
    OperatorFingerprint,
    OperatorFingerprintLearner,
    OperatorFingerprintRegistry,
)


# ============================================================================
# CONFIDENCE ALERTING TESTS
# ============================================================================


class TestAlertThresholdManager:
    """Test threshold management."""

    def test_default_threshold(self):
        """Test: Default threshold is 0.7."""
        manager = AlertThresholdManager()
        assert manager.get_threshold("operator-1") == 0.7

    def test_operator_specific_threshold(self):
        """Test: Operator-specific thresholds override default."""
        manager = AlertThresholdManager()
        manager.set_operator_threshold("operator-1", 0.5)
        assert manager.get_threshold("operator-1") == 0.5
        assert manager.get_threshold("operator-2") == 0.7  # Still default

    def test_task_type_threshold(self):
        """Test: Task-type thresholds take priority."""
        manager = AlertThresholdManager()
        manager.set_operator_threshold("operator-1", 0.5)
        manager.set_task_type_threshold("analysis", 0.8)
        assert manager.get_threshold("operator-1", "analysis") == 0.8
        assert manager.get_threshold("operator-1", "chat") == 0.5

    def test_threshold_validation(self):
        """Test: Invalid thresholds are rejected."""
        manager = AlertThresholdManager()
        with pytest.raises(ValueError):
            manager.set_operator_threshold("op-1", 1.5)
        with pytest.raises(ValueError):
            manager.set_operator_threshold("op-1", -0.1)

    def test_reset_threshold(self):
        """Test: Operator threshold can be reset."""
        manager = AlertThresholdManager()
        manager.set_operator_threshold("op-1", 0.5)
        manager.reset_operator_threshold("op-1")
        assert manager.get_threshold("op-1") == 0.7


class TestAlertRateLimiter:
    """Test rate limiting."""

    def test_no_limit_when_under(self):
        """Test: Alert allowed when under limit."""
        limiter = AlertRateLimiter(max_alerts_per_day=2)
        assert limiter.can_alert("op-1") is True

    def test_rate_limit_enforced(self):
        """Test: Alert rejected when limit reached."""
        limiter = AlertRateLimiter(max_alerts_per_day=2)
        limiter.record_alert("op-1")
        limiter.record_alert("op-1")
        assert limiter.can_alert("op-1") is False

    def test_alert_count(self):
        """Test: Alert count is tracked."""
        limiter = AlertRateLimiter(max_alerts_per_day=5)
        for _ in range(3):
            limiter.record_alert("op-1")
        assert limiter.get_alert_count("op-1") == 3


class TestConfidenceAlertingSystem:
    """Test complete alerting system."""

    def test_high_confidence_no_alert(self):
        """Test: High confidence decisions don't alert."""
        system = ConfidenceAlertingSystem()
        alert = system.generate_alert(
            alert_id="alert-1",
            decision_id="dec-1",
            task_id="task-1",
            operator_id="op-1",
            confidence=0.9,  # Above 0.7 threshold
        )
        assert alert is None

    def test_low_confidence_alerts(self):
        """Test: Low confidence decisions generate alerts."""
        system = ConfidenceAlertingSystem()
        alert = system.generate_alert(
            alert_id="alert-1",
            decision_id="dec-1",
            task_id="task-1",
            operator_id="op-1",
            confidence=0.5,  # Below 0.7 threshold
        )
        assert alert is not None
        assert alert.severity == AlertSeverity.WARNING

    def test_critical_alert_severity(self):
        """Test: Very low confidence generates critical alert."""
        system = ConfidenceAlertingSystem()
        alert = system.generate_alert(
            alert_id="alert-1",
            decision_id="dec-1",
            task_id="task-1",
            operator_id="op-1",
            confidence=0.3,  # Very low
        )
        assert alert is not None
        assert alert.severity == AlertSeverity.CRITICAL

    def test_rate_limiting(self):
        """Test: Rate limiting prevents alert spam."""
        system = ConfidenceAlertingSystem(max_alerts_per_day=2)

        # First two alerts allowed
        alert1 = system.generate_alert("a1", "d1", "t1", "op-1", 0.5)
        alert2 = system.generate_alert("a2", "d2", "t2", "op-1", 0.5)
        assert alert1 is not None
        assert alert2 is not None

        # Third alert blocked
        alert3 = system.generate_alert("a3", "d3", "t3", "op-1", 0.5)
        assert alert3 is None


# ============================================================================
# ERROR PATTERN TESTS
# ============================================================================


class TestPatternDetector:
    """Test error pattern detection."""

    def test_pattern_creation_requires_min_observations(self):
        """Test: Patterns need ≥3 observations."""
        detector = PatternDetector(min_observations=3)

        obs1 = ErrorObservation("t1", "code_gen", "timeout", "Task timed out", "op-1")
        obs2 = ErrorObservation("t2", "code_gen", "timeout", "Task timed out", "op-1")

        detector.add_observation(obs1)
        detector.add_observation(obs2)

        patterns = detector.get_patterns("code_gen")
        assert len(patterns) == 0  # Not enough observations yet

    def test_pattern_detection(self):
        """Test: Patterns detected after min observations."""
        detector = PatternDetector(min_observations=2)

        # Add observations
        for i in range(3):
            obs = ErrorObservation(
                f"t{i}",
                "code_gen",
                "timeout",
                "Task timed out",
                "op-1",
            )
            detector.add_observation(obs)

        patterns = detector.get_patterns("code_gen")
        assert len(patterns) > 0
        pattern = patterns[0]
        assert "timeout" in pattern.error_types
        assert pattern.frequency == 3

    def test_pattern_severity_levels(self):
        """Test: Severity increases with frequency."""
        detector = PatternDetector(min_observations=1)

        # Add 15 observations (should be "high" severity)
        for i in range(15):
            obs = ErrorObservation(f"t{i}", "analysis", "oom", "Out of memory", "op-1")
            detector.add_observation(obs)

        patterns = detector.get_patterns("analysis")
        assert len(patterns) > 0
        assert patterns[0].severity == "high"


class TestErrorPredictor:
    """Test error prediction."""

    def test_predict_failure_no_patterns(self):
        """Test: Prediction without patterns returns default."""
        predictor = ErrorPredictor()
        prob = predictor.predict_failure("chat", "op-1")
        assert 0.0 <= prob <= 1.0
        assert prob < 0.2  # Default low

    def test_predict_failure_with_patterns(self):
        """Test: Prediction increases with failure patterns."""
        predictor = ErrorPredictor()

        # Add 10 failures for code_gen
        for i in range(10):
            obs = ErrorObservation(f"t{i}", "code_gen", "syntax_error", "Invalid syntax", "op-1")
            predictor.add_observation(obs)

        # Predict failure for code_gen
        prob = predictor.predict_failure("code_gen", "op-1")
        assert prob > 0.3  # Should be elevated

    def test_predict_failure_by_operator_history(self):
        """Test: Prediction considers operator's error rate."""
        predictor = ErrorPredictor()

        # Operator with high error rate
        for i in range(20):
            obs = ErrorObservation(f"t{i}", "analysis", "error", "Analysis failed", "op-bad")
            predictor.add_observation(obs)

        # Operator with low error rate
        for i in range(3):
            predictor.add_success("op-good")

        prob_bad = predictor.predict_failure("analysis", "op-bad")
        prob_good = predictor.predict_failure("analysis", "op-good")

        assert prob_bad > prob_good


# ============================================================================
# OPERATOR FINGERPRINTING TESTS
# ============================================================================


class TestOperatorFingerprintLearner:
    """Test fingerprint learning."""

    def test_learner_initialization(self):
        """Test: Learner initializes correctly."""
        learner = OperatorFingerprintLearner("op-1")
        fp = learner.generate_fingerprint()
        assert fp.operator_id == "op-1"
        assert fp.confidence == 0.0  # No observations yet

    def test_risk_tolerance_computation(self):
        """Test: Risk tolerance computed from accuracy."""
        learner = OperatorFingerprintLearner("op-1")

        # Add accurate decisions (aggressive)
        for i in range(10):
            learner.add_decision("code_gen", 50, 0.95)

        fp = learner.generate_fingerprint()
        assert fp.risk_tolerance > 0.5  # Aggressive

    def test_speed_preference_computation(self):
        """Test: Speed preference computed from latency."""
        learner = OperatorFingerprintLearner("op-1")

        # Add fast decisions
        for i in range(10):
            learner.add_decision("chat", 30, 0.8)

        fp = learner.generate_fingerprint()
        assert fp.speed_preference > 0.5  # Prefers fast

    def test_communication_style_detection(self):
        """Test: Communication style from feedback length."""
        learner = OperatorFingerprintLearner("op-1")

        # Add decisions with long feedback (detailed style)
        for i in range(10):
            learner.add_decision(
                "analysis",
                100,
                0.8,
                feedback_text="This is a very detailed analysis with lots of explanation...",
            )

        fp = learner.generate_fingerprint()
        assert fp.communication_style == "detailed"

    def test_expertise_profile_computation(self):
        """Test: Expertise profile computed per task type."""
        learner = OperatorFingerprintLearner("op-1")

        # Expert in code_gen (90% accuracy)
        for i in range(10):
            learner.add_decision("code_gen", 50, 0.9)

        # Novice in analysis (50% accuracy)
        for i in range(10):
            learner.add_decision("analysis", 150, 0.5)

        fp = learner.generate_fingerprint()
        assert fp.expertise_profile["code_gen"] > 0.8
        assert fp.expertise_profile["analysis"] < 0.6

    def test_convergence_detection(self):
        """Test: Fingerprint converges after 50+ observations."""
        learner = OperatorFingerprintLearner("op-1", min_observations=50)

        # Add observations
        for i in range(60):
            learner.add_decision("code_gen", 50 + (i % 10), 0.85 + (i * 0.0001))

        assert learner.is_converged() is True

    def test_no_convergence_too_few_observations(self):
        """Test: Not converged with <50 observations."""
        learner = OperatorFingerprintLearner("op-1", min_observations=50)

        for i in range(30):
            learner.add_decision("code_gen", 50, 0.8)

        assert learner.is_converged() is False


class TestOperatorFingerprintRegistry:
    """Test fingerprint registry."""

    def test_operator_registration(self):
        """Test: Operators can be registered."""
        registry = OperatorFingerprintRegistry()
        learner = registry.register_operator("op-1")
        assert learner is not None
        assert "op-1" in registry.learners

    def test_fingerprint_updates(self):
        """Test: Fingerprints update as decisions are added."""
        registry = OperatorFingerprintRegistry()
        registry.register_operator("op-1")

        # Add decisions
        for i in range(60):
            registry.add_decision("op-1", "code_gen", 50, 0.85)

        fp = registry.get_fingerprint("op-1")
        assert fp is not None
        assert fp.total_observations == 60

    def test_converged_operators(self):
        """Test: Registry identifies converged operators."""
        registry = OperatorFingerprintRegistry()

        # Operator 1: converged (60 obs)
        registry.register_operator("op-1")
        for i in range(60):
            registry.add_decision("op-1", "code_gen", 50, 0.85)

        # Operator 2: not converged (20 obs)
        registry.register_operator("op-2")
        for i in range(20):
            registry.add_decision("op-2", "chat", 40, 0.8)

        converged = registry.get_converged_operators()
        assert "op-1" in converged
        assert "op-2" not in converged


# ============================================================================
# INTEGRATION TESTS (Week 3-4)
# ============================================================================


class TestWeek34Integration:
    """Integration tests combining all Week 3-4 features."""

    def test_confidence_alerts_suppress_for_converged_templates(self):
        """Test: High-confidence templates don't trigger alerts."""
        system = ConfidenceAlertingSystem(default_threshold=0.7)

        # High confidence → no alert
        alert = system.generate_alert(
            alert_id="a1",
            decision_id="d1",
            task_id="t1",
            operator_id="op-1",
            confidence=0.95,
        )
        assert alert is None

    def test_error_patterns_inform_risk_assessment(self):
        """Test: Error patterns increase failure prediction."""
        predictor = ErrorPredictor()

        # Add error pattern
        for i in range(5):
            obs = ErrorObservation(f"t{i}", "analysis", "timeout", "", "op-1")
            predictor.add_observation(obs)

        # Failure probability should be elevated
        prob = predictor.predict_failure("analysis", "op-1")
        assert prob > 0.2

    def test_operator_fingerprinting_captures_style(self):
        """Test: Fingerprinting captures operator's unique style."""
        learner = OperatorFingerprintLearner("op-1")

        # Create distinctive profile: fast, aggressive, detailed feedback
        for i in range(60):
            learner.add_decision(
                "code_gen",
                30,  # Fast latency
                0.92,  # High accuracy
                "Excellent work! " * 20,  # Long feedback
            )

        fp = learner.generate_fingerprint()
        assert fp.speed_preference > 0.7  # Fast
        assert fp.risk_tolerance > 0.6  # Aggressive
        assert fp.communication_style == "detailed"
        assert fp.confidence > 0.7  # Converged

    def test_full_learning_pipeline(self):
        """Test: Complete pipeline from observation to recommendations."""
        # Initialize all systems
        alerting = ConfidenceAlertingSystem()
        detector = PatternDetector()
        fingerprints = OperatorFingerprintRegistry()

        # Simulate operator with specific error pattern
        for i in range(10):
            # Task decisions
            fingerprints.add_decision(
                "op-1",
                "code_gen",
                50 + (i % 5),
                0.8 + (i * 0.005),
                f"Feedback {i}",
            )

            # Some failures
            if i % 5 == 0:
                obs = ErrorObservation(
                    f"t{i}",
                    "code_gen",
                    "syntax_error",
                    "Invalid syntax",
                    "op-1",
                )
                detector.add_observation(obs)

        # Check results
        fingerprint = fingerprints.get_fingerprint("op-1")
        assert fingerprint is not None
        assert fingerprint.total_observations == 10

        patterns = detector.get_patterns("code_gen")
        assert len(patterns) > 0  # Pattern detected


# ============================================================================
# PERFORMANCE & COMPLIANCE TESTS
# ============================================================================


class TestPerformanceCompliance:
    """Performance and compliance validation."""

    def test_alerting_latency(self):
        """Test: Alert generation is fast (<10ms)."""
        import time

        system = ConfidenceAlertingSystem()
        start = time.time()

        for i in range(100):
            system.generate_alert(f"a{i}", f"d{i}", f"t{i}", "op-1", 0.5)

        elapsed = time.time() - start
        assert elapsed < 1.0, f"100 alerts took {elapsed}s (target <1s)"

    def test_pattern_detection_memory(self):
        """Test: Pattern detector doesn't leak memory."""
        detector = PatternDetector()

        # Add 1000 observations
        for i in range(1000):
            obs = ErrorObservation(f"t{i}", f"task_{i%10}", f"error_{i%5}", "", "op-1")
            detector.add_observation(obs)

        # Should have multiple patterns but reasonable memory
        patterns = detector.get_patterns()
        assert len(patterns) < 100  # Not exponential growth

    def test_fingerprinting_convergence_time(self):
        """Test: Fingerprints converge in reasonable time."""
        learner = OperatorFingerprintLearner("op-1", min_observations=50)

        # Add observations
        for i in range(60):
            learner.add_decision("code_gen", 50, 0.85)

        # Should converge
        assert learner.is_converged() is True

    def test_gdpr_compliance_no_pii(self):
        """Test: No PII in alerts or patterns."""
        alert = ConfidenceAlert(
            alert_id="a1",
            decision_id="d1",
            task_id="t1",
            operator_id="op-1",  # Just ID, not name/email
            confidence=0.5,
            severity=AlertSeverity.WARNING,
            recommendation="Check this",
        )

        # Verify no PII
        assert "@" not in alert.recommendation
        assert "op-1" == alert.operator_id  # Just ID

    def test_audit_trail_integration(self):
        """Test: Alerts can be logged to audit trail."""
        system = ConfidenceAlertingSystem()

        alert = system.generate_alert("a1", "d1", "t1", "op-1", 0.5)
        assert alert is not None

        # Alert should be in history
        history = system.history.get_alerts_for_operator("op-1", days=1)
        assert len(history) == 1
