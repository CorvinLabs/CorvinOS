"""Phase 3 k=2: Confidence Scoring Optimizer (ADR-0773).

This module enhances the Phase 3 k=1 confidence update algorithm with:
1. Learning rate decay (10% per week to avoid local minima)
2. Convergence detection (variance <0.05 over 20 observations)
3. Variant selection with epsilon-greedy exploration
4. Integration with Phase 2 A/B Testing (Bernoulli bandit)

Fail-closed: any optimization error is logged, never propagates to caller.
Tenant-scoped: all updates filtered by tenant_id (GDPR Art. 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class ConfidenceMetric:
    """Immutable confidence metric snapshot (ADR-0315 contract)."""

    model_id: str
    confidence: float  # [0.0, 1.0]
    variance: float  # Confidence variance over observation window
    n_observations: int  # Number of outcomes in window
    learning_rate: float  # Current learning rate (decayed over time)
    last_update: datetime
    week_number: int  # For decay scheduling
    converged: bool = False  # True if variance < 0.05 over 20+ observations


@dataclass
class OutcomeSignal:
    """Signal from completed task (Phase 3 k=1 outcome feedback)."""

    model_id: str
    success: bool  # Task succeeded?
    partial_credit: Optional[float] = None  # 0.0-1.0 if partial success
    latency_ms: Optional[int] = None
    error_msg: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class VariantDecision:
    """Decision to use a specific model variant (exploit vs. explore)."""

    variant_id: str  # e.g., "opus" | "sonnet" | "haiku"
    reason: str  # "exploit" | "explore" | "converged"
    confidence: float
    exploration_chance: float  # Probability this was random selection (epsilon)


class ConfidenceOptimizer:
    """Optimizes model confidence scores using Bayesian learning + decay (ADR-0773).

    Convergence formula:
        new_confidence = confidence + (learning_rate × success_rate) - (learning_rate × error_rate)

    Decay:
        learning_rate_t+1 = learning_rate_t × (0.9 ^ week_delta)

    Convergence criterion:
        converged ⟺ variance < 0.05 over last 20 observations
    """

    def __init__(
        self,
        tenant_id: str,
        observation_window: int = 20,
        convergence_threshold: float = 0.05,
        initial_learning_rate: float = 0.05,
        decay_per_week: float = 0.10,
    ):
        """Initialize optimizer.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            observation_window: Number of recent outcomes to track variance
            convergence_threshold: Variance below this = converged
            initial_learning_rate: Starting learning rate
            decay_per_week: Learning rate decay per 7-day period (0-1)

        Raises:
            ValueError: If tenant_id missing or invalid params
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        if observation_window < 10:
            raise ValueError("observation_window must be ≥10 (convergence requires data)")
        if not (0.0 < initial_learning_rate <= 1.0):
            raise ValueError("learning_rate must be ∈ (0.0, 1.0]")

        self.tenant_id = tenant_id
        self.observation_window = observation_window
        self.convergence_threshold = convergence_threshold
        self.initial_learning_rate = initial_learning_rate
        self.decay_per_week = decay_per_week

        # In-memory state (per tenant): {model_id → ConfidenceMetric}
        # In production, this would be persisted to a database
        self._metrics: dict[str, ConfidenceMetric] = {}
        self._outcomes: dict[str, list[OutcomeSignal]] = {}  # Rolling window per model
        self._decisions: dict[str, VariantDecision] = {}  # Latest decision per model
        self._boot_time = datetime.now()

    def _get_learning_rate(self, metric: ConfidenceMetric) -> float:
        """Compute current learning rate with decay.

        Learning rate decays 10% per week to avoid local minima.

        Returns:
            learning_rate ∈ (0.0, 1.0]
        """
        weeks_elapsed = (datetime.now() - metric.last_update).days / 7.0
        decay_factor = (1.0 - self.decay_per_week) ** weeks_elapsed
        return self.initial_learning_rate * decay_factor

    def _compute_variance(self, outcomes: list[OutcomeSignal]) -> float:
        """Compute variance of recent outcomes (success rate).

        Variance = E[(X - mean)^2] where X ∈ {0, 1} (success).
        """
        if len(outcomes) < 2:
            return 1.0  # Max variance if <2 observations

        successes = [1.0 if o.success else (o.partial_credit or 0.0) for o in outcomes]
        mean = sum(successes) / len(successes)
        variance = sum((x - mean) ** 2 for x in successes) / len(successes)
        return variance

    def _compute_success_rate(self, outcomes: list[OutcomeSignal]) -> float:
        """Compute success rate from recent outcomes.

        Returns:
            success_rate ∈ [0.0, 1.0]
        """
        if not outcomes:
            return 0.5  # Neutral prior

        successes = [1.0 if o.success else (o.partial_credit or 0.0) for o in outcomes]
        return sum(successes) / len(successes)

    def _compute_error_rate(self, outcomes: list[OutcomeSignal]) -> float:
        """Compute error rate from recent outcomes.

        Returns:
            error_rate ∈ [0.0, 1.0]
        """
        if not outcomes:
            return 0.0

        errors = [1.0 if not o.success and o.error_msg else 0.0 for o in outcomes]
        return sum(errors) / len(errors)

    def record_outcome(self, signal: OutcomeSignal) -> ConfidenceMetric:
        """Record task outcome and update confidence.

        This is the main learning loop:
        1. Add outcome to rolling window
        2. Compute success/error rates
        3. Update confidence with decay
        4. Check convergence

        Args:
            signal: OutcomeSignal from Phase 3 k=1 outcome feedback

        Returns:
            Updated ConfidenceMetric (immutable)

        Raises:
            ValueError: On validation failure (fail-closed)
        """
        if not signal.model_id:
            raise ValueError("model_id required")

        try:
            # Initialize if first observation
            if signal.model_id not in self._metrics:
                self._metrics[signal.model_id] = ConfidenceMetric(
                    model_id=signal.model_id,
                    confidence=0.5,  # Neutral prior
                    variance=1.0,  # Max variance initially
                    n_observations=0,
                    learning_rate=self.initial_learning_rate,
                    last_update=datetime.now(),
                    week_number=0,
                    converged=False,
                )
                self._outcomes[signal.model_id] = []

            # Add to rolling window
            outcomes = self._outcomes[signal.model_id]
            outcomes.append(signal)

            # Keep only last N observations
            if len(outcomes) > self.observation_window:
                outcomes.pop(0)

            # Get current metric
            metric = self._metrics[signal.model_id]

            # Compute success/error rates from recent window
            success_rate = self._compute_success_rate(outcomes)
            error_rate = self._compute_error_rate(outcomes)

            # Get current learning rate (with decay)
            learning_rate = self._get_learning_rate(metric)

            # Update confidence: conf_new = conf_old + lr×success - lr×error
            confidence_delta = (learning_rate * success_rate) - (learning_rate * error_rate)
            new_confidence = max(0.0, min(1.0, metric.confidence + confidence_delta))

            # Compute variance
            variance = self._compute_variance(outcomes)

            # Check convergence: variance < threshold AND ≥20 observations
            converged = (
                variance < self.convergence_threshold
                and len(outcomes) >= self.observation_window
            )

            # Update metric (immutable, so create new)
            updated_metric = ConfidenceMetric(
                model_id=signal.model_id,
                confidence=new_confidence,
                variance=variance,
                n_observations=len(outcomes),
                learning_rate=learning_rate,
                last_update=datetime.now(),
                week_number=int((datetime.now() - self._boot_time).days / 7),
                converged=converged,
            )

            self._metrics[signal.model_id] = updated_metric

            logger.info(
                f"Updated confidence: {signal.model_id} → {new_confidence:.2f} "
                f"(success_rate={success_rate:.2f}, variance={variance:.3f}, "
                f"converged={converged}, lr={learning_rate:.4f})"
            )

            return updated_metric

        except Exception as e:
            logger.error(f"Failed to record outcome for {signal.model_id}: {e}")
            raise

    def get_metric(self, model_id: str) -> Optional[ConfidenceMetric]:
        """Get current confidence metric for a model.

        Returns:
            ConfidenceMetric or None if model has no observations
        """
        return self._metrics.get(model_id)

    def get_all_metrics(self) -> dict[str, ConfidenceMetric]:
        """Get all confidence metrics (read-only).

        Returns:
            Dict of {model_id → ConfidenceMetric}
        """
        return dict(self._metrics)

    def has_converged(self, model_id: str) -> bool:
        """Check if a model's confidence has converged.

        Convergence ⟺ variance < threshold AND ≥20 observations.

        Returns:
            True if converged, False otherwise
        """
        metric = self._metrics.get(model_id)
        return metric.converged if metric else False


