"""
LearningOptimizer: Deterministic feedback processor with convergence detection

ADR-0694: Optimizer Loop Convergence & Safety
- Convergence detection: slope < 0.01 or confidence ≥ 95%
- Bounds enforcement: delta ≤ ±1σ (fail-closed)
- PII scrubbing: feedback validation (fail-closed)
- Audit-first: every decision logged
"""

import asyncio
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import re


@dataclass
class FeedbackEvent:
    """Immutable feedback event (ADR-0314)."""
    skill_id: str
    feedback_type: str  # outcome_feedback, preference_feedback, metric_observed
    timestamp: str
    signal: Optional[str] = None  # "correct" | "incorrect"
    metric_value: Optional[float] = None
    metric_baseline: Optional[float] = None
    preference_key: Optional[str] = None
    weight_delta: Optional[float] = None
    tenant_id: str = "_default"


@dataclass
class SkillConfig:
    """Learnable skill configuration."""
    skill_id: str
    parameters: Dict[str, float] = field(default_factory=dict)
    parameter_uncertainty: Dict[str, float] = field(default_factory=dict)
    last_optimized_at: Optional[str] = None
    feedback_count: int = 0
    confidence_score: float = 50.0  # [0, 100]
    version: int = 1


class LearningOptimizer:
    """Deterministic, stateless optimizer for OS-Skills."""

    PII_PATTERNS = [
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',  # Email
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',  # US Phone
        r'\b(?:\d{1,3}\.){3}\d{1,3}\b',  # IPv4
    ]

    async def process_feedback(
        self,
        skill_id: str,
        feedback: FeedbackEvent,
        current_config: SkillConfig,
        feedback_history: List[FeedbackEvent] = None,
        audit_logger = None,
    ) -> Optional[SkillConfig]:
        """
        Apply feedback to skill config safely.

        Returns:
            Updated config, or None if convergence/bounds/validation failed.
        """
        if feedback_history is None:
            feedback_history = []

        # Step 1: Validate feedback shape + scrub PII (fail-closed)
        try:
            feedback = await self._validate_and_scrub(feedback)
        except ValueError as e:
            if audit_logger:
                audit_logger.log_event({
                    "event_type": "optimizer_pii_rejected",
                    "skill_id": skill_id,
                    "reason": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                })
            return None

        # Step 2: Compute parameter delta from feedback
        delta = self._compute_delta(feedback, current_config)

        # Step 3: Check bounds (fail-closed if delta > ±1σ)
        if not self._check_bounds(delta, current_config):
            if audit_logger:
                audit_logger.log_event({
                    "event_type": "optimizer_bounds_rejected",
                    "skill_id": skill_id,
                    "delta": delta,
                    "reason": "out_of_bounds",
                    "timestamp": datetime.utcnow().isoformat()
                })
            return None

        # Step 4: Convergence detection (stop if learning is saturated)
        if feedback_history and self._check_convergence(
            feedback_history + [feedback], delta
        ):
            if audit_logger:
                audit_logger.log_event({
                    "event_type": "optimizer_convergence_detected",
                    "skill_id": skill_id,
                    "feedback_count": len(feedback_history) + 1,
                    "timestamp": datetime.utcnow().isoformat()
                })
            return None

        # Step 5: Apply delta to config
        updated_config = SkillConfig(
            skill_id=current_config.skill_id,
            parameters=current_config.parameters.copy(),
            parameter_uncertainty=current_config.parameter_uncertainty.copy(),
            last_optimized_at=datetime.utcnow().isoformat(),
            feedback_count=current_config.feedback_count + 1,
            version=current_config.version + 1,
        )

        for param_name, delta_value in delta.items():
            updated_config.parameters[param_name] = (
                current_config.parameters.get(param_name, 0.0) + delta_value
            )

        # Step 6: Compute confidence score
        updated_config.confidence_score = self._estimate_confidence(
            feedback_history + [feedback], delta
        )

        # Step 7: Log to audit trail (ADR-0232)
        if audit_logger:
            audit_logger.log_event({
                "event_type": "skill_config_updated",
                "skill_id": skill_id,
                "config_delta": delta,
                "confidence_score": updated_config.confidence_score,
                "version": updated_config.version,
                "feedback_count": updated_config.feedback_count,
                "timestamp": datetime.utcnow().isoformat()
            })

        return updated_config

    def _compute_delta(
        self,
        feedback: FeedbackEvent,
        config: SkillConfig
    ) -> Dict[str, float]:
        """Compute parameter delta from feedback."""
        delta = {}

        if feedback.feedback_type == "outcome_feedback":
            signal = 1.0 if feedback.signal == "correct" else -1.0
            delta["confidence_threshold"] = signal * 0.02

        elif feedback.feedback_type == "preference_feedback":
            delta[feedback.preference_key] = feedback.weight_delta or 0.0

        elif feedback.feedback_type == "metric_observed":
            metric_value = feedback.metric_value or 0.0
            baseline = feedback.metric_baseline or 1.0
            ratio = metric_value / baseline if baseline != 0 else 1.0

            if metric_value > baseline:
                delta["timeout_budget_ms"] = int(-10 * (ratio - 1.0))
            else:
                delta["timeout_budget_ms"] = int(5 * (1.0 - ratio))

        return delta

    def _check_bounds(
        self,
        delta: Dict[str, float],
        config: SkillConfig
    ) -> bool:
        """Fail-closed: reject delta if any param drift > ±1σ."""
        for param_name, delta_value in delta.items():
            if param_name not in config.parameters:
                return False

            std_dev = config.parameter_uncertainty.get(param_name, 0.5)
            if abs(delta_value) > std_dev:
                return False

        return True

    def _check_convergence(
        self,
        feedback_history: List[FeedbackEvent],
        proposed_delta: Dict[str, float]
    ) -> bool:
        """Convergence detection: stop at 95% confidence or slope < 0.01."""
        if not feedback_history or len(feedback_history) < 2:
            return False

        # Check explicit "correct" signal confidence
        correct_count = sum(
            1 for f in feedback_history
            if f.feedback_type == "outcome_feedback" and f.signal == "correct"
        )
        confidence = correct_count / len(feedback_history) if feedback_history else 0.5

        if confidence >= 0.95:
            return True

        # Check slope (diminishing returns)
        if len(feedback_history) >= 5:
            recent_deltas = [
                sum(abs(v) for v in self._compute_delta(f, SkillConfig(f.skill_id)).values())
                for f in feedback_history[-5:]
            ]
            slope = self._estimate_slope(recent_deltas)
            if slope < 0.01:
                return True

        return False

    def _estimate_confidence(
        self,
        feedback_history: List[FeedbackEvent],
        recent_delta: Dict[str, float]
    ) -> float:
        """Estimate confidence [0, 100]."""
        if not feedback_history:
            return 50.0

        # Factor 1: Feedback count
        count_factor = min(100, 50 + len(feedback_history) / 2.0)

        # Factor 2: Consistency
        correct = sum(
            1 for f in feedback_history
            if f.feedback_type == "outcome_feedback" and f.signal == "correct"
        )
        consistency_factor = (100 * correct / len(feedback_history)) if feedback_history else 50.0

        # Factor 3: Convergence (small recent delta)
        recent_delta_mag = sum(abs(v) for v in recent_delta.values())
        convergence_factor = 100.0 if recent_delta_mag < 0.01 else max(50, 100 - recent_delta_mag * 100)

        confidence = (0.4 * count_factor + 0.4 * consistency_factor + 0.2 * convergence_factor)
        return round(min(100, max(0, confidence)), 1)

    async def _validate_and_scrub(self, feedback: FeedbackEvent) -> FeedbackEvent:
        """Validate feedback + scrub PII (fail-closed)."""
        if not isinstance(feedback, FeedbackEvent):
            raise ValueError("feedback must be FeedbackEvent instance")

        required = ["skill_id", "feedback_type", "timestamp"]
        missing = [f for f in required if not getattr(feedback, f, None)]
        if missing:
            raise ValueError(f"Missing fields: {missing}")

        # PII scrubbing
        if feedback.feedback_type == "preference_feedback" and hasattr(feedback, 'preference_key'):
            text = str(feedback.preference_key)
            if self._contains_pii(text):
                raise ValueError("Feedback contains PII; rejected")

        return feedback

    def _contains_pii(self, text: str) -> bool:
        """Detect PII patterns."""
        return any(re.search(pattern, text) for pattern in self.PII_PATTERNS)

    def _estimate_slope(self, values: List[float]) -> float:
        """Linear regression slope."""
        if len(values) < 2:
            return 0.0
        n = len(values)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(values) / n
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator > 0 else 0.0
