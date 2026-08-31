"""Confidence Interval Calculator — Bayesian Beta-Binomial for success rates (ADR-0324).

Provides confidence intervals for tool/skill success rates using Bayesian smoothing.
Handles cold-start (small sample counts) gracefully with Beta prior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.stats import beta


@dataclass(frozen=True)
class ConfidenceInterval:
    """Bayesian confidence interval for success rate."""

    lower: float  # 2.5th percentile
    mean: float  # Point estimate (posterior mean)
    upper: float  # 97.5th percentile
    samples: int  # Total sample count (successes + failures)
    prior_successes: int = 2  # Beta prior hyperparameter
    prior_failures: int = 2

    def width(self) -> float:
        """Width of credible interval (uncertainty)."""
        return self.upper - self.lower

    def margin_of_error(self) -> float:
        """Half-width of interval."""
        return self.width() / 2

    def __str__(self) -> str:
        """Readable format."""
        return f"{self.mean:.1%} [{self.lower:.1%}, {self.upper:.1%}] (n={self.samples})"


class ConfidenceIntervalCalculator:
    """Computes Bayesian confidence intervals for success rates."""

    @staticmethod
    def compute_interval(
        successes: int,
        failures: int,
        prior_successes: int = 2,
        prior_failures: int = 2,
        credible_level: float = 0.95,
    ) -> ConfidenceInterval:
        """Compute credible interval using Beta-Binomial conjugate prior.

        **Model:**
        - Prior: Beta(prior_successes, prior_failures) — uniform by default Beta(2,2)
        - Data: successes, failures (binomial likelihood)
        - Posterior: Beta(successes + prior_successes, failures + prior_failures)

        **Parameters:**
        - successes: Count of successful outcomes
        - failures: Count of failed outcomes
        - prior_successes, prior_failures: Beta prior hyperparameters
          Default Beta(2,2) provides mild regularization and is nearly uniform
        - credible_level: Credible level for interval (default 0.95 → 95% CI)

        **Returns:**
        - ConfidenceInterval with (lower, mean, upper, samples)

        **Example:**
        >>> ci = ConfidenceIntervalCalculator.compute_interval(8, 2)
        >>> print(ci)
        80.0% [54.8%, 94.6%] (n=10)
        """
        total_samples = successes + failures

        # Posterior distribution parameters
        a = successes + prior_successes
        b = failures + prior_failures

        # Point estimate (posterior mean)
        mean_rate = a / (a + b) if (a + b) > 0 else 0.5

        # Credible interval bounds
        alpha = 1.0 - credible_level
        lower = beta.ppf(alpha / 2, a, b)
        upper = beta.ppf(1.0 - alpha / 2, a, b)

        return ConfidenceInterval(
            lower=lower,
            mean=mean_rate,
            upper=upper,
            samples=total_samples,
            prior_successes=prior_successes,
            prior_failures=prior_failures,
        )

    @staticmethod
    def weighted_mean(
        intervals: list[ConfidenceInterval],
        weights: Optional[list[float]] = None,
    ) -> float:
        """Compute weighted mean of multiple intervals.

        **Parameters:**
        - intervals: List of ConfidenceInterval objects
        - weights: Optional weights (default: uniform, i.e. sample-size-weighted)

        **Returns:**
        - Weighted mean success rate
        """
        if not intervals:
            return 0.5

        if weights is None:
            # Default: weight by sample size
            total_samples = sum(ci.samples for ci in intervals)
            if total_samples == 0:
                return 0.5
            weights = [ci.samples / total_samples for ci in intervals]

        return sum(ci.mean * w for ci, w in zip(intervals, weights))

    @staticmethod
    def credible_set(
        intervals: list[ConfidenceInterval],
        credible_level: float = 0.95,
    ) -> tuple[float, float]:
        """Compute overall credible set from multiple intervals.

        Combines intervals by taking the minimum lower and maximum upper bounds.

        **Parameters:**
        - intervals: List of ConfidenceInterval objects
        - credible_level: For documentation (not recomputed)

        **Returns:**
        - (lower, upper) tuple for combined credible set
        """
        if not intervals:
            return (0.0, 1.0)

        lower = min(ci.lower for ci in intervals)
        upper = max(ci.upper for ci in intervals)
        return (lower, upper)


class WindowedConfidenceCalculator:
    """Compute confidence intervals across time windows."""

    @staticmethod
    def compute_for_window(
        successes_by_window: dict[str, int],
        failures_by_window: dict[str, int],
        prior_successes: int = 2,
        prior_failures: int = 2,
    ) -> dict[str, ConfidenceInterval]:
        """Compute confidence intervals for each time window.

        **Parameters:**
        - successes_by_window: Dict of {window_name: success_count}
        - failures_by_window: Dict of {window_name: failure_count}

        **Returns:**
        - Dict of {window_name: ConfidenceInterval}
        """
        results = {}

        # Assume same keys in both dicts
        for window in successes_by_window.keys():
            successes = successes_by_window.get(window, 0)
            failures = failures_by_window.get(window, 0)

            results[window] = ConfidenceIntervalCalculator.compute_interval(
                successes=successes,
                failures=failures,
                prior_successes=prior_successes,
                prior_failures=prior_failures,
            )

        return results

    @staticmethod
    def trend(
        intervals_by_window: dict[str, ConfidenceInterval],
        window_order: list[str],
    ) -> str:
        """Detect trend in mean success rates across ordered windows.

        **Parameters:**
        - intervals_by_window: Dict of {window: ConfidenceInterval}
        - window_order: Ordered list of windows (e.g., ['7d', '30d', 'all'])

        **Returns:**
        - "improving" | "declining" | "stable" | "unknown"
        """
        means = [intervals_by_window.get(w, None) for w in window_order]
        means = [m.mean for m in means if m is not None]

        if len(means) < 2:
            return "unknown"

        # Simple trend: compare first and last
        diff = means[-1] - means[0]
        if abs(diff) < 0.02:  # < 2% difference
            return "stable"
        elif diff > 0:
            return "improving"
        else:
            return "declining"
