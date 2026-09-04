"""L5 Metrics API Endpoints — Phase 5: Live Deployment Monitoring

REST endpoints for real-time L5 health status and alerting.

Endpoints:
- GET /v1/metrics/l5/status — Current health snapshot
- GET /v1/metrics/l5/timeseries — Historical metrics
- GET /v1/metrics/l5/alerts — Active alerts
- POST /v1/metrics/l5/alerts/{alert_id}/acknowledge — Acknowledge alert
- POST /v1/metrics/l5/alerts/{alert_id}/resolve — Resolve alert

ADR-0588: L5 Deployment Monitoring
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from threading import RLock
from .. import auth as session_auth
from ..deps import require_session
from core.learning.monitoring_l5 import (
    L5MonitoringSystem,
    L5HealthSnapshot,
    Alert,
    GateHealthStatus,
)


def _validate_tenant_access(rec, requested_tenant_id: str) -> None:
    """
    CRITICAL FIX #4: Validate that authenticated session matches requested tenant.
    Raises 403 if session tenant doesn't match requested tenant.
    """
    if not hasattr(rec, 'tenant_id') or not rec.tenant_id:
        raise HTTPException(status_code=401, detail="Session not properly authenticated")

    if rec.tenant_id != requested_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot access tenant {requested_tenant_id} from session {rec.tenant_id}"
        )

# ============================================================================
# Request/Response Models
# ============================================================================

class GateHealthStatusResponse(BaseModel):
    """Health status for a single gate."""
    gate_name: str
    is_healthy: bool
    latency_p50_ms: Optional[float] = None
    latency_p95_ms: Optional[float] = None
    latency_p99_ms: Optional[float] = None
    avg_latency_ms: Optional[float] = None
    error_rate_pct: float = 0.0
    pending_count: int = 0
    sla_breaches: int = 0
    last_check_timestamp: str = ""


class L5HealthSnapshotResponse(BaseModel):
    """Overall L5 health snapshot."""
    timestamp: str
    all_healthy: bool
    gates: dict[str, GateHealthStatusResponse]
    total_pending: int = 0
    auto_approval_rate_pct: float = 0.0
    rejection_rate_pct: float = 0.0
    config_apply_success_rate_pct: float = 0.0
    avg_operator_latency_ms: Optional[float] = None
    sla_status: str = "OK"
    alerts: list[str] = []


class AlertResponse(BaseModel):
    """Single alert."""
    alert_id: str
    severity: str
    message: str
    gate_name: Optional[str] = None
    skill_id: Optional[str] = None
    timestamp: str = ""
    is_acknowledged: bool = False


class AlertAcknowledgeRequest(BaseModel):
    """Request to acknowledge an alert."""
    tenant_id: str = "_default"


class AlertResolveRequest(BaseModel):
    """Request to resolve an alert."""
    tenant_id: str = "_default"


class TimeseriesDataResponse(BaseModel):
    """Historical timeseries data."""
    start_time: str
    end_time: str
    datapoints: list[dict]


# ============================================================================
# Global Monitoring System Instances (Per-Tenant)
# ============================================================================

# CRITICAL FIX #3: Per-tenant monitoring systems instead of global singleton
_monitoring_systems: Dict[str, L5MonitoringSystem] = {}
_monitoring_lock = RLock()


def get_monitoring_system(tenant_id: str = "_default", audit_backend=None) -> L5MonitoringSystem:
    """Get or create the L5 monitoring system for a specific tenant."""
    from threading import RLock
    global _monitoring_systems, _monitoring_lock

    if not tenant_id:
        raise ValueError("tenant_id cannot be empty")

    with _monitoring_lock:
        if tenant_id not in _monitoring_systems:
            _monitoring_systems[tenant_id] = L5MonitoringSystem(
                audit_backend, tenant_id=tenant_id
            )
        return _monitoring_systems[tenant_id]


# ============================================================================
# Routes
# ============================================================================

router = APIRouter()


@router.get("/v1/metrics/l5/status", response_model=L5HealthSnapshotResponse)
async def get_l5_health_status(
    tenant_id: str = Query("_default"),
    rec=Depends(require_session),
) -> L5HealthSnapshotResponse:
    """
    Get current L5 health status.

    Returns:
        L5HealthSnapshotResponse with gate health, metrics, and alerts
    """
    try:
        # CRITICAL FIX #4: Validate tenant access
        _validate_tenant_access(rec, tenant_id)

        monitoring = get_monitoring_system(tenant_id=tenant_id)
        snapshot = monitoring.get_health_status()
        return L5HealthSnapshotResponse(
            timestamp=snapshot.timestamp,
            all_healthy=snapshot.all_healthy,
            gates={
                k: GateHealthStatusResponse(
                    gate_name=v.gate_name,
                    is_healthy=v.is_healthy,
                    latency_p50_ms=v.latency_p50_ms,
                    latency_p95_ms=v.latency_p95_ms,
                    latency_p99_ms=v.latency_p99_ms,
                    avg_latency_ms=v.avg_latency_ms,
                    error_rate_pct=v.error_rate_pct,
                    pending_count=v.pending_count,
                    sla_breaches=v.sla_breaches,
                    last_check_timestamp=v.last_check_timestamp,
                )
                for k, v in snapshot.gates.items()
            },
            total_pending=snapshot.total_pending,
            auto_approval_rate_pct=snapshot.auto_approval_rate_pct,
            rejection_rate_pct=snapshot.rejection_rate_pct,
            config_apply_success_rate_pct=snapshot.config_apply_success_rate_pct,
            avg_operator_latency_ms=snapshot.avg_operator_latency_ms,
            sla_status=snapshot.sla_status,
            alerts=snapshot.alerts,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch health status: {str(e)}")


@router.get("/v1/metrics/l5/timeseries", response_model=TimeseriesDataResponse)
async def get_l5_timeseries(
    start: str = Query(..., description="Start time (ISO format)"),
    end: str = Query(..., description="End time (ISO format)"),
    tenant_id: str = Query("_default"),
    rec=Depends(require_session),
) -> TimeseriesDataResponse:
    """
    Get historical L5 metrics timeseries.

    Args:
        start: Start time (ISO 8601 format)
        end: End time (ISO 8601 format)

    Returns:
        TimeseriesDataResponse with historical datapoints
    """
    try:
        # CRITICAL FIX #4: Validate tenant access
        _validate_tenant_access(rec, tenant_id)

        monitoring = get_monitoring_system(tenant_id=tenant_id)
        data = monitoring.get_timeseries_data(start, end)
        return TimeseriesDataResponse(
            start_time=data["start_time"],
            end_time=data["end_time"],
            datapoints=data["datapoints"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch timeseries: {str(e)}")


@router.get("/v1/metrics/l5/alerts", response_model=list[AlertResponse])
async def get_l5_alerts(
    tenant_id: str = Query("_default"),
    rec=Depends(require_session),
) -> list[AlertResponse]:
    """
    Get active L5 alerts.

    Returns:
        List of active AlertResponse objects
    """
    try:
        # CRITICAL FIX #4: Validate tenant access
        _validate_tenant_access(rec, tenant_id)

        monitoring = get_monitoring_system(tenant_id=tenant_id)
        alerts = monitoring.get_active_alerts()
        return [
            AlertResponse(
                alert_id=a["alert_id"],
                severity=a["severity"],
                message=a["message"],
                gate_name=a.get("gate_name"),
                skill_id=a.get("skill_id"),
                timestamp=a["timestamp"],
                is_acknowledged=a["is_acknowledged"],
            )
            for a in alerts
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch alerts: {str(e)}")


@router.post("/v1/metrics/l5/alerts/{alert_id}/acknowledge")
async def acknowledge_l5_alert(
    alert_id: str,
    req: AlertAcknowledgeRequest,
    rec=Depends(require_session),
) -> dict:
    """
    Acknowledge an L5 alert.

    Args:
        alert_id: Alert ID to acknowledge
        req: Request with tenant_id

    Returns:
        Success status
    """
    try:
        # BUG FIX #9: Validate tenant access before mutation
        _validate_tenant_access(rec, req.tenant_id)

        monitoring = get_monitoring_system(tenant_id=req.tenant_id)
        success = monitoring.acknowledge_alert(alert_id)
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"status": "acknowledged", "alert_id": alert_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to acknowledge alert: {str(e)}")


@router.post("/v1/metrics/l5/alerts/{alert_id}/resolve")
async def resolve_l5_alert(
    alert_id: str,
    req: AlertResolveRequest,
    rec=Depends(require_session),
) -> dict:
    """
    Resolve (archive) an L5 alert.

    Args:
        alert_id: Alert ID to resolve
        req: Request with tenant_id

    Returns:
        Success status
    """
    try:
        # BUG FIX #9: Validate tenant access before mutation
        _validate_tenant_access(rec, req.tenant_id)

        monitoring = get_monitoring_system(tenant_id=req.tenant_id)
        success = monitoring.resolve_alert(alert_id)
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"status": "resolved", "alert_id": alert_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resolve alert: {str(e)}")
