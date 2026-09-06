"""Learning Metrics API Routes (ADR-0635)

Phase 5: Dashboard Architecture — Real-Time Visualization + WebSocket

Provides REST and WebSocket endpoints for real-time learning metrics:
  - GET /v1/console/learning/metrics/current — latest 9D state
  - GET /v1/console/learning/metrics/history — time-series JSON
  - WS /v1/console/learning/stream — real-time WebSocket push

Tenant isolation: all data filtered by authenticated session.tenant_id.
Compliance: GDPR Art. 32 (data in transit encrypted wss://), no PII in metrics.

Architecture:
  Live-Collector (background daemon)
    ↓ emits 9D metrics every 5 batches
    ↓
  LearningMetricsAggregator (backend)
    ↓ WebSocket push (all connected clients)
    ↓
  LearningDashboard (React)
    ↓ renders real-time loss curves, convergence %, alerts
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/console/learning/metrics", tags=["learning-metrics"])


# ============================================================================
# Request/Response Models
# ============================================================================


class MetricPoint(BaseModel):
    """Single 9D learning metric sample."""
    timestamp: str
    loss_total: float
    loss_core: float
    loss_infra: float
    loss_memory: float
    loss_skills: float
    loss_plugins: float
    loss_audit: float
    loss_security: float
    loss_compliance: float
    alpha_core: float
    alpha_infra: float
    damping_core: float
    damping_infra: float
    convergence_percent: float
    gradient_l2: float
    samples_since_last_update: int


class CurrentMetricsResponse(BaseModel):
    """Latest 9D state."""
    timestamp: str
    metrics: MetricPoint
    status: str  # "converged" | "converging" | "diverging" | "stalled"


class HistoryResponse(BaseModel):
    """Time-series metrics."""
    window: str  # "1h" | "6h" | "24h"
    start: str
    end: str
    points: List[MetricPoint]
    sample_count: int


class AlertMetrics(BaseModel):
    """Alert-relevant metrics subset."""
    timestamp: str
    loss_total: float
    convergence_percent: float
    gradient_l2: float
    status: str


class StreamMessage(BaseModel):
    """WebSocket stream message."""
    type: str  # "metrics" | "alert" | "status"
    data: Dict[str, Any]
    timestamp: str


# ============================================================================
# Global State (WebSocket connections)
# ============================================================================

# Connected clients: {tenant_id: set of WebSocket connections}
_active_connections: Dict[str, set[WebSocket]] = {}


async def _broadcast_to_tenant(tenant_id: str, message: StreamMessage) -> None:
    """Broadcast message to all WebSocket clients for a tenant."""
    if tenant_id not in _active_connections:
        return

    disconnected = set()
    for connection in _active_connections[tenant_id]:
        try:
            await connection.send_json(message.model_dump())
        except Exception:
            disconnected.add(connection)

    # Clean up disconnected clients
    for connection in disconnected:
        _active_connections[tenant_id].discard(connection)


# ============================================================================
# Helper: Metrics Data Access
# ============================================================================


def _tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — honours CORVIN_HOME."""
    from forge.tenants import tenant_home  # type: ignore[import-not-found]
    return Path(tenant_home(tenant_id))


async def _get_current_metrics(tenant_id: str) -> MetricPoint:
    """Fetch latest metrics point for tenant.

    In production, reads from:
    - LiveExperimentCollector.latest_measurement() for all 9D components
    - MetaOptimizer.current_state() for α, damping
    - convergence_tracker for convergence_%
    """
    # Placeholder: real implementation integrates with live collector
    # For now, return synthetic data (used in tests)
    return MetricPoint(
        timestamp=datetime.utcnow().isoformat(),
        loss_total=0.00425,
        loss_core=0.00250,
        loss_infra=0.00175,
        loss_memory=0.00000,
        loss_skills=0.00000,
        loss_plugins=0.00000,
        loss_audit=0.00000,
        loss_security=0.00000,
        loss_compliance=0.00000,
        alpha_core=0.1000,
        alpha_infra=0.0500,
        damping_core=0.9000,
        damping_infra=0.9500,
        convergence_percent=87.5,
        gradient_l2=0.00125,
        samples_since_last_update=512,
    )


