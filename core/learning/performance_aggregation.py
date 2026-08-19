"""Performance Aggregation Pipeline — hourly metrics aggregation (ADR-0324).

Aggregates TOOL_EXECUTED + SKILL_GRADED events into performance metrics:
- Success rates, latency percentiles, cost averages
- Bayesian confidence intervals (converge at 30 samples)
- Trend detection (improving/stable/degrading)
- Caching with 5-minute TTL
- Tenant isolation (all queries scoped by tenant_id)

This module provides the foundation for Gap 2 (Tool Ranking) and Gap 3 (Skill Grading).

ADR-0324 requires:
1. Hourly aggregation of all tool/skill events
2. Immutable metrics (frozen dataclass, fail-fast validation)
3. Bayesian confidence (sample-count driven)
4. Tenant isolation (GDPR Art. 5, 32)
5. Non-blocking emission (metrics events don't block)
6. Caching for performance (<1 second per 10k events)

Status: ADR-0324 PROPOSED
Dependencies: ADR-0321 (Tool Execution), ADR-0314 (Learning Infrastructure)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from .event_schema import LearningEvent, LearningEventType
from .event_persistence import EventStore as AsyncEventStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPerformanceMetrics:
    """Aggregated performance metrics for a tool (immutable).

    Bayesian confidence converges linearly from 0.0 at 0 samples to 1.0 at 30+ samples:
    confidence = min(1.0, total_count / 30)
    """

    tool_id: str
    success_rate: float  # 0.0-1.0
    success_count: int  # N successes
    total_count: int    # N attempts
    avg_latency_ms: int
    p50_latency_ms: int
    p95_latency_ms: int
    p99_latency_ms: int
    avg_cost_cents: int
    cost_samples: int
    confidence: float  # 0.0-1.0 (Bayesian, converges at 30 samples)
    trend: str  # "improving" | "stable" | "degrading"
    days_since_first_sample: int
    last_updated_utc: datetime
    tenant_id: str

    @property
    def is_cold_start(self) -> bool:
        """True if <10 samples (not enough for confidence)."""
        return self.total_count < 10

    def to_event_payload(self) -> dict[str, Any]:
        """Convert to learning event payload format."""
        return {
            "tool_id": self.tool_id,
            "success_rate": self.success_rate,
            "success_count": self.success_count,
            "total_count": self.total_count,
            "avg_latency_ms": self.avg_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "avg_cost_cents": self.avg_cost_cents,
            "cost_samples": self.cost_samples,
            "confidence": self.confidence,
            "trend": self.trend,
            "days_since_first_sample": self.days_since_first_sample,
            "last_updated_utc": self.last_updated_utc.isoformat() + "Z",
        }


@dataclass(frozen=True)
class SkillPerformanceMetrics:
    """Aggregated performance metrics for a skill (immutable)."""

    skill_name: str
    success_rate: float
    success_count: int
    total_count: int
    avg_latency_ms: int
    p50_latency_ms: int
    p95_latency_ms: int
    p99_latency_ms: int
    avg_cost_cents: int
    cost_samples: int
    confidence: float
    trend: str
    days_since_first_sample: int
    last_updated_utc: datetime
    tenant_id: str

    @property
    def is_cold_start(self) -> bool:
        """True if <10 samples."""
        return self.total_count < 10

    def to_event_payload(self) -> dict[str, Any]:
        """Convert to learning event payload format."""
        return {
            "skill_name": self.skill_name,
            "success_rate": self.success_rate,
            "success_count": self.success_count,
            "total_count": self.total_count,
            "avg_latency_ms": self.avg_latency_ms,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "p99_latency_ms": self.p99_latency_ms,
            "avg_cost_cents": self.avg_cost_cents,
            "cost_samples": self.cost_samples,
            "confidence": self.confidence,
            "trend": self.trend,
            "days_since_first_sample": self.days_since_first_sample,
            "last_updated_utc": self.last_updated_utc.isoformat() + "Z",
        }


class PerformanceCache:
    """Thread-safe cache with TTL for aggregated metrics."""

    def __init__(self, ttl_seconds: int = 300):
        """Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cached entries (default 5 minutes)
        """
        self.ttl = ttl_seconds
        self.data: Dict[str, Any] = {}
        self.timestamps: Dict[str, datetime] = {}
        self.lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        async with self.lock:
            if key in self.data:
                created = self.timestamps[key]
                age_seconds = (datetime.now() - created).total_seconds()
                if age_seconds < self.ttl:
                    return self.data[key]
                else:
                    # Expired: remove
                    del self.data[key]
                    del self.timestamps[key]
            return None

    async def set(self, key: str, value: Any) -> None:
        """Set value in cache with current timestamp.

        Args:
            key: Cache key
            value: Value to cache
        """
        async with self.lock:
            self.data[key] = value
            self.timestamps[key] = datetime.now()

    async def clear_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries removed
        """
        async with self.lock:
            now = datetime.now()
            expired = [
                k for k, v in self.timestamps.items()
                if (now - v).total_seconds() > self.ttl
            ]
            for k in expired:
                del self.data[k]
                del self.timestamps[k]
            return len(expired)

    async def clear_all(self) -> None:
        """Clear entire cache."""
        async with self.lock:
            self.data.clear()
            self.timestamps.clear()

    async def size(self) -> int:
        """Get number of cached entries."""
        async with self.lock:
            return len(self.data)


