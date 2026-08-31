"""Performance Aggregation Pipeline — batch metrics computation and caching (ADR-0324).

Background scheduler that periodically aggregates tool/skill metrics from event stream.
Provides efficient queries via caching; only recomputes when stale.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from core.learning.confidence_intervals import ConfidenceIntervalCalculator, ConfidenceInterval
from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.event_store import EventStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPerformanceMetrics:
    """Aggregated performance metrics for a tool."""

    tool_id: str
    tool_name: str
    success_count: int
    failure_count: int
    confidence_lower: float
    confidence_mean: float
    confidence_upper: float
    confidence_samples: int
    median_latency_ms: int
    p95_latency_ms: int
    p99_latency_ms: int
    median_cost_cents: int
    time_window_days: int = 7
    computed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def success_rate(self) -> float:
        """Success rate (0.0 to 1.0)."""
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    @property
    def failure_rate(self) -> float:
        """Failure rate (0.0 to 1.0)."""
        return 1.0 - self.success_rate


@dataclass(frozen=True)
class SkillPerformanceMetrics:
    """Aggregated performance metrics for a skill."""

    skill_id: str
    skill_name: str
    usage_count: int
    success_count: int
    failure_count: int
    partial_count: int
    confidence_lower: float
    confidence_mean: float
    confidence_upper: float
    confidence_samples: int
    avg_outcome_rating: float  # 1-5, -1 if no ratings
    median_latency_ms: int
    median_cost_cents: int
    time_window_days: int = 7
    computed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def success_rate(self) -> float:
        """Success rate (0.0 to 1.0)."""
        total = self.success_count + self.failure_count + self.partial_count
        return self.success_count / total if total > 0 else 0.0


class PerformanceAggregator:
    """Aggregate tool/skill metrics from learning event stream.

    **Design:**
    - Queries EventStore in batches (O(n) but in background)
    - Computes confidence intervals via Bayesian Beta-Binomial
    - Caches results with TTL (1 hour default)
    - Notifies subsystems of updates via event emission

    **Granularity:**
    - Per tool (by tool_id)
    - Per skill (by skill_id)
    - Per (tool_id, task_type)
    - Per (skill_id, task_type)

    **Temporal windows:**
    - 7-day (default)
    - 30-day (trending)
    - all-time (comparison)
    """

    def __init__(self, event_store: EventStore, cache_ttl_minutes: int = 60):
        """Initialize aggregator.

        **Parameters:**
        - event_store: EventStore instance for reading events
        - cache_ttl_minutes: Cache validity (default 60 minutes)
        """
        self.event_store = event_store
        self.cache: dict[str, tuple[datetime, Any]] = {}
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.last_aggregation: dict[str, datetime] = {}

    def _cache_key(
        self,
        entity_type: str,  # "tool" | "skill"
        entity_id: Optional[str],
        task_type: Optional[str],
        time_window_days: int,
        tenant_id: str,
    ) -> str:
        """Generate cache key."""
        parts = [entity_type, entity_id or "*", task_type or "*", f"{time_window_days}d", tenant_id]
        return ":".join(parts)

    def _is_cache_valid(self, key: str) -> bool:
        """Check if cache entry is still valid."""
        if key not in self.cache:
            return False

        timestamp, _ = self.cache[key]
        return datetime.utcnow() - timestamp < self.cache_ttl

    def _percentile(self, data: list[int], p: int) -> int:
        """Compute percentile of data."""
        if not data:
            return 0

        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        return sorted_data[min(idx, len(sorted_data) - 1)]

    def _match_time_window(self, event_timestamp: datetime, time_window_days: int) -> bool:
        """Check if event is within time window."""
        cutoff = datetime.utcnow() - timedelta(days=time_window_days)
        return event_timestamp >= cutoff

    async def aggregate_tool_metrics(
        self,
        tool_id: Optional[str] = None,
        task_type: Optional[str] = None,
        time_window_days: int = 7,
        tenant_id: str = "_default",
        use_cache: bool = True,
    ) -> dict[str, ToolPerformanceMetrics]:
        """Aggregate metrics for tools.

        **Parameters:**
        - tool_id: Filter to specific tool (None = all)
        - task_type: Filter by task type (None = all)
        - time_window_days: Time window for aggregation (7, 30, or 999 for all-time)
        - tenant_id: Tenant for isolation
        - use_cache: Use cached results if valid

        **Returns:**
        - Dict of {tool_id: ToolPerformanceMetrics}
        """
        cache_key = self._cache_key("tool", tool_id, task_type, time_window_days, tenant_id)

        # Check cache
        if use_cache and self._is_cache_valid(cache_key):
            _, cached_result = self.cache[cache_key]
            logger.debug(f"Cache hit for {cache_key}")
            return cached_result

        logger.info(f"Aggregating tool metrics: window={time_window_days}d, tool={tool_id}, task={task_type}")

        # Query events
        events = self.event_store.read_events_by_type(
            event_type=LearningEventType.TOOL_EXECUTED,
            limit=100000,
        )

        # Filter by tenant, tool, task_type, time window
        filtered_events = [
            e for e in events
            if e.tenant_id == tenant_id
            and (not tool_id or e.payload.get("tool_id") == tool_id)
            and (not task_type or e.payload.get("task_type") == task_type)
            and self._match_time_window(e.timestamp_utc, time_window_days)
        ]

        # Aggregate by tool
        metrics_by_tool: dict[str, dict[str, Any]] = {}

        for event in filtered_events:
            payload = event.payload
            tid = payload.get("tool_id", "unknown")

            if tid not in metrics_by_tool:
                metrics_by_tool[tid] = {
                    "successes": 0,
                    "failures": 0,
                    "latencies": [],
                    "costs": [],
                    "tool_name": payload.get("tool_name", tid),
                }

            is_success = payload.get("status") == "success"
            metrics_by_tool[tid]["successes"] += 1 if is_success else 0
            metrics_by_tool[tid]["failures"] += 0 if is_success else 1
            metrics_by_tool[tid]["latencies"].append(payload.get("latency_ms", 0))
            metrics_by_tool[tid]["costs"].append(payload.get("estimated_cost_cents", 0))

        # Convert to ToolPerformanceMetrics
        results = {}
        for tool_id_key, agg in metrics_by_tool.items():
            ci = ConfidenceIntervalCalculator.compute_interval(
                successes=agg["successes"],
                failures=agg["failures"],
            )

            results[tool_id_key] = ToolPerformanceMetrics(
                tool_id=tool_id_key,
                tool_name=agg["tool_name"],
                success_count=agg["successes"],
                failure_count=agg["failures"],
                confidence_lower=ci.lower,
                confidence_mean=ci.mean,
                confidence_upper=ci.upper,
                confidence_samples=ci.samples,
                median_latency_ms=self._percentile(agg["latencies"], 50),
                p95_latency_ms=self._percentile(agg["latencies"], 95),
                p99_latency_ms=self._percentile(agg["latencies"], 99),
                median_cost_cents=self._percentile(agg["costs"], 50),
                time_window_days=time_window_days,
            )

        # Cache result
        self.cache[cache_key] = (datetime.utcnow(), results)
        self.last_aggregation[cache_key] = datetime.utcnow()

        logger.info(f"Aggregation complete: {len(results)} tools, window={time_window_days}d")
        return results

    async def aggregate_skill_metrics(
        self,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        time_window_days: int = 7,
        tenant_id: str = "_default",
        use_cache: bool = True,
    ) -> dict[str, SkillPerformanceMetrics]:
        """Aggregate metrics for skills.

        Similar to aggregate_tool_metrics but queries SKILL_USED events.
        Computes success rate from outcomes where skill participated.

        **Returns:**
        - Dict of {skill_id: SkillPerformanceMetrics}
        """
        cache_key = self._cache_key("skill", skill_id, task_type, time_window_days, tenant_id)

        # Check cache
        if use_cache and self._is_cache_valid(cache_key):
            _, cached_result = self.cache[cache_key]
            logger.debug(f"Cache hit for {cache_key}")
            return cached_result

        logger.info(f"Aggregating skill metrics: window={time_window_days}d, skill={skill_id}, task={task_type}")

        # Query OPERATOR_RATED_SKILL events
        events = self.event_store.read_events_by_type(
            event_type=LearningEventType.OPERATOR_RATED_SKILL,
            limit=100000,
        )

        # Filter by tenant, skill, time window
        filtered_events = [
            e for e in events
            if e.tenant_id == tenant_id
            and (not skill_id or e.skill_name == skill_id)
            and self._match_time_window(e.timestamp_utc, time_window_days)
        ]

        # Aggregate by skill
        metrics_by_skill: dict[str, dict[str, Any]] = {}

        for event in filtered_events:
            payload = event.payload
            sid = payload.get("skill_id", "unknown")

            if sid not in metrics_by_skill:
                metrics_by_skill[sid] = {
                    "usage_count": 0,
                    "successes": 0,
                    "failures": 0,
                    "partials": 0,
                    "ratings": [],
                    "latencies": [],
                    "costs": [],
                    "skill_name": payload.get("skill_name", sid),
                }

            metrics_by_skill[sid]["usage_count"] += 1

            # Outcome type mapping: 1=failure, 2=partial, 3=success
            rating = payload.get("rating", -1)
            if rating == 3:
                metrics_by_skill[sid]["successes"] += 1
            elif rating == 2:
                metrics_by_skill[sid]["partials"] += 1
            elif rating == 1:
                metrics_by_skill[sid]["failures"] += 1

            if rating > 0:
                metrics_by_skill[sid]["ratings"].append(rating)

            metrics_by_skill[sid]["latencies"].append(payload.get("latency_ms", 0))
            metrics_by_skill[sid]["costs"].append(payload.get("estimated_cost_cents", 0))

        # Convert to SkillPerformanceMetrics
        results = {}
        for skill_id_key, agg in metrics_by_skill.items():
            ci = ConfidenceIntervalCalculator.compute_interval(
                successes=agg["successes"],
                failures=agg["failures"] + agg["partials"],
            )

            avg_rating = (
                sum(agg["ratings"]) / len(agg["ratings"])
                if agg["ratings"]
                else -1
            )

            results[skill_id_key] = SkillPerformanceMetrics(
                skill_id=skill_id_key,
                skill_name=agg["skill_name"],
                usage_count=agg["usage_count"],
                success_count=agg["successes"],
                failure_count=agg["failures"],
                partial_count=agg["partials"],
                confidence_lower=ci.lower,
                confidence_mean=ci.mean,
                confidence_upper=ci.upper,
                confidence_samples=ci.samples,
                avg_outcome_rating=avg_rating,
                median_latency_ms=self._percentile(agg["latencies"], 50),
                median_cost_cents=self._percentile(agg["costs"], 50),
                time_window_days=time_window_days,
            )

        # Cache result
        self.cache[cache_key] = (datetime.utcnow(), results)
        self.last_aggregation[cache_key] = datetime.utcnow()

        logger.info(f"Aggregation complete: {len(results)} skills, window={time_window_days}d")
        return results

    def clear_cache(self, older_than_minutes: Optional[int] = None) -> int:
        """Clear cache entries.

        **Parameters:**
        - older_than_minutes: Clear entries older than N minutes (None = all)

        **Returns:**
        - Count of entries cleared
        """
        if older_than_minutes is None:
            count = len(self.cache)
            self.cache.clear()
            return count

        cutoff = datetime.utcnow() - timedelta(minutes=older_than_minutes)
        to_remove = [k for k, (ts, _) in self.cache.items() if ts < cutoff]

        for k in to_remove:
            del self.cache[k]

        return len(to_remove)

    def get_stats(self) -> dict[str, Any]:
        """Get aggregator statistics."""
        return {
            "cache_entries": len(self.cache),
            "valid_entries": sum(1 for k in self.cache if self._is_cache_valid(k)),
            "last_aggregation_times": {k: v.isoformat() for k, v in self.last_aggregation.items()},
        }


class AggregationScheduler:
    """Background scheduler for periodic aggregation.

    Runs aggregation loop hourly, updates metrics, emits events.
    """

    def __init__(self, aggregator: PerformanceAggregator, interval_minutes: int = 60):
        """Initialize scheduler.

        **Parameters:**
        - aggregator: PerformanceAggregator instance
        - interval_minutes: Aggregation interval (default 60)
        """
        self.aggregator = aggregator
        self.interval = timedelta(minutes=interval_minutes)
        self._running = False

    async def start(self, tenant_id: str = "_default"):
        """Start background aggregation loop.

        **Parameters:**
        - tenant_id: Tenant to aggregate for

        Runs indefinitely until stopped.
        """
        self._running = True
        logger.info(f"AggregationScheduler started for tenant {tenant_id}")

        while self._running:
            try:
                await self.run_aggregation(tenant_id)
            except Exception as e:
                logger.error(f"Aggregation error: {e}", exc_info=True)

            await asyncio.sleep(self.interval.total_seconds())

    async def run_aggregation(self, tenant_id: str = "_default"):
        """Run one round of aggregation.

        Aggregates metrics for both 7-day and 30-day windows.
        """
        logger.info(f"Starting aggregation round for tenant {tenant_id}")

        # Aggregate tool metrics (7-day, 30-day)
        for window in [7, 30]:
            try:
                metrics = await self.aggregator.aggregate_tool_metrics(
                    time_window_days=window,
                    tenant_id=tenant_id,
                    use_cache=False,  # Force recomputation
                )
                logger.info(f"Tool aggregation complete: {len(metrics)} tools, {window}d window")
            except Exception as e:
                logger.error(f"Tool aggregation failed ({window}d): {e}")

        # Aggregate skill metrics (7-day, 30-day)
        for window in [7, 30]:
            try:
                metrics = await self.aggregator.aggregate_skill_metrics(
                    time_window_days=window,
                    tenant_id=tenant_id,
                    use_cache=False,  # Force recomputation
                )
                logger.info(f"Skill aggregation complete: {len(metrics)} skills, {window}d window")
            except Exception as e:
                logger.error(f"Skill aggregation failed ({window}d): {e}")

        logger.info(f"Aggregation round complete for tenant {tenant_id}")

    def stop(self):
        """Stop the scheduler."""
        self._running = False
        logger.info("AggregationScheduler stopped")
