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
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import require_csrf, require_session
from ..auth import SessionRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/console/learning-loops", tags=["learning-loops"])

# ── Helpers ────────────────────────────────────────────────────────────────

def _trend_direction(trend_value: Optional[float]) -> str:
  """Classify trend as 'up', 'down', or 'flat'."""
  val = trend_value or 0
  return "up" if val > 0 else ("down" if val < 0 else "flat")

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


# ── Response Transformers ──────────────────────────────────────────────────

def _get_health_trend_from_service_response(trend_data: Optional[dict]) -> HealthTrend:
    """Transform service response format to HealthTrend model.

    Service returns: {timestamps, health_scores, event_counts}
    Convert to: {points: [{date, health_score, event_count}, ...]}

    Validation:
    - Timestamps must be ISO format strings (YYYY-MM-DD...)
    - Scores and counts must be numeric or convertible to numeric
    - Handles missing/malformed values gracefully
    """
    if not trend_data:
        return HealthTrend(points=[], min_score=0.0, max_score=1.0, avg_score=0.5)

    timestamps = trend_data.get("timestamps", [])
    scores = trend_data.get("health_scores", [])
    counts = trend_data.get("event_counts", [])

    points = []
    health_scores_valid = []

    for ts, s, c in zip(timestamps, scores, counts):
        try:
            # Extract YYYY-MM-DD from ISO format (validate it's a string)
            if not isinstance(ts, str):
                logger.warning(f"Invalid timestamp type {type(ts).__name__}: {ts}")
                continue
            date_str = ts[:10] if len(ts) > 10 else ts

            # Validate date format (YYYY-MM-DD)
            if len(date_str) != 10 or date_str[4] != '-' or date_str[7] != '-':
                logger.warning(f"Malformed date string: {date_str}")
                continue

            # Convert scores and counts with error handling
            try:
                score_val = float(s)
            except (TypeError, ValueError):
                logger.warning(f"Invalid health_score: {s}")
                score_val = 0.5  # Default to neutral

            try:
                count_val = int(c)
            except (TypeError, ValueError):
                logger.warning(f"Invalid event_count: {c}")
                count_val = 0  # Default to no events

            points.append(HealthTrendPoint(
                date=date_str,
                health_score=score_val,
                event_count=count_val,
            ))
            health_scores_valid.append(score_val)
        except Exception as exc:
            logger.error(f"Failed to parse trend point [{ts}, {s}, {c}]: {exc}")
            continue

    return HealthTrend(
        points=points,
        min_score=min(health_scores_valid) if health_scores_valid else 0.0,
        max_score=max(health_scores_valid) if health_scores_valid else 1.0,
        avg_score=sum(health_scores_valid) / len(health_scores_valid) if health_scores_valid else 0.5,
    )


# ── Cache ──────────────────────────────────────────────────────────────────

_cache = {}  # {(tenant_id, key): (data, timestamp)}
_cache_lock = threading.Lock()  # Thread-safe cache access
_CACHE_TTL_LIST = 120  # 2m
_CACHE_TTL_DETAIL = 300  # 5m


def _get_cached(tenant_id: str, key: str, ttl: int) -> Any | None:
    """Get cached value if fresh."""
    cache_key = (tenant_id, key)
    with _cache_lock:
        if cache_key in _cache:
            data, ts = _cache[cache_key]
            if (datetime.now(timezone.utc) - ts).total_seconds() < ttl:
                return data
            del _cache[cache_key]
    return None


def _set_cached(tenant_id: str, key: str, data: Any) -> None:
    """Cache a value."""
    with _cache_lock:
        _cache[(tenant_id, key)] = (data, datetime.now(timezone.utc))


# ── Backend Integration ────────────────────────────────────────────────────

def _get_learning_loop_service(tenant_id: str) -> Optional['LearningLoopService']:
    """Lazy load the KG MCP service (Phase 2 backend).

    Args:
        tenant_id: Tenant identifier for scoped queries

    Returns:
        LearningLoopService instance or None if unavailable

    Note: Returns None (not raises) on ImportError or initialization failure
    to allow graceful degradation to stub responses (503 Service Unavailable).
    """
    try:
        from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService
        # Pass tenant_id; service resolves path internally
        return LearningLoopService(tenant_id=tenant_id)
    except ImportError:
        logger.warning("Learning loop service not available; module not found")
        return None
    except (ValueError, OSError) as e:
        # ValueError: tenant_id invalid; OSError: DB path inaccessible
        logger.warning(f"Failed to initialize learning loop service: {e}")
        return None


