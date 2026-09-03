"""VibeMetrics API Endpoints (Phase 2.K=4).

REST API for token measurement dashboarding.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Auth (adversarial review N-06): the REAL console session dependency —
# 401 without a live cookie; the tenant is ``rec.tenant_id`` from the
# authenticated SessionRecord. The previous version fell back to a NO-OP
# dependency returning an anonymous ``{"tenant_id": "default"}`` user.
from .. import auth as session_auth
from ..deps import require_session

from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.token_metrics_aggregator import TokenMetricsAggregator
from core.learning.token_baseline import ComparisonEngine
from core.learning.instance_registry import get_instance_registry


# ===== Request/Response Models =====

class MetricsSessionRequest(BaseModel):
    """Query metrics for a session."""
    session_id: str
    limit: int = 100


class MetricsTurnResponse(BaseModel):
    """Single turn's metrics."""
    turn_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    savings_percent: float
    task_type: Optional[str]
    outcome_quality: Optional[str]
    latency_ms: Optional[float]


class MetricsSummaryResponse(BaseModel):
    """Session summary statistics."""
    session_id: str
    timestamp: str
    turn_count: int
    total_tokens: int
    baseline_tokens: int
    savings_tokens: int
    savings_percent: float
    avg_tokens_per_turn: int
    is_significant: bool
    confidence: float


class MetricsDetailResponse(BaseModel):
    """Complete metrics detail (summary + turns)."""
    session_id: str
    timestamp: str
    summary: MetricsSummaryResponse
    turns: list[MetricsTurnResponse]
    by_task_type: dict
    subsystems: dict


class MetricsExportResponse(BaseModel):
    """Exportable metrics data (CSV-ready)."""
    headers: list[str]
    rows: list[list]


class ComparisonSummaryResponse(BaseModel):
    """Vibe vs Native comparison aggregate."""
    comparison_count: int
    avg_savings_percent: float
    high_confidence_count: int
    high_confidence_pct: float
    total_baseline_tokens: int
    total_vibe_tokens: int
    total_savings_tokens: int


# ===== Router Setup =====

# Initialize module-level singletons (once at import time)
from pathlib import Path

from core.learning.event_emitter import EventEmitter
from core.learning.event_store import EventStore as _LearningEventStore
from core.learning.token_metrics_db import TokenMetricsDB


def _default_tenant_dir() -> Path:
    try:
        from forge.tenants import tenant_home  # type: ignore[import-not-found]
        return Path(tenant_home("_default"))
    except ImportError:
        return Path.home() / ".corvin" / "tenants" / "_default"


# EventEmitter(event_store, queue_size) — ``EventEmitter()`` raised TypeError at import.
_emitter = EventEmitter(_LearningEventStore(_default_tenant_dir()))
# DB under the tenant home (honours CORVIN_HOME) — never a bare ~/.corvin.
_db = TokenMetricsDB(_default_tenant_dir() / "token_metrics.db")
_store = TokenMetricsStore(_emitter, db=_db)
_comparison_engine = ComparisonEngine()
_aggregator = TokenMetricsAggregator(_store, _comparison_engine)


def get_metrics_dependencies():
    """Return shared module-level singleton dependencies.

    These are initialized once at module import and reused across all requests.
    """
    return {
        "store": _store,
        "aggregator": _aggregator,
        "comparison_engine": _comparison_engine,
    }


router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/session/{session_id}", response_model=MetricsDetailResponse)
async def get_session_metrics(
    session_id: str,
    limit: int = 100,
    rec: session_auth.SessionRecord = Depends(require_session),
):
    """Get complete metrics for a session.

    Args:
        session_id: Session identifier
        limit: Max turns to return

    Returns:
        Complete metrics detail (summary + turn list)
    """
    deps = get_metrics_dependencies()
    aggregator = deps["aggregator"]

    dashboard_data = aggregator.get_session_dashboard_data(session_id)
    metrics_list = aggregator.get_session_metrics(session_id)[:limit]

    summary = MetricsSummaryResponse(
        session_id=session_id,
        timestamp=datetime.utcnow().isoformat(),
        turn_count=dashboard_data["summary"]["turn_count"],
        total_tokens=dashboard_data["summary"]["total_tokens"],
        baseline_tokens=dashboard_data["summary"]["baseline_tokens"],
        savings_tokens=dashboard_data["summary"]["savings_tokens"],
        savings_percent=dashboard_data["summary"]["savings_percent"],
        avg_tokens_per_turn=dashboard_data["summary"]["avg_tokens_per_turn"],
        is_significant=dashboard_data["is_significant"],
        confidence=dashboard_data["confidence"],
    )

    turns = [
        MetricsTurnResponse(
            turn_id=m["turn_id"],
            input_tokens=m.get("input_tokens", 0),
            output_tokens=m.get("output_tokens", 0),
            total_tokens=m.get("total_tokens", 0),
            savings_percent=m.get("savings_percent", 0),
            task_type=m.get("task_type"),
            outcome_quality=m.get("outcome_quality"),
            latency_ms=m.get("latency_ms"),
        )
        for m in metrics_list
    ]

    return MetricsDetailResponse(
        session_id=session_id,
        timestamp=datetime.utcnow().isoformat(),
        summary=summary,
        turns=turns,
        by_task_type=dashboard_data["by_task_type"],
        subsystems=dashboard_data["subsystems"],
    )


