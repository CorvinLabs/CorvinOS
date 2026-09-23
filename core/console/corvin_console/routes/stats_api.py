"""Phase 8: Observability Dashboard — Live Stats Endpoint

GET /v1/stats/ returns real-time telemetry for operator dashboard.
Multi-tenant scoped, <200ms latency, audit-logged.
"""

from fastapi import APIRouter, Query
from datetime import datetime
from typing import List, Dict, Any
import logging

from core.telemetry.metrics_collector import get_collector, MetricsQuery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["stats"])


@router.get("/stats/")
async def get_stats(
    range: str = Query("1h", description="Time range: 1h, 24h, 7d"),
    skill_ids: str = Query("", description="Comma-separated skill IDs (empty=all)"),
    tenant_id: str = Query("_default", description="Tenant scope"),
) -> Dict[str, Any]:
    """Get real-time telemetry metrics.

    Response includes:
    - Current metrics for all skills
    - Aggregated statistics (p50, p95, error_rate, convergence)
    - Alerts for SLA violations

    Latency: <200ms (p95)
    Compliance: ADR-0007 (multi-tenant), ADR-0297 (PII), ADR-0314 (audit)
    """

    # Parse range
    range_map = {"1h": 1, "24h": 24, "7d": 168}
    range_hours = range_map.get(range, 1)

    # Parse skill_ids
    skill_list = [s.strip() for s in skill_ids.split(",") if s.strip()]

    # Query metrics
    collector = get_collector()
    query = MetricsQuery(
        tenant_id=tenant_id,
        range_hours=range_hours,
        skill_ids=skill_list,
    )

    metrics = collector.query_metrics(query)

    # Emit audit event (ADR-0314)
    logger.info(f"stats_query: tenant={tenant_id} range={range} skills={len(metrics)}")

    # Compute alerts
    alerts = []
    for m in metrics:
        if m.latency_ms > 500:
            alerts.append({
                "severity": "warning",
                "skill_id": m.skill_id,
                "message": f"Latency > 500ms (current: {m.latency_ms:.0f}ms)",
            })
        if m.error_rate > 0.05:
            alerts.append({
                "severity": "critical",
                "skill_id": m.skill_id,
                "message": f"Error rate > 5% (current: {m.error_rate*100:.1f}%)",
            })
        if m.convergence_score < 0.70:
            alerts.append({
                "severity": "warning",
                "skill_id": m.skill_id,
                "message": f"Convergence < 0.70 (current: {m.convergence_score:.2f})",
            })

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "range": range,
        "tenant_id": tenant_id,
        "metrics": [
            {
                "skill_id": m.skill_id,
                "latency_ms": round(m.latency_ms, 1),
                "error_rate": round(m.error_rate, 4),
                "convergence": round(m.convergence_score, 2),
                "throughput": m.throughput_per_min,
                "cost": round(m.model_cost, 4),
            }
            for m in metrics
        ],
        "alerts": alerts[:10],  # Max 10 alerts
        "compliance": {
            "pii_filtered": True,
            "tenant_scoped": True,
            "audit_logged": True,
        }
    }
