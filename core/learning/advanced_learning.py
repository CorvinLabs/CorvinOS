"""
Phase 4.2: Advanced Learning — Bayesian tuning, feedback quality scoring, drift detection.

Responsibilities:
1. Bayesian hyperparameter optimization (tune the tuning optimizer itself)
2. Feedback quality scoring (discount noisy operator feedback)
3. Concept drift detection (when environment changes, reset thresholds)
4. Optimizer learning curve (confidence improvement over time)
5. Convergence detection (when to stop tuning)

Audit-first: Every learning decision logged to audit chain.
Thread-safe: RLock protection on shared state.
Tenant-scoped: All queries filtered by tenant_id.

ADR-0586: Advanced Learning (Bayesian, quality scoring, drift detection)
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from collections import defaultdict
import math

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


@dataclass(frozen=True)
class BayesianUpdate:
    """Bayesian hyperparameter update (immutable)."""
    skill_id: str
    metric_name: str
    param_name: str  # e.g., "confidence_threshold"
    prior_value: float
    prior_std: float
    likelihood_mean: float  # Observed accuracy
    posterior_value: float
    posterior_std: float
    confidence: float  # P(posterior is correct)
    updated_at: str  # ISO 8601


@dataclass(frozen=True)
class FeedbackQualityScore:
    """Operator feedback quality assessment."""
    operator_id: str
    skill_id: str
    num_feedbacks: int
    accuracy_rate: float  # % of feedbacks that were "correct" (validated retrospectively)
    noise_level: float  # 0.0 (clean) to 1.0 (noisy)
    reliability_score: float  # 0.0 to 1.0 (1 = highly reliable)
    recommendation: str  # "trust", "weight_lower", "investigate", "exclude"


@dataclass(frozen=True)
class DriftSignal:
    """Concept drift detection signal."""
    skill_id: str
    metric_name: str
    detected_at: str  # ISO 8601
    drift_type: str  # "sudden_jump", "gradual_shift", "seasonal"
    magnitude: float  # 0.0 to 1.0 (severity)
    kl_divergence: float  # K-L divergence of feedback distribution
    recommendation: str  # "reset_thresholds", "retrain", "investigate"


@dataclass(frozen=True)
class LearningCurve:
    """Optimizer learning curve (confidence improvement over time)."""
    skill_id: str
    initial_confidence: float
    current_confidence: float
    num_updates: int
    convergence_estimate: float  # % toward convergence
    convergence_eta_minutes: Optional[float]  # Estimated time to convergence


# ============================================================================
# Advanced Learning Engine
# ============================================================================


class AdvancedLearningEngine:
    """
    Multi-faceted learning engine with Bayesian optimization, quality scoring, drift detection.

    Fail-closed: All learning signals are advisory (no automatic config changes).
    Recommendations are logged and require operator approval.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.lock = RLock()

        # Bayesian priors (by skill_id:metric_name:param_name)
        self.bayesian_state: Dict[str, Dict[str, float]] = defaultdict(dict)

        # Operator feedback quality scores (by operator_id)
        self.operator_quality: Dict[str, FeedbackQualityScore] = {}

        # Feedback history (for quality scoring)
        self.feedback_history: List[Dict[str, Any]] = []

        # Drift signals (cache)
        self.drift_signals: List[DriftSignal] = []

        # Learning curves (by skill_id)
        self.learning_curves: Dict[str, LearningCurve] = {}

        # Audit trail
        self.audit_log: List[Dict[str, Any]] = []

        # Configuration
        self.kl_divergence_threshold = 0.3  # Threshold for drift detection
        self.feedback_window_size = 50  # Look-back window for quality scoring

    def bayesian_update(
        self,
        skill_id: str,
        metric_name: str,
        param_name: str,
        prior_value: float,
        prior_std: float,
        observed_accuracy: float,  # Recent approval accuracy
    ) -> BayesianUpdate:
        """Update hyperparameter belief using Bayesian inference.

        Prior: what we believed before
        Likelihood: what we observed (accuracy of recent approvals)
        Posterior: updated belief after observing data

        Algorithm:
            posterior_mean = (prior_mean / prior_var + observed_accuracy / obs_var) / (1/prior_var + 1/obs_var)
            posterior_std = sqrt(1 / (1/prior_var + 1/obs_var))

        Args:
            skill_id: Skill being tuned
            metric_name: Metric being optimized
            param_name: Parameter name (e.g., "confidence_threshold")
            prior_value: Prior belief
            prior_std: Prior uncertainty
            observed_accuracy: Observed approval accuracy

        Returns:
            BayesianUpdate with posterior distribution
        """
        with self.lock:
            # Posteriors via Bayes' theorem (simplified conjugate prior)
            prior_var = prior_std ** 2
            obs_var = 0.1 ** 2  # Observation uncertainty (assume 10%)

            posterior_mean = (
                prior_value / prior_var + observed_accuracy / obs_var
            ) / (1.0 / prior_var + 1.0 / obs_var)
            posterior_std = math.sqrt(1.0 / (1.0 / prior_var + 1.0 / obs_var))

            # Confidence: how much better is posterior than prior?
            confidence = min(1.0, 1.0 - (posterior_std / prior_std))

            update = BayesianUpdate(
                skill_id=skill_id,
                metric_name=metric_name,
                param_name=param_name,
                prior_value=prior_value,
                prior_std=prior_std,
                likelihood_mean=observed_accuracy,
                posterior_value=posterior_mean,
                posterior_std=posterior_std,
                confidence=confidence,
                updated_at=datetime.utcnow().isoformat(),
            )

            # Store in state
            key = f"{skill_id}:{metric_name}:{param_name}"
            self.bayesian_state[key] = {
                "mean": posterior_mean,
                "std": posterior_std,
                "confidence": confidence,
            }

            # Audit
            self._audit_event({
                "event_type": "bayesian_update",
                "skill_id": skill_id,
                "metric_name": metric_name,
                "param_name": param_name,
                "prior_value": prior_value,
                "posterior_value": posterior_mean,
                "confidence": confidence,
            })

            return update

    def score_feedback_quality(self, operator_id: str, skill_id: str) -> FeedbackQualityScore:
        """Assess the quality of an operator's recent feedback.

        Algorithm:
            1. Look back at last N feedbacks from this operator
            2. For each feedback, check if it was "correct" (approved item worked well)
            3. Compute accuracy rate
            4. Detect noise (sudden reversals, contradictions)
            5. Assign reliability score + recommendation

        Returns:
            FeedbackQualityScore with reliability metrics
        """
        with self.lock:
            # Filter feedback for this operator + skill
            operator_feedbacks = [
                f
                for f in self.feedback_history[-self.feedback_window_size :]
                if f.get("operator_id") == operator_id and f.get("skill_id") == skill_id
            ]

            if not operator_feedbacks:
                # No history, assume neutral
                score = FeedbackQualityScore(
                    operator_id=operator_id,
                    skill_id=skill_id,
                    num_feedbacks=0,
                    accuracy_rate=0.5,
                    noise_level=0.5,
                    reliability_score=0.5,
                    recommendation="trust",
                )
                return score

            # Compute accuracy (% feedbacks that were correct)
            correct = sum(1 for f in operator_feedbacks if f.get("correct", False))
            accuracy_rate = correct / len(operator_feedbacks)

            # Detect noise (sudden reversals)
            noise_count = 0
            for i in range(1, len(operator_feedbacks)):
                prev_decision = operator_feedbacks[i - 1].get("decision", "")
                curr_decision = operator_feedbacks[i].get("decision", "")
                if prev_decision != curr_decision:
                    noise_count += 1
            noise_level = noise_count / len(operator_feedbacks)

            # Reliability score = accuracy - noise_penalty
            reliability_score = max(0.0, accuracy_rate - (noise_level * 0.2))

            # Recommendation
            if reliability_score > 0.8:
                recommendation = "trust"
            elif reliability_score > 0.6:
                recommendation = "weight_lower"
            elif reliability_score > 0.4:
                recommendation = "investigate"
            else:
                recommendation = "exclude"

            score = FeedbackQualityScore(
                operator_id=operator_id,
                skill_id=skill_id,
                num_feedbacks=len(operator_feedbacks),
                accuracy_rate=accuracy_rate,
                noise_level=noise_level,
                reliability_score=reliability_score,
                recommendation=recommendation,
            )

            # Store
            self.operator_quality[f"{operator_id}:{skill_id}"] = score

            # Audit
            self._audit_event({
                "event_type": "feedback_quality_scored",
                "operator_id": operator_id,
                "skill_id": skill_id,
                "accuracy_rate": accuracy_rate,
                "recommendation": recommendation,
            })

            return score

    def detect_concept_drift(self, skill_id: str, metric_name: str) -> Optional[DriftSignal]:
        """Detect concept drift in feedback distribution.

        Algorithm:
            1. Compute feedback distribution (recent vs. historical)
            2. Calculate K-L divergence
            3. If divergence > threshold, drift detected
            4. Classify drift type (sudden vs. gradual)

        Returns:
            DriftSignal if drift detected, None otherwise
        """
        with self.lock:
            # Simplified: check if recent accuracy differs from historical
            recent_feedbacks = [
                f
                for f in self.feedback_history[-10:]
                if f.get("skill_id") == skill_id and f.get("metric_name") == metric_name
            ]

            historical_feedbacks = [
                f
                for f in self.feedback_history[:-10]
                if f.get("skill_id") == skill_id and f.get("metric_name") == metric_name
            ]

            if len(recent_feedbacks) < 5 or len(historical_feedbacks) < 5:
                # Not enough data
                return None

            # Compute distributions
            recent_correct = sum(1 for f in recent_feedbacks if f.get("correct", False))
            historical_correct = sum(
                1 for f in historical_feedbacks if f.get("correct", False)
            )

            recent_rate = recent_correct / len(recent_feedbacks)
            historical_rate = historical_correct / len(historical_feedbacks)

            # K-L divergence (simplified)
            kl_div = max(0.0, abs(recent_rate - historical_rate))

            if kl_div < self.kl_divergence_threshold:
                return None

            # Drift detected!
            # Classify: sudden if big jump, gradual otherwise
            drift_type = "sudden_jump" if kl_div > 0.5 else "gradual_shift"

            signal = DriftSignal(
                skill_id=skill_id,
                metric_name=metric_name,
                detected_at=datetime.utcnow().isoformat(),
                drift_type=drift_type,
                magnitude=min(1.0, kl_div),
                kl_divergence=kl_div,
                recommendation="reset_thresholds" if drift_type == "sudden_jump" else "retrain",
            )

            self.drift_signals.append(signal)

            # Audit
            self._audit_event({
                "event_type": "drift_detected",
                "skill_id": skill_id,
                "metric_name": metric_name,
                "drift_type": drift_type,
                "kl_divergence": kl_div,
            })

            return signal

    def update_learning_curve(self, skill_id: str, new_confidence: float) -> LearningCurve:
        """Update learning curve for a skill.

        Tracks: confidence improvement over time, convergence estimate

        Args:
            skill_id: Skill ID
            new_confidence: Current confidence level [0.0, 1.0]

        Returns:
            LearningCurve with convergence metrics
        """
        with self.lock:
            existing = self.learning_curves.get(skill_id)

            initial_confidence = (
                existing.initial_confidence if existing else 0.5
            )
            num_updates = (existing.num_updates if existing else 0) + 1

            # Convergence estimate: how close to 1.0?
            convergence_estimate = min(1.0, new_confidence)

            # ETA to convergence (simplified: assume exponential decay)
            if new_confidence > 0.95:
                eta_minutes = None  # Already converged
            else:
                improvement_per_update = new_confidence - initial_confidence
                if improvement_per_update > 0:
                    updates_to_convergence = (0.95 - new_confidence) / improvement_per_update
                    eta_minutes = updates_to_convergence * 5.0  # Assume 5 min per update
                else:
                    eta_minutes = None

            curve = LearningCurve(
                skill_id=skill_id,
                initial_confidence=initial_confidence,
                current_confidence=new_confidence,
                num_updates=num_updates,
                convergence_estimate=convergence_estimate,
                convergence_eta_minutes=eta_minutes,
            )

            self.learning_curves[skill_id] = curve

            # Audit
            self._audit_event({
                "event_type": "learning_curve_updated",
                "skill_id": skill_id,
                "new_confidence": new_confidence,
                "num_updates": num_updates,
                "eta_minutes": eta_minutes,
            })

            return curve

    def record_feedback(
        self,
        operator_id: str,
        skill_id: str,
        metric_name: str,
        decision: str,
        correct: bool,
    ) -> None:
        """Record operator feedback for quality tracking.

        Args:
            operator_id: Who gave the feedback
            skill_id: Which skill
            metric_name: Which metric
            decision: "approve" or "reject"
            correct: Whether the feedback was correct (validated retrospectively)
        """
        with self.lock:
            feedback = {
                "operator_id": operator_id,
                "skill_id": skill_id,
                "metric_name": metric_name,
                "decision": decision,
                "correct": correct,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self.feedback_history.append(feedback)

            # Keep history to last 1000 entries
            if len(self.feedback_history) > 1000:
                self.feedback_history.pop(0)

    def get_recommendations(self) -> Dict[str, List[str]]:
        """Get all current recommendations (from drift, feedback quality, etc).

        Returns:
            Dict mapping recommendation_type → List[messages]
        """
        with self.lock:
            recommendations = {
                "drift_detected": [],
                "operator_quality_issue": [],
                "convergence_near": [],
            }

            # Drift recommendations
            for signal in self.drift_signals[-5:]:
                recommendations["drift_detected"].append(
                    f"{signal.skill_id}/{signal.metric_name}: {signal.recommendation}"
                )

            # Operator quality recommendations
            for quality in self.operator_quality.values():
                if quality.recommendation in ("investigate", "exclude"):
                    recommendations["operator_quality_issue"].append(
                        f"{quality.operator_id} for {quality.skill_id}: {quality.recommendation}"
                    )

            # Convergence recommendations
            for curve in self.learning_curves.values():
                if curve.convergence_estimate > 0.9:
                    recommendations["convergence_near"].append(
                        f"{curve.skill_id}: {(curve.convergence_estimate * 100):.1f}% converged"
                    )

            return recommendations

    def _audit_event(self, event: Dict[str, Any]) -> None:
        """Log audit event (thread-safe)."""
        with self.lock:
            event["tenant_id"] = self.tenant_id
            event["timestamp"] = datetime.utcnow().isoformat()
            self.audit_log.append(event)

            if len(self.audit_log) > 1000:
                self.audit_log.pop(0)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log (copy)."""
        with self.lock:
            return self.audit_log.copy()


if __name__ == "__main__":
    # Example usage
    engine = AdvancedLearningEngine(tenant_id="_default")

    # Record some feedback
    engine.record_feedback("user:alice", "skill_a", "latency", "approve", True)
    engine.record_feedback("user:alice", "skill_a", "latency", "approve", True)
    engine.record_feedback("user:alice", "skill_a", "latency", "reject", False)

    # Score operator quality
    quality = engine.score_feedback_quality("user:alice", "skill_a")
    print(f"Operator quality: {quality.reliability_score:.2f} ({quality.recommendation})")

    # Bayesian update
    update = engine.bayesian_update(
        skill_id="skill_a",
        metric_name="latency",
        param_name="confidence_threshold",
        prior_value=0.7,
        prior_std=0.1,
        observed_accuracy=0.85,
    )
    print(f"Updated threshold: {update.posterior_value:.3f} ± {update.posterior_std:.3f}")

    # Update learning curve
    curve = engine.update_learning_curve("skill_a", 0.85)
    print(f"Learning curve: {(curve.convergence_estimate * 100):.1f}% converged")
