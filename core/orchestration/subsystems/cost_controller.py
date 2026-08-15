"""Cost Controller subsystem: Budget-aware allocation."""

import logging
from typing import Any, Dict

from .base import Subsystem

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

    @property
    def name(self) -> str:
        return "cost_controller"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to token/cost events."""
        self.hub = hub
        hub.subscribe("task_started", self.on_task_started)
        logger.info("CostController started")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "task_started":
            await self.on_task_started(event_name, event_data)

    async def on_task_started(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Task started."""
        pass

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
        """Handle cost queries."""
        if request_type == "approve_action":
            cost = kwargs.get("cost", 0.0)
            remaining_budget = self.daily_budget_usd - self.spent_today

            if cost > remaining_budget:
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

            self.spent_today += cost
            return True

        elif request_type == "estimate_cost":
            input_tokens = kwargs.get("input_tokens", 0)
            output_tokens = kwargs.get("output_tokens", 0)
            model = kwargs.get("model", self.preferred_model)

            return {
                "estimated_cost": self._estimate_cost(input_tokens, output_tokens, model),
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
