"""Adaptive Strategy Engine — Fingerprint-Gated Strategy Ranking (Phase 2 Improvement 4).

Ranks strategies based on operator fingerprint confidence, combining:
- Empirical success rates
- Operator preference scores (from fingerprint expertise profile + risk/speed alignment)
- Cost efficiency metrics

Delivers confidence-gated adaptive ranking: when fingerprint.confidence >= 0.7,
uses fingerprint-informed ranking; otherwise falls back to empirical-only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from core.learning.operator_fingerprint import OperatorFingerprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyOption:
    """A rankable strategy option with metrics and operator alignment.

    Attributes:
        name: Strategy identifier (e.g., "direct_fix", "pivot_approach")
        required_steps: Number of procedural steps this strategy typically involves
        avg_latency_ms: Average execution time in milliseconds
        avg_cost_cents: Average cost in cents (token-weighted or compute-weighted)
        success_rate: Empirical success rate (0.0-1.0) from historical outcomes
        operator_preference_score: Alignment with operator's profile (0.0-1.0)
    """

    name: str
    required_steps: int
    avg_latency_ms: float
    avg_cost_cents: float
    success_rate: float
    operator_preference_score: float

    def weighted_score(
        self,
        success_weight: float = 0.5,
        preference_weight: float = 0.3,
        efficiency_weight: float = 0.2,
    ) -> float:
        """Compute weighted score for ranking.

        Args:
            success_weight: Weight on success_rate (default 0.5)
            preference_weight: Weight on operator_preference_score (default 0.3)
            efficiency_weight: Weight on cost efficiency (default 0.2)

        Returns:
            Weighted score in range [0.0, 1.0].
        """
        # Cost efficiency: inverse of normalized cost (lower cost = higher efficiency)
        # Assume typical cost range 1-100 cents; normalize to 0-1
        normalized_cost = min(1.0, self.avg_cost_cents / 100.0)
        efficiency = 1.0 - normalized_cost

        score = (
            self.success_rate * success_weight
            + self.operator_preference_score * preference_weight
            + efficiency * efficiency_weight
        )
        return max(0.0, min(1.0, score))  # Clamp to [0, 1]


class AdaptiveStrategyEngine:
    """Fingerprint-gated adaptive strategy ranking engine.

    Combines empirical strategy metrics with operator fingerprint to produce
    ranked strategy recommendations. Confidence gate: if fingerprint.confidence >= 0.7,
    uses fingerprint-informed ranking; otherwise returns strategies unranked or
    in empirical order only.
    """

    def __init__(self):
        """Initialize engine."""
        self.logger = logger

    def _compute_operator_preference_score(
        self,
        fingerprint: OperatorFingerprint,
        strategy: StrategyOption,
        task_type: str,
    ) -> float:
        """Compute operator preference alignment score.

        Combines:
        - Task-type expertise from fingerprint.expertise_profile[task_type]
        - Speed preference alignment: strategies with low latency match fast operators
        - Risk tolerance alignment: strategies with high success_rate match aggressive operators

        Args:
            fingerprint: Operator fingerprint profile
            strategy: Strategy option to score
            task_type: Type of task being performed

        Returns:
            Preference score in range [0.0, 1.0].
        """
        # 1. Task-type expertise (40% of preference score)
        expertise = fingerprint.expertise_profile.get(task_type, 0.5)

        # 2. Speed preference alignment (30% of preference score)
        # Fast operators (speed_preference > 0.7) prefer low-latency strategies
        # Thorough operators prefer higher-latency, careful strategies
        normalized_latency = min(1.0, strategy.avg_latency_ms / 500.0)
        speed_alignment = 1.0 - abs(
            fingerprint.speed_preference - (1.0 - normalized_latency)
        )

        # 3. Risk tolerance alignment (30% of preference score)
        # Aggressive operators (risk_tolerance > 0.7) prefer high-success strategies
        # Conservative operators accept lower success rates
        risk_alignment = 1.0 - abs(
            fingerprint.risk_tolerance - strategy.success_rate
        )

        preference_score = (
            expertise * 0.4 + speed_alignment * 0.3 + risk_alignment * 0.3
        )
        return max(0.0, min(1.0, preference_score))

    def rank_strategies_by_fingerprint(
        self,
        fingerprint: OperatorFingerprint,
        available_strategies: List[StrategyOption],
        task_type: str = "general",
    ) -> List[StrategyOption]:
        """Rank strategies using fingerprint-gated adaptive logic.

        Algorithm:
        1. For each strategy, compute operator preference score using fingerprint
        2. If fingerprint.confidence >= 0.7, use weighted score formula:
           score = success_rate*0.5 + operator_preference_score*0.3 + efficiency*0.2
        3. If fingerprint.confidence < 0.7, use empirical fallback:
           score = success_rate (ignore fingerprint)
        4. Return sorted by score (descending)

        Args:
            fingerprint: Operator's learned fingerprint profile
            available_strategies: List of strategy options to rank
            task_type: Type of task for expertise lookup in fingerprint

        Returns:
            Strategies sorted by weighted score (highest first).
        """
        if not available_strategies:
            return []

        confidence_threshold = 0.7
        is_confident = fingerprint.confidence >= confidence_threshold

        ranked = []
        for strategy in available_strategies:
            if is_confident:
                # Compute operator preference using fingerprint
                pref_score = self._compute_operator_preference_score(
                    fingerprint, strategy, task_type
                )

                # Build new strategy option with computed preference score
                strategy_with_pref = StrategyOption(
                    name=strategy.name,
                    required_steps=strategy.required_steps,
                    avg_latency_ms=strategy.avg_latency_ms,
                    avg_cost_cents=strategy.avg_cost_cents,
                    success_rate=strategy.success_rate,
                    operator_preference_score=pref_score,
                )

                score = strategy_with_pref.weighted_score()
                ranked.append((score, strategy_with_pref))
            else:
                # Fallback: empirical only (success_rate)
                score = strategy.success_rate
                ranked.append((score, strategy))

        # Sort by score descending
        ranked.sort(key=lambda x: x[0], reverse=True)

        top_score = ranked[0][0] if ranked else 0.0
        self.logger.debug(
            f"Ranked {len(ranked)} strategies (fingerprint confidence={fingerprint.confidence:.2f}). "
            f"Top strategy: {ranked[0][1].name if ranked else 'none'} "
            f"(score={top_score:.3f})"
        )

        return [strategy for _, strategy in ranked]

    def get_top_strategy(
        self,
        fingerprint: OperatorFingerprint,
        available_strategies: List[StrategyOption],
        task_type: str = "general",
    ) -> Optional[StrategyOption]:
        """Get highest-ranked strategy.

        Args:
            fingerprint: Operator's learned fingerprint profile
            available_strategies: List of strategy options to rank
            task_type: Type of task for expertise lookup

        Returns:
            Top-ranked strategy, or None if no strategies available.
        """
        ranked = self.rank_strategies_by_fingerprint(
            fingerprint, available_strategies, task_type
        )
        return ranked[0] if ranked else None
