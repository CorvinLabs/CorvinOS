"""Bayesian Template Tuning (Phase 1, Week 1-2).

Learns task templates from outcomes using Bayesian updating.
Tracks accuracy and latency distributions, identifies converged templates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class TaskTemplate:
    """A task execution template."""

    template_id: str
    task_type: str
    engine: str
    prompt_style: str  # "verbose", "concise", "structured"
    temperature: float
    max_tokens: int


@dataclass
class TaskOutcome:
    """Outcome from a template execution."""

    task_id: str
    template_id: str
    accuracy: float  # 0.0-1.0
    latency_ms: int
    cost_cents: int
    quality_score: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class BetaDistribution:
    """Beta distribution parameters (for accuracy, which is 0-1)."""

    alpha: float  # Shape parameter 1
    beta: float  # Shape parameter 2

    def mean(self) -> float:
        """Compute mean of Beta(alpha, beta)."""
        return self.alpha / (self.alpha + self.beta)

    def variance(self) -> float:
        """Compute variance of Beta(alpha, beta)."""
        alpha = self.alpha
        beta = self.beta
        return (alpha * beta) / ((alpha + beta) ** 2 * (alpha + beta + 1))

    def std_dev(self) -> float:
        """Compute standard deviation."""
        return math.sqrt(self.variance())


@dataclass
class GaussianDistribution:
    """Gaussian (normal) distribution parameters (for latency)."""

    mean: float
    variance: float

    def std_dev(self) -> float:
        """Compute standard deviation."""
        return math.sqrt(self.variance)


class BayesianTemplateTuner:
    """Learns task templates from outcomes using Bayesian updating.

    For accuracy: Beta distribution (conjugate prior for Bernoulli)
    For latency: Gaussian distribution (normal approximation)
    """

    def __init__(self, template: TaskTemplate):
        self.template = template
        self.outcomes: list[TaskOutcome] = []

        # Priors (weakly informative)
        # Accuracy: Beta(2, 2) — prior belief that accuracy is ~0.5 with some uncertainty
        self.accuracy_prior = BetaDistribution(alpha=2.0, beta=2.0)
        self.accuracy_posterior = BetaDistribution(alpha=2.0, beta=2.0)

        # Latency: Gaussian with mean 100ms, std 50ms
        self.latency_prior = GaussianDistribution(mean=100.0, variance=2500.0)
        self.latency_posterior = GaussianDistribution(mean=100.0, variance=2500.0)

    def update(self, outcome: TaskOutcome) -> None:
        """Bayesian update with new outcome.

        For accuracy:
        - Update Beta distribution using conjugate updating
        - Treat accuracy as binary outcome (success if accuracy > 0.5)

        For latency:
        - Update Gaussian using normal updating rules
        """
        self.outcomes.append(outcome)

        # Update accuracy (Beta distribution conjugate update)
        # If outcome.accuracy > 0.5, count as success (outcome=1), else failure (outcome=0)
        success = 1 if outcome.accuracy > 0.5 else 0
        self._update_accuracy_beta(success)

        # Update latency (Gaussian Bayesian update)
        self._update_latency_gaussian(outcome.latency_ms)

    def _update_accuracy_beta(self, observation: int) -> None:
        """Update Beta distribution with binary observation.

        Conjugate update: Beta(α, β) + observation → Beta(α + s, β + f)
        where s = successes, f = failures
        """
        if observation == 1:
            new_alpha = self.accuracy_posterior.alpha + 1
            new_beta = self.accuracy_posterior.beta
        else:
            new_alpha = self.accuracy_posterior.alpha
            new_beta = self.accuracy_posterior.beta + 1

        self.accuracy_posterior = BetaDistribution(alpha=new_alpha, beta=new_beta)

    def _update_latency_gaussian(self, observation: float) -> None:
        """Update Gaussian distribution with new latency observation.

        Bayesian update with known observation variance.
        Formula:
        - posterior_var = 1 / (1/prior_var + 1/obs_var)
        - posterior_mean = posterior_var * (prior_mean/prior_var + obs/obs_var)
        """
        # Assume observation has variance of 10% of observation value (natural variation)
        obs_variance = max(100.0, 0.1 * observation)

        # Compute posterior parameters
        prior_var = self.latency_posterior.variance
        prior_mean = self.latency_posterior.mean

        posterior_precision = (1 / prior_var) + (1 / obs_variance)
        posterior_var = 1 / posterior_precision
        posterior_mean = posterior_var * (
            (prior_mean / prior_var) + (observation / obs_variance)
        )

        self.latency_posterior = GaussianDistribution(
            mean=posterior_mean, variance=posterior_var
        )

    def convergence_check(self) -> bool:
        """Check if posterior has converged (variance is small).

        Convergence criteria:
        - At least 50 observations
        - Accuracy posterior variance < 0.05
        - Observed accuracy variance (last 50 outcomes) < 0.05
        - Latency posterior std dev < 20ms

        The posterior-variance check alone is vacuous: Beta(α, β) variance is
        at most ≈ 1/(4·N) once N ≥ 50, i.e. ≤ 0.005 for ANY mix of outcomes,
        so a template alternating 0.9 / 0.1 accuracy "converged" (N-07,
        ``test_convergence_check_requires_low_variance``). Convergence means
        the estimate is stable AND the outcomes are consistent, so the sample
        variance of the observed accuracies is checked as well.
        """
        if len(self.outcomes) < 50:
            return False

        # Check accuracy convergence (posterior)
        accuracy_variance = self.accuracy_posterior.variance()
        if accuracy_variance > 0.05:
            return False

        # Check outcome consistency (sample variance of the last 50 accuracies)
        recent = [o.accuracy for o in self.outcomes[-50:]]
        mean_acc = sum(recent) / len(recent)
        sample_variance = sum((a - mean_acc) ** 2 for a in recent) / len(recent)
        if sample_variance > 0.05:
            return False

        # Check latency convergence
        latency_std_dev = self.latency_posterior.std_dev()
        if latency_std_dev > 20.0:
            return False

        return True

    def get_accuracy_distribution(self) -> dict:
        """Get accuracy distribution summary."""
        return {
            "mean": self.accuracy_posterior.mean(),
            "variance": self.accuracy_posterior.variance(),
            "std_dev": self.accuracy_posterior.std_dev(),
            "alpha": self.accuracy_posterior.alpha,
            "beta": self.accuracy_posterior.beta,
        }

    def get_latency_distribution(self) -> dict:
        """Get latency distribution summary."""
        return {
            "mean": self.latency_posterior.mean,
            "variance": self.latency_posterior.variance,
            "std_dev": self.latency_posterior.std_dev(),
        }

    def get_confidence_interval(self, percentile: float = 0.95) -> dict:
        """Get confidence interval for accuracy posterior.

        For Beta distribution, approximate using normal approximation
        when sample size is large.
        """
        mean = self.accuracy_posterior.mean()
        std = self.accuracy_posterior.std_dev()

        # Z-score for percentile (approximate for 95%)
        z = 1.96 if percentile == 0.95 else 2.576 if percentile == 0.99 else 1.0

        return {
            "mean": mean,
            "lower": max(0.0, mean - z * std),
            "upper": min(1.0, mean + z * std),
            "confidence": percentile,
        }

    def recommend_action(self) -> str:
        """Recommend action based on convergence and accuracy.

        Returns:
        - "adopt": template has converged and accuracy is good
        - "iterate": template is improving but hasn't converged
        - "reject": template has low accuracy
        """
        if len(self.outcomes) < 5:
            return "collect_data"

        mean_accuracy = self.accuracy_posterior.mean()
        converged = self.convergence_check()

        if converged and mean_accuracy > 0.75:
            return "adopt"
        elif mean_accuracy > 0.60 and not converged:
            return "iterate"
        else:
            return "reject"


class TemplateRegistry:
    """Registry of templates and their Bayesian tuners."""

    def __init__(self):
        self.tuners: dict[str, BayesianTemplateTuner] = {}

    def register_template(self, template: TaskTemplate) -> BayesianTemplateTuner:
        """Register a new template."""
        tuner = BayesianTemplateTuner(template)
        self.tuners[template.template_id] = tuner
        return tuner

    def update_outcome(self, outcome: TaskOutcome) -> None:
        """Update template with new outcome."""
        if outcome.template_id in self.tuners:
            self.tuners[outcome.template_id].update(outcome)

    def get_tuner(self, template_id: str) -> Optional[BayesianTemplateTuner]:
        """Get tuner for template."""
        return self.tuners.get(template_id)

    def get_converged_templates(self) -> list[str]:
        """Get IDs of all converged templates."""
        return [
            template_id
            for template_id, tuner in self.tuners.items()
            if tuner.convergence_check()
        ]

    def get_recommendations(self) -> dict[str, str]:
        """Get recommendations for all templates.

        Returns:
            Dict mapping template_id to recommendation
        """
        return {
            template_id: tuner.recommend_action()
            for template_id, tuner in self.tuners.items()
        }

    def get_stats(self) -> dict:
        """Get overall registry statistics."""
        total = len(self.tuners)
        converged = len(self.get_converged_templates())

        recommendations = self.get_recommendations()
        adopt_count = sum(1 for r in recommendations.values() if r == "adopt")
        iterate_count = sum(1 for r in recommendations.values() if r == "iterate")
        reject_count = sum(1 for r in recommendations.values() if r == "reject")

        return {
            "total_templates": total,
            "converged_templates": converged,
            "adoption_ready": adopt_count,
            "iterating": iterate_count,
            "rejected": reject_count,
            "total_outcomes": sum(len(t.outcomes) for t in self.tuners.values()),
        }
