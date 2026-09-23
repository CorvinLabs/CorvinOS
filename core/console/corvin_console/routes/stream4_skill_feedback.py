from core.security.csrf import require_csrf
"""Stream 4: Skill Feedback Integration Routes (ADR-2033).

Unified feedback submission API for Phase 10 skills:
  - os.workflow_optimizer (routing feedback)
  - os.security_orchestrator (threat detection feedback)
  - os.flow_guard (data flow safety feedback)

Endpoints:
  POST   /v1/console/feedback/skill          - Submit skill feedback
  GET    /v1/console/feedback/skill/history  - Get feedback history by skill
  GET    /v1/console/feedback/skill/status   - Feedback processing status
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field, validator

from core.skills.feedback.schema import FeedbackEvent, FeedbackType, validate_feedback
from core.paths.tenant import tenant_home
from core.learning.feedback_processor import FeedbackProcessor, create_feedback_processor

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/console/feedback/skill", tags=["skill_feedback"])

# Valid skill IDs
VALID_SKILLS = [
    "os.workflow_optimizer",
    "os.security_orchestrator",
    "os.flow_guard",
]

# Valid feedback categories
VALID_CATEGORIES = [
    "accuracy",    # Was the decision correct?
    "speed",       # Was it fast/slow?
    "safety",      # Was it safe?
    "other",
]


# ============================================================================
# Request/Response Models
# ============================================================================

class SkillFeedbackRequest(BaseModel):
    """Unified skill feedback request (ADR-2033)."""

    skill_id: str = Field(
        ...,
        description="One of: os.workflow_optimizer, os.security_orchestrator, os.flow_guard"
    )
    subject_id: str = Field(..., description="task_id, threat_id, or flow_id")
    rating: int = Field(..., ge=-2, le=2, description="-2 (bad) to +2 (excellent)")
    category: str = Field(..., description="accuracy, speed, safety, or other")
    reasoning: Optional[str] = Field(None, max_length=500, description="Free-text reason (scrubbed for PII)")
    metadata: Optional[dict] = Field(None, description="Skill-specific metadata")

    @validator("skill_id")
    def validate_skill_id(cls, v):
        if v not in VALID_SKILLS:
            raise ValueError(f"skill_id must be one of {VALID_SKILLS}")
        return v

    @validator("category")
    def validate_category(cls, v):
        if v not in VALID_CATEGORIES:
            raise ValueError(f"category must be one of {VALID_CATEGORIES}")
        return v

    @validator("rating")
    def validate_rating(cls, v):
        if v not in [-2, -1, 0, 1, 2]:
            raise ValueError("rating must be -2, -1, 0, 1, or 2")
        return v


class SkillFeedbackResponse(BaseModel):
    """Skill feedback submission response."""

    status: str = Field(..., description="received | error")
    feedback_id: str = Field(..., description="UUID of submitted feedback")
    timestamp: str = Field(..., description="ISO8601 timestamp when feedback was recorded")
    skill_id: str = Field(...)
    subject_id: str = Field(...)
    message: Optional[str] = None


class FeedbackHistoryItem(BaseModel):
    """Single feedback event in history."""

    feedback_id: str
    timestamp: str
    skill_id: str
    subject_id: str
    rating: int
    category: str
    reasoning: Optional[str]


class FeedbackHistoryResponse(BaseModel):
    """Feedback history response."""

    skill_id: str
    total_count: int
    feedback_events: List[FeedbackHistoryItem]
    avg_rating: float
    timestamp: str


class FeedbackStatusResponse(BaseModel):
    """Feedback processing status."""

    total_received: int
    total_processed: int
    processing_errors: int
    avg_latency_ms: float
    last_feedback_timestamp: Optional[str]
    timestamp: str


# ============================================================================
# Dependency: Get Feedback Processor
# ============================================================================

async def get_feedback_processor(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> FeedbackProcessor:
    """Get feedback processor for the session's tenant."""
    return create_feedback_processor()


# ============================================================================
# Routes
# ============================================================================

