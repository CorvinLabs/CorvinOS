"""Health Monitor subsystem: Detect stalls, errors, and anomalies (ADR-0358).

Maintains health metrics via ContextAPI; records health checks in audit trail.
Subscribes to context updates to detect stalls and react to strategy changes.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from .base import Subsystem
from core.context_engineering.context_api import ContextAPI

logger = logging.getLogger(__name__)


class HealthMonitor(Subsystem):
    """Monitor task health and detect stalls."""

    def __init__(
        self,
        stall_timeout_min: float = 10.0,
        error_rate_threshold: float = 0.3,
        token_burn_check_interval: int = 5,
    ):
        self.stall_timeout_min = stall_timeout_min
        self.error_rate_threshold = error_rate_threshold
        self.token_burn_check_interval = token_burn_check_interval
        self.error_count = 0
        self.total_count = 0
        self.last_activity = datetime.now()
        self.context_api: ContextAPI = None

    @property
    def name(self) -> str:
        return "health_monitor"

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
        hub.subscribe("task_completed", self.on_task_completed)
        hub.subscribe("error_detected", self.on_error_detected)

        # Subscribe to context updates
        asyncio.create_task(
            self.context_api.subscribe_context_updates(self.on_context_updated)
        )

        logger.info("HealthMonitor started with ContextAPI")

    async def on_context_updated(self, payload: Dict[str, Any]) -> None:
        """React when context changes (e.g., strategy, budget).

        Args:
            payload: Contains subsystem, updates, context_stack, timestamp
        """
        updates = payload.get("updates", {})
        if "strategy" in updates:
            old_strategy, new_strategy = updates["strategy"]
            # Detect stall if strategy changes (may indicate recovery)
            self.last_activity = datetime.now()
            logger.debug(
                f"Strategy changed: {old_strategy} -> {new_strategy}. "
                "Resetting activity timer."
            )
        if "budget_remaining" in updates:
            old_budget, new_budget = updates["budget_remaining"]
            if new_budget < old_budget:
                # Budget was consumed; reset activity timer
                self.last_activity = datetime.now()

    async def on_event(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """React to events."""
        if event_name == "task_started":
            self.last_activity = datetime.now()
        elif event_name == "task_completed":
            self.last_activity = datetime.now()
        elif event_name == "error_detected":
            self.error_count += 1
            self.total_count += 1
            await self._check_error_rate()

    async def on_task_started(self, event_name: str, event_data: Dict[str, Any]) -> None:
        """Task started."""
        self.last_activity = datetime.now()

    async def on_task_completed(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Task completed."""
        self.last_activity = datetime.now()
        self.error_count = 0

    async def on_error_detected(
        self, event_name: str, event_data: Dict[str, Any]
    ) -> None:
        """Error detected."""
        self.error_count += 1
        self.total_count += 1
        await self._check_error_rate()

    async def _check_error_rate(self) -> None:
        """Check if error rate exceeds threshold and record in audit trail."""
        if self.total_count > 0:
            rate = self.error_count / self.total_count
            if rate > self.error_rate_threshold:
                # Record high error rate in audit trail
                try:
                    self.context_api.record_decision(
                        decision_type="error_rate_check",
                        value=f"high_{rate:.2f}",
                        reasoning=f"Error rate {rate:.2%} exceeds threshold {self.error_rate_threshold:.2%} ({self.error_count}/{self.total_count} errors)",
                        confidence=1.0,
                    )
                except RuntimeError:
                    pass

                self.publish_event(
                    "error_rate_high",
                    {
                        "error_count": self.error_count,
                        "total_count": self.total_count,
                        "error_rate": rate,
                    },
                )
            else:
                # Record normal error rate
                try:
                    self.context_api.record_decision(
                        decision_type="error_rate_check",
                        value=f"normal_{rate:.2f}",
                        reasoning=f"Error rate {rate:.2%} within threshold ({self.error_count}/{self.total_count} errors)",
                        confidence=1.0,
                    )
                except RuntimeError:
                    pass

    async def _check_stall(self) -> None:
        """Check if task has stalled and record in audit trail."""
        elapsed = (datetime.now() - self.last_activity).total_seconds() / 60
        if elapsed > self.stall_timeout_min:
            # Record stall detection
            try:
                self.context_api.record_decision(
                    decision_type="stall_detection",
                    value=f"stalled_{elapsed:.1f}min",
                    reasoning=f"No activity for {elapsed:.1f} minutes (threshold: {self.stall_timeout_min})",
                    confidence=1.0,
                )
            except RuntimeError:
                pass

            self.publish_event(
                "task_stalled",
                {
                    "stall_duration_min": elapsed,
                    "threshold_min": self.stall_timeout_min,
                },
            )
        else:
            # Record healthy state
            try:
                self.context_api.record_decision(
                    decision_type="stall_detection",
                    value=f"healthy_{elapsed:.1f}min",
                    reasoning=f"Activity detected within {elapsed:.1f} minutes (threshold: {self.stall_timeout_min})",
                    confidence=1.0,
                )
            except RuntimeError:
                pass

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle health queries via ContextAPI."""
        if request_type == "health_status":
            await self._check_stall()

            # Record health status query
            try:
                self.context_api.record_decision(
                    decision_type="health_status_query",
                    value="requested",
                    reasoning=f"Health check requested; errors: {self.error_count}/{self.total_count}",
                    confidence=1.0,
                )
            except RuntimeError:
                pass

            return {
                "status": "healthy",
                "error_count": self.error_count,
                "total_count": self.total_count,
                "last_activity": self.last_activity.isoformat(),
            }
        elif request_type == "error_rate":
            if self.total_count == 0:
                rate = 0.0
            else:
                rate = self.error_count / self.total_count

            # Record rate query
            try:
                self.context_api.record_decision(
                    decision_type="error_rate_query",
                    value=f"{rate:.2f}",
                    reasoning=f"Error rate query requested; rate: {rate:.2%}",
                    confidence=1.0,
                )
            except RuntimeError:
                pass

            return rate

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("HealthMonitor shutdown")

    def clear_session_cache(self, session_id: str | None = None) -> None:
        """Clear session-scoped state on session reset.

        Resets error counts and activity tracking for the session.
        """
        try:
            self.error_count = 0
            self.total_count = 0
            self.last_activity = datetime.now()
            logger.info("HealthMonitor session state cleared")
        except Exception as e:
            logger.error(f"HealthMonitor clear_session_cache failed: {e}")
