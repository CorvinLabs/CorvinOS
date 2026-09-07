"""Learning Metrics API Routes (ADR-0635)

Phase 5: Dashboard Architecture — Real-Time Visualization + WebSocket

  - GET  /v1/console/learning/metrics/current — real status (EventStore)
  - GET  /v1/console/learning/metrics/history — real bucketed time series
  - POST /v1/console/learning/metrics/export  — the tenant's learning events, inline (JSONL/CSV)
  - WS   /v1/console/learning/metrics/stream  — pushes the real status every 10 s

All numbers come from ``routes.learning.learning_status`` /
``learning_series`` — the same computation the ``/learning/status`` and
``/learning/metrics`` routes answer with — so the dashboard can never show a
figure the store does not contain. Until 2026-09-07 this module answered with
synthetic constants (``loss_total=0.00425``, ``convergence_percent=87.5``),
was mounted under a DOUBLED prefix (``/v1/console/v1/console/learning/metrics``,
F-L4), handed out a fake ``download_url`` on export, and its WebSocket took the
tenant from a query parameter without any authentication (F-L2/F-L3).

Tenant isolation: REST from ``require_session``; the WebSocket authenticates
the console session cookie (``auth.load_session``) and derives the tenant from
the session record — closes 1008 without one.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .. import auth as session_auth
from ..deps import require_csrf, require_session
from .learning import (
    LearningStatusResponse,
    MetricsResponse,
    _WINDOWS,
    _event_store,
    _event_time,
    learning_series,
    learning_status,
)

logger = logging.getLogger(__name__)
# Relative: the console router is mounted at /v1/console (standalone.py).
router = APIRouter(prefix="/learning/metrics", tags=["learning-metrics"])

#: Seconds between WebSocket pushes.
STREAM_INTERVAL_S = 10.0


class CurrentMetricsResponse(BaseModel):
    """Latest learning-loop state (real)."""
    timestamp: str
    metrics: LearningStatusResponse
    status: str  # "no_data" | "collecting" | "learning"


class StreamMessage(BaseModel):
    """WebSocket stream message."""
    type: str  # "metrics"
    data: Dict[str, Any]
    timestamp: str


class ExportRequest(BaseModel):
    """Export request."""
    format: str  # "json" | "csv"
    window: str  # "1h" | "6h" | "24h" | "custom"
    start_date: Optional[str] = None  # ISO 8601 if window="custom"
    end_date: Optional[str] = None


# ============================================================================
# REST Endpoints
# ============================================================================


@router.get("/current", response_model=CurrentMetricsResponse)
async def get_current_metrics(session = Depends(require_session)) -> CurrentMetricsResponse:
    """Latest learning metrics for the caller's tenant (computed from the EventStore)."""
    status = learning_status(session.tenant_id)
    return CurrentMetricsResponse(timestamp=status.timestamp, metrics=status, status=status.status)


@router.get("/history", response_model=MetricsResponse)
async def get_metrics_history(
    window: str = Query("1h"),
    limit: int = Query(1000, ge=1, le=10000),
    session = Depends(require_session),
) -> MetricsResponse:
    """Bucketed time series of the tenant's learning events over ``window``."""
    return learning_series(session.tenant_id, window, buckets=min(limit, 96))


# ============================================================================
# Export (Phase 5, ADR-0637) — the data itself, inline; no download tokens
# ============================================================================

_EXPORT_FIELDS = ("event_id", "event_type", "skill_id", "timestamp", "audit_ref", "lom")


def _export_window(request: ExportRequest) -> tuple[datetime, datetime]:
    end = datetime.utcnow()
    if request.window in _WINDOWS:
        return end - _WINDOWS[request.window], end
    if request.window != "custom":
        raise HTTPException(status_code=400, detail="Window must be '1h', '6h', '24h', or 'custom'")
    if not request.start_date:
        raise HTTPException(status_code=400, detail="start_date is required for window='custom'")
    try:
        start = datetime.fromisoformat(request.start_date.rstrip("Z"))
        end = datetime.fromisoformat(request.end_date.rstrip("Z")) if request.end_date else end
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date/end_date must be ISO 8601")
    if end <= start:
        raise HTTPException(status_code=400, detail="end_date must be after start_date")
    return start, end


