"""
Feedback Integration API — ADR-2033

FastAPI routes for unified feedback ingestion, history retrieval, and config management.

Endpoints:
- POST /v1/skills/feedback/ — submit feedback
- GET /v1/skills/feedback/history — retrieve history
- PUT /v1/skills/feedback/config — operator override
- GET /v1/skills/feedback/metrics — queue depth, latency stats
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

from core.skills.feedback.schema import (
    FeedbackType, FeedbackEvent, OutcomeFeedback, PreferenceFeedback,
    ConfidenceFeedback, MetricFeedback, feedback_to_audit_event
)
from core.skills.feedback.consumer import FeedbackConsumer, SkillConfig


logger = logging.getLogger(__name__)

# Global consumer instance (initialized at startup)
_feedback_consumer: Optional[FeedbackConsumer] = None


def get_feedback_consumer() -> FeedbackConsumer:
    """Dependency injection: get global FeedbackConsumer."""
    if _feedback_consumer is None:
        raise RuntimeError("FeedbackConsumer not initialized (call init_feedback_consumer first)")
    return _feedback_consumer


def init_feedback_consumer(consumer: FeedbackConsumer):
    """Initialize the global FeedbackConsumer (called at app startup)."""
    global _feedback_consumer
    _feedback_consumer = consumer


# ============================================================================
# Pydantic Models (API request/response)
# ============================================================================

class FeedbackRequest(BaseModel):
    """Request body for POST /v1/skills/feedback/"""
    skill_id: str = Field(..., description="e.g., 'os.delegation_router'")
    feedback_type: FeedbackType = Field(..., description="outcome|preference|confidence|metric")
    signal: float | bool | str = Field(..., description="Feedback signal")
    reason: Optional[str] = Field(None, description="Optional reason (max 100 chars)")


class FeedbackResponse(BaseModel):
    """Response for POST /v1/skills/feedback/"""
    feedback_id: str = Field(..., description="UUID of stored feedback")
    stored_at: str = Field(..., description="ISO8601 timestamp")
    audit_event_id: Optional[str] = Field(None, description="Audit chain event ID")


class FeedbackHistoryItem(BaseModel):
    """Item in feedback history."""
    feedback_id: str
    skill_id: str
    feedback_type: str  # str to avoid enum serialization issues
    signal: float | bool | str
    reason: Optional[str]
    timestamp: str


class FeedbackHistoryResponse(BaseModel):
    """Response for GET /v1/skills/feedback/history"""
    feedback_events: List[FeedbackHistoryItem] = Field(default_factory=list)
    total_count: int = Field(default=0)
    skill_id: str = Field(default="")
    tenant_id: str = Field(default="")
    query_since: Optional[str] = Field(None)
    query_limit: int = Field(default=100)


class SkillConfigUpdate(BaseModel):
    """Request body for PUT /v1/skills/feedback/config"""
    skill_id: str = Field(..., description="e.g., 'os.delegation_router'")
    confidence_threshold: Optional[float] = Field(None, description="Threshold [0.0, 1.0]")
    learning_enabled: Optional[bool] = Field(None, description="Enable/disable learning")


class SkillConfigResponse(BaseModel):
    """Response for PUT /v1/skills/feedback/config"""
    skill_id: str
    config_hash: str
    applied_at: str
    changes: Dict[str, Any]


class FeedbackMetricsResponse(BaseModel):
    """Response for GET /v1/skills/feedback/metrics"""
    queue_size: int = Field(default=0, description="Current items in queue")
    queue_max_size: int = Field(default=1000)
    latency_p99_ms: float = Field(default=0.0)
    total_feedback_processed: int = Field(default=0)
    total_config_updates: int = Field(default=0)


# ============================================================================
# Router Setup
# ============================================================================

router = APIRouter(prefix="/v1/skills/feedback", tags=["skills-feedback"])

# Telemetry (internal)
_telemetry = {
    "total_feedback_processed": 0,
    "total_config_updates": 0,
    "latency_samples": [],  # [ms, ms, ms...]
}


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/", response_model=FeedbackResponse)
async def submit_feedback(
    request: FeedbackRequest,
    tenant_id: str = Query(..., description="Tenant ID"),
    consumer: FeedbackConsumer = Depends(get_feedback_consumer),
) -> FeedbackResponse:
    """
    Submit feedback for a Skill.

    Args:
        request: FeedbackRequest with skill_id, feedback_type, signal
        tenant_id: Tenant ID (from query param; fail-closed if missing)

    Returns:
        FeedbackResponse with feedback_id and stored_at

    Raises:
        HTTPException 400: validation error
        HTTPException 500: queue full, rejected
    """
    start_time = time.time()

    try:
        # Create feedback event (constructor validates)
        if request.feedback_type == FeedbackType.OUTCOME:
            event = OutcomeFeedback(
                skill_id=request.skill_id,
                tenant_id=tenant_id,
                signal=request.signal,
                reason=request.reason,
            )
        elif request.feedback_type == FeedbackType.PREFERENCE:
            event = PreferenceFeedback(
                skill_id=request.skill_id,
                tenant_id=tenant_id,
                signal=request.signal,
                reason=request.reason,
            )
        elif request.feedback_type == FeedbackType.CONFIDENCE:
            event = ConfidenceFeedback(
                skill_id=request.skill_id,
                tenant_id=tenant_id,
                signal=request.signal,
                reason=request.reason,
            )
        elif request.feedback_type == FeedbackType.METRIC:
            event = MetricFeedback(
                skill_id=request.skill_id,
                tenant_id=tenant_id,
                signal=request.signal,
                reason=request.reason,
            )
        else:
            raise ValueError(f"Unknown feedback_type: {request.feedback_type}")

    except ValueError as e:
        logger.warning(f"Feedback validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    # Submit to consumer (non-blocking queue)
    success, message = await consumer.submit_feedback(event)

    if not success:
        logger.error(f"Feedback rejected: {message}")
        raise HTTPException(status_code=500, detail=message)

    # Record telemetry
    latency_ms = (time.time() - start_time) * 1000
    _telemetry["total_feedback_processed"] += 1
    _telemetry["latency_samples"].append(latency_ms)
    if len(_telemetry["latency_samples"]) > 1000:
        _telemetry["latency_samples"] = _telemetry["latency_samples"][-1000:]

    return FeedbackResponse(
        feedback_id=event.feedback_id,
        stored_at=datetime.utcnow().isoformat() + "Z",
        audit_event_id=None,  # Would be filled by audit backend
    )


@router.get("/history", response_model=FeedbackHistoryResponse)
async def get_feedback_history(
    skill_id: str = Query(..., description="Skill ID to filter"),
    tenant_id: str = Query(..., description="Tenant ID"),
    since_iso: Optional[str] = Query(None, description="ISO8601 timestamp to filter from"),
    limit: int = Query(100, ge=1, le=1000, description="Max results (default 100)"),
    consumer: FeedbackConsumer = Depends(get_feedback_consumer),
) -> FeedbackHistoryResponse:
    """
    Retrieve feedback history for a Skill (tenant-filtered).

    Args:
        skill_id: Skill to filter by
        tenant_id: Tenant ID (fail-closed)
        since_iso: Optional ISO8601 timestamp filter
        limit: Max results to return

    Returns:
        FeedbackHistoryResponse with events and total_count
    """
    # Get history from consumer
    events = consumer.get_feedback_history(skill_id, tenant_id, limit=limit)

    # Filter by timestamp if provided
    if since_iso:
        try:
            since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            events = [e for e in events if datetime.fromisoformat(e.timestamp.replace("Z", "+00:00")) >= since_dt]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid since_iso: {since_iso}")

    # Convert to response model
    items = [
        FeedbackHistoryItem(
            feedback_id=e.feedback_id,
            skill_id=e.skill_id,
            feedback_type=e.feedback_type.value,
            signal=e.signal,
            reason=e.reason,
            timestamp=e.timestamp,
        )
        for e in events
    ]

    return FeedbackHistoryResponse(
        feedback_events=items,
        total_count=len(items),
        skill_id=skill_id,
        tenant_id=tenant_id,
        query_since=since_iso,
        query_limit=limit,
    )


@router.put("/config", response_model=SkillConfigResponse)
async def update_skill_config(
    request: SkillConfigUpdate,
    tenant_id: str = Query(..., description="Tenant ID"),
    consumer: FeedbackConsumer = Depends(get_feedback_consumer),
) -> SkillConfigResponse:
    """
    Operator override: manually update Skill config.

    Args:
        request: SkillConfigUpdate with skill_id and new config params
        tenant_id: Tenant ID (audit trail will include this)

    Returns:
        SkillConfigResponse with applied config and timestamp

    Raises:
        HTTPException 404: Skill not found
    """
    skill_id = request.skill_id
    config = consumer.get_skill_config(skill_id)

    if config is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    # Apply updates
    changes = {}
    if request.confidence_threshold is not None:
        if not (0.0 <= request.confidence_threshold <= 1.0):
            raise HTTPException(status_code=400, detail="confidence_threshold must be in [0.0, 1.0]")
        config.confidence_threshold = request.confidence_threshold
        changes["confidence_threshold"] = request.confidence_threshold

    if request.learning_enabled is not None:
        config.learning_enabled = request.learning_enabled
        changes["learning_enabled"] = request.learning_enabled

    # Record telemetry
    _telemetry["total_config_updates"] += 1

    return SkillConfigResponse(
        skill_id=skill_id,
        config_hash="sha256_placeholder",  # Would be filled by config serialization
        applied_at=datetime.utcnow().isoformat() + "Z",
        changes=changes,
    )


@router.get("/metrics", response_model=FeedbackMetricsResponse)
async def get_feedback_metrics(
    consumer: FeedbackConsumer = Depends(get_feedback_consumer),
) -> FeedbackMetricsResponse:
    """
    Get feedback system metrics (queue depth, latency, etc.).

    Returns:
        FeedbackMetricsResponse
    """
    # Calculate p99 latency
    latency_p99 = 0.0
    if _telemetry["latency_samples"]:
        sorted_latencies = sorted(_telemetry["latency_samples"])
        p99_index = max(0, int(len(sorted_latencies) * 0.99) - 1)
        latency_p99 = sorted_latencies[p99_index]

    return FeedbackMetricsResponse(
        queue_size=consumer.queue.qsize(),
        queue_max_size=consumer.max_queue_size,
        latency_p99_ms=latency_p99,
        total_feedback_processed=_telemetry["total_feedback_processed"],
        total_config_updates=_telemetry["total_config_updates"],
    )


@router.get("/health")
async def health_check(
    consumer: FeedbackConsumer = Depends(get_feedback_consumer),
) -> Dict[str, Any]:
    """
    Health check: queue depth and latency p99.

    Returns:
        {status, queue_size, latency_p99_ms}
    """
    latency_p99 = 0.0
    if _telemetry["latency_samples"]:
        sorted_latencies = sorted(_telemetry["latency_samples"])
        p99_index = max(0, int(len(sorted_latencies) * 0.99) - 1)
        latency_p99 = sorted_latencies[p99_index]

    queue_size = consumer.queue.qsize()
    queue_max = consumer.max_queue_size

    # Fail if queue > 80%
    status = "healthy" if queue_size < (queue_max * 0.8) else "degraded"

    return {
        "status": status,
        "queue_size": queue_size,
        "queue_max": queue_max,
        "latency_p99_ms": latency_p99,
    }
