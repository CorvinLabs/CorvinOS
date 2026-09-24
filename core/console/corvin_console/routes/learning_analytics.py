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

import asyncio
import hashlib
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

# Module-level constant for timezone-aware epoch (avoid recomputation)
_EPOCH_UTC = datetime.fromtimestamp(0, tz=timezone.utc)

router = APIRouter(prefix="/learning-loops", tags=["learning-loops"])

# ── Helpers ────────────────────────────────────────────────────────────────

def _trend_direction(trend_value: Optional[float]) -> str:
    """Classify trend as 'up', 'down', or 'flat'."""
    val = trend_value or 0
    return "up" if val > 0 else ("down" if val < 0 else "flat")

# ── Models ─────────────────────────────────────────────────────────────────

# "unknown" is not a fifth health state — it is the absence of an age. The CEL
# stage-grade store dates none of its records, so no age-based verdict about
# those loops can be read from it, and claiming "stale" there would assert a
# measurement that was never taken.
Status = Literal["active", "dormant", "stale", "degrading", "unknown"]


class LoopHealthScore(BaseModel):
    """Health score with trend direction.

    ``score`` is **optional on purpose**. A loop that has executed thousands of
    times but produced no outcome and no grade has no measured health, and the
    neutral 0.5 this field used to carry was indistinguishable from a real
    score of 0.5 (ADR-0763). ``basis`` names what a present score counts.
    """
    score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Health score 0.0–1.0, null when nothing measured one")
    trend: Literal["up", "down", "flat"] = Field(..., description="7d trend direction")
    previous_score: Optional[float] = Field(None, description="Previous period score")
    basis: str = Field(default="", description="What the score is counted from; empty when there is no score")


class LoopSummary(BaseModel):
    """Minimal loop info for grid display."""
    loop_id: str
    plugin_id: str
    skill_id: Optional[str] = None
    status: Status
    health: LoopHealthScore
    last_event: Optional[datetime] = None
    event_count_7d: int = Field(default=0, description="Events in last 7 days")
    event_count_total: int = Field(default=0, description="Events over the whole scanned window")
    description: Optional[str] = None
    origin: str = Field(default="plugin", description="plugin | os_skill | cel_stage")
    event_source: str = Field(default="", description="Which store these numbers were read from")


class ListWindow(BaseModel):
    """The window a narrowed total was counted over (never travels apart from it)."""
    scanned_events: int = Field(default=0, description="Learning events read this pass")
    truncated: bool = Field(default=False, description="True when the scan hit its bound and older events fell outside it")


class LoopListResponse(BaseModel):
    """Response from GET /list endpoint."""
    loops: List[LoopSummary]
    total: int = Field(..., description="Total count (ignoring pagination)")
    timestamp: datetime
    window: ListWindow = Field(default_factory=ListWindow)


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
    """One point in the 7-day health trend.

    ``health_score`` is null for a day that recorded no outcome. Carrying the
    previous day's value forward would draw a line through data that does not
    exist, and a 0.0 would render a quiet day as a total failure.
    """
    date: str = Field(..., description="ISO date (YYYY-MM-DD)")
    health_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    event_count: int