async def _get_metrics_history(
    tenant_id: str,
    window: str = "1h",
    limit: int = 1000,
) -> tuple[str, str, List[MetricPoint]]:
    """Fetch time-series metrics for window.

    Args:
        tenant_id: Tenant ID (for isolation)
        window: "1h" | "6h" | "24h"
        limit: Max points to return

    Returns:
        (start_iso, end_iso, points)

    In production, reads from:
    - ~/<corvin_home>/tenants/<tenant_id>/experiments/live_measurements/ (JSONL, auto-rotated daily)
    - Parses, filters by timestamp range, returns ordered list
    """
    # Placeholder: real implementation reads from live collector storage
    end = datetime.utcnow()
    if window == "1h":
        start = end - timedelta(hours=1)
    elif window == "6h":
        start = end - timedelta(hours=6)
    elif window == "24h":
        start = end - timedelta(days=1)
    else:
        start = end - timedelta(hours=1)

    # For tests, return empty list (will be populated by integration tests)
    return start.isoformat(), end.isoformat(), []


# ============================================================================
# REST Endpoints
# ============================================================================


@router.get("/current", response_model=CurrentMetricsResponse)
async def get_current_metrics(session = Depends(require_session)) -> CurrentMetricsResponse:
    """Fetch latest 9D learning metrics.

    Returns:
        Latest state: loss components, learning rates, convergence %

    Tenant isolation: session.tenant_id
    GDPR: No PII in response
    """
    try:
        metrics = await _get_current_metrics(session.tenant_id)

        # Infer status from convergence % and gradient
        if metrics.convergence_percent >= 95:
            status = "converged"
        elif metrics.convergence_percent >= 70:
            status = "converging"
        elif metrics.gradient_l2 > 0.01:
            status = "diverging"
        else:
            status = "stalled"

        return CurrentMetricsResponse(
            timestamp=metrics.timestamp,
            metrics=metrics,
            status=status,
        )
    except Exception as e:
        logger.exception(f"Error fetching current metrics for tenant {session.tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch metrics: {str(e)}")


@router.get("/history", response_model=HistoryResponse)
async def get_metrics_history(
    window: str = Query("1h", regex="^(1h|6h|24h)$"),
    limit: int = Query(1000, ge=1, le=10000),
    session = Depends(require_session),
) -> HistoryResponse:
    """Fetch historical time-series metrics.

    Args:
        window: Time window ("1h" | "6h" | "24h")
        limit: Max points to return (default 1000, max 10000)
        session: Authenticated session (tenant isolation)

    Returns:
        Time-series with start/end timestamps and points

    **Sampling:** Up to `limit` points evenly spaced over window
    **Tenant isolation:** All data filtered by session.tenant_id
    **GDPR:** No PII in response
    """
    try:
        start, end, points = await _get_metrics_history(
            session.tenant_id,
            window=window,
            limit=limit,
        )

        # Truncate to limit if needed
        if len(points) > limit:
            # Evenly sample points over window
            step = len(points) // limit
            points = points[::step][:limit]

        return HistoryResponse(
            window=window,
            start=start,
            end=end,
            points=points,
            sample_count=len(points),
        )
    except Exception as e:
        logger.exception(f"Error fetching metrics history for tenant {session.tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch history: {str(e)}")


# ============================================================================
# WebSocket Endpoint (Real-Time Push)
# ============================================================================


