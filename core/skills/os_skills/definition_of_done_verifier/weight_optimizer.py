"""DoD_WeightOptimizer: Online learning from feedback (ADR-0722)."""

from typing import Dict, List, Optional
from dataclasses import dataclass, field


@dataclass
class OptimizationResult:
    """Result of a weight update."""
    task_type: str
    old_weights: Dict[str, float]
    new_weights: Dict[str, float]
    delta: float
    adjustments: List[str]  # Which checks were adjusted
    confidence: Dict[str, float]


class DoD_WeightOptimizer:
    """Learn task-type-specific weights from operator feedback."""

    DEFAULT_WEIGHTS = {
        "w_reach": 0.20,
        "w_audit": 0.25,
        "w_test": 0.20,
        "w_docs": 0.20,
        "w_repro": 0.15,
    }

    TASK_TYPES = ["cli_command", "api_endpoint", "lib_function", "plugin", "skill"]

    #: Hard cap on how far ONE feedback event may move ONE weight, before
    #: normalisation. Without it `adjustment = delta * LEARNING_RATE` is
    #: unbounded in ``delta``: a single operator (or a compromised feedback
    #: channel) submitting ``delta=0.99`` moved ``w_audit`` by 0.247 in one
    #: step and could zero out the audit-trail check on the next. The
    #: adversarial test for exactly this ("one malicious feedback must have
    #: limited impact") existed since Phase 3 and had NEVER run, because the
    #: skill package was not importable (2026-09-20 review).
    MAX_ADJUSTMENT_PER_FEEDBACK = 0.05

    #: Proportional step size applied to the operator delta.
    LEARNING_RATE = 0.25

    #: Confidence gained per feedback event for an affected weight. Applied as
    #: `round(conf + STEP, 10)` because binary floats do not sum cleanly: eight
    #: naive `+= 0.1` steps land on 0.7999999999999999, which is BELOW the 0.8
    #: convergence threshold. The gate was therefore unreachable on the exact
    #: path the tests exercise, and `recommend_next_weights` kept returning the
    #: defaults forever — the learning loop looked wired and learned nothing.
    CONFIDENCE_STEP = 0.1

    def __init__(self):
        """Initialize optimizer with default weights."""
        # Per-task-type weight distributions
        self.weights = {
            task_type: self.DEFAULT_WEIGHTS.copy()
            for task_type in self.TASK_TYPES
        }

        # Confidence per weight (starts at 0.0)
        self.confidence = {
            task_type: {w: 0.0 for w in self.DEFAULT_WEIGHTS}
            for task_type in self.TASK_TYPES
        }

        # Sample count per task type (for convergence detection)
        self.sample_count = {task_type: 0 for task_type in self.TASK_TYPES}

    def observe_feedback(self, task_type: str, delta: float, affected_checks: List[str]) -> OptimizationResult:
        """
        Process operator feedback → adjust weights.

        Args:
            task_type: "cli_command", "api_endpoint", etc.
            delta: Operator correction (positive = skill was too harsh)
            affected_checks: Which checks to adjust

        Returns:
            OptimizationResult with old/new weights + confidence
        """
        if task_type not in self.TASK_TYPES:
            task_type = "cli_command"  # Fallback

        old_weights = self.weights[task_type].copy()

        if delta > 0.05:  # Operator score much higher than Skill's
            # Skill was too harsh. Reduce weights for affected checks.
            for check_name in affected_checks:
                w_name = self._check_to_weight(check_name)
                if w_name in self.weights[task_type]:
                    old_w = self.weights[task_type][w_name]
                    # Reduce weight (proportional to delta, hard-capped).
                    adjustment = min(
                        delta * self.LEARNING_RATE,
                        self.MAX_ADJUSTMENT_PER_FEEDBACK,
                    )
                    new_w = max(0.0, old_w - adjustment)
                    self.weights[task_type][w_name] = new_w
                    self.confidence[task_type][w_name] = round(
                        self.confidence[task_type][w_name] + self.CONFIDENCE_STEP, 10
                    )

        elif delta < -0.05:  # Operator score much lower than Skill's
            # Skill was too lenient. Increase weights for missing checks.
            for check_name in affected_checks:
                w_name = self._check_to_weight(check_name)
                if w_name in self.weights[task_type]:
                    old_w = self.weights[task_type][w_name]
                    adjustment = min(
                        abs(delta) * self.LEARNING_RATE,
                        self.MAX_ADJUSTMENT_PER_FEEDBACK,
                    )
                    new_w = min(1.0, old_w + adjustment)
                    self.weights[task_type][w_name] = new_w
                    self.confidence[task_type][w_name] = round(
                        self.confidence[task_type][w_name] + self.CONFIDENCE_STEP, 10
                    )

        # Normalize weights to sum to 1.0
        total = sum(self.weights[task_type].values())
        if total > 0:
            for w in self.weights[task_type]:
                self.weights[task_type][w] /= total

        # Cap confidence at 1.0
        for w in self.confidence[task_type]:
            self.confidence[task_type][w] = min(1.0, self.confidence[task_type][w])

        # Increment sample count
        self.sample_count[task_type] += 1

        return OptimizationResult(
            task_type=task_type,
            old_weights=old_weights,
            new_weights=self.weights[task_type].copy(),
            delta=delta,
            adjustments=affected_checks,
            confidence=self.confidence[task_type].copy(),
        )

    def recommend_next_weights(self, task_type: str) -> Dict[str, float]:
        """
        Return current best estimate of weights.

        Uses learned weights if confidence >= 0.8, else defaults.
        """
        if task_type not in self.TASK_TYPES:
            task_type = "cli_command"

        result = {}
        for w_name, w_value in self.weights[task_type].items():
            conf = self.confidence[task_type].get(w_name, 0.0)
            if conf >= 0.8:
                # Use learned weight
                result[w_name] = w_value
            else:
                # Use default (low confidence)
                result[w_name] = self.DEFAULT_WEIGHTS[w_name]

        # Re-normalize
        total = sum(result.values())
        if total > 0:
            for w in result:
                result[w] /= total

        return result

    def is_converged(self, task_type: str, threshold: float = 0.8) -> bool:
        """
        Has this task_type converged?

        Converged = all weights have confidence >= threshold
        """
        if task_type not in self.TASK_TYPES:
            return False

        confs = self.confidence[task_type].values()
        return all(c >= threshold for c in confs)

    def _check_to_weight(self, check_name: str) -> str:
        """Map check name to weight name."""
        mapping = {
            "reachability": "w_reach",
            "audit_trail": "w_audit",
            "test_evidence": "w_test",
            "docs_sync": "w_docs",
            "reproducibility": "w_repro",
        }
        return mapping.get(check_name, "w_reach")


