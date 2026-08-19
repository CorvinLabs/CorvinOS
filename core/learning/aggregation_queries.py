"""Aggregation Queries — optimized EventStore queries for performance metrics.

Provides specialized query functions for:
1. Tool events by time window (with pagination)
2. Skill events by time window
3. Distinct tool/skill IDs
4. Operator ratings (Gap 7 integration)

All queries are tenant-scoped (GDPR Art. 5, 32).

ADR-0324 (Performance Aggregation Pipeline)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .event_schema import LearningEvent, LearningEventType
from .event_persistence import EventStore as AsyncEventStore

logger = logging.getLogger(__name__)


async def query_tool_events_by_window(
    event_store: AsyncEventStore,
    tool_id: str,
    tenant_id: str,
    days: int = 7,
    limit: int = 10000,
    offset: int = 0,
) -> Tuple[List[LearningEvent], int]:
    """Query TOOL_EXECUTED events for a tool in time window (with pagination).

    Args:
        event_store: AsyncEventStore instance
        tool_id: Tool ID to query
        tenant_id: Tenant ID (required)
        days: Time window (default 7 days)
        limit: Max results per page
        offset: Pagination offset

    Returns:
        Tuple of (events, total_count)
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)

    # Query EventStore by type with tenant isolation
    all_events = await event_store.read_events(
        tenant_id=tenant_id,
        event_type=LearningEventType.TOOL_EXECUTED,
        since=cutoff_time,
        limit=limit + offset,
    )

    # Filter by tool_id
    filtered = [
        e for e in all_events
        if e.payload.get("tool_id") == tool_id
    ]

    # Pagination
    total = len(filtered)
    paged = filtered[offset : offset + limit]

    return paged, total


async def query_skill_events_by_window(
    event_store: AsyncEventStore,
    skill_name: str,
    tenant_id: str,
    days: int = 7,
    limit: int = 10000,
    offset: int = 0,
) -> Tuple[List[LearningEvent], int]:
    """Query SKILL_GRADED events for a skill in time window.

    Args:
        event_store: AsyncEventStore instance
        skill_name: Skill name to query
        tenant_id: Tenant ID (required)
        days: Time window
        limit: Max results per page
        offset: Pagination offset

    Returns:
        Tuple of (events, total_count)
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)

    # Query EventStore (when SKILL_GRADED events available)
    all_events = await event_store.read_events(
        tenant_id=tenant_id,
        skill_name=skill_name,
        since=cutoff_time,
        limit=limit + offset,
    )

    # Pagination
    total = len(all_events)
    paged = all_events[offset : offset + limit]

    return paged, total


async def get_distinct_tool_ids(
    event_store: AsyncEventStore,
    tenant_id: str,
    days: int = 7,
) -> List[str]:
    """Get unique tool IDs from TOOL_EXECUTED events.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        days: Time window

    Returns:
        List of distinct tool IDs
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)

    # Query all events
    all_events = await event_store.read_events(
        tenant_id=tenant_id,
        event_type=LearningEventType.TOOL_EXECUTED,
        since=cutoff_time,
        limit=100000,
    )

    # Extract unique tool_ids
    tool_ids = set()
    for event in all_events:
        tool_id = event.payload.get("tool_id")
        if tool_id:
            tool_ids.add(tool_id)

    return sorted(list(tool_ids))


async def get_distinct_skill_names(
    event_store: AsyncEventStore,
    tenant_id: str,
    days: int = 7,
) -> List[str]:
    """Get unique skill names from SKILL_GRADED events.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        days: Time window

    Returns:
        List of distinct skill names
    """
    # Since SKILL_GRADED events aren't yet defined, return empty
    return []


async def query_operator_rated_tools(
    event_store: AsyncEventStore,
    tenant_id: str,
    tool_id: Optional[str] = None,
    days: int = 7,
    limit: int = 10000,
) -> List[LearningEvent]:
    """Query OPERATOR_RATED_TOOL events (Gap 7 integration).

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        tool_id: Filter by specific tool (optional)
        days: Time window
        limit: Max results

    Returns:
        List of LearningEvent objects (operator ratings)
    """
    cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)

    # Query EventStore
    all_events = await event_store.read_events(
        tenant_id=tenant_id,
        event_type=LearningEventType.OPERATOR_RATED_TOOL,
        since=cutoff_time,
        limit=limit,
    )

    # Filter by tool_id if specified
    if tool_id:
        all_events = [
            e for e in all_events
            if e.payload.get("tool_id") == tool_id
        ]

    return all_events


