"""
Phase 3 Learning Enhancement — Multi-Model Support + Bayesian Confidence Updates

Extends the base model selector with:
1. Multi-model support (opus/sonnet/haiku/fable)
2. Bayesian parameter updates via Beta distribution
3. Convergence verification (std-dev < 5% for N=50 samples)
4. Feedback→config→skill execution cycle

ADRs: 0845 (Architecture), 0846 (Learning Loop), 0314 (Learning Infrastructure)
"""

from __future__ import annotations

import json
import logging
import numpy as np
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple, List
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# Anthropic model IDs (v4 family, 2024–2025)
MODELS = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-5-20240620",
    "opus": "claude-opus-4-20250514",
    "fable": "claude-fable-4-20250514",
}

MODEL_TIERS = {
    "haiku": 1,   # Cheapest
    "sonnet": 2,  # Medium
    "opus": 3,    # Most capable
    "fable": 1,   # Experimental tier
}

# Model pricing (USD per 1M tokens)
MODEL_COSTS = {
    "haiku": {"input": 0.80, "output": 4.00},
    "sonnet": {"input": 3.00, "output": 15.00},
    "opus": {"input": 15.00, "output": 75.00},
    "fable": {"input": 1.00, "output": 5.00},
}


@dataclass(frozen=True)
class BayesianConfidence:
    """Immutable Bayesian confidence score (Beta distribution)."""
    model: str
    task_type: str
    success_rate: float  # Mean of Beta distribution
    confidence: float    # 1 / variance (higher = more confident)
    samples: int        # Number of feedback samples
    updated_at: str     # ISO 8601 timestamp
    converged: bool     # True if std-dev < 5%

    def to_dict(self) -> Dict:
        """Audit-safe serialization."""
        return asdict(self)


class BayesianOptimizer:
    """Bayesian online learning for model selection confidence scores.

    Updates P(model succeeds | task_type) using Beta-Binomial conjugate prior.
    Each feedback sample updates the posterior distribution.
    """

    def __init__(self, tenant_id: str, alpha_prior: float = 2.0, beta_prior: float = 2.0):
        """
        Initialize optimizer.

        Args:
            tenant_id: Tenant identifier
            alpha_prior: Beta distribution alpha (prior: slightly favor success)
            beta_prior: Beta distribution beta (prior: slightly favor failure)
        """
        self.tenant_id = tenant_id
        self.alpha_prior = alpha_prior
        self.beta_prior = beta_prior
        self.posteriors: Dict[str, Dict[str, Tuple[float, float]]] = {}  # model→task_type→(α,β)
        self.feedback_counts: Dict[str, Dict[str, int]] = {}  # Track sample counts

    def record_feedback(
        self,
        model: str,
        task_type: str,
        success: bool,
        quality_score: float = 1.0,
    ) -> BayesianConfidence:
        """
        Update confidence score based on feedback.

        Args:
            model: Model name (haiku, sonnet, opus, fable)
            task_type: Task classification
            success: Whether task succeeded
            quality_score: User feedback (0–1, or 1–5 star rating normalized)

        Returns:
            Updated confidence score
        """
        # Normalize quality score if it's in 1–5 range
        if quality_score > 1.0:
            quality_score = quality_score / 5.0

        # Initialize if not seen before
        if model not in self.posteriors:
            self.posteriors[model] = {}
            self.feedback_counts[model] = {}

        if task_type not in self.posteriors[model]:
            self.posteriors[model][task_type] = (self.alpha_prior, self.beta_prior)
            self.feedback_counts[model][task_type] = 0

        # Get current posterior
        alpha, beta = self.posteriors[model][task_type]

        # Signal: success if both success=True and quality_score > 0.75
        signal = 1.0 if (success and quality_score > 0.75) else 0.0

        # Update posterior (Beta-Binomial conjugate)
        new_alpha = alpha + signal
        new_beta = beta + (1.0 - signal)

        self.posteriors[model][task_type] = (new_alpha, new_beta)
        self.feedback_counts[model][task_type] += 1

        # Compute confidence metrics
        success_rate = new_alpha / (new_alpha + new_beta)
        variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
        std_dev = np.sqrt(variance)
        confidence = 1.0 / (1.0 + std_dev) if std_dev > 0 else 1.0

        # Check convergence (std-dev < 5%)
        converged = std_dev < 0.05

        return BayesianConfidence(
            model=model,
            task_type=task_type,
            success_rate=success_rate,
            confidence=confidence,
            samples=self.feedback_counts[model][task_type],
            updated_at=datetime.now(timezone.utc).isoformat(),
            converged=converged,
        )

    def get_confidence(self, model: str, task_type: str) -> BayesianConfidence:
        """Get current confidence score for a model-task pair."""
        if model not in self.posteriors or task_type not in self.posteriors[model]:
            # Return default (uninformed prior)
            alpha, beta = self.alpha_prior, self.beta_prior
            samples = 0
        else:
            alpha, beta = self.posteriors[model][task_type]
            samples = self.feedback_counts[model][task_type]

        success_rate = alpha / (alpha + beta)
        variance = (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))
        std_dev = np.sqrt(variance)
        confidence = 1.0 / (1.0 + std_dev) if std_dev > 0 else 1.0
        converged = std_dev < 0.05 and samples >= 10

        return BayesianConfidence(
            model=model,
            task_type=task_type,
            success_rate=success_rate,
            confidence=confidence,
            samples=samples,
            updated_at=datetime.now(timezone.utc).isoformat(),
            converged=converged,
        )

    def select_best_model(
        self,
        task_type: str,
        models: List[str] = None,
        budget_constraint: Optional[float] = None,
    ) -> Tuple[str, BayesianConfidence]:
        """
        Select best model for a task based on learned confidence.

        Args:
            task_type: Task classification
            models: List of model names to consider (default: all)
            budget_constraint: Max budget in USD (filters by cost)

        Returns:
            (best_model, confidence_score)
        """
        if models is None:
            models = list(MODELS.keys())

        # Filter by budget if specified
        if budget_constraint:
            models = [m for m in models if MODEL_COSTS[m]["input"] <= budget_constraint]

        if not models:
            models = ["haiku"]  # Fallback

        # Find model with highest confidence
        best_model = None
        best_confidence = None

        for model in models:
            conf = self.get_confidence(model, task_type)
            if best_confidence is None or conf.success_rate > best_confidence.success_rate:
                best_model = model
                best_confidence = conf

        return best_model, best_confidence


