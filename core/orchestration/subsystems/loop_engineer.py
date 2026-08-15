"""Loop Engineer subsystem: Auto-healing with strategy ladder."""

import logging
from typing import Any, Dict, List

from .base import Subsystem

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

    @property
    def name(self) -> str:
        return "loop_engineer"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to error events."""
        self.hub = hub
        hub.subscribe("error_detected", self.on_error_detected)
        hub.subscribe("strategy_succeeded", self.on_strategy_succeeded)
        hub.subscribe("strategy_failed", self.on_strategy_failed)
        logger.info("LoopEngineer started")

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
        """Apply next strategy from ladder."""
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
            return

        strategy_idx = min(
            self.retry_count[task_id], len(self.strategy_ladder) - 1
        )
        strategy = self.strategy_ladder[strategy_idx]

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
        """Handle strategy queries."""
        if request_type == "next_strategy":
            task_id = kwargs.get("task_id", "unknown")
            if task_id not in self.retry_count:
                self.retry_count[task_id] = 0

            strategy_idx = min(
                self.retry_count[task_id], len(self.strategy_ladder) - 1
            )
            return {
                "strategy": self.strategy_ladder[strategy_idx],
                "attempt": self.retry_count[task_id],
                "max_attempts": self.max_retries,
            }

        elif request_type == "retry_status":
            task_id = kwargs.get("task_id", "unknown")
            return {
                "retry_count": self.retry_count.get(task_id, 0),
                "max_retries": self.max_retries,
            }

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("LoopEngineer shutdown")
