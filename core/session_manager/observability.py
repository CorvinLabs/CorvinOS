"""Phase 3: Observability Dashboard (ADR-0472 Phase 3).

Session timeline, metrics, alerts for autonomous session management.
Production-ready metrics & API for Console dashboard.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import json

logger = logging.getLogger(__name__)


@dataclass
class SessionEvent:
    """Event in autonomous session lifecycle."""
    event_type: str      # "session_started", "split", "error", "completed"
    task_id: str
    session_id: str
    timestamp: float
    metadata: Dict[str, Any]


@dataclass
class SessionMetrics:
    """Aggregated metrics for observability (Phase 3)."""
    total_tasks: int
    total_sessions: int
    total_splits: int
    avg_splits_per_task: float
    error_rate: float
    avg_session_duration_sec: float
    timestamp: float


class ObservabilityCollector:
    """Phase 3: Observability collector (ADR-0472 Phase 3, Production-Ready)."""

    def __init__(self):
        # CRITICAL-010 fix: Make events private to prevent direct manipulation
        self._events: List[SessionEvent] = []
        self.task_metrics: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "sessions": [],
            "splits": 0,
            "errors": 0,
            "start_time": None,
            "end_time": None,
        })

    def get_events(self) -> List[SessionEvent]:
        """Get a copy of events list (read-only, CRITICAL-010 fix).

        Returns:
            Copy of events list (modifications don't affect internal state)
        """
        return list(self._events)

    async def record_event(self, event: SessionEvent) -> None:
        """Record session lifecycle event (Phase 3, with validation - CRITICAL-010 fix)."""
        # CRITICAL-010 fix: Validate event before recording
        if not self._validate_event(event):
            logger.error(f"[Observability] Invalid event rejected: {event}")
            return

        # Use private _events list
        self._events.append(event)

        # Update task-level metrics
        if event.task_id in self.task_metrics:
            metrics = self.task_metrics[event.task_id]
            if event.event_type == "session_started":
                if not metrics["start_time"]:
                    metrics["start_time"] = event.timestamp
            elif event.event_type == "split":
                metrics["splits"] += 1
            elif event.event_type == "error":
                metrics["errors"] += 1
            elif event.event_type == "completed":
                metrics["end_time"] = event.timestamp

        logger.info(f"[Observability] {event.event_type}: {event.task_id}")

    @staticmethod
    def _validate_event(event: SessionEvent) -> bool:
        """Validate event before recording (CRITICAL-010 fix).

        Args:
            event: Event to validate

        Returns:
            True if valid, False otherwise
        """
        # Validate required fields
        if not event.task_id or not event.session_id or event.timestamp <= 0:
            return False

        # Validate event_type is one of the known types
        valid_types = {"session_started", "split", "error", "completed"}
        if event.event_type not in valid_types:
            return False

        # Validate metadata is a dict
        if not isinstance(event.metadata, dict):
            return False

        return True

    async def get_task_timeline(self, task_id: str) -> List[Dict[str, Any]]:
        """Get session timeline for dashboard (Phase 3)."""
        timeline = [asdict(e) for e in self._events if e.task_id == task_id]
        return sorted(timeline, key=lambda x: x["timestamp"])

    async def compute_metrics(self) -> SessionMetrics:
        """Compute production-ready dashboard metrics (Phase 3)."""
        total_tasks = len(self.task_metrics)
        total_sessions = len(self._events)  # Use private _events
        total_splits = sum(
            m["splits"] for m in self.task_metrics.values()
        )
        total_errors = sum(
            m["errors"] for m in self.task_metrics.values()
        )

        avg_splits = (
            total_splits / total_tasks if total_tasks > 0 else 0
        )
        error_rate = (
            total_errors / total_sessions if total_sessions > 0 else 0.0
        )

        # Compute average session duration
        durations = []
        for metrics in self.task_metrics.values():
            if metrics["start_time"] and metrics["end_time"]:
                duration = metrics["end_time"] - metrics["start_time"]
                durations.append(duration)
        avg_duration = (
            sum(durations) / len(durations) if durations else 0.0
        )

        return SessionMetrics(
            total_tasks=total_tasks,
            total_sessions=total_sessions,
            total_splits=total_splits,
            avg_splits_per_task=avg_splits,
            error_rate=error_rate,
            avg_session_duration_sec=avg_duration,
            timestamp=datetime.now().timestamp(),
        )

    async def export_metrics_json(self) -> str:
        """Export metrics as JSON for Console API (Phase 3)."""
        metrics = await self.compute_metrics()
        return json.dumps(asdict(metrics))
