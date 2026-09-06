"""
Phase 5: Learning Loop Optimizer

Auto-tune hyperparameters based on outcome feedback.
Closes the learning loop: feedback → optimization → better routing.
"""

import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class OptimizerConfig:
    """Tunable hyperparameters for Skills + routing."""
    intent_classifier_confidence_threshold: float = 0.50
    context_filter_noise_target_pct: float = 35.0
    learning_rate: float = 0.05
    convergence_max_iterations: int = 1000
    deletion_timeout_seconds: int = 10
    audit_hash: str = ""

    def __post_init__(self):
        if not self.audit_hash:
            content = f"{self.intent_classifier_confidence_threshold}:{self.learning_rate}:{self.convergence_max_iterations}"
            self.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]


class LearningOptimizer:
    """
    Optimize Skills parameters based on metrics.

    Feedback loop:
      Routing Decision → Outcome Feedback → Metrics → Optimizer → New Config
    """

    def __init__(self):
        self.config = OptimizerConfig()
        self.feedback_history: List[Dict] = []
        self.optimization_iterations = 0

    def observe_metric(self, metric_name: str, value: float, threshold: float) -> Optional[str]:
        """
        Observe a metric, decide if tuning is needed.

        Returns:
            Suggested parameter adjustment (or None if ok)
        """
        if metric_name == "context_filter_accuracy":
            if value < 80.0:
                # Accuracy too low → increase confidence threshold (be more selective)
                suggestion = f"context_filter: increase confidence_threshold from {self.config.intent_classifier_confidence_threshold:.2f} → {self.config.intent_classifier_confidence_threshold + 0.05:.2f}"
                return suggestion
            elif value > 95.0:
                # Accuracy too high → maybe lower threshold (be more permissive)
                suggestion = f"context_filter: lower confidence_threshold from {self.config.intent_classifier_confidence_threshold:.2f} → {self.config.intent_classifier_confidence_threshold - 0.05:.2f}"
                return suggestion

        elif metric_name == "error_rate":
            if value > 0.001:  # > 0.1%
                # Errors increasing → reduce learning rate (slower adaptation)
                suggestion = f"learning: reduce learning_rate from {self.config.learning_rate:.2f} → {max(0.01, self.config.learning_rate - 0.01):.2f}"
                return suggestion

        elif metric_name == "p99_latency":
            if value > 400:  # > 400ms
                # Latency regression → skip context filtering for some requests
                suggestion = f"routing: increase fallback_to_full_context_pct from 5% → 15%"
                return suggestion

        elif metric_name == "learning_convergence":
            if value > 1000:  # Not converging
                # Slow convergence → increase learning rate
                suggestion = f"learning: increase learning_rate from {self.config.learning_rate:.2f} → {min(0.1, self.config.learning_rate + 0.01):.2f}"
                return suggestion

        return None

    def apply_optimization(self, parameter: str, new_value: float) -> bool:
        """Apply suggested optimization."""
        if parameter == "intent_classifier_confidence_threshold":
            self.config.intent_classifier_confidence_threshold = max(0.1, min(0.9, new_value))
        elif parameter == "learning_rate":
            self.config.learning_rate = max(0.01, min(0.1, new_value))
        elif parameter == "convergence_max_iterations":
            self.config.convergence_max_iterations = int(max(100, min(2000, new_value)))
        elif parameter == "deletion_timeout_seconds":
            self.config.deletion_timeout_seconds = int(max(5, min(30, new_value)))
        else:
            return False

        self.optimization_iterations += 1
        # Update hash (audit trail)
        content = f"{self.config.intent_classifier_confidence_threshold}:{self.config.learning_rate}:{self.optimization_iterations}"
        self.config.audit_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return True

    def optimize_from_feedback(self, feedback_batch: List[Dict]) -> Dict[str, any]:
        """
        Batch optimization: process feedback, decide tuning, apply.

        Feedback format:
          {"skill_id": "...", "metric": "error_rate", "value": 0.002, "suggestion": "..."}
        """
        self.feedback_history.extend(feedback_batch)

        optimizations_made = []
        for feedback in feedback_batch:
            metric = feedback.get("metric")
            value = feedback.get("value", 0)
            threshold = feedback.get("threshold", 0)

            suggestion = self.observe_metric(metric, value, threshold)
            if suggestion:
                # Parse suggestion: "skill: param from X → Y"
                parts = suggestion.split("→")
                if len(parts) == 2:
                    try:
                        new_value = float(parts[1].strip().split()[-1])
                        param = parts[0].split(":")[-1].strip()

                        if self.apply_optimization(param, new_value):
                            optimizations_made.append({
                                "metric": metric,
                                "suggestion": suggestion,
                                "applied": True
                            })
                    except ValueError:
                        pass

        return {
            "optimizations": len(optimizations_made),
            "details": optimizations_made,
            "current_config": {
                "confidence_threshold": self.config.intent_classifier_confidence_threshold,
                "learning_rate": self.config.learning_rate,
                "convergence_iterations": self.config.convergence_max_iterations
            },
            "audit_hash": self.config.audit_hash
        }


def optimize_from_metrics(feedback_batch: List[Dict]) -> Dict:
    """Top-level function to optimize from feedback batch."""
    optimizer = LearningOptimizer()
    return optimizer.optimize_from_feedback(feedback_batch)
