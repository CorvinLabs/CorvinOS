"""Strategy Advisor subsystem: Predict strategy success."""

import logging
from typing import Any, Dict, List

from .base import Subsystem

logger = logging.getLogger(__name__)


class StrategyAdvisor(Subsystem):
    """Predict strategy success rates."""

    def __init__(
        self,
        model: str = "claude-3.5-sonnet",
        cache_predictions: bool = True,
    ):
        self.model = model
        self.cache_predictions = cache_predictions
        self.prediction_cache: Dict[str, float] = {}
        self.strategy_scores: Dict[str, List[float]] = {}

    @property
    def name(self) -> str:
        return "strategy_advisor"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to strategy events."""
        self.hub = hub
        hub.subscribe("strategy_succeeded", self.on_success)
        hub.subscribe("strategy_failed", self.on_failure)
        logger.info("StrategyAdvisor started")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "strategy_succeeded":
            await self.on_success(event_name, event_data)
        elif event_name == "strategy_failed":
            await self.on_failure(event_name, event_data)

    async def on_success(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Update success score."""
        strategy = event_data.get("strategy", "unknown")

        if strategy not in self.strategy_scores:
            self.strategy_scores[strategy] = []

        self.strategy_scores[strategy].append(1.0)
        self._invalidate_cache(strategy)

    async def on_failure(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Update failure score."""
        strategy = event_data.get("strategy", "unknown")

        if strategy not in self.strategy_scores:
            self.strategy_scores[strategy] = []

        self.strategy_scores[strategy].append(0.0)
        self._invalidate_cache(strategy)

    def _invalidate_cache(self, strategy: str) -> None:
        """Invalidate cache for strategy."""
        if self.cache_predictions and strategy in self.prediction_cache:
            del self.prediction_cache[strategy]

    def _get_success_rate(self, strategy: str) -> float:
        """Get empirical success rate for strategy."""
        if strategy not in self.strategy_scores or not self.strategy_scores[strategy]:
            return 0.5

        scores = self.strategy_scores[strategy]
        return sum(scores) / len(scores)

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle prediction queries."""
        if request_type == "predict_success":
            strategy = kwargs.get("strategy", "unknown")

            if self.cache_predictions and strategy in self.prediction_cache:
                return self.prediction_cache[strategy]

            # Base prediction on empirical success rate
            success_rate = self._get_success_rate(strategy)

            # Adjust for strategy type (heuristic)
            if strategy == "direct_fix":
                adjusted = min(1.0, success_rate * 1.2)
            elif strategy == "pivot_approach":
                adjusted = min(1.0, success_rate * 1.1)
            elif strategy == "decompose":
                adjusted = min(1.0, success_rate * 0.9)
            elif strategy == "escalate":
                adjusted = 0.0  # Escalation is last resort
            else:
                adjusted = success_rate

            if self.cache_predictions:
                self.prediction_cache[strategy] = adjusted

            return adjusted

        elif request_type == "rank_strategies":
            strategies = kwargs.get("strategies", [])
            scored = []

            for strategy in strategies:
                score = await self.handle_request("predict_success", strategy=strategy)
                scored.append({"strategy": strategy, "success_probability": score})

            return sorted(scored, key=lambda x: x["success_probability"], reverse=True)

        elif request_type == "get_empirical_scores":
            return dict(self.strategy_scores)

        elif request_type == "strategy_stats":
            strategy = kwargs.get("strategy", "unknown")
            scores = self.strategy_scores.get(strategy, [])

            if not scores:
                return {
                    "strategy": strategy,
                    "attempts": 0,
                    "successes": 0,
                    "success_rate": 0.0,
                }

            successes = sum(scores)
            return {
                "strategy": strategy,
                "attempts": len(scores),
                "successes": int(successes),
                "success_rate": successes / len(scores),
            }

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("StrategyAdvisor shutdown")
