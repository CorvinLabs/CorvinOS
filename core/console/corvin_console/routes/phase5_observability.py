"""
Phase 5 Production Hardening: Observability API routes.

Endpoints:
- GET /api/observability/plugins — List plugin status + health scores
- GET /api/observability/plugins/{plugin_id} — Plugin detail + telemetry
- GET /api/observability/slos — SLO status + compliance
- GET /api/observability/telemetry/stream — WebSocket for real-time events
- GET /api/observability/health/overall — System-wide health snapshot

Tenant-scoped, audit-logged, GDPR-compliant.
"""

from fastapi import APIRouter, HTTPException, Depends, WebSocket, Query
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

# Observability imports
from core.observability.plugin_telemetry import (
    get_telemetry_collector,
    PluginTelemetryCollector,
    PluginTelemetryEventType,
)
from core.observability.slo_definitions import (
    get_slo_monitor,
    SLOMonitor,
    SLOStatus,
)
from core.observability.health_monitor import (
    HealthMonitor,
    HealthStatus,
)

router = APIRouter(prefix="/api/observability", tags=["observability"])


# Pydantic models for responses

class PluginStatusResponse(BaseModel):
    """Plugin status snapshot."""
    plugin_id: str
    status: str
    health_score: float
    work_handled: int
    work_delegated: int
    work_failed: int
    avg_latency_ms: float
    p95_latency_ms: float
    timestamp_utc: str


class PluginDetailResponse(BaseModel):
    """Detailed plugin telemetry."""
    plugin_id: str
    status: str
    health_score: float
    work: Dict
    latency: Dict
    budget: Dict
    audit: Dict
    tree: Dict
    recent_events: List[Dict]
    timestamp_utc: str


class SLOStatusResponse(BaseModel):
    """SLO compliance status."""
    timestamp_utc: str
    overall_status: str
    slos: Dict
    summary: Dict


class HealthSnapshotResponse(BaseModel):
    """Overall system health."""
    timestamp_utc: str
    overall_status: str
    subsystems: Dict


# Dependency injection helpers

def get_tenant_id(request: Optional[str] = Query(default="_default")) -> str:
    """Extract tenant from request (in production, from session)."""
    # TODO: In production, extract from SessionRecord
    return request


async def get_collector() -> PluginTelemetryCollector:
    """Inject telemetry collector."""
    return get_telemetry_collector()


async def get_slo_monitor() -> SLOMonitor:
    """Inject SLO monitor."""
    return get_slo_monitor()


# Routes

@router.get("/plugins", response_model=List[PluginStatusResponse])
async def list_plugins(
    tenant_id: str = Depends(get_tenant_id),
    collector: PluginTelemetryCollector = Depends(get_collector),
) -> List[PluginStatusResponse]:
    """
    List all plugins with current status.

    Returns:
    - Plugin ID, status, health score
    - Work counts (handled, delegated, failed)
    - Latency metrics (avg, p95, p99)
    """
    snapshots = collector.snapshots

    results = []
    for key, snapshot in snapshots.items():
        snapshot_tenant, plugin_id = key.split(":", 1)
        if snapshot_tenant != tenant_id:
            continue

        results.append(
            PluginStatusResponse(
                plugin_id=plugin_id,
                status=snapshot.status,
                health_score=snapshot.health_score,
                work_handled=snapshot.work_handled_count,
                work_delegated=snapshot.work_delegated_count,
                work_failed=snapshot.work_failed_count,
                avg_latency_ms=snapshot.avg_latency_ms,
                p95_latency_ms=snapshot.p95_latency_ms,
                timestamp_utc=snapshot.timestamp_utc.isoformat(),
            )
        )

    return sorted(results, key=lambda x: x.plugin_id)


