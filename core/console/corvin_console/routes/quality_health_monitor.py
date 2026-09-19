"""Phase C: Real-Time Health Monitoring API

Exposes endpoints for the Quality Gates dashboard:
  GET /v1/console/quality/health/status     — current health status (GREEN/YELLOW/RED)
  GET /v1/console/quality/health/metrics    — latest metrics snapshot
  GET /v1/console/quality/health/history    — metric history (24h, 7d)
  GET /v1/console/quality/health/alerts     — recent alerts
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, Dict, Any
import json
from pathlib import Path

from .. import _bootstrap
from ..deps import require_session

router = APIRouter(prefix="/quality/health", tags=["quality-health-monitoring"])


def _home() -> Path:
    return Path(_bootstrap.forge_paths.corvin_home())


@router.get("/status")
async def get_health_status(session: Dict[str, Any] = Depends(require_session)) -> Dict[str, Any]:
    """
    Get current health status (GREEN/YELLOW/RED).

    Returns:
    {
      "status": "GREEN" | "YELLOW" | "RED",
      "p99_latency_ms": 290.5,
      "error_rate_percent": 0.07,
      "cpu_percent": 65.2,
      "memory_percent": 58.3,
      "trend_latency": "stable" | "improving" | "degrading",
      "trend_error_rate": "stable" | "improving" | "degrading",
      "alert_count": 0,
      "alerts": [...]
    }
    """
    try:
        from core.quality.health_monitor_realtime import get_monitor
        monitor = get_monitor()
        status = monitor.get_health_status()

        return {
            "status": status.overall_status,
            "timestamp": status.timestamp,
            "p99_latency_ms": status.last_snapshot.p99_latency_ms,
            "error_rate_percent": status.last_snapshot.error_rate_percent,
            "cpu_percent": status.last_snapshot.cpu_percent,
            "memory_percent": status.last_snapshot.memory_percent,
            "throughput_ops_sec": status.last_snapshot.throughput_ops_sec,
            "trend_latency": status.trend_p99_latency,
            "trend_error_rate": status.trend_error_rate,
            "trend_throughput": status.trend_throughput,
            "alert_count": status.alert_count,
            "alerts": status.alerts,
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Health monitor not initialized")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Health check error: {str(e)}")


@router.get("/metrics")
async def get_latest_metrics(session: Dict[str, Any] = Depends(require_session)) -> Dict[str, Any]:
    """Get latest metric snapshot."""
    try:
        home = _home()
        metrics_path = home / "metrics" / "health" / "latest_health.json"

        if not metrics_path.exists():
            return {
                "message": "No metrics collected yet",
                "status": "initializing"
            }

        with open(metrics_path) as f:
            data = json.load(f)

        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Metrics fetch error: {str(e)}")


@router.get("/history")
async def get_metric_history(
    hours: Optional[int] = Query(24, description="Hours of history (1-168)"),
    session: Dict[str, Any] = Depends(require_session),
) -> Dict[str, Any]:
    """
    Get metric history for charting.

    Returns: list of snapshots with p50/p95/p99 latency, throughput, error rate, CPU, memory
    """
    try:
        if hours < 1 or hours > 168:
            raise HTTPException(status_code=400, detail="Hours must be 1-168")

        from core.quality.health_monitor_realtime import get_monitor
        monitor = get_monitor()
        history = monitor.get_metric_history(hours=hours)

        return {
            "period_hours": hours,
            "snapshot_count": history["count"],
            "snapshots": history["snapshots"],
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Health monitor not initialized")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"History fetch error: {str(e)}")


@router.get("/alerts")
async def get_recent_alerts(
    limit: Optional[int] = Query(50, description="Max alerts to return"),
    session: Dict[str, Any] = Depends(require_session),
) -> Dict[str, Any]:
    """Get recent alert log."""
    try:
        from core.quality.health_monitor_realtime import get_monitor
        monitor = get_monitor()
        alerts = monitor.get_error_log(limit=limit)

        return {
            "alert_count": len(alerts),
            "alerts": alerts,
        }
    except ImportError:
        raise HTTPException(status_code=503, detail="Health monitor not initialized")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Alert fetch error: {str(e)}")


@router.get("/quality-gates")
async def get_quality_gates_report(session: Dict[str, Any] = Depends(require_session)) -> Dict[str, Any]:
    """Get latest quality gates test report."""
    try:
        home = _home()
        gates_path = home / "metrics" / "quality_gates" / "latest_quality_gates.json"

        if not gates_path.exists():
            return {
                "message": "No quality gates report yet",
                "status": "not_run"
            }

        with open(gates_path) as f:
            report = json.load(f)

        return {
            "status": report.get("overall_status"),
            "timestamp": report.get("timestamp"),
            "gate_results": report.get("gate_results", []),
            "summary": report.get("summary", {}),
            "recommendations": report.get("recommendations", []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Quality gates fetch error: {str(e)}")


@router.get("/optimizer")
async def get_optimizer_status(session: Dict[str, Any] = Depends(require_session)) -> Dict[str, Any]:
    """Get auto-optimizer status and recent optimizations."""
    try:
        from core.quality.auto_optimizer import AutoOptimizer
        optimizer = AutoOptimizer()

        return {
            "total_proposals": len(optimizer.proposals),
            "applied_count": sum(1 for r in optimizer.results if r.status == "APPLIED"),
            "blocked_count": sum(1 for r in optimizer.results if r.status == "BLOCKED"),
            "recent_results": [
                {
                    "proposal_id": r.proposal_id,
                    "status": r.status,
                    "improvement_percent": r.improvement_percent,
                }
                for r in optimizer.results[-10:]
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimizer status error: {str(e)}")
