"""SkillConfigTuner — Wire Learning Feedback into Skill Optimization (Track E).

Maps feedback from learning loop → config deltas per skill type.

Three tuning strategies:
1. Router: Adjust routing_threshold based on confidence/accuracy feedback
2. Adapter: Adjust attention_weight based on context relevance feedback
3. Workflow: Adjust latency_target_ms based on performance feedback

All changes clamped to ±10% (fail-closed per CLAUDE.md compliance).

ADR-0675, ADR-0676 integration.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class FeedbackSignal:
    """Normalized feedback signal from learning loop."""

    skill_id: str
    feedback_type: str  # "quality_rating", "confidence", "latency"
    value: float  # Normalized to 0-1
    execution_id: Optional[str] = None
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


@dataclass
class ConfigDelta:
    """Configuration change recommendation from tuner."""

    skill_id: str
    param_name: str
    old_value: float
    new_value: float
    delta_percent: float  # % change
    confidence: float  # How confident in this change (0-1)
    reason: str
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


class SkillConfigTuner:
    """Tunes skill config based on feedback signals.

    Responsibilities:
    1. Convert feedback signals to config deltas per skill type
    2. Apply gradient descent for parameter optimization
    3. Clamp deltas to ±10% (fail-closed safety margin)
    4. Compute convergence detection signals
    5. Audit all tuning decisions
    """

    # Gradient descent tuning parameters
    LEARNING_RATE = 0.05  # Step size for gradient updates
    MAX_DELTA_PERCENT = 0.10  # ±10% per iteration (fail-closed)
    CONVERGENCE_THRESHOLD = 0.01  # Loss improvement threshold

    # Skill type → tuning strategies
    SKILL_TYPES = {
        "router": ["routing_threshold"],
        "adapter": ["attention_weight"],
        "workflow": ["latency_target_ms"],
    }

    # Parameter bounds (domain-specific)
    PARAM_BOUNDS = {
        "routing_threshold": (0.5, 0.95),
        "attention_weight": (0.0, 1.0),
        "latency_target_ms": (50.0, 500.0),
    }

    def __init__(self, skill_type: str = "router", audit_backend=None):
        """Initialize tuner for a skill type.

        Args:
            skill_type: "router", "adapter", or "workflow"
            audit_backend: Optional audit backend for logging changes
        """
        if skill_type not in self.SKILL_TYPES:
            raise ValueError(f"Unknown skill_type: {skill_type}")

        self.skill_type = skill_type
        self.audit_backend = audit_backend
        self.tuning_history: List[ConfigDelta] = []

    def compute_loss(self, metrics: Dict[str, float]) -> float:
        """Compute loss from execution metrics.

        Loss = (1 - confidence_score) + 0.1 * (latency_ms / 100)

        Args:
            metrics: dict with keys:
                - confidence_score: 0-1
                - latency_ms: milliseconds

        Returns:
            Loss value (lower is better)
        """
        confidence = metrics.get("confidence_score", 0.5)
        latency_ms = metrics.get("latency_ms", 200.0)

        loss = (1 - confidence) + 0.1 * (latency_ms / 100.0)
        return loss

    def feedback_to_signal(
        self, feedback: Dict[str, Any]
    ) -> Optional[FeedbackSignal]:
        """Convert raw feedback dict to normalized signal.

        Args:
            feedback: dict with keys:
                - skill_id: str
                - quality_rating: 1-5 (maps to confidence 0-1)
                - execution_id: optional str

        Returns:
            FeedbackSignal or None if invalid
        """
        try:
            skill_id = feedback.get("skill_id")
            quality_rating = feedback.get("quality_rating")

            if not skill_id or quality_rating is None:
                logger.warning("Feedback missing skill_id or quality_rating")
                return None

            if not (1 <= quality_rating <= 5):
                logger.warning(f"Invalid quality_rating: {quality_rating}")
                return None

            # Map 1-5 to 0-1 confidence
            confidence = quality_rating / 5.0

            signal = FeedbackSignal(
                skill_id=skill_id,
                feedback_type="quality_rating",
                value=confidence,
                execution_id=feedback.get("execution_id"),
                timestamp=feedback.get("timestamp", datetime.utcnow().isoformat()),
            )
            return signal
        except Exception as e:
            logger.error(f"Failed to convert feedback to signal: {e}")
            return None

    def compute_config_delta(
        self,
        signal: FeedbackSignal,
        current_config: Dict[str, Any],
        loss_before: float,
    ) -> Optional[ConfigDelta]:
        """Compute config delta from feedback signal via gradient descent.

        Args:
            signal: Normalized feedback signal (confidence 0-1)
            current_config: Current skill config dict
            loss_before: Loss before optimization (baseline)

        Returns:
            ConfigDelta or None if no change recommended
        """
        # Router skill: tune routing_threshold
        if self.skill_type == "router":
            param_name = "routing_threshold"
            current_value = current_config.get(param_name, 0.7)

            # If confidence is high (>0.8), increase threshold (be more selective)
            # If confidence is low (<0.6), decrease threshold (be more permissive)
            if signal.value >= 0.8:
                gradient = 1.0  # Increase threshold
            elif signal.value < 0.6:
                gradient = -1.0  # Decrease threshold
            else:
                gradient = 0.0  # No change

            # Apply gradient with learning rate + clamping
            raw_delta = gradient * self.LEARNING_RATE
            clamped_delta = max(-self.MAX_DELTA_PERCENT, min(self.MAX_DELTA_PERCENT, raw_delta))

            if abs(clamped_delta) < 0.001:  # Effectively zero
                return None

            new_value = current_value * (1 + clamped_delta)
            new_value = self._clamp_value(param_name, new_value)

        # Adapter skill: tune attention_weight
        elif self.skill_type == "adapter":
            param_name = "attention_weight"
            current_value = current_config.get(param_name, 0.5)

            # High confidence: increase attention weight (trust context more)
            # Low confidence: decrease attention weight (trust context less)
            if signal.value >= 0.8:
                gradient = 1.0
            elif signal.value < 0.6:
                gradient = -1.0
            else:
                gradient = 0.0

            raw_delta = gradient * self.LEARNING_RATE
            clamped_delta = max(-self.MAX_DELTA_PERCENT, min(self.MAX_DELTA_PERCENT, raw_delta))

            if abs(clamped_delta) < 0.001:
                return None

            new_value = current_value * (1 + clamped_delta)
            new_value = self._clamp_value(param_name, new_value)

        # Workflow skill: tune latency_target_ms
        elif self.skill_type == "workflow":
            param_name = "latency_target_ms"
            current_value = current_config.get(param_name, 200.0)

            # High confidence with fast latency: increase target (optimize for speed)
            # Low confidence: decrease target (optimize for accuracy over speed)
            if signal.value >= 0.8:
                gradient = 1.0  # Increase latency target (allow faster executions)
            elif signal.value < 0.6:
                gradient = -1.0
            else:
                gradient = 0.0

            raw_delta = gradient * self.LEARNING_RATE
            clamped_delta = max(-self.MAX_DELTA_PERCENT, min(self.MAX_DELTA_PERCENT, raw_delta))

            if abs(clamped_delta) < 0.001:
                return None

            new_value = current_value * (1 + clamped_delta)
            new_value = self._clamp_value(param_name, new_value)

        else:
            return None

        # Create delta record
        delta = ConfigDelta(
            skill_id=signal.skill_id,
            param_name=param_name,
            old_value=current_value,
            new_value=new_value,
            delta_percent=(new_value - current_value) / current_value
            if current_value != 0
            else 0.0,
            confidence=signal.value,
            reason=f"Feedback signal: {signal.value:.2f} (type: {signal.feedback_type})",
            timestamp=datetime.utcnow().isoformat(),
        )

        return delta

    def _clamp_value(self, param_name: str, value: float) -> float:
        """Clamp parameter to valid range."""
        if param_name in self.PARAM_BOUNDS:
            lower, upper = self.PARAM_BOUNDS[param_name]
            return max(lower, min(upper, value))
        return value

    def estimate_loss_after_delta(
        self, delta: ConfigDelta, metrics: Dict[str, float]
    ) -> float:
        """Estimate loss after applying the delta.

        Args:
            delta: ConfigDelta to apply
            metrics: Current execution metrics

        Returns:
            Estimated new loss
        """
        # Simplified: assume feedback signal reflects true improvement
        # In production, this would use a learned model
        confidence = delta.confidence  # From feedback signal
        latency_ms = metrics.get("latency_ms", 200.0)

        # Estimate: confidence from feedback should reduce loss
        estimated_loss = (1 - confidence) + 0.1 * (latency_ms / 100.0)
        return estimated_loss

    def should_apply_delta(
        self, delta: ConfigDelta, loss_before: float, loss_estimated_after: float
    ) -> bool:
        """Determine if delta should be applied (converged or improved).

        Args:
            delta: ConfigDelta candidate
            loss_before: Loss before change
            loss_estimated_after: Estimated loss after change

        Returns:
            True if change should be applied
        """
        improvement = loss_before - loss_estimated_after

        if improvement >= self.CONVERGENCE_THRESHOLD:
            logger.info(
                f"Delta improves loss: {improvement:.4f} "
                f"({loss_before:.4f} → {loss_estimated_after:.4f})"
            )
            return True

        logger.info(f"Delta minimal improvement: {improvement:.4f}, skipping")
        return False

    def apply_delta_to_config(
        self, delta: ConfigDelta, current_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply delta to config (update dict in-place).

        Args:
            delta: ConfigDelta
            current_config: Config dict to update

        Returns:
            Updated config dict
        """
        new_config = current_config.copy()
        new_config[delta.param_name] = delta.new_value
        new_config["version"] = new_config.get("version", 0) + 1

        # Audit if backend available
        if self.audit_backend:
            audit_event = {
                "event_type": "skill_config_delta_applied",
                "skill_id": delta.skill_id,
                "param_name": delta.param_name,
                "delta_percent": delta.delta_percent,
                "old_value": delta.old_value,
                "new_value": delta.new_value,
                "confidence": delta.confidence,
                "reason": delta.reason,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self.audit_backend.write_event(audit_event)

        self.tuning_history.append(delta)
        logger.info(
            f"Delta applied: {delta.skill_id} {delta.param_name} "
            f"{delta.old_value:.4f}→{delta.new_value:.4f} "
            f"({delta.delta_percent*100:+.1f}%)"
        )

        return new_config

    def get_tuning_history(self) -> List[ConfigDelta]:
        """Get all tuning deltas applied."""
        return self.tuning_history.copy()

    def compute_convergence_metrics(
        self, losses: List[float], window: int = 5
    ) -> Dict[str, float]:
        """Detect convergence (loss improvement plateauing).

        Args:
            losses: List of loss values over time
            window: Window size for slope calculation

        Returns:
            dict with keys:
                - is_converged: bool
                - slope: float (recent slope)
                - plateau_confidence: float (0-1)
        """
        if len(losses) < window:
            return {
                "is_converged": False,
                "slope": 0.0,
                "plateau_confidence": 0.0,
            }

        # Compute recent slope (last 'window' samples)
        recent_losses = losses[-window:]
        diffs = [recent_losses[i + 1] - recent_losses[i] for i in range(len(recent_losses) - 1)]
        avg_slope = sum(diffs) / len(diffs)

        # If slope is near zero, converged
        is_converged = abs(avg_slope) < self.CONVERGENCE_THRESHOLD
        plateau_confidence = 1.0 if is_converged else 0.0

        return {
            "is_converged": is_converged,
            "slope": avg_slope,
            "plateau_confidence": plateau_confidence,
        }


if __name__ == "__main__":
    # Example usage
    tuner = SkillConfigTuner(skill_type="router")

    # Sample feedback
    feedback = {
        "skill_id": "os.delegation_router",
        "quality_rating": 5,  # Excellent
        "execution_id": "exec_001",
    }

    # Convert to signal
    signal = tuner.feedback_to_signal(feedback)
    print(f"Signal: {signal}")

    # Current config
    config = {
        "routing_threshold": 0.7,
        "attention_weight": 0.5,
        "latency_target_ms": 200.0,
        "version": 0,
    }

    # Metrics from execution
    metrics = {"confidence_score": 0.85, "latency_ms": 180.0}
    loss_before = tuner.compute_loss(metrics)
    print(f"Loss before: {loss_before:.4f}")

    # Compute delta
    delta = tuner.compute_config_delta(signal, config, loss_before)
    if delta:
        print(f"Delta: {delta}")
        loss_after = tuner.estimate_loss_after_delta(delta, metrics)
        print(f"Loss after: {loss_after:.4f}")
        print(f"Should apply: {tuner.should_apply_delta(delta, loss_before, loss_after)}")