@router.post("/export")
async def export_metrics(request: ExportRequest, session = Depends(require_csrf)) -> Response:
    """Export the tenant's learning events in the window as JSONL or CSV (GDPR Art. 20).

    Content-free rows: ids, type, skill, timestamp, chain reference, LoM — the
    same fields the core chain carries. The body IS the export; there is no
    download link to forge or to leak.
    """
    if request.format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="Format must be 'json' or 'csv'")
    start, end = _export_window(request)

    store = _event_store(session.tenant_id)
    rows = []
    for event in store.query_events(
        session.tenant_id, since=start.strftime("%Y-%m-%d"), until=end.strftime("%Y-%m-%d"), limit=100000
    ):
        if not (start <= _event_time(event.timestamp) <= end):
            continue
        rows.append(
            {
                "event_id": event.event_id,
                "event_type": event.event_type.value,
                "skill_id": event.skill_id,
                "timestamp": event.timestamp,
                "audit_ref": event.audit_ref,
                "lom": event.lom,
            }
        )

    stamp = end.strftime("%Y%m%dT%H%M%SZ")
    if request.format == "json":
        body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
        media, ext = "application/x-ndjson", "jsonl"
    else:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        body, media, ext = buf.getvalue(), "text/csv", "csv"

    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="learning-events-{session.tenant_id}-{stamp}.{ext}"',
            "X-Rows-Exported": str(len(rows)),
            "X-Export-Start": start.isoformat() + "Z",
            "X-Export-End": end.isoformat() + "Z",
        },
    )


# ============================================================================
# WebSocket Endpoint (Real-Time Push) — session-cookie authenticated
# ============================================================================

# Connected clients: {tenant_id: set of WebSocket connections}
_active_connections: Dict[str, set[WebSocket]] = {}


def _authenticate_websocket(websocket: WebSocket) -> Optional[session_auth.SessionRecord]:
    """The live console session behind the cookie, or None (same rule as ``require_session``)."""
    sid = websocket.cookies.get(session_auth.COOKIE_NAME)
    if not sid:
        return None
    return session_auth.load_session(sid)


@router.websocket("/stream")
async def websocket_metrics_stream(websocket: WebSocket):
    """Real-time push of the tenant's learning status.

    Authentication: the ``corvin_console_sid`` cookie must resolve to a live
    session; the tenant is the session's tenant. No cookie / expired session →
    close 1008 (policy violation) before accept. There is no ``tenant_id``
    parameter — a client must not be able to pick a tenant.
    """
    rec = _authenticate_websocket(websocket)
    if rec is None:
        await websocket.close(code=1008, reason="no session")
        return
    tenant_id = rec.tenant_id

    await websocket.accept()
    _active_connections.setdefault(tenant_id, set()).add(websocket)

    send_task = asyncio.create_task(_send_metrics_loop(websocket, tenant_id))
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.debug("learning metrics stream closed (tenant %s)", tenant_id)
    finally:
        send_task.cancel()
        _active_connections[tenant_id].discard(websocket)


async def _send_metrics_loop(websocket: WebSocket, tenant_id: str) -> None:
    """Push the real status every ``STREAM_INTERVAL_S`` seconds (first push immediately)."""
    try:
        while True:
            status = learning_status(tenant_id)
            message = StreamMessage(
                type="metrics", data=status.model_dump(), timestamp=datetime.utcnow().isoformat() + "Z"
            )
            await websocket.send_json(message.model_dump())
            await asyncio.sleep(STREAM_INTERVAL_S)
    except asyncio.CancelledError:
        pass  # Expected when connection closes
    except Exception as e:  # noqa: BLE001 — a failed push ends the stream, never fakes a frame
        logger.warning("learning metrics stream aborted for tenant %s: %s", tenant_id, type(e).__name__)
        try:
            await websocket.close(code=1011, reason="metrics unavailable")
        except Exception:  # noqa: BLE001
            pass
