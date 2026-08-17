"""Loop Engineer subsystem: Auto-healing with strategy ladder (ADR-0358).

Maintains strategy state via ContextAPI; records all decisions in audit trail.
Subscribes to context updates to react to budget/model changes.
"""

import asyncio
import logging
from typing import Any, Dict, List

from .base import Subsystem
from core.context_engineering.context_api import ContextAPI

logger = logging.getLogger(__name__)


class LoopEngineer(Subsystem):
    """Apply healing strategies to fix errors automatically."""

    def __init__(
        self,
        max_retries: int = 5,
        strategy_ladder: List[str] = None,
    ):
        self.max_retries = max_retries
        self.strategy_ladder = strategy_ladder or [
            "direct_fix",
            "pivot_approach",
            "decompose",
            "escalate",
        ]
        self.retry_count: Dict[str, int] = {}
        self.strategy_history: Dict[str, List[Dict[str, Any]]] = {}
        self.context_api: ContextAPI = None

    @property
    def name(self) -> str:
        return "loop_engineer"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Inject ContextAPI and subscribe to events."""
        self.hub = hub

        # Inject ContextAPI for context access
        self.context_api = ContextAPI(self.name, hub.context_bus)

        # Subscribe to hub events
        hub.subscribe("error_detected", self.on_error_detected)
        hub.subscribe("strategy_succeeded", self.on_strategy_succeeded)
        hub.subscribe("strategy_failed", self.on_strategy_failed)

        # Subscribe to context updates
        asyncio.create_task(
            self.context_api.subscribe_context_updates(self.on_context_updated)
        )

        logger.info("LoopEngineer started with ContextAPI")

    async def on_context_updated(self, payload: Dict[str, Any]) -> None:
        """React when context changes (e.g., budget, model updates).

        Args:
            payload: Contains subsystem, updates, context_stack, timestamp
        """
        updates = payload.get("updates", {})
        if "budget_remaining" in updates:
            old_budget, new_budget = updates["budget_remaining"]
            # Log budget changes but don't change strategy
            logger.debug(
                f"Budget changed: {old_budget} -> {new_budget}. "
                "May affect cost_controller strategy selection."
            )
        if "model" in updates:
            old_model, new_model = updates["model"]
            logger.debug(
                f"Model changed: {old_model} -> {new_model}. "
                "Subsystem-specific cost calculations may be affected."
            )

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "error_detected":
            await self._apply_strategy(event_data)
        elif event_name == "strategy_succeeded":
            task_id = event_data.get("task_id")
            if task_id:
                self.retry_count[task_id] = 0
        elif event_name == "strategy_failed":
            await self._escalate_if_needed(event_data)

    async def on_error_detected(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Error detected."""
        await self._apply_strategy(event_data)

    async def on_strategy_succeeded(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Strategy succeeded."""
        task_id = event_data.get("task_id")
        if task_id:
            self.retry_count[task_id] = 0

    async def on_strategy_failed(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Strategy failed."""
        await self._escalate_if_needed(event_data)

    async def _apply_strategy(self, event_data: Dict[str, Any]) -> None:
        """Apply next strategy from ladder via ContextAPI."""
        task_id = event_data.get("task_id", "unknown")
        error = event_data.get("error")

        if task_id not in self.retry_count:
            self.retry_count[task_id] = 0
            self.strategy_history[task_id] = []

        if self.retry_count[task_id] >= self.max_retries:
            self.publish_event(
                "escalation_needed",
                {
                    "task_id": task_id,
                    "error": str(error),
                    "reason": "max retries exceeded",
                },
            )
            # Record escalation decision in audit trail
            try:
                self.context_api.record_decision(
                    decision_type="strategy_escalation",
                    value="escalate",
                    reasoning=f"Max retries ({self.max_retries}) exceeded; error: {str(error)[:100]}",
                    confidence=0.95,
                )
            except RuntimeError:
                # Context not initialized; continue
                logger.debug("Context not initialized; decision not recorded")
            return

        strategy_idx = min(
            self.retry_count[task_id], len(self.strategy_ladder) - 1
        )
        strategy = self.strategy_ladder[strategy_idx]

        # Update strategy in context via ContextAPI
        try:
            self.context_api.update_context(
                strategy=strategy,
                strategy_confidence=0.8 + (0.05 * self.retry_count[task_id]),  # Increase with attempts
            )

            # Record strategy decision in audit trail
            self.context_api.record_decision(
                decision_type="strategy_selection",
                value=strategy,
                reasoning=f"Error: {type(error).__name__ if error else 'unknown'} → {strategy} (attempt {self.retry_count[task_id] + 1}/{self.max_retries})",
                confidence=0.85,
            )
        except RuntimeError as e:
            logger.warning(f"Context not initialized; strategy not recorded: {e}")

        self.publish_event(
            "strategy_applied",
            {
                "task_id": task_id,
                "strategy": strategy,
                "attempt": self.retry_count[task_id] + 1,
                "error": str(error),
            },
        )

        self.retry_count[task_id] += 1

    async def _escalate_if_needed(self, event_data: Dict[str, Any]) -> None:
        """Escalate if strategy failed."""
        task_id = event_data.get("task_id", "unknown")

        if task_id in self.retry_count and self.retry_count[task_id] >= self.max_retries:
            self.publish_event(
                "escalation_needed",
                {
                    "task_id": task_id,
                    "reason": "all strategies exhausted",
                },
            )

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle strategy queries via ContextAPI."""
        if request_type == "next_strategy":
            task_id = kwargs.get("task_id", "unknown")
            if task_id not in self.retry_count:
                self.retry_count[task_id] = 0

            strategy_idx = min(
                self.retry_count[task_id], len(self.strategy_ladder) - 1
            )

            # Try to read strategy from context if available
            strategy = self.strategy_ladder[strategy_idx]
            try:
                context_strategy = self.context_api.query_context("strategy")
                if context_strategy:
                    strategy = context_strategy
            except RuntimeError:
                # Context not initialized; use local strategy
                pass

            return {
                "strategy": strategy,
                "attempt": self.retry_count[task_id],
                "max_attempts": self.max_retries,
            }

        elif request_type == "retry_status":
            task_id = kwargs.get("task_id", "unknown")
            return {
                "retry_count": self.retry_count.get(task_id, 0),
                "max_retries": self.max_retries,
            }

        elif request_type == "strategy_confidence":
            # Read confidence from context
            try:
                confidence = self.context_api.query_context("strategy_confidence")
                return {"confidence": confidence if confidence is not None else 0.5}
            except RuntimeError:
                return {"confidence": 0.5}

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("LoopEngineer shutdown")
