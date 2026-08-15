"""Safety Validator subsystem: Prevent unsafe actions."""

import logging
import re
from typing import Any, Dict, List

from .base import Subsystem

logger = logging.getLogger(__name__)


class SafetyValidator(Subsystem):
    """Validate safety of proposed actions."""

    def __init__(
        self,
        forbidden_actions: List[str] = None,
        max_retry_attempts: int = 3,
    ):
        self.forbidden_actions = forbidden_actions or [
            "rm -rf",
            "sudo",
            "delete_all",
            "drop database",
            ":q!",
        ]
        self.max_retry_attempts = max_retry_attempts
        self.violation_count: Dict[str, int] = {}

    @property
    def name(self) -> str:
        return "safety_validator"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to action events."""
        self.hub = hub
        hub.subscribe("strategy_applied", self.on_strategy)
        logger.info("SafetyValidator started")

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "strategy_applied":
            await self.on_strategy(event_name, event_data)

    async def on_strategy(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Validate strategy safety."""
        strategy = event_data.get("strategy", "")
        task_id = event_data.get("task_id", "unknown")

        if not self._is_safe(strategy):
            self.publish_event(
                "safety_check_failed",
                {
                    "task_id": task_id,
                    "strategy": strategy,
                    "reason": "forbidden action detected",
                },
            )

            if task_id not in self.violation_count:
                self.violation_count[task_id] = 0

            self.violation_count[task_id] += 1

    def _is_safe(self, action: str) -> bool:
        """Check if action is safe."""
        action_lower = action.lower()

        for forbidden in self.forbidden_actions:
            if forbidden.lower() in action_lower:
                return False

        # Additional checks
        if re.search(r"delete|drop|truncate", action_lower):
            # Check for confirmation
            if "confirm" not in action_lower:
                return False

        return True

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle safety queries."""
        if request_type == "is_safe":
            action = kwargs.get("action", "")
            return self._is_safe(action)

        elif request_type == "check_resource":
            resource = kwargs.get("resource", "")
            # Check if resource is protected
            protected = ["prod", "main", "master", "critical"]
            return not any(p in resource.lower() for p in protected)

        elif request_type == "violation_status":
            task_id = kwargs.get("task_id", "unknown")
            violations = self.violation_count.get(task_id, 0)

            return {
                "violation_count": violations,
                "max_attempts": self.max_retry_attempts,
                "should_escalate": violations >= self.max_retry_attempts,
            }

        elif request_type == "get_forbidden_actions":
            return self.forbidden_actions

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("SafetyValidator shutdown")
