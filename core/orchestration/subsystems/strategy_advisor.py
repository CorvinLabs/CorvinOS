"""Strategy Advisor subsystem: Predict strategy success."""

import logging
from typing import Any, Dict, List, Optional

from .base import Subsystem
from core.learning.adaptive_strategy import (
    AdaptiveStrategyEngine,
    StrategyOption,
    STRATEGY_BASE_COST_CENTS,
    STRATEGY_COST_INCREMENT_CENTS,
    STRATEGY_BASE_LATENCY_MS,
    STRATEGY_LATENCY_INCREMENT_MS,
    STRATEGY_DEFAULT_SUCCESS_RATE,
)
from core.learning.operator_fingerprint import OperatorFingerprint

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
        self.adaptive_engine = AdaptiveStrategyEngine()

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
        """Get empirical success rate for strategy.

        Returns STRATEGY_DEFAULT_SUCCESS_RATE (0.5) if no history exists
        (e.g., fresh install, unknown strategy).
        """
        if strategy not in self.strategy_scores or not self.strategy_scores[strategy]:
            return STRATEGY_DEFAULT_SUCCESS_RATE

        scores = self.strategy_scores[strategy]
        return sum(scores) / len(scores)

    def build_strategy_options(
        self,
        strategy_names: List[str],
    ) -> List[StrategyOption]:
        """Build StrategyOption list from empirical data for given strategies.

        Constructs StrategyOption objects with real empirical success rates and
        cost/latency estimates calibrated to strategy position. For strategies
        with no history, uses STRATEGY_DEFAULT_SUCCESS_RATE (0.5) to avoid
        biasing fresh installs.

        Calibration (per ADR-0370/0371):
        - Cost: STRATEGY_BASE_COST_CENTS + (i * STRATEGY_COST_INCREMENT_CENTS)
        - Latency: STRATEGY_BASE_LATENCY_MS + (i * STRATEGY_LATENCY_INCREMENT_MS)
        - Success rate: Empirical from strategy_scores, or default for fresh installs

        Args:
            strategy_names: List of strategy names to build options for.

        Returns:
            List of StrategyOption objects with empirical metrics.
        """
        if not strategy_names:
            logger.warning("build_strategy_options called with empty strategy list")
            return []

        options = []
        for i, strategy_name in enumerate(strategy_names):
            # Get empirical success rate, or default if no history
            success_rate = self._get_success_rate(strategy_name)

            # Calibrated cost estimate: increases with strategy complexity
            avg_cost_cents = STRATEGY_BASE_COST_CENTS + (i * STRATEGY_COST_INCREMENT_CENTS)

            # Calibrated latency estimate: increases with strategy complexity
            avg_latency_ms = STRATEGY_BASE_LATENCY_MS + (i * STRATEGY_LATENCY_INCREMENT_MS)

            # Required steps: scales with strategy complexity
            required_steps = 2 + i

            option = StrategyOption(
                name=strategy_name,
                required_steps=required_steps,
                avg_latency_ms=avg_latency_ms,
                avg_cost_cents=avg_cost_cents,
                success_rate=success_rate,  # Real empirical data
                operator_preference_score=0.7,  # Default; can be overridden by fingerprint
            )
            options.append(option)
            logger.debug(
                f"Built StrategyOption '{strategy_name}' (index {i}): "
                f"success_rate={success_rate:.2f} (empirical), cost={avg_cost_cents:.1f}¢, "
                f"latency={avg_latency_ms:.0f}ms"
            )

        return options

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

    def get_strategy(
        self,
        available_strategies: List[StrategyOption],
        fingerprint: Optional[OperatorFingerprint] = None,
        task_type: str = "general",
    ) -> Optional[StrategyOption]:
        """Get top-ranked strategy using fingerprint-gated adaptive ranking.

        Algorithm:
        1. If fingerprint is provided and confidence >= 0.7:
           Use adaptive ranking combining success rate, operator preference, and cost.
        2. Else:
           Use empirical fallback (success rate only).
        3. Return top-ranked strategy.

        Args:
            available_strategies: List of StrategyOption objects to rank.
            fingerprint: Optional operator fingerprint for adaptive ranking.
            task_type: Type of task for expertise lookup (default "general").

        Returns:
            Top-ranked StrategyOption, or None if no strategies available.
        """
        if not available_strategies:
            logger.warning("No strategies available for ranking")
            return None

        # If fingerprint provided and confident, use adaptive ranking
        if fingerprint is not None and fingerprint.confidence >= 0.7:
            ranked_strategies = self.adaptive_engine.rank_strategies_by_fingerprint(
                fingerprint, available_strategies, task_type
            )
            top_strategy = ranked_strategies[0] if ranked_strategies else None

            if top_strategy:
                logger.info(
                    f"Selected strategy '{top_strategy.name}' (adaptive ranking, "
                    f"fingerprint confidence={fingerprint.confidence:.2f})"
                )
            return top_strategy

        # Fallback: empirical ranking (success rate only, already in StrategyOption)
        logger.debug(
            f"Using empirical fallback ranking "
            f"(fingerprint confidence={fingerprint.confidence if fingerprint else 'N/A'})"
        )
        # Reuse StrategyOption.success_rate already computed in build_strategy_options()
        scored = [
            {
                "strategy": strategy,
                "success_rate": strategy.success_rate,
            }
            for strategy in available_strategies
        ]

        ranked = sorted(scored, key=lambda x: x["success_rate"], reverse=True)
        return ranked[0]["strategy"] if ranked else None

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("StrategyAdvisor shutdown")

    def clear_session_cache(self, session_id: str | None = None) -> None:
        """Clear session-scoped state on session reset.

        Clears prediction cache but preserves strategy scores for long-term
        learning across sessions.
        """
        try:
            self.prediction_cache.clear()
            logger.info("StrategyAdvisor session state cleared")
        except Exception as e:
            logger.error(f"StrategyAdvisor clear_session_cache failed: {e}")
