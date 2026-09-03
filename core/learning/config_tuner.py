"""Phase 2a.3: Config Tuner — Gradient descent optimization of Skill parameters.

Loss = (1 - confidence_score) + 0.1 * (latency_ms / 100)
Gradient descent: new_param = old_param + learning_rate * gradient

Safety: Config changes clamped to ±10% per iteration (fail-closed).
"""

from dataclasses import dataclass
from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    """Mutable Skill configuration (parameters to be tuned)."""
    skill_id: str
    routing_threshold: float       # Confidence threshold for routing (0.5-0.95)
    attention_weight: float        # Context weight multiplier (0.0-1.0)
    latency_target_ms: float       # Target P99 latency (50-500ms)
    version: int = 0               # Config version (incremented on update)

    def to_dict(self) -> Dict:
        return {
            "skill_id": self.skill_id,
            "routing_threshold": self.routing_threshold,
            "attention_weight": self.attention_weight,
            "latency_target_ms": self.latency_target_ms,
            "version": self.version,
        }


@dataclass
class ConfigTunerResult:
    """Tuning result: old config → new config with metrics."""
    skill_id: str
    old_config: SkillConfig
    new_config: SkillConfig
    loss_before: float
    loss_after: float
    loss_improvement: float  # negative = improvement
    converged: bool
    reason: str


class ConfigTuner:
    """Tunes Skill parameters via gradient descent."""

    LEARNING_RATE = 0.05          # Step size for gradient descent
    MAX_PARAM_DELTA = 0.10        # Max ±10% change per iteration (fail-closed)
    LOSS_EPSILON = 0.01           # Convergence threshold

    # Parameter bounds (domain-specific)
    PARAM_BOUNDS = {
        "routing_threshold": (0.5, 0.95),
        "attention_weight": (0.0, 1.0),
        "latency_target_ms": (50, 500),
    }

    def __init__(self, drift_detector, feedback_store):
        """Initialize tuner with dependencies.

        Args:
            drift_detector: ConfidenceDriftDetector for baseline metrics
            feedback_store: FeedbackStore for historical feedback
        """
        self.drift_detector = drift_detector
        self.feedback_store = feedback_store

    def tune_config(self, old_config: SkillConfig, tenant_id: str) -> ConfigTunerResult:
        """Tune a Skill's configuration via gradient descent.

        Args:
            old_config: Current configuration
            tenant_id: Tenant scope

        Returns:
            ConfigTunerResult with new config and metrics
        """
        # Step 1: Get current loss (baseline)
        drift_report = self.drift_detector.detect_drift(old_config.skill_id, tenant_id)
        loss_before = self._compute_loss(
            confidence=drift_report.feedback_confidence,
            latency_ms=old_config.latency_target_ms
        )

        # Step 2: Compute gradients for each parameter
        new_config = SkillConfig(
            skill_id=old_config.skill_id,
            routing_threshold=old_config.routing_threshold,
            attention_weight=old_config.attention_weight,
            latency_target_ms=old_config.latency_target_ms,
            version=old_config.version + 1
        )

        # Gradient for routing_threshold
        grad_routing = self._gradient_routing_threshold(drift_report.feedback_confidence)
        new_config.routing_threshold = self._clamp_param(
            old_config.routing_threshold + self.LEARNING_RATE * grad_routing,
            "routing_threshold",
            old_config.routing_threshold
        )

        # Gradient for attention_weight
        grad_attention = self._gradient_attention_weight(drift_report.feedback_confidence)
        new_config.attention_weight = self._clamp_param(
            old_config.attention_weight + self.LEARNING_RATE * grad_attention,
            "attention_weight",
            old_config.attention_weight
        )

        # Gradient for latency_target
        grad_latency = self._gradient_latency(drift_report.feedback_confidence)
        new_config.latency_target_ms = self._clamp_param(
            old_config.latency_target_ms + self.LEARNING_RATE * grad_latency,
            "latency_target_ms",
            old_config.latency_target_ms
        )

        # Step 3: Evaluate new loss
        loss_after = self._compute_loss(
            confidence=drift_report.feedback_confidence,
            latency_ms=new_config.latency_target_ms
        )

        loss_improvement = loss_after - loss_before  # Negative = improvement
        converged = abs(loss_improvement) < self.LOSS_EPSILON

        return ConfigTunerResult(
            skill_id=old_config.skill_id,
            old_config=old_config,
            new_config=new_config,
            loss_before=loss_before,
            loss_after=loss_after,
            loss_improvement=loss_improvement,
            converged=converged,
            reason=self._convergence_reason(loss_improvement, converged)
        )

    @staticmethod
    def _compute_loss(confidence: float, latency_ms: float) -> float:
        """Loss function: (1 - confidence) + 0.1 * (latency / 100)."""
        return (1.0 - confidence) + 0.1 * (latency_ms / 100.0)

    @staticmethod
    def _gradient_routing_threshold(confidence: float) -> float:
        """Gradient: Decrease threshold if confidence low (accept more tasks)."""
        # If confidence < 0.8, lower threshold to route more; if > 0.8, raise it
        return 0.8 - confidence  # Positive = increase threshold

    @staticmethod
    def _gradient_attention_weight(confidence: float) -> float:
        """Gradient: Increase weight if confidence is improving."""
        return confidence - 0.5  # Positive = increase weight

    @staticmethod
    def _gradient_latency(confidence: float) -> float:
        """Gradient: Reduce latency target if confidence low."""
        return (0.8 - confidence) * -100  # Negative = reduce latency target

    def _clamp_param(self, new_value: float, param_name: str, old_value: float) -> float:
        """Clamp parameter to bounds + max delta (fail-closed safety)."""
        # Bound 1: Hard bounds (domain-specific)
        lower, upper = self.PARAM_BOUNDS.get(param_name, (float('-inf'), float('inf')))
        clamped = max(lower, min(upper, new_value))

        # Bound 2: Max delta ±10% (fail-closed, prevent wild swings)
        max_delta = old_value * self.MAX_PARAM_DELTA
        delta_clamped = max(old_value - max_delta, min(old_value + max_delta, clamped))

        if delta_clamped != new_value:
            logger.warning(
                f"Param {param_name} clamped: {new_value:.4f} → {delta_clamped:.4f} "
                f"(bounds [{lower:.2f}, {upper:.2f}], max_delta ±{max_delta:.4f})"
            )

        return delta_clamped

    @staticmethod
    def _convergence_reason(loss_improvement: float, converged: bool) -> str:
        """Generate human-readable convergence message."""
        if converged:
            return f"Converged: loss delta {loss_improvement:.4f} < epsilon 0.01"
        elif loss_improvement < 0:
            return f"Improving: loss delta {loss_improvement:.4f} (negative = good)"
        else:
            return f"Diverging: loss delta {loss_improvement:.4f} (positive = bad)"