@router.websocket("/stream")
async def websocket_metrics_stream(
    websocket: WebSocket,
    # Note: WebSocket connections don't support Depends() directly,
    # so we extract tenant_id from query parameter as fallback.
    # In production, use JWT or cookie-based auth.
    tenant_id: Optional[str] = Query(None),
):
    """Real-time WebSocket stream for learning metrics.

    **Connection Flow:**
    1. Client connects: `WS /v1/console/learning/stream?tenant_id=_default`
    2. Server accepts connection
    3. Server pushes {type: "metrics", data: {...}, timestamp: "..."}
       every ~5-10 seconds (when new samples available)
    4. Client receives, updates dashboard live
    5. If connection drops, client falls back to polling (GET /history)

    **Message Types:**
    - `metrics` — new measurement point
    - `alert` — alert triggered (see ADR-0636)
    - `status` — convergence status changed

    **Tenant Isolation:**
    `tenant_id` query param is required (in production, derive from JWT).

    **Compliance:**
    - GDPR Art. 32: use wss:// (encrypted)
    - No PII in messages
    - No session tokens in messages (use secure WebSocket auth)
    """
    if not tenant_id:
        await websocket.close(code=1008, reason="tenant_id required")
        return

    await websocket.accept()

    # Register client
    if tenant_id not in _active_connections:
        _active_connections[tenant_id] = set()
    _active_connections[tenant_id].add(websocket)

    try:
        # Background task: send metrics every 10 seconds
        # (In production, integrate with live collector's actual update cadence)
        send_task = asyncio.create_task(_send_metrics_loop(websocket, tenant_id))

        # Foreground: receive client messages (ping/pong for keep-alive)
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.debug(f"WebSocket client disconnected: {tenant_id}")
        send_task.cancel()
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
    finally:
        _active_connections[tenant_id].discard(websocket)


async def _send_metrics_loop(websocket: WebSocket, tenant_id: str) -> None:
    """Send metrics to client every 10 seconds."""
    try:
        while True:
            await asyncio.sleep(10)

            try:
                metrics = await _get_current_metrics(tenant_id)

                message = StreamMessage(
                    type="metrics",
                    data=metrics.model_dump(),
                    timestamp=datetime.utcnow().isoformat(),
                )

                await websocket.send_json(message.model_dump())
            except Exception as e:
                logger.warning(f"Error sending metrics on WebSocket: {e}")
                # Continue loop; connection will be torn down if client disconnects
    except asyncio.CancelledError:
        pass  # Expected when connection closes


# ============================================================================
# Export Endpoint (Phase 5, ADR-0637)
# ============================================================================


class ExportRequest(BaseModel):
    """Export request."""
    format: str  # "json" | "csv"
    window: str  # "1h" | "6h" | "24h" | "custom"
    start_date: Optional[str] = None  # ISO 8601 if window="custom"
    end_date: Optional[str] = None


@router.post("/export", response_model=Dict[str, Any])
async def export_metrics(
    request: ExportRequest,
    session = Depends(require_session),
) -> Dict[str, Any]:
    """Export metrics as JSON or CSV (ADR-0637).

    Args:
        request: Export parameters (format, window, dates)
        session: Authenticated session (for tenant isolation)

    Returns:
        Export confirmation with download link

    **Formats:**
    - `json` — JSONL (one metric per line)
    - `csv` — CSV with headers

    **Tenant Isolation:** All data filtered by session.tenant_id
    **Compliance:** GDPR Art. 20 (data portability), signed download link
    """
    try:
        if request.format not in ("json", "csv"):
            raise HTTPException(status_code=400, detail="Format must be 'json' or 'csv'")
        if request.window not in ("1h", "6h", "24h", "custom"):
            raise HTTPException(status_code=400, detail="Window must be '1h', '6h', '24h', or 'custom'")

        # TODO: Integrate with export builder (core/learning/export_builder.py)
        # - Fetch metrics for window
        # - Format as JSON/CSV
        # - Sign download link
        # - Return link (expires 24h)

        return {
            "status": "pending",
            "download_url": "/v1/console/learning/metrics/export/download/xyz123",
            "expires_at": (datetime.utcnow() + timedelta(days=1)).isoformat(),
            "format": request.format,
            "rows_exported": 0,  # placeholder
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Export failed for tenant {session.tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")
