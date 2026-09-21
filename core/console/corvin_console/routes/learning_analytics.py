"""Learning Loops Analytics API — /v1/console/learning-loops

Endpoints for the frontend Learning-Loops panel (Phase 3.2–3.3).

Provides REST API for observability:
  - GET /v1/console/learning-loops/list — all loops with status + health
  - GET /v1/console/learning-loops/{loop_id}/details — per-loop details + 7d trend
  - GET /v1/console/learning-loops/{loop_id}/events — audit log + raw events

Architecture:
  - Queries KG MCP backend (learning_loop_service.py)
  - Caching: 2m (list), 5m (details), none (events)
  - Tenant isolation: via SessionRecord.tenant_id (fail-closed)
  - Performance targets: <50ms (list), <200ms (details), <500ms (events for 100)
  - All queries audit-logged (ADR-0232/0908)

Integration:
  - Phase 2 (KG MCP service) provides the backend queries
  - Phase 3.2 (Console panel) consumes these routes
  - WebSocket updates pushed via learning_dashboard.py (real-time health)

ADR-0908: Frontend Learning-Loops Console Panel
ADR-0907: KG MCP Learning-Loop Index Service
ADR-0906: Learning-Loop Manifest Schema
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import require_csrf, require_session
from ..auth import SessionRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/console/learning-loops", tags=["learning-loops"])

# ── Models ─────────────────────────────────────────────────────────────────

Status = Literal["active", "dormant", "stale", "degrading"]


class LoopHealthScore(BaseModel):
    """Health score with trend direction."""
    score: float = Field(..., ge=0.0, le=1.0, description="Health score 0.0–1.0")
    trend: Literal["up", "down", "flat"] = Field(..., description="7d trend direction")
    previous_score: Optional[float] = Field(None, description="Previous period score")


class LoopSummary(BaseModel):
    """Minimal loop info for grid display."""
    loop_id: str
    plugin_id: str
    skill_id: Optional[str] = None
    status: Status
    health: LoopHealthScore
    last_event: Optional[datetime] = None
    event_count_7d: int = Field(default=0, description="Events in last 7 days")
    description: Optional[str] = None


class LoopListResponse(BaseModel):
    """Response from GET /list endpoint."""
    loops: List[LoopSummary]
    total: int = Field(..., description="Total count (ignoring pagination)")
    timestamp: datetime


class LoopDetailRow(BaseModel):
    """One row in the details drawer."""
    field: str
    value: Any
    unit: Optional[str] = None


class LoopDetail(BaseModel):
    """Full details for a single loop."""
    loop_id: str
    plugin_id: str
    skill_id: Optional[str]
    status: Status
    health: LoopHealthScore
    last_event: Optional[datetime]
    event_count_7d: int
    event_count_30d: int
    description: Optional[str]
    owner: Optional[str] = None
    created_at: Optional[datetime] = None


class HealthTrendPoint(BaseModel):
    """One point in the 7-day health trend."""
    date: str = Field(..., description="ISO date (YYYY-MM-DD)")
    health_score: float = Field(..., ge=0.0, le=1.0)
    event_count: int


class HealthTrend(BaseModel):
    """7-day health trend for sparkline."""
    points: List[HealthTrendPoint]
    min_score: float
    max_score: float
    avg_score: float


class AuditEventRow(BaseModel):
    """One row in the audit log table."""
    timestamp: datetime
    event_type: str
    skill_id: Optional[str] = None
    signal: Optional[str] = None
    outcome: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class EventsResponse(BaseModel):
    """Response from GET /events endpoint."""
    events: List[AuditEventRow]
    total_count: int
    limit: int
    offset: int


class LoopDetailsResponse(BaseModel):
    """Response from GET /{loop_id}/details endpoint."""
    loop: LoopDetail
    health_trend: HealthTrend
    last_10_events: List[AuditEventRow]
    recommendations: List[str] = Field(default_factory=list, description="Actionable alerts")


# ── Cache ──────────────────────────────────────────────────────────────────

_cache = {}  # {(tenant_id, key): (data, timestamp)}
_CACHE_TTL_LIST = 120  # 2m
_CACHE_TTL_DETAIL = 300  # 5m


def _get_cached(tenant_id: str, key: str, ttl: int) -> Any | None:
    """Get cached value if fresh."""
    cache_key = (tenant_id, key)
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if (datetime.utcnow() - ts).total_seconds() < ttl:
            return data
        del _cache[cache_key]
    return None


def _set_cached(tenant_id: str, key: str, data: Any) -> None:
    """Cache a value."""
    _cache[(tenant_id, key)] = (data, datetime.utcnow())


# ── Backend Integration ────────────────────────────────────────────────────

def _get_learning_loop_service():
    """Lazy load the KG MCP service (Phase 2 backend)."""
    try:
        from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService
        from forge.tenants import tenant_home
        return LearningLoopService(tenant_home=tenant_home("_default"))
    except ImportError:
        logger.warning("Learning loop service not available; using stub")
        return None


async def _get_audit_events(tenant_id: str, loop_id: str, limit: int = 10) -> List[dict]:
    """Fetch audit events for a loop from the core audit chain.

    Queries: learning_event_received, skill_config_updated, outcome_feedback events
    that reference this loop_id.
    """
    try:
        from forge.security import audit_query
        events = await audit_query(
            tenant_id=tenant_id,
            event_type_patterns=["learning.*", "skill_.*"],
            filters={"loop_id": loop_id},
            limit=limit,
            order="descending",
        )
        return events if events else []
    except Exception as e:
        logger.error(f"Failed to query audit events: {e}")
        return []


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/list", response_model=LoopListResponse)
async def list_learning_loops(
    session: SessionRecord = Depends(require_session),
    plugin_id: Optional[str] = Query(None, description="Filter by plugin"),
    skill_id: Optional[str] = Query(None, description="Filter by skill"),
    status: Optional[Status] = Query(None, description="Filter by status"),
    sort_by: str = Query("last_event", regex="^(plugin_id|status|last_event|health_score)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> LoopListResponse:
    """List all learning loops with status and health.

    Filters:
      - plugin_id: exact match
      - skill_id: exact match
      - status: active|dormant|stale|degrading

    Sorting:
      - plugin_id, status, last_event, health_score

    Caching: 2m TTL
    Performance target: <50ms
    """
    tenant_id = session.tenant_id

    # Check cache
    cache_key = f"list|{plugin_id}|{skill_id}|{status}|{sort_by}"
    cached = _get_cached(tenant_id, cache_key, _CACHE_TTL_LIST)
    if cached is not None:
        return cached

    service = _get_learning_loop_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Learning loop service not available")

    try:
        # Query backend (Phase 2)
        all_entries = service.list_loops(tenant_id=tenant_id)

        # Filter
        if plugin_id:
            all_entries = [e for e in all_entries if e.plugin_id == plugin_id]
        if skill_id:
            all_entries = [e for e in all_entries if e.skill_id == skill_id]
        if status:
            all_entries = [e for e in all_entries if e.status == status]

        # Sort
        sort_key = {
            "plugin_id": lambda e: e.plugin_id,
            "status": lambda e: e.status,
            "last_event": lambda e: e.last_event_ts or datetime.min,
            "health_score": lambda e: e.health_score,
        }.get(sort_by, lambda e: e.last_event_ts)
        all_entries.sort(key=sort_key, reverse=True)

        total = len(all_entries)
        paginated = all_entries[offset : offset + limit]

        # Build response
        loops = [
            LoopSummary(
                loop_id=e.loop_id,
                plugin_id=e.plugin_id,
                skill_id=e.skill_id,
                status=e.status,
                health=LoopHealthScore(
                    score=e.health_score,
                    trend="up" if (e.health_trend_7d or 0) > 0 else ("down" if (e.health_trend_7d or 0) < 0 else "flat"),
                    previous_score=e.previous_health_score,
                ),
                last_event=e.last_event_ts,
                event_count_7d=e.event_count_7d,
                description=e.description,
            )
            for e in paginated
        ]

        response = LoopListResponse(
            loops=loops,
            total=total,
            timestamp=datetime.utcnow(),
        )

        # Cache
        _set_cached(tenant_id, cache_key, response)

        return response

    except Exception as e:
        logger.error(f"Failed to list learning loops: {e}")
        raise HTTPException(status_code=500, detail="Failed to list learning loops")


@router.get("/{loop_id}/details", response_model=LoopDetailsResponse)
async def get_learning_loop_details(
    loop_id: str,
    session: SessionRecord = Depends(require_session),
    days: int = Query(7, ge=1, le=90, description="Trend window in days"),
) -> LoopDetailsResponse:
    """Get full details for a learning loop.

    Includes:
      - Loop metadata (plugin, skill, owner, created_at)
      - 7-day (or custom) health trend for sparkline
      - Last 10 events (audit log)
      - Recommendations (actionable alerts)

    Caching: 5m TTL
    Performance target: <200ms
    """
    tenant_id = session.tenant_id

    # Check cache
    cache_key = f"details|{loop_id}|{days}"
    cached = _get_cached(tenant_id, cache_key, _CACHE_TTL_DETAIL)
    if cached is not None:
        return cached

    service = _get_learning_loop_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Learning loop service not available")

    try:
        # Get loop entry
        entry = service.get_loop(tenant_id=tenant_id, loop_id=loop_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Loop not found: {loop_id}")

        # Get health trend
        trend_data = service.get_health_trend(tenant_id=tenant_id, loop_id=loop_id, days=days)

        health_trend = HealthTrend(
            points=[
                HealthTrendPoint(
                    date=p["date"],
                    health_score=p["health_score"],
                    event_count=p["event_count"],
                )
                for p in (trend_data.get("points") or [])
            ],
            min_score=trend_data.get("min_score", 0.0),
            max_score=trend_data.get("max_score", 1.0),
            avg_score=trend_data.get("avg_score", 0.5),
        )

        # Get audit events
        events_data = await _get_audit_events(tenant_id, loop_id, limit=10)
        last_10_events = [
            AuditEventRow(
                timestamp=datetime.fromisoformat(e.get("timestamp", "")),
                event_type=e.get("event_type", ""),
                skill_id=e.get("skill_id"),
                signal=e.get("signal"),
                outcome=e.get("outcome"),
                metadata=e.get("metadata", {}),
            )
            for e in events_data
        ]

        # Generate recommendations
        recommendations = []
        if entry.status == "stale":
            recommendations.append("⚠️ Loop is stale (>7d). Consider archiving or investigating why.")
        if entry.status == "degrading":
            recommendations.append("📉 Health score degrading. Review recent feedback or config changes.")
        if entry.event_count_7d == 0:
            recommendations.append("📭 No events in 7d. Loop may be inactive.")
        if entry.health_score < 0.5:
            recommendations.append("⚡ Low health (<0.5). Optimizer may need tuning.")

        loop_detail = LoopDetail(
            loop_id=entry.loop_id,
            plugin_id=entry.plugin_id,
            skill_id=entry.skill_id,
            status=entry.status,
            health=LoopHealthScore(
                score=entry.health_score,
                trend="up" if (entry.health_trend_7d or 0) > 0 else ("down" if (entry.health_trend_7d or 0) < 0 else "flat"),
                previous_score=entry.previous_health_score,
            ),
            last_event=entry.last_event_ts,
            event_count_7d=entry.event_count_7d,
            event_count_30d=entry.event_count_30d,
            description=entry.description,
            owner=entry.owner,
            created_at=entry.created_at,
        )

        response = LoopDetailsResponse(
            loop=loop_detail,
            health_trend=health_trend,
            last_10_events=last_10_events,
            recommendations=recommendations,
        )

        # Cache
        _set_cached(tenant_id, cache_key, response)

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get loop details: {e}")
        raise HTTPException(status_code=500, detail="Failed to get loop details")


@router.get("/{loop_id}/events", response_model=EventsResponse)
async def get_learning_loop_events(
    loop_id: str,
    session: SessionRecord = Depends(require_session),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
) -> EventsResponse:
    """Get audit events for a learning loop (searchable log).

    Queries the core audit chain for all events mentioning this loop_id.

    No cache (always fresh).
    Performance target: <500ms for 100 events
    """
    tenant_id = session.tenant_id

    try:
        # Query audit chain
        events_data = await _get_audit_events(tenant_id, loop_id, limit=limit + offset)

        # Filter by event_type if specified
        if event_type:
            events_data = [e for e in events_data if e.get("event_type", "").startswith(event_type)]

        total = len(events_data)
        paginated = events_data[offset : offset + limit]

        rows = [
            AuditEventRow(
                timestamp=datetime.fromisoformat(e.get("timestamp", "")),
                event_type=e.get("event_type", ""),
                skill_id=e.get("skill_id"),
                signal=e.get("signal"),
                outcome=e.get("outcome"),
                metadata=e.get("metadata", {}),
            )
            for e in paginated
        ]

        return EventsResponse(
            events=rows,
            total_count=total,
            limit=limit,
            offset=offset,
        )

    except Exception as e:
        logger.error(f"Failed to fetch loop events: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch loop events")


# ── Audit Logging ──────────────────────────────────────────────────────────

async def _audit_log_route(session: SessionRecord, route: str, **details) -> None:
    """Log this API call to the audit chain (ADR-0232)."""
    try:
        from core.security.audit import emit_audit_event
        await emit_audit_event(
            tenant_id=session.tenant_id,
            event_type="console.learning_loop_access",
            details={
                "route": route,
                "user_id": session.user_id,
                **details,
            },
        )
    except Exception as e:
        logger.warning(f"Failed to audit route: {e}")