class PerformanceAggregator:
    """Compute metrics from EventStore, emit aggregation_complete events.

    Runs hourly aggregation job that:
    1. Queries TOOL_EXECUTED and SKILL_GRADED events
    2. Computes success rates, latency percentiles, cost averages
    3. Calculates Bayesian confidence (converges at 30 samples)
    4. Detects trends (improving/stable/degrading)
    5. Caches results (5-minute TTL)
    6. Emits tool_metrics_aggregated events for observability

    Tenant-scoped: all queries respect tenant_id (GDPR Art. 5, 32).
    """

    def __init__(
        self,
        event_store: AsyncEventStore,
        event_emitter: Optional[Any] = None,
        cache_ttl_seconds: int = 300,
        aggregation_interval_seconds: int = 3600,
    ):
        """Initialize aggregator.

        Args:
            event_store: AsyncEventStore instance for querying
            event_emitter: EventEmitter instance (optional, for observability)
            cache_ttl_seconds: Cache time-to-live (default 5 minutes)
            aggregation_interval_seconds: Aggregation frequency (default 1 hour)
        """
        self.event_store = event_store
        self.event_emitter = event_emitter
        self.cache = PerformanceCache(ttl_seconds=cache_ttl_seconds)
        self.aggregation_interval = aggregation_interval_seconds
        self._aggregation_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start hourly aggregation loop."""
        if self._aggregation_task is None:
            self._aggregation_task = asyncio.create_task(self._run_aggregation_loop())
            logger.info("PerformanceAggregator started (hourly job)")

    async def stop(self) -> None:
        """Stop aggregation loop."""
        if self._aggregation_task:
            self._aggregation_task.cancel()
            try:
                await self._aggregation_task
            except asyncio.CancelledError:
                pass
            self._aggregation_task = None
            logger.info("PerformanceAggregator stopped")

    async def _run_aggregation_loop(self) -> None:
        """Background loop: run aggregation every hour."""
        while True:
            try:
                await asyncio.sleep(self.aggregation_interval)
                await self.aggregate_all_metrics()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Aggregation job failed: {e}", exc_info=True)

    async def aggregate_all_metrics(self, tenant_id: str) -> None:
        """Aggregate metrics for all tools and skills.

        Args:
            tenant_id: Tenant to aggregate for (required)
        """
        logger.info(f"Starting performance aggregation (tenant={tenant_id})...")

        try:
            # Aggregate tools
            tool_metrics = await self._aggregate_tool_metrics(tenant_id=tenant_id)
            logger.info(f"Aggregated {len(tool_metrics)} tools")

            # Aggregate skills (future: when SKILL_GRADED events available)
            skill_metrics = await self._aggregate_skill_metrics(tenant_id=tenant_id)
            logger.info(f"Aggregated {len(skill_metrics)} skills")

            # Cache all metrics
            for tool_id, metrics in tool_metrics.items():
                await self.cache.set(f"tool:{metrics.tenant_id}:{tool_id}", metrics)

            for skill_name, metrics in skill_metrics.items():
                await self.cache.set(f"skill:{metrics.tenant_id}:{skill_name}", metrics)

            # Emit observability events
            await self._emit_aggregation_events(tool_metrics, skill_metrics)

        except Exception as e:
            logger.error(f"Aggregation failed: {e}", exc_info=True)

    async def _aggregate_tool_metrics(
        self, tenant_id: str, days: int = 7
    ) -> Dict[str, ToolPerformanceMetrics]:
        """Aggregate TOOL_EXECUTED events into metrics.

        Args:
            tenant_id: Tenant to aggregate for (required)
            days: Time window for aggregation (default 7 days)

        Returns:
            Dict[tool_id -> ToolPerformanceMetrics]
        """
        # Query all TOOL_EXECUTED events
        events = await self._query_tool_events(tenant_id=tenant_id, days=days)

        # Group by tool_id
        tools_by_id: Dict[str, List[LearningEvent]] = {}
        for event in events:
            payload = event.payload
            tool_id = payload.get("tool_id")
            if tool_id:
                if tool_id not in tools_by_id:
                    tools_by_id[tool_id] = []
                tools_by_id[tool_id].append(event)

        # Compute metrics for each tool
        metrics = {}
        for tool_id, tool_events in tools_by_id.items():
            tool_metrics = self._compute_tool_metrics(
                tool_id=tool_id,
                events=tool_events,
                tenant_id=tenant_id,
            )
            if tool_metrics:
                metrics[tool_id] = tool_metrics

        return metrics

    async def _aggregate_skill_metrics(
        self, tenant_id: str, days: int = 7
    ) -> Dict[str, SkillPerformanceMetrics]:
        """Aggregate SKILL_GRADED events into metrics.

        Args:
            tenant_id: Tenant to aggregate for (required)
            days: Time window for aggregation

        Returns:
            Dict[skill_name -> SkillPerformanceMetrics]
        """
        # Query all SKILL_GRADED events (when available)
        # For now, return empty dict (future Gap)
        return {}

    def _compute_tool_metrics(
        self,
        tool_id: str,
        events: List[LearningEvent],
        tenant_id: Optional[str] = None,
    ) -> Optional[ToolPerformanceMetrics]:
        """Compute metrics from list of TOOL_EXECUTED events.

        Args:
            tool_id: Tool ID
            events: List of LearningEvent objects
            tenant_id: Tenant ID for metrics

        Returns:
            ToolPerformanceMetrics or None if no events
        """
        if not events:
            return None

        payloads = [e.payload for e in events]

        # Extract status/success
        successes = sum(
            1 for p in payloads if p.get("status") == "success"
        )
        total = len(payloads)
        success_rate = successes / total if total > 0 else 0.0

        # Extract latencies (in ms)
        latencies = [
            p.get("latency_ms", 0) for p in payloads
            if p.get("latency_ms") is not None
        ]
        latencies = [l for l in latencies if l >= 0]
        latencies.sort()

        if latencies:
            p50_idx = len(latencies) // 2
            p95_idx = max(0, int(len(latencies) * 0.95) - 1)
            p99_idx = max(0, int(len(latencies) * 0.99) - 1)

            avg_latency = sum(latencies) / len(latencies)
            p50 = latencies[min(p50_idx, len(latencies) - 1)]
            p95 = latencies[min(p95_idx, len(latencies) - 1)]
            p99 = latencies[min(p99_idx, len(latencies) - 1)]
        else:
            avg_latency = p50 = p95 = p99 = 0

        # Extract costs (in cents)
        costs = [
            p.get("estimated_cost_cents", 0) for p in payloads
            if p.get("estimated_cost_cents") is not None
        ]
        costs = [c for c in costs if c >= 0]
        avg_cost = sum(costs) / len(costs) if costs else 0

        # Bayesian confidence (converges linearly: 0 at 0 samples -> 1.0 at 30 samples)
        confidence = min(1.0, total / 30)

        # Trend detection (last 10% vs overall)
        recent_cutoff = max(0, int(len(payloads) * 0.9))
        if recent_cutoff < len(payloads):
            recent_successes = sum(
                1 for p in payloads[recent_cutoff:]
                if p.get("status") == "success"
            )
            recent_success_rate = (
                recent_successes / (len(payloads) - recent_cutoff)
                if len(payloads) - recent_cutoff > 0
                else success_rate
            )
        else:
            recent_success_rate = success_rate

        trend_delta = recent_success_rate - success_rate
        if trend_delta > 0.05:
            trend = "improving"
        elif trend_delta < -0.05:
            trend = "degrading"
        else:
            trend = "stable"

        # Days since first event
        first_timestamp = min(
            e.timestamp_utc for e in events
        ) if events else datetime.now(timezone.utc)
        days_since = (
            datetime.now(timezone.utc) - first_timestamp
        ).days

        return ToolPerformanceMetrics(
            tool_id=tool_id,
            success_rate=success_rate,
            success_count=successes,
            total_count=total,
            avg_latency_ms=int(avg_latency),
            p50_latency_ms=int(p50),
            p95_latency_ms=int(p95),
            p99_latency_ms=int(p99),
            avg_cost_cents=int(avg_cost),
            cost_samples=len(costs),
            confidence=confidence,
            trend=trend,
            days_since_first_sample=days_since,
            last_updated_utc=datetime.now(timezone.utc),
            tenant_id=tenant_id or "unknown",
        )

    async def _query_tool_events(
        self,
        tenant_id: str,
        days: int = 7,
        limit: int = 100000,
    ) -> List[LearningEvent]:
        """Query TOOL_EXECUTED events from EventStore.

        Args:
            tenant_id: Tenant to query (required)
            days: Time window
            limit: Max events to return

        Returns:
            List of LearningEvent objects
        """
        # Query EventStore by type and time window
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)

        # Use EventStore's async read_events with filtering
        events = await self.event_store.read_events(
            tenant_id=tenant_id,
            event_type=LearningEventType.TOOL_EXECUTED,
            since=cutoff_time,
            limit=limit,
        )

        return events

    async def get_tool_metrics(
        self, tool_id: str, tenant_id: str
    ) -> Optional[ToolPerformanceMetrics]:
        """Get cached metrics for a tool.

        Args:
            tool_id: Tool ID
            tenant_id: Tenant ID

        Returns:
            ToolPerformanceMetrics if available, None otherwise
        """
        cache_key = f"tool:{tenant_id}:{tool_id}"
        metrics = await self.cache.get(cache_key)
        if metrics is None:
            # Not in cache; compute on-demand
            events = await self._query_tool_events(tenant_id=tenant_id)
            tool_events = [
                e for e in events
                if e.payload.get("tool_id") == tool_id
            ]
            if tool_events:
                metrics = self._compute_tool_metrics(
                    tool_id=tool_id,
                    events=tool_events,
                    tenant_id=tenant_id,
                )
                if metrics:
                    await self.cache.set(cache_key, metrics)
        return metrics

    async def list_tools_by_performance(
        self,
        tenant_id: str,
        limit: int = 10,
        sort_by: str = "success_rate",
    ) -> List[ToolPerformanceMetrics]:
        """List tools sorted by performance metric.

        Args:
            tenant_id: Tenant ID (required)
            limit: Max results
            sort_by: "success_rate" | "confidence" | "avg_latency_ms"

        Returns:
            List of ToolPerformanceMetrics sorted by metric (descending)
        """
        # Query all metrics
        all_metrics = await self._aggregate_tool_metrics(tenant_id=tenant_id, days=7)

        # Sort by metric
        if sort_by == "success_rate":
            sorted_metrics = sorted(
                all_metrics.values(),
                key=lambda m: m.success_rate,
                reverse=True,
            )
        elif sort_by == "confidence":
            sorted_metrics = sorted(
                all_metrics.values(),
                key=lambda m: m.confidence,
                reverse=True,
            )
        elif sort_by == "avg_latency_ms":
            sorted_metrics = sorted(
                all_metrics.values(),
                key=lambda m: m.avg_latency_ms,
            )
        else:
            sorted_metrics = list(all_metrics.values())

        return sorted_metrics[:limit]

    async def _emit_aggregation_events(
        self,
        tool_metrics: Dict[str, ToolPerformanceMetrics],
        skill_metrics: Dict[str, SkillPerformanceMetrics],
    ) -> None:
        """Emit observability events for aggregated metrics.

        Args:
            tool_metrics: Dict of tool metrics
            skill_metrics: Dict of skill metrics
        """
        if not self.event_emitter:
            return

        for tool_id, metrics in tool_metrics.items():
            try:
                event = LearningEvent(
                    event_type=LearningEventType.METRIC_AGGREGATED,
                    tenant_id=metrics.tenant_id,
                    instance_id="aggregator",
                    skill_name=None,
                    session_id="batch-job",
                    timestamp_utc=datetime.now(timezone.utc),
                    payload={
                        "metric_type": "tool_performance",
                        "tool_id": tool_id,
                        **metrics.to_event_payload(),
                    },
                )
                await self.event_emitter.emit(event)
            except Exception as e:
                logger.warning(f"Failed to emit aggregation event for {tool_id}: {e}")