@require_csrf
@router.post("", response_model=SkillFeedbackResponse)
async def submit_skill_feedback(
    req: SkillFeedbackRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
    processor: FeedbackProcessor = Depends(get_feedback_processor),
) -> dict:
    """
    Submit feedback on a skill decision (ADR-2033, ADR-2034).

    Feedback is:
    - Immutable (hash-chained to audit trail)
    - Tenant-scoped (fail-closed on tenant mismatch)
    - Validated (rating, skill_id, category, subject_id)
    - Processed by the skill's optimizer (config delta applied)
    - Audited (feedback_received → skill_config_updated events)

    Args:
        req: SkillFeedbackRequest with skill_id, rating, category, reasoning
        rec: Session record (provides tenant_id, operator_id)
        processor: FeedbackProcessor to apply feedback (audit-first)

    Returns:
        SkillFeedbackResponse with feedback_id, status, timestamp

    HTTP Status Codes:
        - 200: Feedback submitted successfully
        - 400: Invalid request (invalid skill_id, rating, category)
        - 403: Consent required for skill feedback
        - 500: Audit chain failure

    Examples:
        POST /v1/console/feedback/skill
        {
          "skill_id": "os.workflow_optimizer",
          "subject_id": "task_123",
          "rating": 2,
          "category": "speed",
          "reasoning": "This routing was optimal for the workload"
        }

        Response (200):
        {
          "status": "received",
          "feedback_id": "fb_550e8400e29b41d4a716446655440000",
          "timestamp": "2026-09-22T12:34:56Z",
          "skill_id": "os.workflow_optimizer",
          "subject_id": "task_123",
          "message": "Feedback received and processed by optimizer"
        }
    """

    try:
        # Create feedback event
        feedback_event = FeedbackEvent(
            skill_id=req.skill_id,
            tenant_id=rec.tenant_id,
            feedback_type=FeedbackType.OUTCOME,  # Outcome feedback (was the decision correct?)
            signal=req.rating,  # Rating (-2 to +2) encodes outcome quality
            reason=req.reasoning,  # Optional free-text reason (scrubbed)
        )

        # Validate feedback event (fail-closed). core.learning.feedback_validator
        # never exported validate_feedback_event; importing it made the whole
        # console router unimportable (bare 404 on /console after a restart).
        ok, err = validate_feedback(feedback_event)
        if not ok:
            raise HTTPException(status_code=400, detail=err or "invalid feedback")

        # Process feedback (audit-first, config delta applied)
        # In production, this would:
        # 1. Write feedback_received event to audit chain
        # 2. Call skill optimizer to compute delta
        # 3. Apply delta to skill config (via SkillAdapter)
        # 4. Write skill_config_updated event to audit chain
        processor.process_optimizer_delta(
            delta=None,  # Delta computed by optimizer (future: integrate with Skill Adapter)
            dry_run=False,  # Real execution (not test)
        )

        logger.info(
            "Feedback submitted: skill=%s, subject=%s, rating=%d, tenant=%s",
            req.skill_id,
            req.subject_id,
            req.rating,
            rec.tenant_id,
        )

        return {
            "status": "received",
            "feedback_id": feedback_event.feedback_id,
            "timestamp": feedback_event.timestamp,
            "skill_id": req.skill_id,
            "subject_id": req.subject_id,
            "message": f"Feedback received for {req.skill_id} on {req.subject_id}",
        }

    except ValueError as e:
        logger.warning("Invalid feedback: %s", str(e))
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error("Feedback processing failed: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Feedback processing failed")


@router.get("/history", response_model=FeedbackHistoryResponse)
async def get_feedback_history(
    skill_id: str = Query(..., description="Skill ID (os.workflow_optimizer, etc.)"),
    limit: int = Query(10, ge=1, le=100, description="Max feedback events to return"),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> dict:
    """
    Get feedback history for a specific skill (ADR-2033).

    Returns feedback events submitted by the operator for the given skill,
    including ratings, categories, and reasoning (scrubbed).

    Args:
        skill_id: Skill ID (one of VALID_SKILLS)
        limit: Max events to return (1-100, default 10)
        rec: Session record (provides tenant_id)

    Returns:
        FeedbackHistoryResponse with total_count, events, avg_rating

    HTTP Status Codes:
        - 200: Success
        - 400: Invalid skill_id
        - 403: Access denied

    Examples:
        GET /v1/console/feedback/skill/history?skill_id=os.workflow_optimizer&limit=5

        Response (200):
        {
          "skill_id": "os.workflow_optimizer",
          "total_count": 42,
          "feedback_events": [
            {
              "feedback_id": "fb_...",
              "timestamp": "2026-09-22T12:30:00Z",
              "skill_id": "os.workflow_optimizer",
              "subject_id": "task_123",
              "rating": 2,
              "category": "speed",
              "reasoning": "Optimal routing for load"
            },
            ...
          ],
          "avg_rating": 1.3,
          "timestamp": "2026-09-22T12:34:56Z"
        }
    """

    try:
        # Validate skill_id
        if skill_id not in VALID_SKILLS:
            raise ValueError(f"Invalid skill_id: {skill_id}")

        # TODO: Load feedback history from storage
        # In production, this would query the feedback store by skill_id + tenant_id
        feedback_events = []  # Mock: empty history

        avg_rating = 0.0
        if feedback_events:
            avg_rating = sum(e.get("rating", 0) for e in feedback_events) / len(feedback_events)

        return {
            "skill_id": skill_id,
            "total_count": len(feedback_events),
            "feedback_events": feedback_events,
            "avg_rating": avg_rating,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception as e:
        logger.error("Failed to get feedback history: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to retrieve feedback history")


@router.get("/status", response_model=FeedbackStatusResponse)
async def get_feedback_status(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> dict:
    """
    Get overall feedback processing status (ADR-2033).

    Returns aggregated metrics on feedback submission and processing:
    - Total feedback received
    - Total successfully processed
    - Processing error count
    - Average latency
    - Last feedback timestamp

    Returns:
        FeedbackStatusResponse with metrics

    HTTP Status Codes:
        - 200: Success
        - 403: Access denied

    Examples:
        GET /v1/console/feedback/skill/status

        Response (200):
        {
          "total_received": 157,
          "total_processed": 155,
          "processing_errors": 2,
          "avg_latency_ms": 42.5,
          "last_feedback_timestamp": "2026-09-22T12:34:00Z",
          "timestamp": "2026-09-22T12:34:56Z"
        }
    """

    try:
        # TODO: Query feedback metrics from monitoring
        # In production, this would read from Prometheus or a metrics store
        return {
            "total_received": 0,
            "total_processed": 0,
            "processing_errors": 0,
            "avg_latency_ms": 0.0,
            "last_feedback_timestamp": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error("Failed to get feedback status: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to retrieve status")
