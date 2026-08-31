"""Phase 3c: Multi-Agent Routing — ACS/TDE carve-out based on complexity + cost.

Implements ADR-0217 carve-out rules:
- Big-data vocabulary → ACS (parallel workers)
- High complexity (>0.8) + Opus → TDE (advanced reasoning)
- Default → native (Claude Code)

Includes cost estimation for routing decision.

ADR: ADR-0271 (Multi-Agent Routing Cost Model)
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Tuple

logger = logging.getLogger(__name__)


class DelegationTarget(Enum):
    """Routing target for task."""

    NATIVE = "native"
    """Claude Code (default, fastest)."""

    ACS = "acs"
    """Autonomous Compute Shell (parallel, bulk data)."""

    TDE = "tde"
    """Tiered Delegation Engine (Opus, reasoning)."""


@dataclass
class RoutingDecision:
    """Result of multi-agent routing."""

    target: DelegationTarget
    """Where to send task."""

    confidence: float
    """Confidence in decision (0.0-1.0)."""

    estimated_cost_usd: float
    """Estimated cost for this routing."""

    reasoning: str
    """Human-readable explanation."""

    carve_out_rule: str
    """Which rule triggered: 'big_data', 'high_complexity', 'default'."""


class CostEstimator:
    """Estimate costs for different routing targets."""

    # Baseline costs (USD per task, approximate)
    COST_NATIVE_HAIKU = 0.001  # ~10K input tokens @ $0.80/M
    COST_NATIVE_OPUS = 0.010  # ~100K input tokens @ $15/M
    COST_ACS_PER_WORKER = 0.005  # per worker-hour
    COST_TDE = 0.050  # complex reasoning, multiple passes

    def estimate_native_cost(self, model: str) -> float:
        """Estimate native (in-process) cost.

        Args:
            model: Model name ('haiku' or 'opus')

        Returns:
            Estimated cost in USD
        """
        if model == "haiku":
            return self.COST_NATIVE_HAIKU
        elif model == "opus":
            return self.COST_NATIVE_OPUS
        else:
            return self.COST_NATIVE_HAIKU  # default

    def estimate_acs_cost(self, task_volume: int = 100) -> float:
        """Estimate ACS cost.

        Args:
            task_volume: Approximate # of data items to process

        Returns:
            Estimated cost in USD
        """
        # ACS scales: 100 items ≈ 0.005 USD, 10K items ≈ 0.5 USD
        num_workers = max(1, min(10, task_volume // 100))
        return num_workers * self.COST_ACS_PER_WORKER

    def estimate_tde_cost(self) -> float:
        """Estimate TDE cost (fixed for complex reasoning).

        Returns:
            Estimated cost in USD
        """
        return self.COST_TDE


class MultiAgentRouter:
    """Route tasks to appropriate agent based on complexity + cost."""

    def __init__(self):
        """Initialize router."""
        self.cost_estimator = CostEstimator()
        self.big_data_keywords = {
            "database", "warehouse", "query", "sql", "batch", "bulk",
            "million", "billion", "large", "scale", "parallel",
            "map", "reduce", "aggregat", "stream", "pipeline",
        }
        self.high_complexity_keywords = {
            "refactor", "architecture", "design", "strategy",
            "optimization", "performance", "incident", "outage",
            "critical", "emergency", "security", "compliance",
        }

    def route(
        self,
        task_description: str,
        task_complexity: float,
        model_recommendation: str = "haiku",
    ) -> RoutingDecision:
        """Route task to appropriate agent.

        Args:
            task_description: Task description
            task_complexity: Complexity score (0.0-1.0)
            model_recommendation: Recommended model ('haiku' or 'opus')

        Returns:
            RoutingDecision with target + cost estimate
        """
        task_lower = task_description.lower()

        # Check for big-data carve-out (ADR-0217)
        big_data_score = sum(
            1 for kw in self.big_data_keywords
            if kw in task_lower
        )
        is_big_data = big_data_score >= 2

        if is_big_data:
            cost = self.cost_estimator.estimate_acs_cost()
            return RoutingDecision(
                target=DelegationTarget.ACS,
                confidence=0.85,
                estimated_cost_usd=cost,
                reasoning=f"Big-data task detected ({big_data_score} keywords). ACS parallelization recommended.",
                carve_out_rule="big_data",
            )

        # Check for high-complexity + Opus (TDE carve-out)
        if task_complexity > 0.80 and model_recommendation == "opus":
            cost = self.cost_estimator.estimate_tde_cost()
            return RoutingDecision(
                target=DelegationTarget.TDE,
                confidence=0.90,
                estimated_cost_usd=cost,
                reasoning=f"High complexity ({task_complexity:.2f}) + Opus required. TDE reasoning engine recommended.",
                carve_out_rule="high_complexity",
            )

        # Default: native
        cost = self.cost_estimator.estimate_native_cost(model_recommendation)
        return RoutingDecision(
            target=DelegationTarget.NATIVE,
            confidence=0.75,
            estimated_cost_usd=cost,
            reasoning=f"Standard task. Native routing with {model_recommendation.upper()} model.",
            carve_out_rule="default",
        )

    def cost_comparison(
        self,
        task_description: str,
        task_complexity: float,
        model_recommendation: str = "haiku",
    ) -> Dict:
        """Show cost comparison across all routing options.

        Args:
            task_description: Task description
            task_complexity: Complexity score
            model_recommendation: Recommended model

        Returns:
            Dict comparing costs and trade-offs
        """
        decision = self.route(task_description, task_complexity, model_recommendation)

        # Estimate costs for all options
        cost_native = self.cost_estimator.estimate_native_cost("haiku")
        cost_native_opus = self.cost_estimator.estimate_native_cost("opus")
        cost_acs = self.cost_estimator.estimate_acs_cost()
        cost_tde = self.cost_estimator.estimate_tde_cost()

        # Calculate savings relative to opus baseline
        savings = cost_native_opus - decision.estimated_cost_usd

        return {
            "recommended_target": decision.target.value,
            "recommended_cost": decision.estimated_cost_usd,
            "cost_breakdown": {
                "native_haiku": cost_native,
                "native_opus": cost_native_opus,
                "acs": cost_acs,
                "tde": cost_tde,
            },
            "savings_vs_opus": max(0.0, savings),  # Savings if recommendation is cheaper than opus
            "reasoning": decision.reasoning,
            "carve_out_rule": decision.carve_out_rule,
        }
