"""Cost Controller subsystem: Budget-aware allocation (ADR-0358).

Maintains budget state via ContextAPI; records all cost estimates in audit trail.
Subscribes to context updates to react to model changes.
"""

import asyncio
import logging
from typing import Any, Dict

from .base import Subsystem
from core.context_engineering.context_api import ContextAPI

logger = logging.getLogger(__name__)

# API pricing (simplified)
MODEL_COSTS = {
    "claude-3.5-haiku": {"input": 0.80 / 1000000, "output": 4.0 / 1000000},
    "claude-3.5-sonnet": {"input": 3.0 / 1000000, "output": 15.0 / 1000000},
    "claude-opus-5": {"input": 15.0 / 1000000, "output": 75.0 / 1000000},
}


class CostController(Subsystem):
    """Enforce budget and estimate costs."""

    def __init__(
        self,
        daily_budget_usd: float = 50.0,
        preferred_model: str = "claude-3.5-haiku",
        cost_warning_threshold: float = 0.8,
    ):
        self.daily_budget_usd = daily_budget_usd
        self.preferred_model = preferred_model
        self.cost_warning_threshold = cost_warning_threshold
        self.spent_today: float = 0.0
        self.token_count: Dict[str, int] = {"input": 0, "output": 0}
        self.context_api: ContextAPI = None
        self.cost_per_strategy: Dict[str, list] = {}  # ADR-0373: [cost, success] pairs
        self.strategy_efficiency: Dict[str, float] = {}  # cost per success

    @property
    def name(self) -> str:
        return "cost_controller"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Inject ContextAPI and subscribe to events."""
        self.hub = hub

        # Inject ContextAPI for context access
        self.context_api = ContextAPI(self.name, hub.context_bus)

        # Subscribe to hub events
        hub.subscribe("task_started", self.on_task_started)

        # Subscribe to context updates
        asyncio.create_task(
            self.context_api.subscribe_context_updates(self.on_context_updated)
        )

        logger.info("CostController started with ContextAPI")

    async def on_context_updated(self, payload: Dict[str, Any]) -> None:
        """React when context changes (e.g., model, budget updates).

        Args:
            payload: Contains subsystem, updates, context_stack, timestamp
        """
        updates = payload.get("updates", {})
        if "model" in updates:
            old_model, new_model = updates["model"]
            logger.debug(
                f"Model changed: {old_model} -> {new_model}. "
                "Cost estimation will use new model rates."
            )
        if "budget_remaining" in updates:
            old_budget, new_budget = updates["budget_remaining"]
            # Check if we're approaching threshold
            if new_budget / self.daily_budget_usd < self.cost_warning_threshold:
                logger.warning(
                    f"Budget warning: {new_budget:.2f}/${self.daily_budget_usd} remaining"
                )

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "task_started":
            await self.on_task_started(event_name, event_data)

    async def on_task_started(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Task started."""
        pass

    def track_cost_per_strategy(self, strategy: str, cost_cents: float, success: bool) -> None:
        """Track cost/success for strategy (ADR-0373: cost optimization)."""
        if strategy not in self.cost_per_strategy:
            self.cost_per_strategy[strategy] = []
        self.cost_per_strategy[strategy].append((cost_cents, success))
        self._update_efficiency(strategy)

    def _update_efficiency(self, strategy: str) -> None:
        """Update cost-per-success efficiency metric."""
        data = self.cost_per_strategy.get(strategy, [])
        if not data:
            return
        successes = sum(1 for _, s in data if s)
        if successes > 0:
            total_cost = sum(c for c, _ in data)
            self.strategy_efficiency[strategy] = total_cost / successes

    def get_cost_efficiency(self, strategy: str) -> float:
        """Get cost-per-success for strategy (lower is better)."""
        return self.strategy_efficiency.get(strategy, 1.0)

    def _estimate_cost(
        self, input_tokens: int, output_tokens: int, model: str = None
    ) -> float:
        """Estimate cost for API call."""
        if model is None:
            model = self.preferred_model

        if model not in MODEL_COSTS:
            return 0.0

        costs = MODEL_COSTS[model]
        return (input_tokens * costs["input"]) + (output_tokens * costs["output"])

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle cost queries via ContextAPI."""
        if request_type == "approve_action":
            cost = kwargs.get("cost", 0.0)

            # Try to read budget from context
            try:
                remaining_budget = self.context_api.query_context("budget_remaining")
                if remaining_budget is None:
                    remaining_budget = self.daily_budget_usd - self.spent_today
            except RuntimeError:
                remaining_budget = self.daily_budget_usd - self.spent_today

            if cost > remaining_budget:
                # Record cost approval denial
                try:
                    self.context_api.record_decision(
                        decision_type="cost_approval",
                        value="denied",
                        reasoning=f"Cost {cost:.4f} exceeds remaining budget {remaining_budget:.4f}",
                        confidence=1.0,
                    )
                except RuntimeError:
                    pass

                self.publish_event(
                    "cost_exceeded",
                    {
                        "requested": cost,
                        "remaining": remaining_budget,
                        "daily_budget": self.daily_budget_usd,
                    },
                )
                return False

            if (self.spent_today + cost) / self.daily_budget_usd >= self.cost_warning_threshold:
                self.publish_event(
                    "cost_warning",
                    {
                        "current": self.spent_today + cost,
                        "budget": self.daily_budget_usd,
                    },
                )

            # Update budget via ContextAPI
            new_remaining = remaining_budget - cost
            try:
                self.context_api.update_context(budget_remaining=new_remaining)
                self.context_api.record_decision(
                    decision_type="cost_approval",
                    value="approved",
                    reasoning=f"Cost {cost:.4f} approved; {new_remaining:.4f} remaining",
                    confidence=1.0,
                )
            except RuntimeError:
                pass

            self.spent_today += cost
            return True

        elif request_type == "estimate_cost":
            input_tokens = kwargs.get("input_tokens", 0)
            output_tokens = kwargs.get("output_tokens", 0)
            model = kwargs.get("model")

            # If model not specified, try to read from context
            if model is None:
                try:
                    context_model = self.context_api.query_context("model")
                    model = context_model if context_model else self.preferred_model
                except RuntimeError:
                    model = self.preferred_model

            estimated = self._estimate_cost(input_tokens, output_tokens, model)

            # Record estimate
            try:
                self.context_api.record_decision(
                    decision_type="cost_estimate",
                    value=f"{estimated:.6f}",
                    reasoning=f"Input: {input_tokens} tokens, Output: {output_tokens} tokens, Model: {model}",
                    confidence=0.9,
                )
            except RuntimeError:
                pass

            return {
                "estimated_cost": estimated,
                "model": model,
            }

        elif request_type == "cheaper_alternative":
            input_tokens = kwargs.get("input_tokens", 0)
            output_tokens = kwargs.get("output_tokens", 0)

            alternatives = []
            for model in MODEL_COSTS.keys():
                cost = self._estimate_cost(input_tokens, output_tokens, model)
                alternatives.append({"model": model, "cost": cost})

            return sorted(alternatives, key=lambda x: x["cost"])

        elif request_type == "budget_status":
            # Try to read from context first
            try:
                remaining = self.context_api.query_context("budget_remaining")
                if remaining is None:
                    remaining = self.daily_budget_usd - self.spent_today
            except RuntimeError:
                remaining = self.daily_budget_usd - self.spent_today

            return {
                "spent": self.spent_today,
                "remaining": remaining,
                "daily_budget": self.daily_budget_usd,
                "percent_used": (self.spent_today / self.daily_budget_usd) * 100,
            }

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("CostController shutdown")