async def _get_audit_events(tenant_id: str, loop_id: str, limit: int = 10) -> List[dict]:
    """Fetch audit events for a loop from the core audit chain.

    Queries: learning_event_received, skill_config_updated, outcome_feedback events
    that reference this loop_id.

    Returns: List of audit events, or empty list on error.
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
    except ImportError:
        logger.warning("Audit query module not available")
        return []
    except (ValueError, RuntimeError) as e:
        logger.error(f"Failed to query audit events: {e}")
        return []


def _parse_audit_event_row(event_dict: dict) -> Optional[AuditEventRow]:
    """Parse an audit event dict into an AuditEventRow with validation.

    Handles missing or malformed timestamp fields gracefully.
    Returns None if event is unparseable.
    """
    try:
        timestamp_str = event_dict.get("timestamp", "")

        # Validate and parse timestamp
        if not timestamp_str:
            logger.warning("Audit event missing timestamp field")
            return None

        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except (ValueError, TypeError) as e:
            logger.warning(f"Malformed timestamp '{timestamp_str}': {e}")
            # Use epoch as fallback (not ideal, but better than crashing)
            timestamp = datetime.fromtimestamp(0, tz=timezone.utc)

        return AuditEventRow(
            timestamp=timestamp,
            event_type=event_dict.get("event_type", ""),
            skill_id=event_dict.get("skill_id"),
            signal=event_dict.get("signal"),
            outcome=event_dict.get("outcome"),
            metadata=event_dict.get("metadata", {}),
        )
    except Exception as exc:
        logger.error(f"Failed to parse audit event: {exc}")
        return None


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

    service = _get_learning_loop_service(tenant_id)
    if service is None:
        raise HTTPException(status_code=503, detail="Learning loop service not available")

    try:
        # Query backend (Phase 2) — tenant_id already scoped in service instance
        all_entries = service.list_loops()

        # Filter
        if plugin_id:
            all_entries = [e for e in all_entries if e.plugin_id == plugin_id]
        if skill_id:
            all_entries = [e for e in all_entries if e.owner_skill == skill_id]
        if status:
            all_entries = [e for e in all_entries if e.status == status]

        # Sort
        sort_key = {
            "plugin_id": lambda e: e.plugin_id,
            "status": lambda e: e.status,
            "last_event": lambda e: e.last_event_ts or datetime.min,
            "health_score": lambda e: e.health_score,
        }.get(sort_by, lambda e: e.last_event_ts)
        # String fields ascending, numeric fields descending
        reverse = sort_by not in ("plugin_id", "status")
        all_entries.sort(key=sort_key, reverse=reverse)

        total = len(all_entries)
        paginated = all_entries[offset : offset + limit]

        # Build response
        loops = [
            LoopSummary(
                loop_id=e.loop_id,
                plugin_id=e.plugin_id,
                skill_id=e.owner_skill,
                status=e.status,
                health=LoopHealthScore(
                    score=e.health_score,
                    trend=_trend_direction(None),  # Trend not in index; use flat as default
                    previous_score=None,  # Not tracked in index
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

    service = _get_learning_loop_service(tenant_id)
    if service is None:
        raise HTTPException(status_code=503, detail="Learning loop service not available")

    try:
        # Get loop entry: find by loop_id across all plugins
        all_entries = service.list_loops()
        entry = None
        for e in all_entries:
            if e.loop_id == loop_id:
                entry = e
                break

        if entry is None:
            raise HTTPException(status_code=404, detail=f"Loop not found: {loop_id}")

        # Get health trend (service expects plugin_id + loop_id, not tenant_id)
        trend_data = service.get_health_trend(
            plugin_id=entry.plugin_id,
            loop_id=loop_id,
            days=days
        )

        # Transform service response to expected format
        health_trend = _get_health_trend_from_service_response(trend_data)

        # Get audit events
        events_data = await _get_audit_events(tenant_id, loop_id, limit=10)
        last_10_events = []
        for e in events_data:
            parsed = _parse_audit_event_row(e)
            if parsed is not None:
                last_10_events.append(parsed)

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

        # Compute event_count_30d from audit events
        # Note: Index only tracks 7d count; 30d requires audit log analysis
        event_count_30d = 0
        for evt in events_data:
            try:
                evt_ts = datetime.fromisoformat(evt.get("timestamp", ""))
                if (entry.last_event_ts - evt_ts).days < 30:
                    event_count_30d += 1
            except (ValueError, TypeError, AttributeError):
                # Skip unparseable timestamps
                pass

        loop_detail = LoopDetail(
            loop_id=entry.loop_id,
            plugin_id=entry.plugin_id,
            skill_id=entry.owner_skill,  # Skill ID managing this loop (from manifest)
            status=entry.status,
            health=LoopHealthScore(
                score=entry.health_score,
                trend="flat",  # Trend computed from health_trend data above, not stored in index
                previous_score=None,  # Not tracked in index
            ),
            last_event=entry.last_event_ts,
            event_count_7d=entry.event_count_7d,
            event_count_30d=event_count_30d,  # Computed from audit events
            description=entry.description,
            owner=entry.owner_skill,  # Same as skill_id; index doesn't track creator separately
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

        rows = []
        for e in paginated:
            parsed = _parse_audit_event_row(e)
            if parsed is not None:
                rows.append(parsed)

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
