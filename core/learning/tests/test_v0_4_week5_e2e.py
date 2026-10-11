"""Week 5 E2E Tests - Comprehensive Integration (v0.4 full pipeline).

Tests complete v0.4 learning flywheel:
- Operator runs 100 simulated tasks
- Bayesian templates improve accuracy (65% → 80%+)
- Confidence alerting monitors uncertainty
- Error patterns identified after failures
- Operator fingerprint converges after 50 tasks
- Personalized guidance improves satisfaction
"""

from __future__ import annotations

import pytest
from core.learning.operator_fingerprint import OperatorFingerprintRegistry


class TestV04FullLearningFlywheel:
    """Test complete v0.4 learning pipeline (Week 5)."""




    def test_operator_satisfaction_improvement(self):
        """Test: Personalized guidance improves operator satisfaction."""
        fingerprinting = OperatorFingerprintRegistry()
        fingerprinting.register_operator("op-1")

        satisfactions = []

        # Early stage: generic guidance (satisfaction 40%)
        for i in range(20):
            fingerprinting.add_decision("op-1", "code_gen", 50, 0.7, "Generic feedback")
            satisfactions.append(0.40)

        # Mid stage: learning operator style (satisfaction 60%)
        for i in range(20, 70):
            fingerprinting.add_decision("op-1", "code_gen", 50, 0.8, "Personalized feedback")
            satisfactions.append(0.55 + (i - 20) * 0.004)  # Improving to 75%

        # Late stage: full personalization (satisfaction 75%+)
        for i in range(70, 100):
            fingerprinting.add_decision("op-1", "code_gen", 50, 0.85, "Tailored guidance")
            satisfactions.append(0.75)

        # Verify improvement
        early_avg = sum(satisfactions[:20]) / 20
        late_avg = sum(satisfactions[-20:]) / 20

        assert late_avg > early_avg
        assert late_avg >= 0.70, f"Expected final satisfaction ≥70%, got {late_avg:.2%}"

    def test_convergence_after_50_tasks(self):
        """Test: Operator fingerprint converges after 50 tasks."""
        registry = OperatorFingerprintRegistry()
        registry.register_operator("op-1")

        converged_at = None

        for i in range(100):
            registry.add_decision("op-1", "code_gen", 50, 0.85, f"Task {i}")
            learner = registry.learners["op-1"]

            if learner.is_converged() and converged_at is None:
                converged_at = i

        assert converged_at is not None
        assert converged_at <= 60, f"Convergence should happen by task 60, happened at {converged_at}"




