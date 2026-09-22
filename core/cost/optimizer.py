"""Cost Optimizer for Stories 7-8 (Savings Recommendations & ROI).

Provides:
- LoRA fine-tuning recommendations
- Prompt caching suggestions
- Batch processing advice
- ROI projections

License: Apache-2.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizationRecommendation:
    """A cost savings recommendation (Story 7)."""

    recommendation_id: str
    timestamp: str  # ISO 8601

    title: str                    # E.g., "LoRA fine-tuning for Claude Opus"
    description: str              # Detailed explanation
    optimization_type: str        # "lora", "prompt_cache", "batch", "model_downsample"

    estimated_savings_pct: float  # Expected % cost reduction
    estimated_savings_monthly: str  # Decimal stringified (EUR)

    implementation_cost: str      # Cost to implement (EUR)
    roi_weeks: int               # Weeks to break even

    applicable_models: list[str]  # Which models this applies to
    affected_calls_pct: float     # % of calls that could benefit

    confidence: float             # 0.0-1.0 confidence

    def to_dict(self) -> dict:
        return {
            "recommendation_id": self.recommendation_id,
            "timestamp": self.timestamp,
            "title": self.title,
            "description": self.description,
            "optimization_type": self.optimization_type,
            "estimated_savings_pct": self.estimated_savings_pct,
            "estimated_savings_monthly": self.estimated_savings_monthly,
            "implementation_cost": self.implementation_cost,
            "roi_weeks": self.roi_weeks,
            "applicable_models": self.applicable_models,
            "affected_calls_pct": self.affected_calls_pct,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ROIProjection:
    """ROI projection for an optimization (Story 8)."""

    recommendation_id: str
    optimization_type: str

    weekly_savings: list[str]     # List of weekly savings (EUR, Decimal stringified)
    cumulative_savings: list[str] # Cumulative savings over weeks
    payback_week: int            # Week at which cumulative > implementation_cost

    def to_dict(self) -> dict:
        return {
            "recommendation_id": self.recommendation_id,
            "optimization_type": self.optimization_type,
            "weekly_savings": self.weekly_savings,
            "cumulative_savings": self.cumulative_savings,
            "payback_week": self.payback_week,
        }


class CostOptimizer:
    """Generate cost savings recommendations and ROI projections (Stories 7-8)."""

    def __init__(self):
        """Initialize optimizer."""
        self.recommendation_id_counter = 0

    def generate_recommendations(
        self,
        daily_spend: Decimal,
        daily_call_count: int,
        model_distribution: dict[str, int],
        cache_hit_rate: float,
    ) -> list[OptimizationRecommendation]:
        """Generate cost optimization recommendations (Story 7).

        Args:
            daily_spend: Average daily spend (EUR)
            daily_call_count: Average daily LLM calls
            model_distribution: {model_id: call_count}
            cache_hit_rate: Current cache hit rate (0.0-1.0)

        Returns:
            List of recommendations ranked by impact
        """
        recommendations = []

        # Recommendation 1: LoRA fine-tuning for Opus usage
        if model_distribution.get("claude-opus-5", 0) > 100:
            rec = self._recommend_lora(daily_spend, model_distribution)
            recommendations.append(rec)

        # Recommendation 2: Prompt caching
        if cache_hit_rate < 0.5:
            rec = self._recommend_prompt_cache(daily_spend, cache_hit_rate)
            recommendations.append(rec)

        # Recommendation 3: Batch processing
        if daily_call_count > 500:
            rec = self._recommend_batch(daily_spend, daily_call_count)
            recommendations.append(rec)

        # Recommendation 4: Model downsampling (Haiku for simple tasks)
        if model_distribution.get("claude-opus-5", 0) > daily_call_count * 0.5:
            rec = self._recommend_model_downsample(daily_spend, model_distribution)
            recommendations.append(rec)

        # Sort by estimated savings descending
        return sorted(
            recommendations,
            key=lambda r: float(r.estimated_savings_monthly),
            reverse=True,
        )

    def project_roi(
        self,
        recommendation: OptimizationRecommendation,
        weeks: int = 12,
    ) -> ROIProjection:
        """Project ROI for an optimization over N weeks (Story 8).

        Args:
            recommendation: The optimization to project
            weeks: Number of weeks to project (default: 12)

        Returns:
            ROIProjection with weekly and cumulative savings
        """
        monthly_savings = Decimal(recommendation.estimated_savings_monthly)
        weekly_savings = monthly_savings / Decimal("4.33")  # Approx weeks per month

        weekly_list = []
        cumulative_list = []
        cumulative = Decimal("0")
        payback_week = 0

        implementation_cost = Decimal(recommendation.implementation_cost)

        for week in range(1, weeks + 1):
            weekly_list.append(str(weekly_savings))
            cumulative += weekly_savings
            cumulative_list.append(str(cumulative))

            # Track payback week
            if payback_week == 0 and cumulative >= implementation_cost:
                payback_week = week

        if payback_week == 0:
            payback_week = weeks  # Never breaks even in projection window

        return ROIProjection(
            recommendation_id=recommendation.recommendation_id,
            optimization_type=recommendation.optimization_type,
            weekly_savings=weekly_list,
            cumulative_savings=cumulative_list,
            payback_week=payback_week,
        )

    def _recommend_lora(
        self,
        daily_spend: Decimal,
        model_distribution: dict[str, int],
    ) -> OptimizationRecommendation:
        """Recommend LoRA fine-tuning (Story 7)."""
        self.recommendation_id_counter += 1
        rec_id = f"rec-lora-{self.recommendation_id_counter}"

        opus_calls = model_distribution.get("claude-opus-5", 0)
        total_calls = sum(model_distribution.values())
        opus_pct = (opus_calls / total_calls * 100) if total_calls > 0 else 0

        # Estimate: LoRA reduces Opus costs by 40% for repeat tasks
        monthly_spend = daily_spend * Decimal("30")
        estimated_savings = monthly_spend * Decimal("0.40") * (Decimal(opus_pct) / Decimal("100"))

        return OptimizationRecommendation(
            recommendation_id=rec_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            title="LoRA Fine-tuning for Claude Opus",
            description=(
                f"Your usage is {opus_pct:.0f}% Claude Opus. "
                "Fine-tuning a LoRA adapter can reduce costs by 40% "
                "for repeated task patterns."
            ),
            optimization_type="lora",
            estimated_savings_pct=40.0,
            estimated_savings_monthly=str(estimated_savings),
            implementation_cost="500",  # EUR, one-time
            roi_weeks=2,
            applicable_models=["claude-opus-5"],
            affected_calls_pct=opus_pct,
            confidence=0.85,
        )

    def _recommend_prompt_cache(
        self,
        daily_spend: Decimal,
        cache_hit_rate: float,
    ) -> OptimizationRecommendation:
        """Recommend prompt caching (Story 7)."""
        self.recommendation_id_counter += 1
        rec_id = f"rec-cache-{self.recommendation_id_counter}"

        # Current cache hit rate is low; caching can improve it to ~70%
        target_hit_rate = 0.70
        improvement = target_hit_rate - cache_hit_rate

        # Cache reads cost 10% of normal input tokens
        monthly_spend = daily_spend * Decimal("30")
        # Assume 30% of tokens are cached and repeating
        savings_potential = monthly_spend * Decimal("0.30") * (
            Decimal("1") - Decimal("0.1")
        )  # (100% - 10% cache cost)
        estimated_savings = savings_potential * Decimal(improvement)

        return OptimizationRecommendation(
            recommendation_id=rec_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            title="Prompt Caching for Repeated Context",
            description=(
                f"Current cache hit rate: {cache_hit_rate*100:.0f}%. "
                "Prompt caching can improve this to ~70%, saving 9% "
                "on repeated context (cached reads cost 90% less)."
            ),
            optimization_type="prompt_cache",
            estimated_savings_pct=9.0,
            estimated_savings_monthly=str(estimated_savings),
            implementation_cost="0",  # Free with Claude API
            roi_weeks=0,
            applicable_models=["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
            affected_calls_pct=float(improvement) * 100,
            confidence=0.72,
        )

    def _recommend_batch(
        self,
        daily_spend: Decimal,
        daily_call_count: int,
    ) -> OptimizationRecommendation:
        """Recommend batch processing (Story 7)."""
        self.recommendation_id_counter += 1
        rec_id = f"rec-batch-{self.recommendation_id_counter}"

        # Batch processing is ~50% cheaper but requires batching 30% of calls
        monthly_spend = daily_spend * Decimal("30")
        batch_eligible = Decimal(daily_call_count) * Decimal("0.30")
        estimated_savings = (monthly_spend * Decimal("0.30")) * Decimal("0.50")

        return OptimizationRecommendation(
            recommendation_id=rec_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            title="Batch Processing for Non-Interactive Tasks",
            description=(
                f"With {daily_call_count} daily calls, batch processing "
                "30% of non-interactive requests could save ~15% on compute costs."
            ),
            optimization_type="batch",
            estimated_savings_pct=15.0,
            estimated_savings_monthly=str(estimated_savings),
            implementation_cost="200",  # Engineering time
            roi_weeks=3,
            applicable_models=["claude-opus-5", "claude-sonnet-5"],
            affected_calls_pct=30.0,
            confidence=0.65,
        )

    def _recommend_model_downsample(
        self,
        daily_spend: Decimal,
        model_distribution: dict[str, int],
    ) -> OptimizationRecommendation:
        """Recommend downsampling models (Story 7)."""
        self.recommendation_id_counter += 1
        rec_id = f"rec-downsample-{self.recommendation_id_counter}"

        opus_calls = model_distribution.get("claude-opus-5", 0)
        sonnet_calls = model_distribution.get("claude-sonnet-5", 0)
        total_calls = sum(model_distribution.values())

        # Assume 30% of Opus calls could use Sonnet instead (2x cheaper)
        monthly_spend = daily_spend * Decimal("30")
        opus_monthly = monthly_spend * (Decimal(opus_calls) / Decimal(total_calls))
        estimated_savings = opus_monthly * Decimal("0.30") * Decimal("0.50")

        return OptimizationRecommendation(
            recommendation_id=rec_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            title="Downsample Opus → Sonnet for Simple Tasks",
            description=(
                f"30% of your Opus calls could use Claude Sonnet instead. "
                "Sonnet is 50% cheaper and better for routine tasks."
            ),
            optimization_type="model_downsample",
            estimated_savings_pct=7.5,
            estimated_savings_monthly=str(estimated_savings),
            implementation_cost="100",  # Task classification logic
            roi_weeks=2,
            applicable_models=["claude-opus-5"],
            affected_calls_pct=30.0,
            confidence=0.68,
        )