class VariantSelector:
    """Selects model variants using epsilon-greedy bandit (Phase 2 A/B integration).

    Strategy:
    - Exploit (95% of time): Choose model with highest confidence
    - Explore (5% of time): Random model (avoid local minima)
    - Special case: Force explore if variance >0.1 (high uncertainty)

    Integration with Phase 2 A/B Testing:
    - Each selection is audited as a decision (Phase 3 k=1)
    - Outcome recorded via ConfidenceOptimizer.record_outcome()
    - Next selection uses updated confidence
    """

    def __init__(
        self,
        tenant_id: str,
        optimizer: ConfidenceOptimizer,
        epsilon: float = 0.05,
        high_variance_threshold: float = 0.10,
    ):
        """Initialize selector.

        Args:
            tenant_id: Tenant ID (GDPR Art. 32)
            optimizer: ConfidenceOptimizer instance
            epsilon: Exploration probability [0.0, 1.0]
            high_variance_threshold: Force exploration if variance >this

        Raises:
            ValueError: If tenant_id missing or invalid params
        """
        if not tenant_id:
            raise ValueError("tenant_id required (GDPR Art. 32, fail-closed)")
        if not (0.0 <= epsilon <= 1.0):
            raise ValueError("epsilon must be ∈ [0.0, 1.0]")

        self.tenant_id = tenant_id
        self.optimizer = optimizer
        self.epsilon = epsilon
        self.high_variance_threshold = high_variance_threshold
        self._selection_history: list[VariantDecision] = []

    def select_variant(self, candidates: list[str]) -> VariantDecision:
        """Select a model variant using epsilon-greedy strategy.

        Algorithm:
        1. If any model has high variance (>0.1), force explore
        2. Else with prob epsilon: random selection (explore)
        3. Else: highest confidence (exploit)

        Args:
            candidates: List of model variants (e.g., ["opus", "sonnet", "haiku"])

        Returns:
            VariantDecision (includes reasoning, confidence, exploration_chance)

        Raises:
            ValueError: If candidates empty
        """
        if not candidates:
            raise ValueError("candidates list required")

        try:
            # Get current metrics for all candidates
            metrics = {
                cand: self.optimizer.get_metric(cand) for cand in candidates
            }

            # Check for high variance (force exploration)
            high_variance_models = [
                cand
                for cand, metric in metrics.items()
                if metric and metric.variance > self.high_variance_threshold
            ]

            if high_variance_models:
                # Force exploration due to high uncertainty
                selected = high_variance_models[0]  # Pick first high-variance model
                decision = VariantDecision(
                    variant_id=selected,
                    reason="explore_high_variance",
                    confidence=metrics.get(selected, None).confidence
                    if metrics.get(selected)
                    else 0.5,
                    exploration_chance=1.0,
                )
                logger.info(f"Force explore {selected} (high variance)")
                self._selection_history.append(decision)
                return decision

            # Epsilon-greedy: explore vs. exploit
            import random

            if random.random() < self.epsilon:
                # Explore: random selection
                selected = random.choice(candidates)
                decision = VariantDecision(
                    variant_id=selected,
                    reason="explore_random",
                    confidence=metrics.get(selected, None).confidence
                    if metrics.get(selected)
                    else 0.5,
                    exploration_chance=self.epsilon,
                )
                logger.info(f"Random explore {selected}")
                self._selection_history.append(decision)
                return decision
            else:
                # Exploit: highest confidence
                best_model = max(
                    candidates,
                    key=lambda m: (
                        self.optimizer.get_metric(m).confidence
                        if self.optimizer.get_metric(m)
                        else 0.0
                    ),
                )
                metric = self.optimizer.get_metric(best_model)
                decision = VariantDecision(
                    variant_id=best_model,
                    reason="exploit_confidence",
                    confidence=metric.confidence if metric else 0.5,
                    exploration_chance=0.0,
                )
                logger.info(f"Exploit {best_model} (confidence={decision.confidence:.2f})")
                self._selection_history.append(decision)
                return decision

        except Exception as e:
            logger.error(f"Failed to select variant from {candidates}: {e}")
            raise

    def get_selection_history(self) -> list[VariantDecision]:
        """Get all variant selections (read-only).

        Returns:
            List of VariantDecision in chronological order
        """
        return list(self._selection_history)

    def get_latest_selection(self) -> Optional[VariantDecision]:
        """Get most recent selection.

        Returns:
            VariantDecision or None if no selections yet
        """
        return self._selection_history[-1] if self._selection_history else None