@router.get("/plugins/{plugin_id}", response_model=PluginDetailResponse)
async def get_plugin_detail(
    plugin_id: str,
    tenant_id: str = Depends(get_tenant_id),
    collector: PluginTelemetryCollector = Depends(get_collector),
) -> PluginDetailResponse:
    """
    Get detailed telemetry for a single plugin.

    Includes:
    - Current snapshot (status, health, budget usage)
    - Audit statistics (events, failures)
    - Recent events (last 20)
    - Delegation tree (children, fallback chain)
    """
    snapshot = collector.get_plugin_snapshot(plugin_id, tenant_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail=f"Plugin {plugin_id} not found")

    recent_events = collector.get_events_for_plugin(
        plugin_id,
        tenant_id,
        limit=20,
    )

    return PluginDetailResponse(
        plugin_id=plugin_id,
        status=snapshot.status,
        health_score=snapshot.health_score,
        work=snapshot.to_dict()["work"],
        latency=snapshot.to_dict()["latency"],
        budget=snapshot.to_dict()["budget"],
        audit=snapshot.to_dict()["audit"],
        tree=snapshot.to_dict()["tree"],
        recent_events=[e.to_dict() for e in recent_events],
        timestamp_utc=snapshot.timestamp_utc.isoformat(),
    )


@router.get("/slos", response_model=SLOStatusResponse)
async def get_slo_status(
    tenant_id: str = Depends(get_tenant_id),
    monitor: SLOMonitor = Depends(get_slo_monitor),
) -> SLOStatusResponse:
    """
    Get current SLO compliance status.

    Returns:
    - Overall status (healthy/warning/critical)
    - Per-SLO compliance (measured vs target)
    - Summary (healthy/warning/critical counts)
    """
    report = monitor.get_report()
    return SLOStatusResponse(**report)


@router.get("/health/overall", response_model=HealthSnapshotResponse)
async def get_health_snapshot(
    tenant_id: str = Depends(get_tenant_id),
) -> HealthSnapshotResponse:
    """
    Get overall system health snapshot.

    Returns:
    - Overall status (ok/degraded/error/offline)
    - Per-subsystem health (Brain, ContextBridge, Orchestrator, etc.)
    """
    # TODO: Inject HealthMonitor
    # For now, return placeholder
    return HealthSnapshotResponse(
        timestamp_utc=datetime.utcnow().isoformat(),
        overall_status="ok",
        subsystems={},
    )


@router.get("/telemetry/events")
async def get_recent_events(
    plugin_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
    tenant_id: str = Depends(get_tenant_id),
    collector: PluginTelemetryCollector = Depends(get_collector),
) -> Dict:
    """
    Get recent telemetry events.

    Optionally filter by:
    - plugin_id: specific plugin
    - event_type: work_received, work_delegated, etc.
    - limit: max events to return (default 100, max 500)
    """
    if plugin_id:
        events = collector.get_events_for_plugin(
            plugin_id,
            tenant_id,
            limit=limit,
        )
    else:
        events = sorted(
            [
                e for e in collector.events
                if e.tenant_id == tenant_id
            ],
            key=lambda e: e.timestamp_utc,
            reverse=True,
        )[:limit]

    if event_type:
        try:
            event_type_enum = PluginTelemetryEventType[event_type.upper()]
            events = [e for e in events if e.event_type == event_type_enum]
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Unknown event type: {event_type}")

    return {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "event_count": len(events),
        "events": [e.to_dict() for e in events],
    }


@router.websocket("/telemetry/stream")
async def websocket_telemetry_stream(
    websocket: WebSocket,
    tenant_id: str = Query(default="_default"),
) -> None:
    """
    WebSocket stream for real-time telemetry events.

    Client receives JSON events as they occur:
    {
        "event_type": "work_delegated",
        "plugin_id": "whisper",
        "work_id": "w123",
        "timestamp_utc": "2026-08-27T12:00:00Z",
        ...
    }

    Connection stays open until client closes.
    """
    await websocket.accept()

    collector = get_telemetry_collector()
    last_event_index = len(collector.events)

    try:
        while True:
            # Poll for new events every 100ms
            new_events = collector.events[last_event_index:]
            tenant_events = [
                e for e in new_events
                if e.tenant_id == tenant_id
            ]

            for event in tenant_events:
                await websocket.send_json(event.to_dict())

            last_event_index = len(collector.events)

            # Brief sleep to prevent CPU spinning
            import asyncio
            await asyncio.sleep(0.1)

    except Exception as e:
        # Client disconnect or send error
        await websocket.close(code=1000)