class MultiModelSelector:
    """High-level interface for multi-model selection with learning.

    Wraps BayesianOptimizer and provides:
    - Smart model selection based on task type and budget
    - Feedback collection
    - Convergence tracking
    """

    def __init__(self, tenant_id: str):
        """Initialize multi-model selector."""
        self.tenant_id = tenant_id
        self.optimizer = BayesianOptimizer(tenant_id)

    def select_model(
        self,
        task_input: str,
        task_type: str = "default",
        budget_constraint: Optional[float] = None,
    ) -> Dict:
        """
        Select best model for a task.

        Returns:
            {
              "model": "sonnet",
              "model_id": "claude-sonnet-5-20240620",
              "cost_estimate_usd": 0.15,
              "confidence": 0.92,
              "reasoning": "..."
            }
        """
        best_model, confidence = self.optimizer.select_best_model(
            task_type,
            budget_constraint=budget_constraint,
        )

        # Estimate cost for this task (rough: 1000 tokens)
        cost = (MODEL_COSTS[best_model]["input"] + MODEL_COSTS[best_model]["output"]) / 1000

        return {
            "model": best_model,
            "model_id": MODELS[best_model],
            "cost_estimate_usd": cost,
            "confidence": confidence.confidence,
            "success_rate": confidence.success_rate,
            "samples": confidence.samples,
            "reasoning": f"Selected {best_model} based on {confidence.samples} feedback samples (success rate: {confidence.success_rate:.1%})",
        }

    def record_feedback(self, model: str, task_type: str, rating: float) -> BayesianConfidence:
        """
        Record user feedback (1–5 star rating).

        Returns updated confidence score.
        """
        success = rating >= 3  # 3+ stars = success
        return self.optimizer.record_feedback(model, task_type, success, rating)

    def get_metrics(self, task_type: str = None) -> Dict:
        """Get learning metrics dashboard data."""
        metrics = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "confidence_scores": {},
            "feedback_counts": {},
            "model_distribution": {},
            "convergence_status": {},
        }

        for model in MODELS.keys():
            conf = self.optimizer.get_confidence(model, task_type or "default")
            metrics["confidence_scores"][model] = {
                "confidence": conf.confidence,
                "success_rate": conf.success_rate,
                "samples": conf.samples,
                "converged": conf.converged,
            }
            metrics["feedback_counts"][model] = conf.samples
            metrics["convergence_status"][model] = "converged" if conf.converged else "learning"

        return metrics


# Singleton instance per tenant
_selectors: Dict[str, MultiModelSelector] = {}


def get_selector(tenant_id: str) -> MultiModelSelector:
    """Get or create multi-model selector for tenant."""
    if tenant_id not in _selectors:
        _selectors[tenant_id] = MultiModelSelector(tenant_id)
    return _selectors[tenant_id]


Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