class HealthTrend(BaseModel):
    """7-day health trend for sparkline. Aggregates ignore days with no score."""
    points: List[HealthTrendPoint]
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    avg_score: Optional[float] = None


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
    - IMPORTANT: Returns empty points (not fabricated data) if no trend available.
      Per ADR-0763, never fabricate sample data. Empty trend indicates no measurements.
    """
    if not trend_data:
        # Return empty trend (not sample data) when unavailable.
        # Sentinel value 0.5 = "no measurements"; clients use this to distinguish
        # empty state from real data (which ranges 0.0–1.0). Per ADR-0763.
        return HealthTrend(points=[], min_score=0.5, max_score=0.5, avg_score=0.5)

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
        min_score=min(health_scores_valid) if health_scores_valid else 0.5,  # 0.5 sentinel: no measurements
        max_score=max(health_scores_valid) if health_scores_valid else 0.5,  # 0.5 sentinel: no measurements
        avg_score=sum(health_scores_valid) / len(health_scores_valid) if health_scores_valid else 0.5,
    )


# ── Cache ──────────────────────────────────────────────────────────────────

_cache = {}  # {(tenant_id, key_hash): (data, timestamp, ttl)}
_cache_lock = threading.Lock()  # Thread-safe cache access
_CACHE_TTL_LIST = 120  # 2m
_CACHE_TTL_DETAIL = 300  # 5m
_CACHE_CLEANUP_INTERVAL = 600  # Cleanup expired entries every 10 minutes
_last_cache_cleanup = datetime.now(timezone.utc)


def _hash_cache_key(plugin_id: Optional[str], skill_id: Optional[str],
                   status: Optional[str], sort_by: str) -> str:
    """Hash cache parameters to avoid collision/injection vulnerabilities.

    Uses SHA256 hash of normalized parameters instead of string concatenation
    to prevent cache key collisions and parameter injection attacks.
    """
    # Normalize sort_by first (before including in hash)
    normalized_sort_by = sort_by or "last_event"
    if normalized_sort_by not in ("plugin_id", "status", "last_event", "health_score"):
        normalized_sort_by = "last_event"

    # Normalize to strings, replacing None with empty string
    parts = [
        plugin_id or "",
        skill_id or "",
        status or "",
        normalized_sort_by,  # Use normalized value in hash
    ]

    key_str = "|".join(parts)
    return hashlib.sha256(key_str.encode()).hexdigest()


def _hash_simple_cache_key(*args: Any) -> str:
    """Hash simple cache parameters (no normalization needed).

    Used for details endpoint and other simple parameter combinations.
    Prevents cache key collisions and parameter injection attacks.
    """
    # Convert all args to strings and hash
    key_str = "|".join(str(arg or "") for arg in args)
    return hashlib.sha256(key_str.encode()).hexdigest()


def _cleanup_expired_cache() -> None:
    """Remove expired cache entries (proactive cleanup to prevent memory leak)."""
    global _last_cache_cleanup
    now = datetime.now(timezone.utc)

    # Quick check before acquiring lock (TOCTOU race is acceptable for cache cleanup)
    # Only run cleanup every 10 minutes to avoid per-request overhead
    if (now - _last_cache_cleanup).total_seconds() < _CACHE_CLEANUP_INTERVAL:
        return

    with _cache_lock:
        # Double-check inside lock to prevent multiple cleanups if multiple threads passed the first check
        if (now - _last_cache_cleanup).total_seconds() < _CACHE_CLEANUP_INTERVAL:
            return

        expired_keys = []
        for cache_key, entry in _cache.items():
            # Entry format: (data, ts, ttl)
            if len(entry) >= 3:
                data, ts, ttl = entry[0], entry[1], entry[2]
            else:
                # Fallback for old format (shouldn't happen, but defensive)
                # Use minimum TTL to avoid keeping stale entries longer than intended
                data, ts = entry[0], entry[1]
                ttl = _CACHE_TTL_LIST  # 120s minimum for list cache entries

            # Use per-entry TTL (stored when cached)
            if (now - ts).total_seconds() >= ttl:
                expired_keys.append(cache_key)

        for key in expired_keys:
            del _cache[key]

        _last_cache_cleanup = now


def _get_cached(tenant_id: str, key_hash: str, ttl: int) -> Any | None:
    """Get cached value if fresh.

    Args:
        tenant_id: Tenant identifier
        key_hash: SHA256 hash of cache parameters (from _hash_cache_key)
        ttl: Time-to-live in seconds
    """
    cache_key = (tenant_id, key_hash)
    _cleanup_expired_cache()  # Proactive cleanup on every access

    with _cache_lock:
        if cache_key in _cache:
            entry = _cache[cache_key]
            # Handle both 2-tuple (legacy) and 3-tuple (with TTL) formats
            if len(entry) >= 3:
                data, ts, stored_ttl = entry[0], entry[1], entry[2]
            else:
                data, ts = entry[0], entry[1]
                stored_ttl = ttl

            if (datetime.now(timezone.utc) - ts).total_seconds() < stored_ttl:
                return data
            del _cache[cache_key]
    return None


def _set_cached(tenant_id: str, key_hash: str, data: Any, ttl: int) -> None:
    """Cache a value.

    Args:
        tenant_id: Tenant identifier
        key_hash: SHA256 hash of cache parameters (from _hash_cache_key)
        data: Data to cache
        ttl: Time-to-live in seconds (stored with entry for per-entry cleanup)
    """
    with _cache_lock:
        _cache[(tenant_id, key_hash)] = (data, datetime.now(timezone.utc), ttl)


# ── Backend Integration ────────────────────────────────────────────────────

def _get_learning_loop_service(tenant_id: str) -> Optional['LearningLoopService']:
    """Lazy load the KG MCP service (Phase 2 backend).

    Args:
        tenant_id: Tenant identifier for scoped queries

    Returns:
        LearningLoopService instance or None if unavailable

    Note: Returns None (not raises) on any initialization failure
    to allow graceful degradation to stub responses (503 Service Unavailable).
    """
    try:
        from core.knowledge_graph.mcp.learning_loop_service import LearningLoopService
        # Pass tenant_id; service resolves path internally
        return LearningLoopService(tenant_id=tenant_id)
    except ImportError:
        logger.warning("Learning loop service not available; module not found")
        return None
    except Exception as e:
        # Catch all exceptions (ValueError, OSError, TypeError, etc.) to ensure
        # graceful degradation. Log the error for debugging but don't crash.
        logger.warning(f"Failed to initialize learning loop service: {type(e).__name__}: {e}")
        return None


_MANIFEST_SYNC_TTL = 300  # 5 minutes
_manifest_sync_at: dict[str, datetime] = {}
_manifest_sync_lock = threading.Lock()


def _sync_index_from_manifest(service: 'LearningLoopService', tenant_id: str) -> None:
    """Reconcile the loop index with the loops declared by installed plugins.

    ADR-0906 makes a plugin's ``learning_loops:`` manifest section the source of
    truth for WHICH loops exist; the index (ADR-0907) then carries their runtime
    metrics, updated per event by the hook in
    ``core.learning.event_persistence.EventStore._update_kg_index``. Nothing
    registered a loop in the first place, so that hook could only ever log
    "index entry not found" and the panel showed an empty list.

    ``ManifestRefreshService`` was written for this job but polls
    ``/v1/console/capabilities/manifest`` over HTTP with aiohttp and has no
    production caller — a console process issuing an authenticated request to
    itself to read data it can compute in-process. ``_get_learning_loops()`` is
    that same computation as a plain function, so this calls it directly: no
    socket, no session cookie, no optional dependency.

    Additive plus archival: a loop whose plugin no longer declares it is
    archived (audited by the service), never silently dropped. Failures are
    logged and swallowed — a stale index degrades the panel, an exception
    would take the route down.
    """
    now = datetime.now(timezone.utc)
    with _manifest_sync_lock:
        last = _manifest_sync_at.get(tenant_id)
        if last is not None and (now - last).total_seconds() < _MANIFEST_SYNC_TTL:
            return
        _manifest_sync_at[tenant_id] = now

    try:
        from .capabilities import _get_learning_loops

        declared = {
            (d["plugin_id"], d["loop_id"]): d
            for d in _get_learning_loops(tenant_id)
        }
        indexed = {(e.plugin_id, e.loop_id) for e in service.list_loops()}

        for key, d in declared.items():
            if key in indexed:
                continue
            service.insert_from_manifest(
                plugin_id=d["plugin_id"],
                loop_id=d["loop_id"],
                description=d.get("description", ""),
                event_source=d.get("event_source", ""),
                feedback_types=d.get("feedback_types", []),
                aggregation=d.get("aggregation", "rolling_mean_7d"),
                health_threshold=d.get("health_threshold"),
                dormancy_alert_hours=d.get("dormancy_alert_hours", 24),
                owner_skill=d.get("owner_skill"),
            )
            logger.info("Indexed learning loop %s:%s", *key)

        for plugin_id, loop_id in indexed - set(declared):
            service.archive_loop(plugin_id, loop_id)
            logger.info("Archived learning loop %s:%s", plugin_id, loop_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Learning loop manifest sync failed: %s", exc)


def _read_loop_audit_events(tenant_id: str, loop_id: str, limit: int) -> List[dict]:
    """Newest-first ``learning.*`` / ``skill_*`` records naming *loop_id*.

    Reads the tenant's ONE chain (``tenant_audit_chain``) AND its seam-linked
    history (ADR-2058 ``iter_chain_records``) — a loop's past must not vanish
    from this view because the canonical file was restarted after a chain loss.
    Content-free: only the identifiers the row model shows, never ``details``.
    A record naming a DIFFERENT tenant is dropped (fail-closed isolation).
    """
    from core.paths import tenant_audit_chain  # noqa: PLC0415
    from core.paths.chain_history import iter_chain_records  # noqa: PLC0415

    path = tenant_audit_chain(tenant_id)
    if not path.exists():
        return []
    matched: List[dict] = []
    for rec in iter_chain_records(path, needles=('"loop_id"',)):
        event_type = str(rec.get("event_type") or "")
        if not (event_type.startswith("learning.") or event_type.startswith("skill_")):
            continue
        details = rec.get("details") or {}
        if not isinstance(details, dict) or details.get("loop_id") != loop_id:
            continue
        rec_tenant = details.get("tenant_id") or rec.get("tenant_id")
        if rec_tenant and rec_tenant != tenant_id:
            continue
        ts = rec.get("ts")
        if isinstance(ts, (int, float)) and not isinstance(ts, bool):
            timestamp = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        else:
            timestamp = str(ts or rec.get("timestamp") or "")
        matched.append({
            "timestamp": timestamp,
            "event_type": event_type,
            "skill_id": details.get("skill_id"),
            "signal": details.get("signal") if isinstance(details.get("signal"), str) else None,
            "outcome": details.get("outcome") if isinstance(details.get("outcome"), str) else None,
            "metadata": {},
        })
    matched.reverse()
    return matched[: max(0, limit)]


async def _get_audit_events(tenant_id: str, loop_id: str, limit: int = 10) -> List[dict]:
    """Fetch audit events for a loop from the core audit chain (newest first).

    Queries ``learning.*`` / ``skill_*`` records that reference this loop_id,
    across the canonical chain and its seam-linked history. Until 2026-09-24
    this imported a ``forge.security.audit_query`` that does not exist, so the
    ImportError branch answered ``[]`` for every loop, always.

    Returns: List of audit events, or empty list on error (graceful degradation).
    """
    try:
        return await asyncio.to_thread(_read_loop_audit_events, tenant_id, loop_id, limit)
    except Exception as e:
        # Catch all exceptions to ensure graceful degradation. Log for
        # debugging but return empty list.
        logger.error(f"Failed to query audit events: {type(e).__name__}: {e}")
        return []


def _parse_audit_event_row(event_dict: dict) -> Optional[AuditEventRow]:
    """Parse an audit event dict into an AuditEventRow with validation.

    Handles missing or malformed timestamp fields gracefully.
    All returned timestamps are guaranteed timezone-aware (UTC).
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
            # Ensure timezone-aware (fromisoformat can return naive datetime)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError) as e:
            logger.warning(f"Malformed timestamp '{timestamp_str}': {e}")
            # Use epoch as fallback (not ideal, but better than crashing)
            timestamp = _EPOCH_UTC

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

