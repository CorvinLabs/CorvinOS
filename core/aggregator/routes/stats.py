"""Stats Aggregator Routes (ADR-0638)

Public endpoints for accessing aggregated learning metrics across all tenant instances.

Endpoints:
  - GET  /v1/stats                    → global 9D metrics summary
  - GET  /v1/stats/instances          → list all instances + their metrics
  - GET  /v1/stats/history?window=7d  → time-series historical data

All endpoints return JSON with UTF-8 encoding.
No authentication required (public data).
Tenant isolation: responses never leak per-tenant data in public endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel

from core.aggregator.metrics_server import MetricsServer, get_metrics_server

router = APIRouter(prefix="/stats", tags=["stats-aggregator"])


# ============================================================================
# Response Models
# ============================================================================


class LossComponentsResponse(BaseModel):
    """Tier 1 (Core) loss components."""
    routing: float
    confidence: float
    feedback: float
    attention: float
    latency: float
    diversity: float


class InfrastructureComponentsResponse(BaseModel):
    """Tier 2 (Infrastructure) loss components."""
    memory: float
    skills: float
    plugins: float


class TenantStatsResponse(BaseModel):
    """Per-tenant metrics snapshot."""
    tenant_id: str
    timestamp: str
    status: str  # "no_data" | "collecting" | "learning" | "error"
    event_count: int
    last_event_time: Optional[str]

    # Loss metrics
    loss_total: float
    loss_core: float
    loss_infra: float
    loss_meta: float

    # Component breakdown
    components_tier1: LossComponentsResponse
    components_tier2: InfrastructureComponentsResponse


class GlobalStatsResponse(BaseModel):
    """Global aggregated metrics."""
    timestamp: str
    instance_count: int

    # Global statistics (across all instances)
    loss_total: Dict[str, float]  # mean, median, min, max, stddev
    loss_core: float
    loss_infra: float
    loss_meta: float

    # Component averages (Tier 1)
    components_tier1_mean: LossComponentsResponse

    # Component averages (Tier 2)
    components_tier2_mean: InfrastructureComponentsResponse

    # Health summary
    instances_healthy: int
    instances_degraded: int
    instances_error: int

    # Total events across all instances
    total_events: int

    # Metadata
    collection_interval_sec: int


class HistoryPointResponse(BaseModel):
    """Single point in time-series history."""
    timestamp: str
    loss_total_mean: float
    loss_total_median: float
    instance_count: int
    instances_healthy: int
    total_events: int


class HistoryResponse(BaseModel):
    """Time-series historical data."""
    window_days: int
    points: List[HistoryPointResponse]


class HealthResponse(BaseModel):
    """Collector health status."""
    running: bool
    last_collection_time: Optional[str]
    collection_interval_sec: int
    instances_cached: int
    has_error: bool
    error_message: Optional[str]


# ============================================================================
# Dependency injection
# ============================================================================


async def _get_metrics_server() -> MetricsServer:
    """Dependency: get the metrics server instance."""
    server = get_metrics_server()
    if not server:
        raise HTTPException(status_code=503, detail="Metrics server not initialized")
    return server


# ============================================================================
# REST Endpoints
# ============================================================================


@router.get("/", response_model=GlobalStatsResponse)
async def get_global_stats(
    server: MetricsServer = Depends(_get_metrics_server),
) -> GlobalStatsResponse:
    """Get global aggregated metrics across all instances.

    Returns:
      - Overall loss statistics (mean, median, min, max, stddev)
      - Per-tier averages (Tier 1 core loops, Tier 2 infrastructure)
      - Health summary (number of healthy/degraded/error instances)
      - Total event count across all instances
    """
    metrics = server.get_latest_global_metrics()
    if not metrics:
        # No metrics collected yet; return defaults
        return GlobalStatsResponse(
            timestamp=datetime.utcnow().isoformat(),
            instance_count=0,
            loss_total={
                "mean": 0.0,
                "median": 0.0,
                "min": 0.0,
                "max": 0.0,
                "stddev": 0.0,
            },
            loss_core=0.0,
            loss_infra=0.0,
            loss_meta=0.0,
            components_tier1_mean=LossComponentsResponse(
                routing=0.0,
                confidence=0.0,
                feedback=0.0,
                attention=0.0,
                latency=0.0,
                diversity=0.0,
            ),
            components_tier2_mean=InfrastructureComponentsResponse(
                memory=0.0,
                skills=0.0,
                plugins=0.0,
            ),
            instances_healthy=0,
            instances_degraded=0,
            instances_error=0,
            total_events=0,
            collection_interval_sec=server.collection_interval_sec,
        )

    return GlobalStatsResponse(
        timestamp=metrics.timestamp,
        instance_count=metrics.instance_count,
        loss_total={
            "mean": metrics.loss_total_mean,
            "median": metrics.loss_total_median,
            "min": metrics.loss_total_min,
            "max": metrics.loss_total_max,
            "stddev": metrics.loss_total_stddev,
        },
        loss_core=metrics.loss_core_mean,
        loss_infra=metrics.loss_infra_mean,
        loss_meta=metrics.loss_meta_mean,
        components_tier1_mean=LossComponentsResponse(
            routing=metrics.loss_routing_mean,
            confidence=metrics.loss_confidence_mean,
            feedback=metrics.loss_feedback_mean,
            attention=metrics.loss_attention_mean,
            latency=metrics.loss_latency_mean,
            diversity=metrics.loss_diversity_mean,
        ),
        components_tier2_mean=InfrastructureComponentsResponse(
            memory=metrics.loss_memory_mean,
            skills=metrics.loss_skills_mean,
            plugins=metrics.loss_plugins_mean,
        ),
        instances_healthy=metrics.instances_healthy,
        instances_degraded=metrics.instances_degraded,
        instances_error=metrics.instances_error,
        total_events=metrics.total_events,
        collection_interval_sec=server.collection_interval_sec,
    )


@router.get("/instances", response_model=List[TenantStatsResponse])
async def list_instances(
    server: MetricsServer = Depends(_get_metrics_server),
) -> List[TenantStatsResponse]:
    """List all instances with their current metrics.

    Each instance is identified by its tenant_id (e.g., "_default", "tenant_a").

    Returns:
      - List of TenantStatsResponse objects
      - Sorted by tenant_id for consistency
    """
    tenant_metrics_dict = server.get_all_tenant_metrics()

    results = []
    for tenant_id in sorted(tenant_metrics_dict.keys()):
        metrics = tenant_metrics_dict[tenant_id]
        results.append(TenantStatsResponse(
            tenant_id=metrics.tenant_id,
            timestamp=metrics.timestamp,
            status=metrics.status,
            event_count=metrics.event_count,
            last_event_time=metrics.last_event_time,
            loss_total=metrics.loss_total,
            loss_core=metrics.loss_core,
            loss_infra=metrics.loss_infra,
            loss_meta=metrics.loss_meta,
            components_tier1=LossComponentsResponse(
                routing=metrics.loss_routing,
                confidence=metrics.loss_confidence,
                feedback=metrics.loss_feedback,
                attention=metrics.loss_attention,
                latency=metrics.loss_latency,
                diversity=metrics.loss_diversity,
            ),
            components_tier2=InfrastructureComponentsResponse(
                memory=metrics.loss_memory,
                skills=metrics.loss_skills,
                plugins=metrics.loss_plugins,
            ),
        ))

    return results


@router.get("/history", response_model=HistoryResponse)
async def get_history(
    window: str = Query("7d", regex="^([0-9]+(h|d))$"),
    server: MetricsServer = Depends(_get_metrics_server),
) -> HistoryResponse:
    """Get historical aggregated metrics over a time window.

    Args:
        window: Time window as "Xd" (days) or "Xh" (hours)
                Examples: "7d", "24h", "1d"
                Default: "7d"

    Returns:
        Time-series data with points chronologically ordered
    """
    # Parse window
    if window.endswith("d"):
        days = int(window[:-1])
        hours = 0
    elif window.endswith("h"):
        hours = int(window[:-1])
        days = 0
    else:
        raise HTTPException(status_code=400, detail="Window must be in format '7d' or '24h'")

    # Query history
    days_to_query = max(1, days if days > 0 else (hours + 23) // 24)
    history_metrics = server.collector.query_global_metrics_history(
        days=days_to_query,
        limit=10000
    )

    # Convert to response format
    points = [
        HistoryPointResponse(
            timestamp=m.timestamp,
            loss_total_mean=m.loss_total_mean,
            loss_total_median=m.loss_total_median,
            instance_count=m.instance_count,
            instances_healthy=m.instances_healthy,
            total_events=m.total_events,
        )
        for m in history_metrics
    ]

    return HistoryResponse(
        window_days=days_to_query,
        points=points,
    )


@router.get("/health", response_model=HealthResponse)
async def get_collector_health(
    server: MetricsServer = Depends(_get_metrics_server),
) -> HealthResponse:
    """Get the collector's health status.

    Used for monitoring and debugging collector issues.

    Returns:
        Health status including uptime, error state, and cache size
    """
    health = server.get_health_status()
    return HealthResponse(**health)
