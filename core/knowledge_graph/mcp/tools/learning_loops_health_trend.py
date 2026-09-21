"""MCP Tool: learning_loops.get_health_trend() — Get health trend for a loop.

Returns 7-day (or custom) health trend for a learning loop, including:
- Timestamps of events
- Health scores over time
- Event counts per day
- Optimization deltas

Tool signature:
  learning_loops.get_health_trend(
      loop_id: str,
      tenant_id: str,
      days: int = 7
  ) -> HealthTrend

Performance:
- Queries audit chain for signals, <200ms typical
- Returns empty gracefully for >30 days

ADR-0907: KG MCP Learning-Loop Tools (Stream 2.3)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, List

from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService


class HealthTrend:
    """Health trend data for a learning loop."""

    def __init__(
        self,
        loop_id: str,
        plugin_id: str,
        period_start: datetime,
        period_end: datetime,
        timestamps: List[datetime],
        health_scores: List[float],
        event_counts: dict,
        current_status: str,
        current_health_score: float,
        optimization_deltas: Optional[dict] = None,
    ):
        self.loop_id = loop_id
        self.plugin_id = plugin_id
        self.period_start = period_start
        self.period_end = period_end
        self.timestamps = timestamps
        self.health_scores = health_scores
        self.event_counts = event_counts
        self.current_status = current_status
        self.current_health_score = current_health_score
        self.optimization_deltas = optimization_deltas or {}

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return {
            "loop_id": self.loop_id,
            "plugin_id": self.plugin_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "timestamps": [ts.isoformat() for ts in self.timestamps],
            "health_scores": self.health_scores,
            "event_counts": self.event_counts,
            "current_status": self.current_status,
            "current_health_score": self.current_health_score,
            "optimization_deltas": self.optimization_deltas,
        }


def get_health_trend(
    service: LearningLoopService,
    tenant_id: str,
    plugin_id: str,
    loop_id: str,
    days: int = 7,
) -> dict:
    """Get health trend for a loop over N days.

    Args:
        service: LearningLoopService instance
        tenant_id: Tenant ID (for audit/isolation)
        plugin_id: Plugin ID
        loop_id: Loop ID
        days: Number of days to look back (default 7, max 30)

    Returns:
        Dict with health trend data or error message
    """
    from core.tenants.validation import validate_tenant_id
    import logging

    logger = logging.getLogger(__name__)

    try:
        validate_tenant_id(tenant_id)
    except ValueError as exc:
        logger.error(f"Invalid tenant_id: {exc}")
        return {"error": "Invalid tenant"}

    # Validate service tenant
    if service.tenant_id != tenant_id:
        logger.error(f"Service tenant mismatch: {service.tenant_id} != {tenant_id}")
        return {"error": "Tenant mismatch"}

    # Validate days parameter
    if days < 1 or days > 30:
        days = min(30, max(1, days))

    # Get loop entry
    try:
        trend = service.get_health_trend(plugin_id, loop_id, days=days)
    except Exception as exc:
        logger.error(f"Failed to get health trend: {exc}")
        return {"error": str(exc)}

    if trend is None:
        return {"error": f"Loop not found: {plugin_id}:{loop_id}"}

    # Parse timestamps and health scores
    timestamps = []
    health_scores = []
    event_counts = {}

    try:
        if "timestamps" in trend:
            timestamps = [
                datetime.fromisoformat(ts) if isinstance(ts, str) else ts
                for ts in trend.get("timestamps", [])
            ]
        if "health_scores" in trend:
            health_scores = trend.get("health_scores", [])
        if "event_counts" in trend:
            event_counts = trend.get("event_counts", {})
    except Exception as exc:
        logger.error(f"Error parsing trend data: {exc}")

    # Build response
    health_obj = HealthTrend(
        loop_id=loop_id,
        plugin_id=plugin_id,
        period_start=datetime.fromisoformat(trend["period_start"]),
        period_end=datetime.fromisoformat(trend["period_end"]),
        timestamps=timestamps,
        health_scores=health_scores,
        event_counts=event_counts,
        current_status=trend.get("current_status", "unknown"),
        current_health_score=trend.get("current_health_score", 0.0),
        optimization_deltas={},
    )

    return health_obj.to_dict()