# ============================================================================
# Tests
# ============================================================================

def test_config_tuner():
    """Unit test: Gradient descent tuning."""

    class MockDriftReport:
        def __init__(self, feedback_conf):
            self.feedback_confidence = feedback_conf
            self.skill_id = "test_skill"

    class MockDriftDetector:
        def __init__(self, feedback_conf):
            self.feedback_conf = feedback_conf
        def detect_drift(self, skill_id, tenant_id):
            return MockDriftReport(self.feedback_conf)

    class MockFeedbackStore:
        pass

    # Test 1: Improve low-confidence Skill
    print("Test 1: Improve low-confidence Skill...")
    detector = MockDriftDetector(feedback_conf=0.5)  # 50% confidence
    tuner = ConfigTuner(detector, MockFeedbackStore())

    old_config = SkillConfig(
        skill_id="test_skill",
        routing_threshold=0.8,
        attention_weight=0.5,
        latency_target_ms=100
    )

    result = tuner.tune_config(old_config, "_default")

    assert result.loss_before > 0, "Initial loss should be positive"
    assert result.new_config.version == 1, "Version should increment"
    print(f"  Loss before: {result.loss_before:.4f}")
    print(f"  Loss after:  {result.loss_after:.4f}")
    print(f"  Improvement: {result.loss_improvement:.4f}")
    print(f"  Reason: {result.reason}")
    assert abs(result.new_config.routing_threshold - old_config.routing_threshold) <= 0.08, "Should clamp delta"
    print("  ✅ Pass")

    # Test 2: Tuning high-confidence Skill
    print("\nTest 2: Tune high-confidence Skill...")
    detector = MockDriftDetector(feedback_conf=0.95)  # 95% confidence
    tuner = ConfigTuner(detector, MockFeedbackStore())

    result = tuner.tune_config(old_config, "_default")

    print(f"  Loss before: {result.loss_before:.4f}")
    print(f"  Loss after:  {result.loss_after:.4f}")
    print(f"  Improvement: {result.loss_improvement:.4f}")
    assert result.loss_improvement < 0, "High confidence should improve loss"
    print("  ✅ Pass")

    # Test 3: Parameter bounds enforcement
    print("\nTest 3: Parameter bounds enforcement...")
    config = SkillConfig(
        skill_id="test",
        routing_threshold=0.94,  # Near upper bound
        attention_weight=0.5,
        latency_target_ms=100
    )

    detector = MockDriftDetector(feedback_conf=0.99)  # Very high
    tuner = ConfigTuner(detector, MockFeedbackStore())
    result = tuner.tune_config(config, "_default")

    assert result.new_config.routing_threshold <= 0.95, "Should not exceed upper bound"
    assert result.new_config.attention_weight >= 0.0, "Should not go below lower bound"
    print("  ✅ Bounds respected")

    print("\n✅ All config tuner tests pass!")


if __name__ == "__main__":
    print("Running Phase 2a.3 Config Tuner Tests...\n")
    test_config_tuner()
    print("\n🎉 Gradient descent ready!")
