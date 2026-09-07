"""Learning Dashboard API endpoints (ADR-0321).

Provides REST API for observability:
  - GET /api/learning/summary — system-wide metrics
  - GET /api/learning/skills/{skill_name} — per-skill stats
  - GET /api/learning/user/{user_id} — user-scoped metrics
  - WS /api/learning/stream — real-time WebSocket updates

Tenant isolation enforced. All queries audit-logged.

Auth (adversarial review N-06): every route depends on the REAL console
session (``deps.require_session`` → 401 without a live cookie) and the tenant
is ``rec.tenant_id`` from the authenticated ``SessionRecord`` — never an env
var, never an anonymous default. The previous version fell back to a NO-OP
dependency that returned an anonymous ``{"tenant_id": "_default"}`` user.
The console session IS the tenant operator (local login, credential-less,
CLAUDE.md § user_backend), so tenant-scoped reads are authorised by it.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, Optional
from datetime import datetime
from pathlib import Path
import json
import logging

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

try:
    # Try direct import first (when PYTHONPATH includes project root)
    from core.learning.dashboard import (
        LearningDashboard,
        DashboardMetrics,
        SkillPerformance,
        SubscriberLimitExceeded,
    )
    from core.learning.event_store import EventStore
except ImportError:
    # Fallback: use relative import from parent structure
    import sys
    from pathlib import Path
    project_root = Path(__file__).resolve().parents[3]  # Navigate up to project root
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from core.learning.dashboard import (
        LearningDashboard,
        DashboardMetrics,
        SkillPerformance,
        SubscriberLimitExceeded,
    )
    from core.learning.event_store import EventStore

from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])

# Global dashboard instance (per-tenant)
_dashboards: Dict[str, LearningDashboard] = {}


def _tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — honours CORVIN_HOME (never a bare ~/.corvin)."""
    from forge.tenants import tenant_home  # type: ignore[import-not-found]

    return Path(tenant_home(tenant_id))


def get_dashboard(tenant_id: str, audit_backend=None) -> LearningDashboard:
    """Get or initialize dashboard for tenant.

    The store is ``event_store.EventStore(tenant_home)`` — the SAME store the
    EventEmitter writes to (``<tenant_home>/learning/events/``).
    """
    if tenant_id not in _dashboards:
        event_store = EventStore(tenant_home=_tenant_home(tenant_id))

        _dashboards[tenant_id] = LearningDashboard(
            tenant_id=tenant_id,
            event_store=event_store,
            audit_backend=audit_backend,
            cache_ttl_seconds=5,
        )
    return _dashboards[tenant_id]