async def aggregate_metrics_for_all_tools(
    event_store: AsyncEventStore,
    tenant_id: str,
    days: int = 7,
) -> Dict[str, List[LearningEvent]]:
    """Aggregate tool events grouped by tool_id.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        days: Time window

    Returns:
        Dict[tool_id -> List[LearningEvent]]
    """
    # Get distinct tool IDs
    tool_ids = await get_distinct_tool_ids(
        event_store=event_store,
        tenant_id=tenant_id,
        days=days,
    )

    # Query events for each tool
    metrics_by_tool = {}
    for tool_id in tool_ids:
        events, _ = await query_tool_events_by_window(
            event_store=event_store,
            tool_id=tool_id,
            tenant_id=tenant_id,
            days=days,
            limit=100000,
        )
        if events:
            metrics_by_tool[tool_id] = events

    return metrics_by_tool


async def get_tool_event_count(
    event_store: AsyncEventStore,
    tenant_id: str,
    tool_id: str,
    days: int = 7,
) -> int:
    """Get count of TOOL_EXECUTED events for a tool.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        tool_id: Tool ID
        days: Time window

    Returns:
        Event count
    """
    _, total = await query_tool_events_by_window(
        event_store=event_store,
        tool_id=tool_id,
        tenant_id=tenant_id,
        days=days,
        limit=1,
    )
    return total


async def get_operator_rating_count(
    event_store: AsyncEventStore,
    tenant_id: str,
    tool_id: str,
    days: int = 7,
) -> int:
    """Get count of operator ratings for a tool.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        tool_id: Tool ID
        days: Time window

    Returns:
        Rating count
    """
    ratings = await query_operator_rated_tools(
        event_store=event_store,
        tenant_id=tenant_id,
        tool_id=tool_id,
        days=days,
        limit=100000,
    )
    return len(ratings)


async def get_average_operator_rating(
    event_store: AsyncEventStore,
    tenant_id: str,
    tool_id: str,
    days: int = 7,
) -> Optional[float]:
    """Get average operator rating for a tool.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        tool_id: Tool ID
        days: Time window

    Returns:
        Average rating (1-5) or None if no ratings
    """
    ratings = await query_operator_rated_tools(
        event_store=event_store,
        tenant_id=tenant_id,
        tool_id=tool_id,
        days=days,
        limit=100000,
    )

    if not ratings:
        return None

    rating_values = [
        e.payload.get("rating") for e in ratings
        if e.payload.get("rating") is not None
    ]

    if not rating_values:
        return None

    return sum(rating_values) / len(rating_values)


async def get_tools_with_low_confidence(
    event_store: AsyncEventStore,
    tenant_id: str,
    confidence_threshold: float = 0.5,
    days: int = 7,
) -> List[Tuple[str, float]]:
    """Get tools with confidence below threshold (cold-start detection).

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        confidence_threshold: Minimum confidence (0.0-1.0)
        days: Time window

    Returns:
        List of (tool_id, confidence) tuples sorted by confidence ascending
    """
    # Get all tool metrics
    metrics = await aggregate_metrics_for_all_tools(
        event_store=event_store,
        tenant_id=tenant_id,
        days=days,
    )

    # Compute confidence for each tool
    low_confidence = []
    for tool_id, events in metrics.items():
        total = len(events)
        confidence = min(1.0, total / 30)  # Matches PerformanceAggregator formula

        if confidence < confidence_threshold:
            low_confidence.append((tool_id, confidence))

    # Sort by confidence ascending
    low_confidence.sort(key=lambda x: x[1])

    return low_confidence


async def get_tools_by_trend(
    event_store: AsyncEventStore,
    tenant_id: str,
    trend: str = "improving",
    days: int = 7,
) -> List[str]:
    """Get tools with a specific trend.

    Args:
        event_store: AsyncEventStore instance
        tenant_id: Tenant ID (required)
        trend: "improving" | "stable" | "degrading"
        days: Time window

    Returns:
        List of tool IDs with the specified trend
    """
    # Get all tool metrics
    metrics = await aggregate_metrics_for_all_tools(
        event_store=event_store,
        tenant_id=tenant_id,
        days=days,
    )

    # Compute trend for each tool
    trending_tools = []
    for tool_id, events in metrics.items():
        payloads = [e.payload for e in events]

        # Overall success rate
        successes = sum(1 for p in payloads if p.get("status") == "success")
        total = len(payloads)
        success_rate = successes / total if total > 0 else 0.0

        # Recent success rate (last 10%)
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

        # Detect trend
        trend_delta = recent_success_rate - success_rate
        if trend_delta > 0.05:
            detected_trend = "improving"
        elif trend_delta < -0.05:
            detected_trend = "degrading"
        else:
            detected_trend = "stable"

        if detected_trend == trend:
            trending_tools.append(tool_id)

    return trending_tools
