"""TokenMetrics persistence layer — EventStore integration (Phase 1.K2).

Stores token measurements immutably and provides query interface for aggregation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from core.learning.event_schema import LearningEventType, LearningEvent, TokenMetricsPayload
from core.learning.event_emitter import EventEmitter


class TokenMetricsStore:
    """Persistence layer for token measurements.

    Stores TokenMetricsPayload events via EventEmitter (immutable, hash-chained).
    Provides query interface for aggregation by task type, domain, subsystem, etc.
    """

    def __init__(self, event_emitter: EventEmitter):
        """Initialize store with EventEmitter backend.

        Args:
            event_emitter: EventEmitter for writing events (immutable)
        """
        self.event_emitter = event_emitter
        # In-memory cache for fast queries (Phase 1; will be DB-backed in Phase 2+)
        self._cache: dict[str, LearningEvent] = {}

    def write_token_metrics(
        self,
        counter,  # TokenCounter
        tenant_id: str,
        instance_id: str,
        session_id: str,
        user_id: Optional[str] = None,
        skill_name: Optional[str] = None,
    ) -> str:
        """Write token metrics event to EventStore.

        Args:
            counter: TokenCounter with measurement data
            tenant_id: Tenant identifier
            instance_id: Instance identifier
            session_id: Session identifier
            user_id: Optional user identifier
            skill_name: Optional skill being measured

        Returns:
            event_id of the written event
        """
        # Convert TokenCounter to LearningEvent
        event = counter.to_event(
            tenant_id=tenant_id,
            instance_id=instance_id,
            session_id=session_id,
            user_id=user_id,
            skill_name=skill_name,
        )

        # Write to EventEmitter (immutable, hash-chained)
        self.event_emitter.emit(event)

        # Cache for fast queries
        self._cache[event.event_id] = event

        return event.event_id

    def get_event(self, event_id: str) -> Optional[LearningEvent]:
        """Retrieve a single token metrics event by ID.

        Args:
            event_id: Event identifier

        Returns:
            LearningEvent if found, None otherwise
        """
        return self._cache.get(event_id)

    def query_by_turn(self, turn_id: str) -> Optional[LearningEvent]:
        """Find token metrics for a specific turn.

        Args:
            turn_id: Turn identifier

        Returns:
            TokenMetricsPayload event if found
        """
        for event in self._cache.values():
            if event.event_type == LearningEventType.TOKEN_METRICS:
                if isinstance(event.payload, dict):
                    metrics = event.payload.get("token_metrics", {})
                    if metrics.get("turn_id") == turn_id:
                        return event
        return None

    def query_by_session(self, session_id: str, limit: int = 1000) -> list[LearningEvent]:
        """Find all token metrics for a session.

        Args:
            session_id: Session identifier
            limit: Maximum number of results (default 1000)

        Returns:
            List of TokenMetricsPayload events
        """
        results = []
        for event in self._cache.values():
            if event.event_type == LearningEventType.TOKEN_METRICS and event.session_id == session_id:
                results.append(event)
                if len(results) >= limit:
                    break
        return sorted(results, key=lambda e: e.timestamp_utc, reverse=True)

    def query_by_timespan(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[LearningEvent]:
        """Find token metrics within a time range.

        Args:
            tenant_id: Tenant identifier
            start: Start time (inclusive)
            end: End time (inclusive)
            limit: Maximum number of results

        Returns:
            List of TokenMetricsPayload events in time range
        """
        results = []
        for event in self._cache.values():
            if (
                event.event_type == LearningEventType.TOKEN_METRICS
                and event.tenant_id == tenant_id
                and start <= event.timestamp_utc <= end
            ):
                results.append(event)
                if len(results) >= limit:
                    break
        return sorted(results, key=lambda e: e.timestamp_utc)

    def aggregate_by_task_type(self, session_id: str) -> dict[str, dict]:
        """Aggregate token metrics by task type.

        Args:
            session_id: Session identifier

        Returns:
            dict mapping task_type -> {turns, total_tokens, baseline, savings_percent}
        """
        events = self.query_by_session(session_id)
        aggregates = {}

        for event in events:
            if event.event_type != LearningEventType.TOKEN_METRICS:
                continue

            metrics = event.payload.get("token_metrics", {})
            task_type = metrics.get("task_type", "unknown")

            if task_type not in aggregates:
                aggregates[task_type] = {
                    "turns": 0,
                    "total_tokens": 0,
                    "baseline_tokens": 0,
                    "savings_tokens": 0,
                    "savings_percent": 0.0,
                }

            agg = aggregates[task_type]
            agg["turns"] += 1
            agg["total_tokens"] += metrics.get("total_tokens", 0)
            agg["baseline_tokens"] += metrics.get("baseline_tokens", 0) or 0
            agg["savings_tokens"] += metrics.get("savings_tokens", 0) or 0

        # Calculate percentages
        for agg in aggregates.values():
            if agg["baseline_tokens"] > 0:
                agg["savings_percent"] = (agg["savings_tokens"] / agg["baseline_tokens"]) * 100
            else:
                agg["savings_percent"] = 0.0

        return aggregates

    def aggregate_by_subsystem(self, session_id: str) -> dict[str, dict]:
        """Aggregate token savings by subsystem.

        Args:
            session_id: Session identifier

        Returns:
            dict mapping subsystem -> {count, total_tokens, avg_tokens_per_trigger}
        """
        events = self.query_by_session(session_id)
        aggregates = {}

        for event in events:
            if event.event_type != LearningEventType.TOKEN_METRICS:
                continue

            metrics = event.payload.get("token_metrics", {})
            subsystem_tokens = metrics.get("subsystem_tokens", {})

            for subsystem, tokens in subsystem_tokens.items():
                if subsystem not in aggregates:
                    aggregates[subsystem] = {
                        "count": 0,
                        "total_tokens": 0,
                        "avg_tokens": 0.0,
                    }

                agg = aggregates[subsystem]
                agg["count"] += 1
                agg["total_tokens"] += tokens

        # Calculate averages
        for agg in aggregates.values():
            if agg["count"] > 0:
                agg["avg_tokens"] = agg["total_tokens"] / agg["count"]

        return aggregates

    def summary(self, session_id: str) -> dict:
        """Get summary stats for a session.

        Args:
            session_id: Session identifier

        Returns:
            dict with turn_count, total_tokens, baseline_tokens, savings stats
        """
        events = self.query_by_session(session_id)

        summary = {
            "turn_count": 0,
            "total_tokens": 0,
            "baseline_tokens": 0,
            "savings_tokens": 0,
            "savings_percent": 0.0,
            "avg_tokens_per_turn": 0.0,
            "subsystems": self.aggregate_by_subsystem(session_id),
            "by_task_type": self.aggregate_by_task_type(session_id),
        }

        for event in events:
            if event.event_type != LearningEventType.TOKEN_METRICS:
                continue

            metrics = event.payload.get("token_metrics", {})
            summary["turn_count"] += 1
            summary["total_tokens"] += metrics.get("total_tokens", 0)
            summary["baseline_tokens"] += metrics.get("baseline_tokens", 0) or 0
            summary["savings_tokens"] += metrics.get("savings_tokens", 0) or 0

        if summary["turn_count"] > 0:
            summary["avg_tokens_per_turn"] = summary["total_tokens"] / summary["turn_count"]

        if summary["baseline_tokens"] > 0:
            summary["savings_percent"] = (summary["savings_tokens"] / summary["baseline_tokens"]) * 100

        return summary
