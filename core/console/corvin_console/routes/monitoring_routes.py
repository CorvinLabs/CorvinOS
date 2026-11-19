"""Production Monitoring Dashboard Routes (ADR-0906) — NOT BACKED BY DATA.

The five endpoints below are mounted under ``/v1/console/monitoring`` and
answer with an EMPTY result plus ``available: false``. No metrics backend is
wired to them on this build.

Until 2026-11-19 every one of them returned invented values presented as
measurements: a constant health score of 87.5 with 168.25 h uptime, twelve
"forged" skills, three OS skills with made-up latency percentiles and error
texts, two "active" alerts created relative to the request time, and a
synthetic upward trend line. None of it was read from anywhere. A console is
a production surface: a route without a data source says so and returns
nothing; it never returns sample data.

Wire format
-----------

Every response carries ``available`` (false), ``reason``, ``timestamp`` and
``tenant_id`` (from the authenticated session), plus the empty payload of its
kind:

GET /v1/console/monitoring/health
  → ``health_score``/``status``/``uptime_hours``/``last_check``: null

GET /v1/console/monitoring/autonomous-stats
  → every counter and average: null

GET /v1/console/monitoring/skill-performance
  → ``skills``: []

GET /v1/console/monitoring/alerts
  → ``active_alerts``: [], ``recent_alerts``: [], ``total_active``: 0

GET /v1/console/monitoring/metrics/timeseries?metric={name}&days={7}
  → ``data_points``: [] (the metric name and day range are still validated:
    an unknown metric is a 400)

All endpoints require a valid session and are read-only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import require_session
from .. import auth as session_auth

log = logging.getLogger(__name__)

router = APIRouter()

#: The one sentence every endpoint here returns as ``reason``.
UNAVAILABLE_REASON = "Monitoring metrics are not available on this build."

_TIMESERIES_METRICS = ("confidence", "latency_p95_ms", "error_rate")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unavailable(tenant_id: str, **empty: Any) -> dict[str, Any]:
    """The honest envelope: nothing measured, said explicitly."""
    return {
        "available": False,
        "reason": UNAVAILABLE_REASON,
        **empty,
        "timestamp": _iso(_now()),
        "tenant_id": tenant_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="System health score (not available on this build)",
)
def get_health(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """No health score is computed on this build: ``available: false``."""
    return _unavailable(
        rec.tenant_id,
        health_score=None, status="unknown", uptime_hours=None, last_check=None,
    )


@router.get(
    "/autonomous-stats",
    status_code=status.HTTP_200_OK,
    summary="Autonomous Forge statistics (not available on this build)",
)
def get_autonomous_stats(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """No forge statistics are collected on this build: ``available: false``."""
    return _unavailable(
        rec.tenant_id,
        skills_forged=None, skills_approved=None, skills_deferred=None,
        skills_active=None, success_rate=None, avg_optimization_confidence=None,
        avg_latency_ms=None, avg_error_rate=None, last_fork_timestamp=None,
    )


@router.get(
    "/skill-performance",
    status_code=status.HTTP_200_OK,
    summary="Per-skill performance metrics (not available on this build)",
)
def get_skill_performance(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """No per-skill metrics are collected on this build: an empty list."""
    return _unavailable(rec.tenant_id, skills=[])


@router.get(
    "/alerts",
    status_code=status.HTTP_200_OK,
    summary="Active and recent alerts (not available on this build)",
)
def get_alerts(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """No alert rules are evaluated on this build: empty lists."""
    return _unavailable(
        rec.tenant_id, active_alerts=[], recent_alerts=[], total_active=0,
    )


@router.get(
    "/metrics/timeseries",
    status_code=status.HTTP_200_OK,
    summary="Historical time series (not available on this build)",
)
def get_timeseries(
    metric: Annotated[str, Query(..., description="Metric name (confidence, latency_p95_ms, error_rate)")],
    # The default goes on the parameter, never inside Query() in an Annotated:
    # FastAPI asserts on that at app-BUILD time, so it does not fail this route —
    # it fails the whole `import corvin_console`, which removes /console and every
    # /v1/console/* route from the gateway (ADR-0015 opt-in mount).
    days: Annotated[int, Query(ge=1, le=30, description="Number of days of history")] = 7,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)] = None,
) -> dict[str, Any]:
    """No history is stored on this build: zero data points for the window.

    The parameters are validated as before (unknown metric → 400, days outside
    1-30 → 422), so a caller's mistake is not hidden behind the empty answer.
    """
    if metric not in _TIMESERIES_METRICS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid metric: {metric}. Must be one of {list(_TIMESERIES_METRICS)}",
        )
    window_end = _now()
    return _unavailable(
        rec.tenant_id,
        metric_name=metric,
        data_points=[],
        window_start=_iso(window_end - timedelta(days=days)),
        window_end=_iso(window_end),
        aggregation_interval_sec=3600,
    )
