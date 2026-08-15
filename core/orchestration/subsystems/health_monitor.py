"""Health Monitor subsystem: Detect stalls, errors, and anomalies.

Refactored from engine_healer.py for Brain architecture.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from .base import Subsystem

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

    @property
    def name(self) -> str:
        return "health_monitor"

    @property
    def version(self) -> str:
        return "1.0.0"

    def startup(self, hub) -> None:
        """Subscribe to task events."""
        self.hub = hub
        hub.subscribe("task_started", self.on_task_started)
        hub.subscribe("task_completed", self.on_task_completed)
        hub.subscribe("error_detected", self.on_error_detected)
        logger.info("HealthMonitor started")

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
        """Check if error rate exceeds threshold."""
        if self.total_count > 0:
            rate = self.error_count / self.total_count
            if rate > self.error_rate_threshold:
                self.publish_event(
                    "error_rate_high",
                    {
                        "error_count": self.error_count,
                        "total_count": self.total_count,
                        "error_rate": rate,
                    },
                )

    async def _check_stall(self) -> None:
        """Check if task has stalled."""
        elapsed = (datetime.now() - self.last_activity).total_seconds() / 60
        if elapsed > self.stall_timeout_min:
            self.publish_event(
                "task_stalled",
                {
                    "stall_duration_min": elapsed,
                    "threshold_min": self.stall_timeout_min,
                },
            )

    async def handle_request(self, request_type: str, **kwargs) -> Any:
        """Handle health queries."""
        if request_type == "health_status":
            await self._check_stall()
            return {
                "status": "healthy",
                "error_count": self.error_count,
                "total_count": self.total_count,
                "last_activity": self.last_activity.isoformat(),
            }
        elif request_type == "error_rate":
            if self.total_count == 0:
                return 0.0
            return self.error_count / self.total_count

        raise ValueError(f"Unknown request type: {request_type}")

    def shutdown(self) -> None:
        """Cleanup."""
        logger.info("HealthMonitor shutdown")