@router.get("/summary", summary="Get system-wide learning metrics")
async def get_learning_summary(
    rec: session_auth.SessionRecord = Depends(require_session),
    since: Optional[str] = Query(None, description="ISO 8601 timestamp (min date)"),
    until: Optional[str] = Query(None, description="ISO 8601 timestamp (max date)"),
) -> Dict[str, Any]:
    """Get aggregated system metrics (cached 5s).

    Returns summary of:
      - Accuracy (success rates across all skills)
      - Latency (response times, p50/p95/p99)
      - Confidence (model confidence scores)
      - Satisfaction (user satisfaction)
      - Total event count

    Tenant isolation enforced (users only see their own data).
    """
    tenant_id = rec.tenant_id
    dashboard = get_dashboard(tenant_id)

    try:
        cache_key = f"{tenant_id}:summary"
        was_cached = dashboard.cache.get(cache_key) is not None
        metrics = dashboard.get_summary_stats()
        return {
            "status": "ok",
            "data": metrics.to_dict(),
            # Real cache-hit indicator. This was hard-coded ``True`` regardless
            # of whether the cache was consulted (round-4 review, F11).
            "cached": was_cached,
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{skill_name}", summary="Get per-skill performance metrics")
async def get_skill_metrics(
    skill_name: str,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Per-skill metrics aggregated from the tenant's REAL learning events.

    Returns, over the skill's most recent events:
      - ``accuracy`` — successes / total across recorded OUTCOME events
      - ``latency_ms`` — mean of ``latency_ms``/``duration_ms`` in the signals
      - ``confidence`` — mean of recorded CONFIDENCE events
      - ``user_satisfaction`` — mean ``quality_rating`` of FEEDBACK events
      - ``usage_count`` — recorded SKILL_EXECUTED events
      - ``last_updated`` — timestamp of the newest event

    A dimension with NO recorded events is ``null`` — "not recorded", not zero.
    Until 2026-09-07 every field here was a hard-coded ``None``/0 behind this
    docstring's promises (round-4 review, F11).

    Tenant isolation enforced.
    """
    tenant_id = rec.tenant_id
    dashboard = get_dashboard(tenant_id)

    try:
        perf = dashboard.get_skill_stats(skill_name)
        return {
            "status": "ok",
            "data": perf.to_dict(),
        }
    except Exception as e:
        logger.error(f"Error fetching skill metrics for {skill_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}", summary="Get user-scoped learning metrics")
async def get_user_metrics(
    user_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Per-user metrics — NOT RECORDED by this system.

    Returns ``available: false`` plus a ``reason``. ADR-0314 learning events
    are content-free and tenant-scoped: they carry no user dimension at all
    (GDPR Art. 5), so satisfaction / engagement / query-count per user cannot
    be computed from them. The endpoint is kept so a client gets an explicit,
    machine-readable "not recorded" instead of a 404 — and, since 2026-09-07,
    instead of a docstring promising metrics that were hard-coded ``None``
    (round-4 review, F11).

    Per-tenant isolation enforced: the authenticated session's tenant only.
    The console session is the tenant OPERATOR (there is no per-user console
    login today — CLAUDE.md § user_backend), so it may read the metrics of any
    user of its own tenant; a user of another tenant is unreachable by
    construction (the dashboard is bound to ``rec.tenant_id``).
    """
    tenant_id = rec.tenant_id
    dashboard = get_dashboard(tenant_id)

    try:
        stats = dashboard.get_user_stats(user_id)
        return {
            "status": "ok",
            "data": stats,
        }
    except Exception as e:
        logger.error(f"Error fetching user metrics for {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/subscribe", summary="Register for WebSocket updates")
async def subscribe_for_updates(
    user_scoped: bool = Query(False, description="Subscribe to user-scoped metrics only"),
    user_scoped_user_id: Optional[str] = Query(None, description="User id for a user-scoped subscription"),
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> Dict[str, Any]:
    """Register for real-time dashboard updates via WebSocket.

    Returns:
      subscriber_id: Use this to connect to /api/learning/stream?subscriber_id=...
      ws_url: WebSocket URL for client to connect to

    If user_scoped=True, only user-specific metrics are pushed.
    If user_scoped=False (admin only), system-wide metrics.

    **CSRF (round-4 console review, F5).** ``require_session`` alone was not
    enough: the handler reads only query params, so with
    ``content-type: text/plain`` this POST stays a CORS *simple* request — a
    browser sends it cross-site with the console cookie and no preflight, and
    every such call allocated a permanent server-side subscriber. It now takes
    ``require_csrf``, and the store is capped (503 at the cap) instead of
    growing without bound.
    """
    tenant_id = rec.tenant_id
    # A console session has no user identity of its own (operator session);
    # a user-scoped subscription must name the user explicitly.
    user_id = (user_scoped_user_id or None) if user_scoped else None

    dashboard = get_dashboard(tenant_id)

    try:
        subscriber_id = dashboard.subscribe_for_updates(user_id=user_id)
    except SubscriberLimitExceeded as e:
        logger.warning("learning dashboard subscriber cap reached (tenant=%s)", tenant_id)
        raise HTTPException(status_code=503, detail=str(e)) from None
    except Exception as e:
        logger.error(f"Error subscribing to updates: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    return {
        "status": "ok",
        "subscriber_id": subscriber_id,
        "ws_url": f"/api/learning/stream?subscriber_id={subscriber_id}",
        "tenant_id": tenant_id,
    }


@router.post("/unsubscribe", summary="Unregister WebSocket subscriber")
async def unsubscribe(
    subscriber_id: str,
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> Dict[str, Any]:
    """Unregister from WebSocket updates."""
    tenant_id = rec.tenant_id
    dashboard = get_dashboard(tenant_id)

    try:
        removed = dashboard.unsubscribe(subscriber_id)
        if not removed:
            raise HTTPException(status_code=404, detail="Subscriber not found")

        return {
            "status": "ok",
            "message": "Unsubscribed successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unsubscribing: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# WebSocket Handler (requires WebSocket support in FastAPI)
# ============================================================================


@router.websocket("/stream")
async def websocket_dashboard_stream(
    websocket: WebSocket,
    subscriber_id: str,
    corvin_console_sid: Annotated[str | None, Cookie()] = None,
):
    """WebSocket endpoint for real-time dashboard updates.

    Client connects with subscriber_id from /subscribe endpoint.
    Server pushes metrics_updated, skill_alert, etc. as they occur.

    Message format:
      {
        "type": "metrics_updated" | "skill_alert" | "user_alert",
        "data": { ... },
        "timestamp": "2026-09-02T12:34:56Z"
      }
    """
    # Same session gate as the HTTP routes (chat.py websocket pattern):
    # no cookie / expired session → close 4401, never an anonymous tenant.
    if not corvin_console_sid:
        await websocket.close(code=4401)
        return
    rec = session_auth.load_session(corvin_console_sid)
    if rec is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    tenant_id = rec.tenant_id

    dashboard = get_dashboard(tenant_id)

    # Touch subscriber to mark as active
    if not dashboard.touch_subscriber(subscriber_id):
        await websocket.send_json({
            "type": "error",
            "message": "Subscriber not found",
        })
        await websocket.close(code=4000)
        return

    try:
        while True:
            # Wait for client ping/pong or messages
            data = await websocket.receive_text()
            message = json.loads(data) if data else {}

            # Handle client requests
            if message.get("type") == "ping":
                # Keep-alive: update activity
                dashboard.touch_subscriber(subscriber_id)
                await websocket.send_json({"type": "pong"})

            elif message.get("type") == "request_update":
                # Client requests fresh metrics
                req_type = message.get("request_type", "summary")
                try:
                    if req_type == "summary":
                        metrics = dashboard.get_summary_stats()
                        await websocket.send_json({
                            "type": "metrics_updated",
                            "data": metrics.to_dict(),
                            "timestamp": datetime.utcnow().isoformat() + "Z",
                        })
                except Exception as e:
                    logger.warning(f"Error sending update to {subscriber_id}: {e}")

    except WebSocketDisconnect:
        logger.debug(f"WebSocket client {subscriber_id} disconnected")
        dashboard.unsubscribe(subscriber_id)
    except Exception as e:
        logger.error(f"WebSocket error for {subscriber_id}: {e}")
        try:
            await websocket.close(code=4001, reason=str(e))
        except:
            pass
        finally:
            dashboard.unsubscribe(subscriber_id)


# ============================================================================
# Utility Functions
# ============================================================================


# Optional: Health check endpoint
@router.get("/health", summary="Dashboard health check")
async def dashboard_health(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> Dict[str, Any]:
    """Health check for learning dashboard (test EventStore connectivity)."""
    tenant_id = rec.tenant_id
    dashboard = get_dashboard(tenant_id)

    try:
        # Test EventStore connectivity
        event_count = dashboard.event_store.count_events(tenant_id)
        subscriber_count = dashboard.get_subscriber_count()

        return {
            "status": "healthy",
            "event_count": event_count,
            "subscriber_count": subscriber_count,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        logger.error(f"Dashboard health check failed: {e}")
        return {
            "status": "degraded",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
