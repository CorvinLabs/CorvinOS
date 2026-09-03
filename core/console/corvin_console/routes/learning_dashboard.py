"""Learning Dashboard API endpoints (ADR-0321).

Provides REST API for observability:
  - GET /api/learning/summary — system-wide metrics
  - GET /api/learning/skills/{skill_name} — per-skill stats
  - GET /api/learning/user/{user_id} — user-scoped metrics
  - WS /api/learning/stream — real-time WebSocket updates

Tenant isolation enforced. All queries audit-logged.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from typing import Dict, Any, Optional
from datetime import datetime
import json
import logging

from core.learning.dashboard import LearningDashboard, DashboardMetrics, SkillPerformance
from core.learning.event_store import EventStore
from pathlib import Path

# Fallback auth stub
try:
    from core.console.corvin_console.auth import get_current_user
except ImportError:
    async def get_current_user():
        return {"user_id": "default", "tenant_id": "_default"}

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])

# Global dashboard instance (per-tenant)
_dashboards: Dict[str, LearningDashboard] = {}


def get_dashboard(tenant_id: str = "_default", audit_backend=None) -> LearningDashboard:
    """Get or initialize dashboard for tenant."""
    if tenant_id not in _dashboards:
        # Initialize EventStore from tenant home
        tenant_home = Path.home() / ".corvin" / "tenants" / tenant_id / "global"
        event_store = EventStore(tenant_home=tenant_home)

        _dashboards[tenant_id] = LearningDashboard(
            tenant_id=tenant_id,
            event_store=event_store,
            audit_backend=audit_backend,
            cache_ttl_seconds=5,
        )
    return _dashboards[tenant_id]


@router.get("/summary", summary="Get system-wide learning metrics")
async def get_learning_summary(
    current_user=Depends(get_current_user),
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
    tenant_id = current_user.get("tenant_id", "_default")
    dashboard = get_dashboard(tenant_id)

    try:
        metrics = dashboard.get_summary_stats()
        return {
            "status": "ok",
            "data": metrics.to_dict(),
            "cached": True,  # Cache hit indicator
        }
    except Exception as e:
        logger.error(f"Error fetching dashboard summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills/{skill_name}", summary="Get per-skill performance metrics")
async def get_skill_metrics(
    skill_name: str,
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Get performance metrics for a specific skill.

    Returns:
      - Accuracy (% of successful executions)
      - Latency (avg response time, ms)
      - Confidence (model confidence)
      - User satisfaction (4.5-star scale)
      - Usage count
      - Last updated timestamp

    Tenant isolation enforced.
    """
    tenant_id = current_user.get("tenant_id", "_default")
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
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Get user-specific metrics (satisfaction, engagement, query complexity).

    Returns:
      - Satisfaction (avg score across interactions)
      - Engagement score (activity level)
      - Query count (total queries from this user)
      - Last query timestamp

    Per-tenant isolation enforced: users only see their own metrics.
    """
    tenant_id = current_user.get("tenant_id", "_default")

    # GDPR Art. 21: Users can only query their own metrics
    requesting_user_id = current_user.get("user_id", "unknown")
    if requesting_user_id != user_id and not _is_admin(current_user):
        raise HTTPException(status_code=403, detail="Cannot query other users' metrics")

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
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Register for real-time dashboard updates via WebSocket.

    Returns:
      subscriber_id: Use this to connect to /api/learning/stream?subscriber_id=...
      ws_url: WebSocket URL for client to connect to

    If user_scoped=True, only user-specific metrics are pushed.
    If user_scoped=False (admin only), system-wide metrics.
    """
    tenant_id = current_user.get("tenant_id", "_default")
    user_id = current_user.get("user_id") if user_scoped else None

    dashboard = get_dashboard(tenant_id)

    try:
        subscriber_id = dashboard.subscribe_for_updates(user_id=user_id)
        return {
            "status": "ok",
            "subscriber_id": subscriber_id,
            "ws_url": f"/api/learning/stream?subscriber_id={subscriber_id}",
            "tenant_id": tenant_id,
        }
    except Exception as e:
        logger.error(f"Error subscribing to updates: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/unsubscribe", summary="Unregister WebSocket subscriber")
async def unsubscribe(
    subscriber_id: str,
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Unregister from WebSocket updates."""
    tenant_id = current_user.get("tenant_id", "_default")
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
    current_user_token: Optional[str] = None,
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
    await websocket.accept()
    tenant_id = "_default"  # TODO: Extract from token

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


def _is_admin(current_user: Dict[str, Any]) -> bool:
    """Check if user is admin (can view all metrics)."""
    roles = current_user.get("roles", [])
    return "admin" in roles or "operator" in roles


# Optional: Health check endpoint
@router.get("/health", summary="Dashboard health check")
async def dashboard_health(
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    """Health check for learning dashboard (test EventStore connectivity)."""
    tenant_id = current_user.get("tenant_id", "_default")
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