@router.get("/session/{session_id}/summary", response_model=MetricsSummaryResponse)
async def get_session_summary(
    session_id: str,
    rec: session_auth.SessionRecord = Depends(require_session),
):
    """Get summary stats only (lightweight endpoint).

    Args:
        session_id: Session identifier

    Returns:
        Summary statistics
    """
    deps = get_metrics_dependencies()
    aggregator = deps["aggregator"]

    dashboard_data = aggregator.get_session_dashboard_data(session_id)

    return MetricsSummaryResponse(
        session_id=session_id,
        timestamp=datetime.utcnow().isoformat(),
        turn_count=dashboard_data["summary"]["turn_count"],
        total_tokens=dashboard_data["summary"]["total_tokens"],
        baseline_tokens=dashboard_data["summary"]["baseline_tokens"],
        savings_tokens=dashboard_data["summary"]["savings_tokens"],
        savings_percent=dashboard_data["summary"]["savings_percent"],
        avg_tokens_per_turn=dashboard_data["summary"]["avg_tokens_per_turn"],
        is_significant=dashboard_data["is_significant"],
        confidence=dashboard_data["confidence"],
    )


@router.get("/stats", response_model=dict)
async def get_cluster_stats(rec: session_auth.SessionRecord = Depends(require_session)):
    """Get cluster-wide statistics (all instances, all sessions).

    Returns:
        Aggregate stats across entire cluster
    """
    registry = get_instance_registry()
    cluster_stats = registry.aggregate_stats()

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "cluster": cluster_stats,
        "summary": {
            "instance_count": cluster_stats["instance_count"],
            "total_turns": cluster_stats["total_turns"],
            "total_tokens": cluster_stats["total_tokens"],
            "avg_tokens_per_turn": cluster_stats["avg_tokens_per_turn"],
            "avg_savings_percent": cluster_stats["avg_savings_percent"],
        },
    }


@router.post("/session/{session_id}/export", response_model=MetricsExportResponse)
async def export_session_metrics(
    session_id: str,
    format: str = "csv",
    rec: session_auth.SessionRecord = Depends(require_session),
):
    """Export session metrics in specified format.

    Args:
        session_id: Session identifier
        format: "csv" or "json"

    Returns:
        Exportable metrics data
    """
    deps = get_metrics_dependencies()
    aggregator = deps["aggregator"]

    metrics_list = aggregator.get_session_metrics(session_id)

    headers = [
        "turn_id",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "savings_percent",
        "task_type",
        "outcome_quality",
        "latency_ms",
    ]

    rows = []
    for m in metrics_list:
        rows.append([
            m.get("turn_id", ""),
            str(m.get("input_tokens", 0)),
            str(m.get("output_tokens", 0)),
            str(m.get("total_tokens", 0)),
            str(m.get("savings_percent", 0)),
            m.get("task_type", ""),
            m.get("outcome_quality", ""),
            str(m.get("latency_ms", 0)),
        ])

    return MetricsExportResponse(headers=headers, rows=rows)


@router.get("/comparison/summary", response_model=ComparisonSummaryResponse)
async def get_comparison_summary(rec: session_auth.SessionRecord = Depends(require_session)):
    """Get Vibe vs Native comparison summary.

    Returns:
        Comparison statistics across all comparisons
    """
    deps = get_metrics_dependencies()
    comparison_engine = deps["comparison_engine"]

    comp_data = comparison_engine.aggregate_comparisons()

    return ComparisonSummaryResponse(
        comparison_count=comp_data["comparison_count"],
        avg_savings_percent=comp_data["avg_savings_percent"],
        high_confidence_count=comp_data["high_confidence_count"],
        high_confidence_pct=comp_data["high_confidence_pct"],
        total_baseline_tokens=comp_data["total_baseline_tokens"],
        total_vibe_tokens=comp_data["total_vibe_tokens"],
        total_savings_tokens=comp_data["total_savings_tokens"],
    )
