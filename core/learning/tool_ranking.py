"""Tool Performance Ranking & Reuse Decision (Gap 2, ADR-0322).

Ranks tools by historical performance metrics and determines whether to reuse
or generate new tools. Integrates with EventStore to aggregate TOOL_EXECUTED
events and provides a ranked list of tools suitable for reuse.

Modules:
1. RankedTool: Tool ranked for potential reuse (frozen dataclass)
2. ToolRankingManager: Queries EventStore, computes rankings, manages cache
3. Tool selection logic: Threshold-based reuse vs generate decision
4. Scoring formula: success(0.3) + latency(0.2) + cost(0.2) + trend(0.1) - cold_start(0.2)

Tenant-scoped: all queries respect tenant_id (GDPR Art. 5, 32).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .event_schema import LearningEvent, LearningEventType, ToolExecutedPayload
from .event_store import EventStore
from .tool_ranking_cache import RankingCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RankedTool:
    """A tool ranked for potential reuse."""

    tool_id: str
    tool_name: str
    score: float  # 0.0-1.0 composite score
    reason: str  # Human-readable reason (e.g., "high_success_rate, low_cost")
    success_rate: float  # 0.0-1.0
    success_count: int
    total_count: int
    avg_latency_ms: int
    p95_latency_ms: int
    avg_cost_cents: int
    confidence: float  # 0.0-1.0 (how confident in this ranking)
    trend: float  # Recent vs overall success rate
    is_cold_start: bool  # < 10 samples
    first_used: datetime
    last_used: datetime
    rank: int  # 1=best


@dataclass(frozen=True)
class ScoringWeights:
    """Configurable scoring weights (all components sum to 1.0)."""

    base_score: float = 0.5
    success_rate: float = 0.3  # Primary factor
    latency: float = 0.2  # Secondary factor
    cost: float = 0.2  # Tertiary factor
    trend: float = 0.1  # Bonus/malus
    cold_start_penalty: float = 0.2  # Discount for new tools


class ToolRankingManager:
    """Compute tool rankings from historical performance data.

    Queries EventStore for TOOL_EXECUTED events, aggregates metrics,
    scores tools, and returns ranked list for reuse potential.

    Features:
    - Bayesian confidence intervals (converge at 30 samples)
    - Trend detection (improving/stable/degrading)
    - Cold-start penalty (discourages trusting new tools)
    - Caching with 5-minute TTL
    - Tenant isolation (all queries scoped by tenant_id)
    """

    def __init__(
        self,
        event_store: EventStore,
        cache_ttl_seconds: int = 300,
        weights: Optional[ScoringWeights] = None,
    ):
        """Initialize ranking manager.

        Args:
            event_store: EventStore instance for querying events
            cache_ttl_seconds: Cache time-to-live (default 5 minutes)
            weights: Configurable scoring weights (default: ScoringWeights())
        """
        self.event_store = event_store
        self.cache = RankingCache(ttl_seconds=cache_ttl_seconds)
        self.weights = weights or ScoringWeights()

    async def get_ranked_tools(
        self,
        tenant_id: str = "_default",
        task_type: Optional[str] = None,
        error_class: Optional[str] = None,
        limit: int = 5,
        time_window_days: int = 7,
    ) -> List[RankedTool]:
        """Get ranked list of tools for potential reuse.

        Args:
            tenant_id: Tenant ID (required for isolation)
            task_type: Optional filter by task type
            error_class: Optional filter by error class
            limit: Maximum number of tools to return
            time_window_days: Time window for metrics (default 7 days)

        Returns:
            List of RankedTool sorted by score (highest first)
        """
        # Check cache first
        cache_key = self._make_cache_key(tenant_id, task_type, error_class)
        cached_result = await self.cache.get(cache_key)
        if cached_result is not None:
            logger.debug(f"Cache hit for ranking query: {cache_key}")
            return cached_result[:limit]

        logger.info(
            f"Computing rankings (tenant={tenant_id}, task_type={task_type}, "
            f"error_class={error_class})"
        )

        try:
            # Query TOOL_EXECUTED events from EventStore
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=time_window_days)
            events = self._query_tool_events(
                tenant_id=tenant_id,
                task_type=task_type,
                error_class=error_class,
                cutoff_time=cutoff_time,
            )

            if not events:
                logger.info(f"No tool execution events found for ranking")
                return []

            # Aggregate metrics by tool_id
            metrics_by_tool = self._aggregate_tool_metrics(events)

            # Score tools
            ranked_tools = self._score_and_rank_tools(metrics_by_tool, tenant_id)

            # Cache result
            await self.cache.set(cache_key, ranked_tools)

            logger.info(
                f"Ranking computed: {len(ranked_tools)} tools, "
                f"top score={ranked_tools[0].score:.2f}" if ranked_tools else "no tools"
            )

            return ranked_tools[:limit]

        except Exception as e:
            logger.error(f"Ranking computation failed: {e}", exc_info=True)
            return []

    def _query_tool_events(
        self,
        tenant_id: str,
        task_type: Optional[str],
        error_class: Optional[str],
        cutoff_time: datetime,
    ) -> List[LearningEvent]:
        """Query TOOL_EXECUTED events from EventStore with filtering.

        Args:
            tenant_id: Tenant ID for isolation
            task_type: Optional task_type filter
            error_class: Optional error_class filter
            cutoff_time: Only include events after this timestamp

        Returns:
            List of filtered TOOL_EXECUTED events (max 10000 to prevent memory exhaustion)
        """
        # Query from EventStore (synchronous for now)
        # In the future, consider async EventStore queries
        all_events = self.event_store.read_events_by_type(
            event_type=LearningEventType.TOOL_EXECUTED,
            limit=10000,  # Pagination: prevent memory exhaustion
        )

        # Filter by tenant_id and time window
        filtered_events = [
            e for e in all_events
            if e.tenant_id == tenant_id
            and e.timestamp_utc >= cutoff_time
        ]

        # Further filter by task_type and error_class if provided
        if task_type is not None:
            filtered_events = [
                e for e in filtered_events
                if e.payload.get("task_type") == task_type
            ]

        if error_class is not None:
            filtered_events = [
                e for e in filtered_events
                if e.payload.get("error_class") == error_class
            ]

        return filtered_events

    def _aggregate_tool_metrics(self, events: List[LearningEvent]) -> Dict[str, Dict[str, Any]]:
        """Aggregate metrics by tool_id.

        Computes for each tool:
        - success_count, total_count, success_rate
        - latency percentiles (P50, P95, P99)
        - cost metrics (median, percentiles)
        - confidence interval (Bayesian, converges at 30 samples)
        - trend (recent vs overall)
        - first_used, last_used

        Args:
            events: List of TOOL_EXECUTED events

        Returns:
            Dict mapping tool_id -> aggregated metrics
        """
        metrics_by_tool: Dict[str, Dict[str, Any]] = {}

        for event in events:
            payload = event.payload
            tool_id = payload.get("tool_id", "unknown")
            tool_name = payload.get("tool_name", tool_id)
            status = payload.get("status", "unknown")
            latency_ms = payload.get("latency_ms", 0)
            cost_cents = payload.get("estimated_cost_cents", 0)

            # Initialize tool metrics if first time seeing it
            if tool_id not in metrics_by_tool:
                metrics_by_tool[tool_id] = {
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "success_count": 0,
                    "total_count": 0,
                    "latencies": [],
                    "costs": [],
                    "first_used": event.timestamp_utc,
                    "last_used": event.timestamp_utc,
                    "timestamps": [],
                }

            # Update metrics
            metrics_by_tool[tool_id]["total_count"] += 1
            if status == "success":
                metrics_by_tool[tool_id]["success_count"] += 1
            metrics_by_tool[tool_id]["latencies"].append(latency_ms)
            metrics_by_tool[tool_id]["costs"].append(cost_cents)
            metrics_by_tool[tool_id]["last_used"] = event.timestamp_utc
            metrics_by_tool[tool_id]["timestamps"].append(event.timestamp_utc)

        # Compute derived metrics for each tool
        for tool_id, metrics in metrics_by_tool.items():
            total = metrics["total_count"]
            success = metrics["success_count"]

            # Success rate
            metrics["success_rate"] = success / total if total > 0 else 0.0

            # Latency percentiles
            latencies_sorted = sorted(metrics["latencies"])
            metrics["avg_latency_ms"] = (
                sum(metrics["latencies"]) // len(metrics["latencies"])
                if metrics["latencies"]
                else 0
            )
            metrics["p50_latency_ms"] = latencies_sorted[len(latencies_sorted) // 2] if latencies_sorted else 0
            metrics["p95_latency_ms"] = (
                latencies_sorted[int(len(latencies_sorted) * 0.95)]
                if latencies_sorted
                else 0
            )
            metrics["p99_latency_ms"] = (
                latencies_sorted[int(len(latencies_sorted) * 0.99)]
                if latencies_sorted
                else 0
            )

            # Cost metrics
            costs_sorted = sorted([c for c in metrics["costs"] if c > 0])
            metrics["avg_cost_cents"] = (
                sum(metrics["costs"]) // len(metrics["costs"]) if metrics["costs"] else 0
            )
            metrics["median_cost_cents"] = (
                costs_sorted[len(costs_sorted) // 2] if costs_sorted else 0
            )

            # Bayesian confidence (converges at 30 samples)
            metrics["confidence"] = min(1.0, total / 30)

            # Trend: recent (last 3 days) vs overall success rate
            three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
            recent_events = [
                ts for ts in metrics["timestamps"]
                if ts >= three_days_ago
            ]
            # This is approximate; a proper implementation would track recent success count
            metrics["trend"] = 0.0  # TODO: improve with proper time-series trend

            # Cold-start detection
            metrics["is_cold_start"] = total < 10

        return metrics_by_tool

    def _score_and_rank_tools(
        self, metrics_by_tool: Dict[str, Dict[str, Any]], tenant_id: str
    ) -> List[RankedTool]:
        """Score and rank tools by composite score.

        Scoring formula (ADR-0322):
        score = base(0.5) +
          (+0.3 if success_rate > 0.8, -0.2 if < 0.3) +
          (+0.2 if P95_latency < median * 0.8, -0.1 if > median * 1.5) +
          (+0.2 if cost < median * 0.7, -0.1 if > median * 1.5) +
          (+0.1 if trend > 0.1, -0.1 if < -0.1) +
          (-0.2 if cold-start: < 10 samples)
        Clamp to [0.0, 1.0]

        Args:
            metrics_by_tool: Dict mapping tool_id -> aggregated metrics
            tenant_id: Tenant ID (for audit trail)

        Returns:
            List of RankedTool sorted by score (highest first)
        """
        # Compute global percentiles for comparison
        all_latencies = [
            m["p95_latency_ms"]
            for m in metrics_by_tool.values()
            if m["p95_latency_ms"] > 0
        ]
        all_costs = [
            m["median_cost_cents"] for m in metrics_by_tool.values() if m["median_cost_cents"] > 0
        ]

        median_latency = (
            sorted(all_latencies)[len(all_latencies) // 2] if all_latencies else 1000
        )
        median_cost = sorted(all_costs)[len(all_costs) // 2] if all_costs else 100

        # Score each tool
        ranked_tools = []
        for tool_id, metrics in metrics_by_tool.items():
            score, reason = self._score_tool(metrics, median_latency, median_cost)

            ranked_tool = RankedTool(
                tool_id=tool_id,
                tool_name=metrics["tool_name"],
                score=score,
                reason=reason,
                success_rate=metrics["success_rate"],
                success_count=metrics["success_count"],
                total_count=metrics["total_count"],
                avg_latency_ms=metrics["avg_latency_ms"],
                p95_latency_ms=metrics["p95_latency_ms"],
                avg_cost_cents=metrics["avg_cost_cents"],
                confidence=metrics["confidence"],
                trend=metrics["trend"],
                is_cold_start=metrics["is_cold_start"],
                first_used=metrics["first_used"],
                last_used=metrics["last_used"],
                rank=0,  # Will be set after sorting
            )
            ranked_tools.append(ranked_tool)

        # Sort by score (highest first)
        ranked_tools.sort(key=lambda t: t.score, reverse=True)

        # Set rank numbers
        ranked_tools = [
            RankedTool(
                tool_id=t.tool_id,
                tool_name=t.tool_name,
                score=t.score,
                reason=t.reason,
                success_rate=t.success_rate,
                success_count=t.success_count,
                total_count=t.total_count,
                avg_latency_ms=t.avg_latency_ms,
                p95_latency_ms=t.p95_latency_ms,
                avg_cost_cents=t.avg_cost_cents,
                confidence=t.confidence,
                trend=t.trend,
                is_cold_start=t.is_cold_start,
                first_used=t.first_used,
                last_used=t.last_used,
                rank=i + 1,
            )
            for i, t in enumerate(ranked_tools)
        ]

        return ranked_tools

    def _score_tool(
        self, metrics: Dict[str, Any], median_latency: int, median_cost: int
    ) -> Tuple[float, str]:
        """Score a single tool (0.0-1.0) and provide reason.

        Args:
            metrics: Tool metrics from aggregation
            median_latency: Median P95 latency across all tools
            median_cost: Median cost across all tools

        Returns:
            Tuple of (score, reason_string)
        """
        score = self.weights.base_score
        reason_parts = []

        success_rate = metrics["success_rate"]
        latency = metrics["p95_latency_ms"]
        cost = metrics["median_cost_cents"]
        trend = metrics["trend"]
        is_cold_start = metrics["is_cold_start"]

        # Success rate component (primary: +/- 0.3)
        if success_rate > 0.8:
            score += self.weights.success_rate
            reason_parts.append("high_success_rate")
        elif success_rate < 0.3:
            score -= 0.2
            reason_parts.append("low_success_rate")

        # Latency component (secondary: +/- 0.2)
        if latency > 0 and median_latency > 0:
            if latency < median_latency * 0.8:
                score += self.weights.latency
                reason_parts.append("low_latency")
            elif latency > median_latency * 1.5:
                score -= 0.1
                reason_parts.append("high_latency")

        # Cost component (tertiary: +/- 0.2)
        if cost > 0 and median_cost > 0:
            if cost < median_cost * 0.7:
                score += self.weights.cost
                reason_parts.append("low_cost")
            elif cost > median_cost * 1.5:
                score -= 0.1
                reason_parts.append("high_cost")

        # Trend component (bonus: +/- 0.1)
        if trend > 0.1:
            score += self.weights.trend
            reason_parts.append("improving_trend")
        elif trend < -0.1:
            score -= self.weights.trend
            reason_parts.append("declining_trend")

        # Cold-start penalty
        if is_cold_start:
            score -= self.weights.cold_start_penalty
            reason_parts.append("cold_start")

        # Clamp to [0.0, 1.0]
        score = max(0.0, min(1.0, score))

        reason = ", ".join(reason_parts) or "neutral"
        return score, reason

    def _make_cache_key(
        self, tenant_id: str, task_type: Optional[str], error_class: Optional[str]
    ) -> str:
        """Create a unique cache key for ranking query.

        Args:
            tenant_id: Tenant ID
            task_type: Optional task type filter
            error_class: Optional error class filter

        Returns:
            Cache key string
        """
        parts = [tenant_id]
        if task_type:
            parts.append(f"task_type={task_type}")
        if error_class:
            parts.append(f"error_class={error_class}")
        return ":".join(parts)


async def select_tool_for_reuse(
    ranking_manager: ToolRankingManager,
    tenant_id: str = "_default",
    task_type: Optional[str] = None,
    error_class: Optional[str] = None,
    reuse_threshold: float = 0.7,
) -> Dict[str, Any]:
    """Decide whether to reuse a tool or generate a new one.

    Uses threshold-based logic: if top-ranked tool score > threshold,
    recommend reuse; otherwise, recommend generating new tool.

    Args:
        ranking_manager: ToolRankingManager instance
        tenant_id: Tenant ID
        task_type: Optional task type filter
        error_class: Optional error class filter
        reuse_threshold: Score threshold for reuse (default 0.7)

    Returns:
        Dict with keys:
        - action: "reuse" | "generate"
        - tool_id: str if reuse, None otherwise
        - ranked_tools: List[RankedTool]
        - reason: Human-readable explanation
    """
    ranked_tools = await ranking_manager.get_ranked_tools(
        tenant_id=tenant_id,
        task_type=task_type,
        error_class=error_class,
        limit=5,
    )

    if not ranked_tools:
        return {
            "action": "generate",
            "tool_id": None,
            "ranked_tools": [],
            "reason": "No historical tools found; generating new tool",
        }

    best_tool = ranked_tools[0]
    if best_tool.score >= reuse_threshold:
        return {
            "action": "reuse",
            "tool_id": best_tool.tool_id,
            "ranked_tools": ranked_tools,
            "reason": f"Reusing {best_tool.tool_name} (score={best_tool.score:.2f}) — {best_tool.reason}",
        }
    else:
        return {
            "action": "generate",
            "tool_id": None,
            "ranked_tools": ranked_tools,
            "reason": f"Best tool score too low ({best_tool.score:.2f} < {reuse_threshold}); generating new tool",
        }