def _core_loop_summaries(tenant_id: str) -> tuple[List[LoopSummary], ListWindow]:
    """The loops CorvinOS runs itself, measured from its own stores.

    ADR-0906 covers loops a PLUGIN declares. It has no way to express the loops
    the OS runs — the skills whose executions, outcomes and feedback are already
    in the tenant event store, and the CEL pipeline stages the outcome loop
    grades. Those are not indexed and do not need to be: their store IS the
    record, so reading it live cannot drift from it the way a copy would.
    A failure here contributes nothing and never fails the route.
    """
    try:
        from core.learning.loop_discovery import discover_core_loops
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop discovery unavailable: %s", exc)
        return [], ListWindow()

    try:
        result = discover_core_loops(tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop discovery failed: %s", exc)
        return [], ListWindow()

    summaries = [
        LoopSummary(
            loop_id=obs.loop_id,
            plugin_id="corvinos",
            skill_id=obs.owner,
            status=obs.status,
            health=LoopHealthScore(
                score=obs.health_score,
                # No dated history exists for these loops yet, so no direction
                # can be computed. "flat" is the schema's neutral, not a claim
                # that the score held steady.
                trend="flat",
                previous_score=None,
                basis=obs.health_basis,
            ),
            last_event=obs.last_event_ts,
            event_count_7d=obs.event_count_7d,
            event_count_total=obs.event_count_total,
            description=obs.description,
            origin=obs.origin,
            event_source=obs.event_source,
        )
        for obs in result.loops
    ]
    return summaries, ListWindow(scanned_events=result.scanned, truncated=result.truncated)


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

    # Check cache (use hashed key to prevent collision/injection)
    cache_key_hash = _hash_cache_key(plugin_id, skill_id, status, sort_by)
    cached = _get_cached(tenant_id, cache_key_hash, _CACHE_TTL_LIST)
    if cached is not None:
        return cached

    # Two sources, one list. Plugin-declared loops live in the index (ADR-0906
    # declares them, the auto-indexing hook fills their metrics); the loops the
    # OS runs itself are read live from the stores that already record them.
    # The index being unavailable must not hide the core loops — it did until
    # 2026-09-21, when a single 503 from an unrelated storage defect emptied the
    # whole panel.
    core_loops, window = _core_loop_summaries(tenant_id)

    indexed: List[LoopSummary] = []
    service = _get_learning_loop_service(tenant_id)
    if service is not None:
        # Pick up loops declared by plugins installed since the last sync.
        _sync_index_from_manifest(service, tenant_id)
        try:
            for e in service.list_loops():
                indexed.append(
                    LoopSummary(
                        loop_id=e.loop_id,
                        plugin_id=e.plugin_id,
                        skill_id=e.owner_skill,
                        status=e.status,
                        health=LoopHealthScore(
                            score=e.health_score,
                            trend=_trend_direction(None),  # no dated history in the index
                            previous_score=None,
                            basis="index health score",
                        ),
                        last_event=e.last_event_ts,
                        event_count_7d=e.event_count_7d,
                        event_count_total=e.event_count_7d,
                        description=e.description,
                        origin="plugin",
                        event_source="learning loop index",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Indexed loop listing failed: %s", exc)
    elif not core_loops:
        # Nothing to show from either source AND the index is broken — that is a
        # real service failure, not an empty install.
        raise HTTPException(status_code=503, detail="Learning loop service not available")

    try:
        all_entries = core_loops + indexed

        # Filter
        if plugin_id:
            all_entries = [e for e in all_entries if e.plugin_id == plugin_id]
        if skill_id:
            all_entries = [e for e in all_entries if e.skill_id == skill_id]
        if status:
            all_entries = [e for e in all_entries if e.status == status]

        # Sort. A loop with no measured health sorts last under health_score
        # rather than being treated as a zero, which would rank "not measured"
        # below a genuinely failing loop.
        sort_key = {
            "plugin_id": lambda e: e.plugin_id,
            "status": lambda e: e.status,
            "last_event": lambda e: e.last_event or _EPOCH_UTC,
            "health_score": lambda e: (e.health.score is not None, e.health.score or 0.0),
        }.get(sort_by, lambda e: e.last_event or _EPOCH_UTC)
        reverse = sort_by not in ("plugin_id", "status")
        all_entries.sort(key=sort_key, reverse=reverse)

        total = len(all_entries)
        loops = all_entries[offset : offset + limit]

        response = LoopListResponse(
            loops=loops,
            total=total,
            timestamp=datetime.now(timezone.utc),
            window=window,
        )

        # Cache (use hashed key with appropriate TTL)
        _set_cached(tenant_id, cache_key_hash, response, _CACHE_TTL_LIST)

        # Audit-log this API call (ADR-0232)
        # Fire-and-forget on audit failure: don't block response on audit errors
        try:
            await _audit_log_route(session, "/learning-loops/list",
                                 plugin_id=plugin_id, skill_id=skill_id, status=status)
        except Exception as exc:
            logger.warning(f"Failed to audit GET /list: {exc}")

        return response

    except Exception as e:
        logger.error(f"Failed to list learning loops: {e}")
        raise HTTPException(status_code=500, detail="Failed to list learning loops")


def _core_loop_detail(tenant_id: str, loop_id: str, days: int) -> Optional[LoopDetailsResponse]:
    """Full detail for a ``core:`` loop, or None when the id is not one / not found."""
    try:
        from core.learning import loop_discovery
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop discovery unavailable: %s", exc)
        return None

    if loop_discovery.core_loop_owner(loop_id) is None:
        return None

    try:
        observed = next(
            (o for o in loop_discovery.discover_core_loops(tenant_id).loops if o.loop_id == loop_id),
            None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop discovery failed: %s", exc)
        return None
    if observed is None:
        return None

    points = [
        HealthTrendPoint(date=p.date, health_score=p.health_score, event_count=p.event_count)
        for p in loop_discovery.core_loop_trend(tenant_id, loop_id, days=days)
    ]
    scored = [p.health_score for p in points if p.health_score is not None]
    trend = HealthTrend(
        points=points,
        min_score=min(scored) if scored else None,
        max_score=max(scored) if scored else None,
        avg_score=(sum(scored) / len(scored)) if scored else None,
    )

    rows = loop_discovery.core_loop_events(tenant_id, loop_id, limit=100)
    last_10 = [
        AuditEventRow(
            timestamp=r.timestamp,
            event_type=r.event_type,
            skill_id=r.skill_id,
            signal=r.signal,
            outcome=r.outcome,
            metadata={},
        )
        for r in rows[-10:][::-1]
    ]

    count_30d = sum(
        1
        for r in rows
        if r.timestamp >= datetime.now(timezone.utc) - timedelta(days=30)
    )

    # Recommendations state what the numbers support and nothing more. A loop
    # with no measured health gets a note saying exactly that, not a warning
    # about a low score it does not have.
    recommendations: List[str] = []
    if observed.health_score is None:
        recommendations.append(
            "No health score: this loop records executions but no outcomes or grades, "
            "so nothing has measured whether its decisions were right."
        )
    elif observed.health_score < 0.5:
        recommendations.append(
            f"Health below 0.5 ({observed.health_basis}). Review recent feedback or config changes."
        )
    if observed.status == "stale":
        recommendations.append("No events for more than 7 days — the loop may no longer be running.")
    elif observed.status == "unknown":
        recommendations.append(
            "Activity age is unknown: this loop's source store does not date its records."
        )
    if observed.outcome_total and observed.outcome_total < 10:
        recommendations.append(
            f"Only {observed.outcome_total} outcomes recorded — too few to read the score as a rate."
        )

    detail = LoopDetail(
        loop_id=observed.loop_id,
        plugin_id="corvinos",
        skill_id=observed.owner,
        status=observed.status,
        health=LoopHealthScore(
            score=observed.health_score,
            trend=_trend_direction(
                (scored[-1] - scored[0]) if len(scored) > 1 else None
            ),
            previous_score=scored[0] if len(scored) > 1 else None,
            basis=observed.health_basis,
        ),
        last_event=observed.last_event_ts,
        event_count_7d=observed.event_count_7d,
        event_count_30d=count_30d,
        description=observed.description,
        owner=observed.owner,
        created_at=None,
    )

    return LoopDetailsResponse(
        loop=detail,
        health_trend=trend,
        last_10_events=last_10,
        recommendations=recommendations,
    )


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

    # Check cache (use hashed key to prevent collision/injection)
    cache_key_hash = _hash_simple_cache_key(loop_id, days)
    cached = _get_cached(tenant_id, cache_key_hash, _CACHE_TTL_DETAIL)
    if cached is not None:
        return cached

    core_detail = _core_loop_detail(tenant_id, loop_id, days)
    if core_detail is not None:
        _set_cached(tenant_id, cache_key_hash, core_detail, _CACHE_TTL_DETAIL)
        try:
            await _audit_log_route(session, f"/learning-loops/{loop_id}/details", days=days)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to audit GET /details: {exc}")
        return core_detail

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

        # Compute trend direction from health_trend points (simple regression: first vs. last)
        trend_value = 0.0
        if health_trend.points and len(health_trend.points) > 1:
            # Sort points by date to ensure correct trend direction (earliest to latest)
            sorted_points = sorted(health_trend.points, key=lambda p: p.date)
            first_score = sorted_points[0].health_score
            last_score = sorted_points[-1].health_score
            trend_value = last_score - first_score  # Positive = improving, negative = degrading

        # Get audit events
        events_data = await _get_audit_events(tenant_id, loop_id, limit=100)
        last_10_events = []
        event_count_30d = 0
        now = datetime.now(timezone.utc)
        since_30d = now - timedelta(days=30)

        for e in events_data:
            parsed = _parse_audit_event_row(e)
            if parsed is not None:
                # Keep first 10 for display
                if len(last_10_events) < 10:
                    last_10_events.append(parsed)

                # Count events within last 30 days (from now, not from last event)
                if parsed.timestamp >= since_30d:
                    event_count_30d += 1

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
            skill_id=entry.owner_skill,  # Skill ID managing this loop (from manifest)
            status=entry.status,
            health=LoopHealthScore(
                score=entry.health_score,
                trend=_trend_direction(trend_value),  # Computed from health_trend points
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

        # Cache (use hashed key with appropriate TTL)
        _set_cached(tenant_id, cache_key_hash, response, _CACHE_TTL_DETAIL)

        # Audit-log this API call (ADR-0232)
        try:
            # Non-blocking audit (fire-and-forget); don't block response on audit failure
            await _audit_log_route(session, f"/learning-loops/{loop_id}/details", days=days)
        except Exception as exc:
            logger.warning(f"Failed to audit GET /details: {exc}")

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get loop details: {e}")
        raise HTTPException(status_code=500, detail="Failed to get loop details")


def _core_loop_event_rows(tenant_id: str, loop_id: str, want: int) -> Optional[List[AuditEventRow]]:
    """Event rows for a ``core:`` loop, newest first — or None if not a core loop."""
    try:
        from core.learning import loop_discovery
    except Exception as exc:  # noqa: BLE001
        logger.warning("core loop discovery unavailable: %s", exc)
        return None

    if loop_discovery.core_loop_owner(loop_id) is None:
        return None

    rows = loop_discovery.core_loop_events(tenant_id, loop_id, limit=max(want, 100))
    return [
        AuditEventRow(
            timestamp=r.timestamp,
            event_type=r.event_type,
            skill_id=r.skill_id,
            signal=r.signal,
            outcome=r.outcome,
            metadata={},
        )
        for r in rows[::-1]
    ]


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

    Note: Pagination (offset/limit) is computed over filtered results, not the entire
    dataset. Filtering happens client-side after fetching a large batch from the backend.
    This means total_count reflects only filtered events, not total unfiltered events.
    """
    tenant_id = session.tenant_id

    # A core loop's events live in the tenant event store, not in the audit
    # chain's loop_id-tagged records — the chain filter below keys on a loop_id
    # these events do not carry.
    core_rows = _core_loop_event_rows(tenant_id, loop_id, limit + offset)
    if core_rows is not None:
        if event_type:
            core_rows = [r for r in core_rows if r.event_type.startswith(event_type)]
        total = len(core_rows)
        response = EventsResponse(
            events=core_rows[offset : offset + limit],
            total_count=total,
            limit=limit,
            offset=offset,
        )
        try:
            await _audit_log_route(session, f"/learning-loops/{loop_id}/events",
                                   limit=limit, offset=offset, event_type=event_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to audit GET /events: {exc}")
        return response

    try:
        # Query audit chain with oversampling to account for event_type filtering loss.
        # Critical: must account for BOTH offset and limit to support high-offset pagination.
        # Multiplier 5× is REQUIRED for correctness (not optimization):
        #   - User requests offset=100, limit=50 (positions 100–150 in filtered set)
        #   - If filter keeps 20%, we need 750 raw events to get 150 filtered
        #   - (limit + offset) * 5 = 150 * 5 = 750 ✓ Correct
        #   - Any lower multiplier fails for high offset + selective filtering
        # Note: pagination is over filtered results, not total unfiltered results.
        fetch_limit = max(500, (limit + offset) * 5)
        events_data = await _get_audit_events(tenant_id, loop_id, limit=fetch_limit)

        # Filter by event_type if specified (post-query filtering in Python)
        if event_type:
            events_data = [e for e in events_data if e.get("event_type", "").startswith(event_type)]

        # Calculate total count from filtered results
        total = len(events_data)

        # Slice for pagination (pagination is over filtered results)
        paginated = events_data[offset : offset + limit]

        rows = []
        for e in paginated:
            parsed = _parse_audit_event_row(e)
            if parsed is not None:
                rows.append(parsed)

        response = EventsResponse(
            events=rows,
            total_count=total,
            limit=limit,
            offset=offset,
        )

        # Audit-log this API call (ADR-0232)
        try:
            # Non-blocking audit (fire-and-forget); don't block response on audit failure
            await _audit_log_route(session, f"/learning-loops/{loop_id}/events",
                                 limit=limit, offset=offset, event_type=event_type)
        except Exception as exc:
            logger.warning(f"Failed to audit GET /events: {exc}")

        return response

    except Exception as e:
        logger.error(f"Failed to fetch loop events: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch loop events")


# ── Audit Logging ──────────────────────────────────────────────────────────

async def _audit_log_route(session: SessionRecord, route: str, **details) -> None:
    """Log this API call to the audit chain (ADR-0232).

    Non-blocking audit logging. Failures are logged but don't interrupt the API response.

    Args:
        session: Session record with tenant_id and user_id
        route: API route path (e.g., /learning-loops/list)
        **details: Additional details to log (filters, pagination params, etc.)
    """
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
        logger.warning(f"Failed to audit route {route}: {type(e).__name__}: {e}")
