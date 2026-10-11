"""Tests for v0.4 Weeks 3-4 (Confidence Alerting, Error Patterns, Operator Fingerprinting)."""

from __future__ import annotations

import pytest
from uuid import uuid4

from core.learning.operator_fingerprint import (
    OperatorFingerprint,
    OperatorFingerprintLearner,
    OperatorFingerprintRegistry,
)


# ============================================================================
# CONFIDENCE ALERTING TESTS
# ============================================================================








# ============================================================================
# ERROR PATTERN TESTS
# ============================================================================






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



# ============================================================================
# PERFORMANCE & COMPLIANCE TESTS
# ============================================================================


class TestPerformanceCompliance:
    """Performance and compliance validation."""



    def test_fingerprinting_convergence_time(self):
        """Test: Fingerprints converge in reasonable time."""
        learner = OperatorFingerprintLearner("op-1", min_observations=50)

        # Add observations
        for i in range(60):
            learner.add_decision("code_gen", 50, 0.85)

        # Should converge
        assert learner.is_converged() is True