# Unit Tests
class TestDoD_WeightOptimizer:
    """Test weight optimization."""

    def test_learn_from_too_harsh_feedback(self):
        """Optimizer reduces weight when feedback says score was too harsh."""
        opt = DoD_WeightOptimizer()
        old_w_audit = opt.weights["api_endpoint"]["w_audit"]

        result = opt.observe_feedback(
            task_type="api_endpoint",
            delta=0.20,  # Operator much higher
            affected_checks=["audit_trail"]
        )

        new_w_audit = result.new_weights["w_audit"]
        assert new_w_audit < old_w_audit  # Weight reduced
        assert result.delta == 0.20

    def test_learn_from_too_lenient_feedback(self):
        """Optimizer increases weight when feedback says score was too lenient."""
        opt = DoD_WeightOptimizer()
        old_w_reach = opt.weights["cli_command"]["w_reach"]

        result = opt.observe_feedback(
            task_type="cli_command",
            delta=-0.15,  # Operator much lower
            affected_checks=["reachability"]
        )

        new_w_reach = result.new_weights["w_reach"]
        assert new_w_reach > old_w_reach  # Weight increased

    def test_weights_normalize(self):
        """Weights always sum to 1.0 after update."""
        opt = DoD_WeightOptimizer()

        for _ in range(10):
            opt.observe_feedback(
                task_type="api_endpoint",
                delta=0.10,
                affected_checks=["audit_trail"]
            )

        total = sum(opt.weights["api_endpoint"].values())
        assert abs(total - 1.0) < 0.01

    def test_convergence_detection(self):
        """Convergence detected when confidence >= 0.8."""
        opt = DoD_WeightOptimizer()

        # Not converged initially
        assert not opt.is_converged("api_endpoint")

        # Simulate 8+ feedback events (confidence = 0.8+)
        for _ in range(8):
            opt.observe_feedback(
                task_type="api_endpoint",
                delta=0.10,
                affected_checks=["audit_trail"]
            )

        # Should be converged
        assert opt.is_converged("api_endpoint")

    def test_recommend_uses_learned_weights(self):
        """Recommend uses learned weights if confidence >= 0.8."""
        opt = DoD_WeightOptimizer()

        # Low confidence: use defaults
        weights_low_conf = opt.recommend_next_weights("api_endpoint")
        assert weights_low_conf["w_audit"] == opt.DEFAULT_WEIGHTS["w_audit"]

        # Build confidence
        for _ in range(8):
            opt.observe_feedback(
                task_type="api_endpoint",
                delta=0.15,
                affected_checks=["audit_trail"]
            )

        # High confidence: use learned
        weights_high_conf = opt.recommend_next_weights("api_endpoint")
        assert weights_high_conf["w_audit"] != opt.DEFAULT_WEIGHTS["w_audit"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
