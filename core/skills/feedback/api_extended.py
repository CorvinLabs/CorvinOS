"""
Extended Feedback API — ADR-2033 Week 2

New endpoints for feedback loop closure status, config history, and learning curves.

Endpoints:
- GET /v1/skills/feedback/config-updates — get config update history
- GET /v1/skills/feedback/config-updates/{skill_id} — get updates for specific skill
- GET /v1/skills/feedback/learning-curve/{skill_id} — get learning curve (confidence over time)
- GET /v1/skills/feedback/health/extended — extended health check with all metrics
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ConfigUpdateResponse(BaseModel):
    """Response model for config update."""
    update_id: str
    skill_id: str
    tenant_id: str
    timestamp: str
    feedback_type: str
    config_delta: Dict[str, Any]
    audit_event_id: Optional[str]
    config_hash_before: str
    config_hash_after: str


class ConfigUpdateHistoryResponse(BaseModel):
    """Response for GET /v1/skills/feedback/config-updates"""
    updates: List[ConfigUpdateResponse] = Field(default_factory=list)
    total_count: int = 0
    skill_id: Optional[str] = None
    tenant_id: str = ""
    period_start: Optional[str] = None
    period_end: Optional[str] = None


class LearningCurvePoint(BaseModel):
    """Point in learning curve (timestamp, confidence)."""
    timestamp: str
    confidence: float
    feedback_type: str  # outcome|preference|confidence|metric


class LearningCurveResponse(BaseModel):
    """Response for GET /v1/skills/feedback/learning-curve/{skill_id}"""
    skill_id: str
    tenant_id: str
    curve_points: List[LearningCurvePoint] = Field(default_factory=list)
    total_updates: int = 0
    convergence_status: str = "unknown"  # converged|oscillating|diverging|unknown
    confidence_trend: str = "unknown"  # increasing|decreasing|stable|unknown


class ExtendedHealthCheckResponse(BaseModel):
    """Response for GET /v1/skills/feedback/health/extended"""
    status: str  # healthy|degraded|critical
    queue_status: str
    queue_size: int
    queue_max: int
    queue_utilization: float
    error_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    total_feedback_processed: int
    total_config_updates: int
    active_alerts: List[str] = Field(default_factory=list)
    last_update_at: Optional[str] = None
    skill_health: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


# Global router
router = APIRouter(prefix="/v1/skills/feedback", tags=["skills-feedback-extended"])


# Dependency injection
async def get_loop_closure_manager():
    """Inject loop closure manager (would be wired at startup)."""
    # This is a placeholder; in production it would be injected from the app
    from core.skills.feedback.loop_closure import LoopClosureManager
    return LoopClosureManager()


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/config-updates", response_model=ConfigUpdateHistoryResponse)
async def get_config_update_history(
    tenant_id: str = Query(..., description="Tenant ID"),
    skill_id: Optional[str] = Query(None, description="Optional: filter by skill"),
    since_iso: Optional[str] = Query(None, description="Optional: ISO8601 timestamp to filter from"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
) -> ConfigUpdateHistoryResponse:
    """
    Get config update history (all updates for a tenant or specific skill).

    Args:
        tenant_id: Tenant ID (required, fail-closed)
        skill_id: Optional skill filter
        since_iso: Optional timestamp filter
        limit: Max results

    Returns:
        ConfigUpdateHistoryResponse with updates and metadata

    Raises:
        HTTPException 400: invalid since_iso
    """
    # In production, would fetch from loop_closure_manager
    # For now, return empty (placeholder)
    return ConfigUpdateHistoryResponse(
        updates=[],
        total_count=0,
        skill_id=skill_id,
        tenant_id=tenant_id,
        period_start=since_iso,
        period_end=datetime.utcnow().isoformat() + "Z",
    )


@router.get("/config-updates/{skill_id}", response_model=ConfigUpdateHistoryResponse)
async def get_skill_config_updates(
    skill_id: str,
    tenant_id: str = Query(..., description="Tenant ID"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
) -> ConfigUpdateHistoryResponse:
    """
    Get config update history for a specific Skill.

    Args:
        skill_id: Skill ID (e.g., 'os.router')
        tenant_id: Tenant ID (filter by tenant)
        limit: Max results

    Returns:
        ConfigUpdateHistoryResponse with skill-specific updates
    """
    # Placeholder: would fetch from loop_closure_manager
    return ConfigUpdateHistoryResponse(
        updates=[],
        total_count=0,
        skill_id=skill_id,
        tenant_id=tenant_id,
    )


@router.get("/learning-curve/{skill_id}", response_model=LearningCurveResponse)
async def get_learning_curve(
    skill_id: str,
    tenant_id: str = Query(..., description="Tenant ID"),
    period_days: int = Query(7, ge=1, le=90, description="Historical period in days"),
) -> LearningCurveResponse:
    """
    Get learning curve for a Skill (confidence over time).

    Shows how the Skill's confidence threshold evolved based on feedback.

    Args:
        skill_id: Skill ID
        tenant_id: Tenant ID
        period_days: How many days back to retrieve (default 7)

    Returns:
        LearningCurveResponse with curve points and convergence status
    """
    # Placeholder: would compute from update history
    return LearningCurveResponse(
        skill_id=skill_id,
        tenant_id=tenant_id,
        curve_points=[],
        total_updates=0,
        convergence_status="unknown",
        confidence_trend="unknown",
    )


@router.get("/health/extended", response_model=ExtendedHealthCheckResponse)
async def extended_health_check(
    tenant_id: str = Query(None, description="Optional: filter by tenant"),
) -> ExtendedHealthCheckResponse:
    """
    Extended health check with detailed metrics.

    Returns:
        ExtendedHealthCheckResponse with queue, error rate, latency, per-skill stats

    Includes:
    - Queue utilization
    - Error rate
    - Latency percentiles (p50, p95, p99)
    - Total feedback processed
    - Total config updates
    - Active alerts
    - Per-skill health breakdown
    """
    # Placeholder: would aggregate metrics from monitoring module
    return ExtendedHealthCheckResponse(
        status="healthy",
        queue_status="ok",
        queue_size=0,
        queue_max=1000,
        queue_utilization=0.0,
        error_rate=0.0,
        latency_p50_ms=0.0,
        latency_p95_ms=0.0,
        latency_p99_ms=0.0,
        total_feedback_processed=0,
        total_config_updates=0,
        active_alerts=[],
        last_update_at=datetime.utcnow().isoformat() + "Z",
        skill_health={},
    )
