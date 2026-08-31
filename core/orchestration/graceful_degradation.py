"""Graceful Degradation (Phase 2, Week 9).

Handles the case when all engines fail - returns quality_unavailable instead of crashing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta


@dataclass
class QualityUnavailableResponse:
    """Response when all engines fail."""

    task_id: str
    reason: str
    message_to_operator: str
    retry_after_seconds: int = 30
    timestamp: str = ""
    last_healthy_engine: Optional[str] = None

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


class GracefulDegradationHandler:
    """Handles cascading failures gracefully."""

    def __init__(self):
        self.engine_health_window: dict[str, list[bool]] = {}  # Engine → last 10 health states
        self.consecutive_failures: dict[str, int] = {}
        self.last_healthy_engine: Optional[str] = None
        self.last_failure_time: Optional[datetime] = None

    def record_engine_failure(self, engine_name: str) -> None:
        """Record an engine failure."""
        if engine_name not in self.engine_health_window:
            self.engine_health_window[engine_name] = []
            self.consecutive_failures[engine_name] = 0

        self.engine_health_window[engine_name].append(False)
        if len(self.engine_health_window[engine_name]) > 10:
            self.engine_health_window[engine_name].pop(0)

        self.consecutive_failures[engine_name] += 1
        self.last_failure_time = datetime.utcnow()

    def record_engine_success(self, engine_name: str) -> None:
        """Record an engine success."""
        if engine_name not in self.engine_health_window:
            self.engine_health_window[engine_name] = []
            self.consecutive_failures[engine_name] = 0

        self.engine_health_window[engine_name].append(True)
        if len(self.engine_health_window[engine_name]) > 10:
            self.engine_health_window[engine_name].pop(0)

        self.consecutive_failures[engine_name] = 0
        self.last_healthy_engine = engine_name

    def all_engines_failed(self, cascade_result) -> bool:
        """Check if cascade exhausted all engines."""
        return (
            not cascade_result.success and
            cascade_result.cascade_level >= 4  # Passed all 4 engines (0-3 levels + exhaustion)
        )

    def handle_complete_failure(self, task_id: str, cascade_result) -> QualityUnavailableResponse:
        """Generate degraded response when all engines fail."""
        reasons = []

        # Analyze why all engines failed
        for engine_name, failures in self.consecutive_failures.items():
            if failures > 0:
                reasons.append(f"{engine_name}: {failures} consecutive failures")

        reason = " | ".join(reasons) if reasons else "Unknown (all engines unreachable)"

        message = (
            "All processing engines are currently unavailable. "
            "Your request could not be processed. "
            "The system will retry automatically in 30 seconds. "
            f"Reason: {reason}"
        )

        return QualityUnavailableResponse(
            task_id=task_id,
            reason=reason,
            message_to_operator=message,
            retry_after_seconds=30,
            last_healthy_engine=self.last_healthy_engine,
        )

    def is_recovering(self) -> bool:
        """Check if system is recovering from outage."""
        if not self.last_failure_time:
            return False

        # System is recovering if last failure was >2 minutes ago
        return (datetime.utcnow() - self.last_failure_time) > timedelta(minutes=2)

    def get_health_status(self) -> dict:
        """Get health status of all engines."""
        status = {}

        for engine_name, health_states in self.engine_health_window.items():
            if health_states:
                recent_successes = sum(health_states[-5:])  # Last 5 attempts
                status[engine_name] = {
                    "recent_success_rate": recent_successes / 5.0 * 100 if len(health_states) >= 5 else 0,
                    "consecutive_failures": self.consecutive_failures.get(engine_name, 0),
                    "status": "healthy" if recent_successes >= 3 else "degraded",
                }

        return status
