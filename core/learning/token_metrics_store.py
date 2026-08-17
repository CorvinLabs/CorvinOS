"""TokenMetrics persistence layer — EventStore integration (Phase 1.K2).

Stores token measurements immutably and provides query interface for aggregation.
Now with DB backend support (Phase 2.K=2).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from core.learning.event_schema import LearningEventType, LearningEvent, TokenMetricsPayload
from core.learning.event_emitter import EventEmitter

if TYPE_CHECKING:
    from core.learning.token_metrics_db import TokenMetricsDB


class TokenMetricsStore:
    """Persistence layer for token measurements.

    Stores TokenMetricsPayload events via EventEmitter (immutable, hash-chained).
    Optionally persists to DB backend (SQLite/PostgreSQL) for efficient queries.
    Provides query interface for aggregation by task type, domain, subsystem, etc.
    """

    def __init__(
        self,
        event_emitter: EventEmitter,
        db: Optional[TokenMetricsDB] = None,
    ):
        """Initialize store with EventEmitter and optional DB backend.

        Args:
            event_emitter: EventEmitter for writing events (immutable, always used)
            db: Optional TokenMetricsDB backend for persistent storage.
                If None, queries use in-memory cache only (Phase 1 behavior).
        """
        self.event_emitter = event_emitter
        self.db = db
        # In-memory cache for fast queries (write-through when DB available)
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
        """Write token metrics event to EventStore and optional DB backend.

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

        # Write to EventEmitter (immutable, hash-chained) — always, first
        self.event_emitter.emit(event)

        # Write to DB backend if available (non-blocking, log errors only)
        if self.db:
            try:
                import asyncio
                # If we're already in an async context, use create_task to avoid blocking
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(self.db.insert_token_metrics(event))
                except RuntimeError:
                    # No event loop running; attempt sync write with warning
                    import logging
                    logging.warning(
                        "DB write called from sync context; "
                        "consider making caller async"
                    )
            except Exception as e:
                import logging
                logging.warning(f"Failed to write token metrics to DB: {e}")

        # Cache for fast queries
        self._cache[event.event_id] = event

        return event.event_id

    def get_event(self, event_id: str) -> Optional[LearningEvent]:
        """Retrieve a single token metrics event by ID from cache.

        Args:
            event_id: Event identifier

        Returns:
            LearningEvent if found in cache, None otherwise
        """
        return self._cache.get(event_id)

    # Synchronous methods for Phase 1 compatibility (cache-only, non-async)

    def query_by_turn_sync(self, turn_id: str) -> Optional[LearningEvent]:
        """Retrieve token metrics for a specific turn (cache-only, synchronous).

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

    def query_by_session_sync(self, session_id: str, limit: int = 1000) -> list[LearningEvent]:
        """Find all token metrics for a session (cache-only, synchronous).

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

    def query_by_timespan_sync(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[LearningEvent]:
        """Find token metrics within a time range (cache-only, synchronous).

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

    def aggregate_by_task_type_sync(self, session_id: str) -> dict[str, dict]:
        """Aggregate token metrics by task type (cache-only, synchronous).

        Args:
            session_id: Session identifier

        Returns:
            dict mapping task_type -> {turns, total_tokens, baseline, savings_percent}
        """
        events = self.query_by_session_sync(session_id)
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

    def aggregate_by_subsystem_sync(self, session_id: str) -> dict[str, dict]:
        """Aggregate token savings by subsystem (cache-only, synchronous).

        Args:
            session_id: Session identifier

        Returns:
            dict mapping subsystem -> {count, total_tokens, avg_tokens}
        """
        events = self.query_by_session_sync(session_id)
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

    def summary_sync(self, session_id: str) -> dict:
        """Get summary stats for a session (cache-only, synchronous).

        Args:
            session_id: Session identifier

        Returns:
            dict with turn_count, total_tokens, baseline_tokens, savings stats
        """
        events = self.query_by_session_sync(session_id)

        summary = {
            "turn_count": 0,
            "total_tokens": 0,
            "baseline_tokens": 0,
            "savings_tokens": 0,
            "savings_percent": 0.0,
            "avg_tokens_per_turn": 0.0,
            "subsystems": self.aggregate_by_subsystem_sync(session_id),
            "by_task_type": self.aggregate_by_task_type_sync(session_id),
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

    async def close(self) -> None:
        """Close database connection.

        Safe to call even if db is None (no-op in that case).
        """
        if self.db:
            try:
                await self.db.close()
            except Exception as e:
                import logging
                logging.warning(f"Error closing metrics DB: {e}")

    async def query_by_turn(
        self, turn_id: str, tenant_id: str
    ) -> Optional[LearningEvent]:
        """Find token metrics for a specific turn.

        Queries DB backend if available; falls back to in-memory cache.

        Args:
            turn_id: Turn identifier
            tenant_id: Tenant identifier (for DB isolation)

        Returns:
            TokenMetricsPayload event if found
        """
        # Prefer DB query if available
        if self.db:
            try:
                row = await self.db.query_by_turn(turn_id, tenant_id)
                if row:
                    event = LearningEvent(
                        event_type=LearningEventType.TOKEN_METRICS,
                        tenant_id=row.get("tenant_id", tenant_id),
                        instance_id=row.get("instance_id", ""),
                        session_id=row.get("session_id", ""),
                        timestamp_utc=row.get("timestamp_utc"),
                        event_id=row.get("event_id", ""),
                        user_id=row.get("user_id"),
                        skill_name=row.get("skill_name"),
                        payload={"token_metrics": row},
                    )
                    return event
                return None
            except Exception as e:
                import logging
                logging.warning(f"DB query_by_turn failed, falling back to cache: {e}")

        # Fallback to in-memory cache
        for event in self._cache.values():
            if event.event_type == LearningEventType.TOKEN_METRICS:
                if isinstance(event.payload, dict):
                    metrics = event.payload.get("token_metrics", {})
                    if metrics.get("turn_id") == turn_id:
                        return event
        return None

    async def query_by_session(
        self, session_id: str, tenant_id: str, limit: int = 1000
    ) -> list[LearningEvent]:
        """Find all token metrics for a session.

        Queries DB backend if available; falls back to in-memory cache.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier (for DB isolation)
            limit: Maximum number of results (default 1000)

        Returns:
            List of TokenMetricsPayload events, sorted by timestamp DESC
        """
        # Prefer DB query if available
        if self.db:
            try:
                rows = await self.db.query_by_session(session_id, tenant_id, limit)
                # Convert dict rows back to LearningEvent objects
                events = []
                for row in rows:
                    event = LearningEvent(
                        event_type=LearningEventType.TOKEN_METRICS,
                        tenant_id=row.get("tenant_id", tenant_id),
                        instance_id=row.get("instance_id", ""),
                        session_id=row.get("session_id", session_id),
                        timestamp_utc=row.get("timestamp_utc"),
                        event_id=row.get("event_id", ""),
                        user_id=row.get("user_id"),
                        skill_name=row.get("skill_name"),
                        payload={"token_metrics": row},
                    )
                    events.append(event)
                return events
            except Exception as e:
                import logging
                logging.warning(f"DB query failed, falling back to cache: {e}")

        # Fallback to in-memory cache
        results = []
        for event in self._cache.values():
            if event.event_type == LearningEventType.TOKEN_METRICS and event.session_id == session_id:
                results.append(event)
                if len(results) >= limit:
                    break
        return sorted(results, key=lambda e: e.timestamp_utc, reverse=True)

    async def query_by_timespan(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[LearningEvent]:
        """Find token metrics within a time range.

        Queries DB backend if available; falls back to in-memory cache.

        Args:
            tenant_id: Tenant identifier
            start: Start time (inclusive)
            end: End time (inclusive)
            limit: Maximum number of results

        Returns:
            List of TokenMetricsPayload events in time range, sorted by timestamp ASC
        """
        # Prefer DB query if available
        if self.db:
            try:
                rows = await self.db.query_by_timespan(tenant_id, start, end, limit)
                events = []
                for row in rows:
                    event = LearningEvent(
                        event_type=LearningEventType.TOKEN_METRICS,
                        tenant_id=row.get("tenant_id", tenant_id),
                        instance_id=row.get("instance_id", ""),
                        session_id=row.get("session_id", ""),
                        timestamp_utc=row.get("timestamp_utc"),
                        event_id=row.get("event_id", ""),
                        user_id=row.get("user_id"),
                        skill_name=row.get("skill_name"),
                        payload={"token_metrics": row},
                    )
                    events.append(event)
                return events
            except Exception as e:
                import logging
                logging.warning(f"DB query_by_timespan failed, falling back to cache: {e}")

        # Fallback to in-memory cache
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

    async def aggregate_by_task_type(
        self, session_id: str, tenant_id: str
    ) -> dict[str, dict]:
        """Aggregate token metrics by task type.

        Uses DB backend if available; falls back to in-memory aggregation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier (for DB isolation)

        Returns:
            dict mapping task_type -> {turns, total_tokens, baseline, savings_percent}
        """
        # Prefer DB aggregation if available
        if self.db:
            try:
                return await self.db.aggregate_by_task_type(session_id, tenant_id)
            except Exception as e:
                import logging
                logging.warning(f"DB task aggregation failed, falling back to cache: {e}")

        # Fallback: in-memory aggregation
        events = await self.query_by_session(session_id, tenant_id)
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

    async def aggregate_by_subsystem(
        self, session_id: str, tenant_id: str
    ) -> dict[str, dict]:
        """Aggregate token savings by subsystem.

        Uses DB backend if available; falls back to in-memory aggregation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier (for DB isolation)

        Returns:
            dict mapping subsystem -> {count, total_tokens, avg_tokens}
        """
        # Prefer DB aggregation if available
        if self.db:
            try:
                return await self.db.aggregate_by_subsystem(session_id, tenant_id)
            except Exception as e:
                import logging
                logging.warning(f"DB subsystem aggregation failed, falling back to cache: {e}")

        # Fallback: in-memory aggregation
        events = await self.query_by_session(session_id, tenant_id)
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

    async def summary(self, session_id: str, tenant_id: str) -> dict:
        """Get summary stats for a session.

        Uses DB backend if available for efficiency; falls back to in-memory aggregation.

        Args:
            session_id: Session identifier
            tenant_id: Tenant identifier (for DB isolation)

        Returns:
            dict with turn_count, total_tokens, baseline_tokens, savings stats, subsystems, by_task_type
        """
        # Prefer DB aggregation if available
        if self.db:
            try:
                return await self.db.summary(session_id, tenant_id)
            except Exception as e:
                import logging
                logging.warning(f"DB summary query failed, falling back to cache: {e}")

        # Fallback: in-memory aggregation from cache
        events = await self.query_by_session(session_id, tenant_id)

        summary = {
            "turn_count": 0,
            "total_tokens": 0,
            "baseline_tokens": 0,
            "savings_tokens": 0,
            "savings_percent": 0.0,
            "avg_tokens_per_turn": 0.0,
            "subsystems": await self.aggregate_by_subsystem(session_id, tenant_id),
            "by_task_type": await self.aggregate_by_task_type(session_id, tenant_id),
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
