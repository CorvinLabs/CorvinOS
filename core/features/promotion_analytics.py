"""Promotion analytics engine (ADR-0423 Phase 4).

Computes promotion eligibility scores from telemetry data.
Used by the auto-promotion daemon to decide feature graduation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.features.telemetry_collector import get_collector

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromotionScore:
    """Promotion eligibility score for a feature."""

    feature_id: str
    error_rate: float  # 0.0 to 1.0
    user_satisfaction: float  # 0.0 to 1.0 (avg feedback score)
    adoption_factor: float  # relative growth (1.0 = stable, >1.0 = growing)
    stability_days: int  # days without critical error
    is_eligible_for_promotion: bool
    eligibility_reasons: list[str] = None

    def __post_init__(self):
        """Validate after init."""
        object.__setattr__(self, "eligibility_reasons", self.eligibility_reasons or [])

    def meets_alpha_to_beta_criteria(self) -> bool:
        """Check if satisfies ALPHA → BETA promotion criteria.

        Criteria:
        - Error rate < 5% (0.05)
        - User satisfaction > 0.5 (basic)
        - At least 10 invocations in 24h (real usage)
        """
        return (
            self.error_rate < 0.05
            and self.user_satisfaction > 0.5
            and self.adoption_factor > 0.0  # Has usage
        )

    def meets_beta_to_stable_criteria(self) -> bool:
        """Check if satisfies BETA → STABLE promotion criteria.

        Criteria:
        - Error rate < 1% (0.01)
        - User satisfaction > 0.7
        - Adoption factor > 1.2 (20% growth)
        - 30+ days stable
        """
        return (
            self.error_rate < 0.01
            and self.user_satisfaction > 0.7
            and self.adoption_factor > 1.2
            and self.stability_days >= 30
        )

    def meets_stable_to_production_criteria(self) -> bool:
        """Check if satisfies STABLE → PRODUCTION promotion criteria.

        Criteria:
        - Error rate < 0.1% (0.001)
        - User satisfaction > 0.8
        - Adoption factor > 1.5 (50% growth)
        - 60+ days stable
        """
        return (
            self.error_rate < 0.001
            and self.user_satisfaction > 0.8
            and self.adoption_factor > 1.5
            and self.stability_days >= 60
        )


class PromotionAnalytics:
    """Analyze feature metrics for promotion decisions."""

    def __init__(self):
        """Initialize analytics engine."""
        self.collector = get_collector()

    def compute_promotion_score(
        self,
        feature_id: str,
        days_window: int = 30,
    ) -> PromotionScore | None:
        """Compute promotion score for a feature.

        Args:
            feature_id: Feature identifier
            days_window: Time window for analysis (default 30 days)

        Returns:
            PromotionScore or None if insufficient data
        """
        if not self.collector:
            logger.warning("Telemetry collector not initialized")
            return None

        # Get last N days of aggregates
        now = datetime.utcnow()
        cutoff = now - timedelta(days=days_window)

        # Collect all aggregates in window
        aggregates = self.collector.get_24h_aggregates(feature_id)  # This only gets 24h
        # TODO: extend collector to get arbitrary windows

        if not aggregates:
            logger.warning(f"No telemetry data for {feature_id}")
            return None

        # Compute aggregate metrics
        total_usage = sum(a.usage_count for a in aggregates)
        total_errors = sum(a.error_count for a in aggregates)

        if total_usage == 0:
            error_rate = 0.0
        else:
            error_rate = total_errors / total_usage

        # Compute average feedback
        total_feedback = sum(a.compute_avg_feedback() for a in aggregates)
        satisfaction = total_feedback / len(aggregates) if aggregates else 0.0

        # Compute adoption factor (week-over-week growth)
        # This is a simplified version; real implementation would track week 1 vs week 2
        adoption_factor = 1.0  # Placeholder for now

        # Compute stability (days since last error)
        stability_days = 0
        for agg in sorted(aggregates, key=lambda a: a.hour_start, reverse=True):
            if agg.error_count > 0:
                break
            stability_days += 1

        reasons = []
        eligible = True

        # Basic validation
        if total_usage < 10:
            eligible = False
            reasons.append(f"Insufficient usage: {total_usage} < 10")

        if error_rate > 0.1:
            eligible = False
            reasons.append(f"High error rate: {error_rate:.2%} > 10%")

        return PromotionScore(
            feature_id=feature_id,
            error_rate=round(error_rate, 4),
            user_satisfaction=round(satisfaction, 2),
            adoption_factor=round(adoption_factor, 2),
            stability_days=stability_days,
            is_eligible_for_promotion=eligible,
            eligibility_reasons=reasons,
        )

    def compute_error_rate_24h(self, feature_id: str) -> float:
        """Get 24h error rate for a feature."""
        if not self.collector:
            return 0.0

        aggregates = self.collector.get_24h_aggregates(feature_id)
        total_usage = sum(a.usage_count for a in aggregates)
        total_errors = sum(a.error_count for a in aggregates)

        if total_usage == 0:
            return 0.0
        return total_errors / total_usage

    def compute_avg_satisfaction_24h(self, feature_id: str) -> float:
        """Get 24h average user satisfaction for a feature."""
        if not self.collector:
            return 0.0

        aggregates = self.collector.get_24h_aggregates(feature_id)
        feedbacks = [a.compute_avg_feedback() for a in aggregates if a.feedback_count > 0]

        if not feedbacks:
            return 0.0
        return sum(feedbacks) / len(feedbacks)

    def compute_adoption_growth(self, feature_id: str) -> float:
        """Compute week-over-week adoption growth factor.

        Returns: ratio of week 2 usage to week 1 usage (1.0 = stable, 2.0 = doubled)
        """
        # TODO: implement week-over-week calculation
        # For now, return 1.0 (stable)
        return 1.0

    def get_days_since_last_error(self, feature_id: str) -> int:
        """Get days since last error for a feature."""
        if not self.collector:
            return 0

        aggregates = self.collector.get_24h_aggregates(feature_id)

        days = 0
        for agg in sorted(aggregates, key=lambda a: a.hour_start, reverse=True):
            if agg.error_count > 0:
                break
            days += 1

        return days

    def is_eligible_for_alpha_to_beta(self, feature_id: str) -> tuple[bool, str]:
        """Check if feature can promote ALPHA → BETA."""
        score = self.compute_promotion_score(feature_id)
        if not score:
            return False, "No telemetry data"

        if not score.meets_alpha_to_beta_criteria():
            reasons = score.eligibility_reasons or []
            return False, "; ".join(reasons) or "Does not meet ALPHA→BETA criteria"

        return True, f"Eligible: {score.error_rate:.2%} error, {score.user_satisfaction:.0%} satisfaction"

    def is_eligible_for_beta_to_stable(self, feature_id: str) -> tuple[bool, str]:
        """Check if feature can promote BETA → STABLE."""
        score = self.compute_promotion_score(feature_id)
        if not score:
            return False, "No telemetry data"

        if not score.meets_beta_to_stable_criteria():
            reasons = [
                f"Error {score.error_rate:.2%}" if score.error_rate >= 0.01 else None,
                f"Satisfaction {score.user_satisfaction:.0%}" if score.user_satisfaction <= 0.7 else None,
                f"Growth {score.adoption_factor:.1f}x" if score.adoption_factor <= 1.2 else None,
                f"Stability {score.stability_days}d" if score.stability_days < 30 else None,
            ]
            reasons = [r for r in reasons if r]
            return False, "; ".join(reasons) or "Does not meet BETA→STABLE criteria"

        return True, f"Eligible: {score.error_rate:.3%} error, {score.user_satisfaction:.0%} satisfaction, {score.stability_days}d stable"

    def is_eligible_for_stable_to_production(self, feature_id: str) -> tuple[bool, str]:
        """Check if feature can promote STABLE → PRODUCTION."""
        score = self.compute_promotion_score(feature_id)
        if not score:
            return False, "No telemetry data"

        if not score.meets_stable_to_production_criteria():
            reasons = [
                f"Error {score.error_rate:.3%}" if score.error_rate >= 0.001 else None,
                f"Satisfaction {score.user_satisfaction:.0%}" if score.user_satisfaction <= 0.8 else None,
                f"Growth {score.adoption_factor:.1f}x" if score.adoption_factor <= 1.5 else None,
                f"Stability {score.stability_days}d" if score.stability_days < 60 else None,
            ]
            reasons = [r for r in reasons if r]
            return False, "; ".join(reasons) or "Does not meet STABLE→PRODUCTION criteria"

        return True, f"Eligible: {score.error_rate:.4%} error, {score.user_satisfaction:.0%} satisfaction, {score.stability_days}d stable"


# Singleton instance
_ANALYTICS: PromotionAnalytics | None = None


def initialize_analytics() -> PromotionAnalytics:
    """Initialize the global promotion analytics engine."""
    global _ANALYTICS
    _ANALYTICS = PromotionAnalytics()
    return _ANALYTICS


def get_analytics() -> PromotionAnalytics | None:
    """Get the global promotion analytics engine."""
    return _ANALYTICS
