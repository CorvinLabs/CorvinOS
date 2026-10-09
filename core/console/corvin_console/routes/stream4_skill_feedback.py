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

from core.skills.feedback import history_store
from core.skills.feedback.loop_closure import apply_outcome_to_skill
from core.skills.feedback.schema import FeedbackEvent, FeedbackType, OutcomeFeedback, validate_feedback
from core.skills.os_skills.audit_integration import emit_skill_audit_event

from .. import auth as session_auth
from ..deps import require_session
from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/feedback/skill", tags=["skill_feedback"])

# Valid skill IDs
VALID_SKILLS = [
    "os.delegation_router",  # reads its learned confidence_threshold back (load_skill_config)
    "os.workflow_optimizer",
    "os.security_orchestrator",
    "os.flow_guard",
]

# Skills that read their learned config back (``load_skill_config``). Feedback for any
# other skill is audited and stored but changes nothing — saying otherwise would be false.
CONFIG_CONSUMERS = frozenset({"os.delegation_router"})

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
        description="One of: os.delegation_router, os.workflow_optimizer, os.security_orchestrator, os.flow_guard"
    )
    # Bounded + charset-restricted: subject_id is echoed into the log line and
    # the response. 8436dcb1e dropped the iteration-3 constraint, which let a
    # newline forge log records again (tests/learning/
    # test_adversarial_review_iteration3_fixes.py::TestInputValidation001).
    subject_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="task_id, threat_id, or flow_id (alphanumeric + underscore/hyphen only)",
    )
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
    config_applied: bool = Field(False, description="True when the feedback changed the skill's learned config")
    config_version: Optional[str] = Field(None, description="Version id of the applied config, if any")


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
    """Feedback submission status (counts are read from the tenant's stored feedback)."""

    total_received: int
    last_feedback_timestamp: Optional[str]
    timestamp: str


# ============================================================================
# Routes
# ============================================================================

@router.post("", response_model=SkillFeedbackResponse)
async def submit_skill_feedback(
    req: SkillFeedbackRequest,
    rec: session_auth.SessionRecord = Depends(require_session),
) -> dict:
    """
    Submit feedback on a skill decision (ADR-2033, ADR-2034).

    Feedback is:
    - Immutable (hash-chained to audit trail)
    - Tenant-scoped (fail-closed on tenant mismatch)
    - Validated (rating, skill_id, category, subject_id)
    - Audited FIRST (``skill_feedback`` event, outcome signal only) — no chain
      commit, no stored feedback (HTTP 500)
    - Stored per tenant (ids, rating, category; the free-text reason is never kept)
    - NOT applied to a skill config here: turning feedback into a config delta is
      the optimizer's job (loop closure), not this route's

    Args:
        req: SkillFeedbackRequest with skill_id, rating, category, reasoning
        rec: Session record (provides tenant_id, operator_id)

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
        # The schema's OUTCOME signal is bool or "yes"/"no"/"other" — the rating's sign
        # is the outcome (>0 correct, <0 incorrect, 0 neither); its magnitude is kept in
        # the stored history, not in the signal.
        signal = True if req.rating > 0 else False if req.rating < 0 else "other"
        feedback_event = FeedbackEvent(
            skill_id=req.skill_id,
            tenant_id=rec.tenant_id,
            feedback_type=FeedbackType.OUTCOME,  # Outcome feedback (was the decision correct?)
            signal=signal,
            reason=None,  # free text is never persisted or chained
        )

        # Validate feedback event (fail-closed). core.learning.feedback_validator
        # never exported validate_feedback_event; importing it made the whole
        # console router unimportable (bare 404 on /console after a restart).
        ok, err = validate_feedback(feedback_event)
        if not ok:
            raise HTTPException(status_code=400, detail=err or "invalid feedback")

        # Audit-first: the outcome signal is chained before anything is stored. The
        # helper reports False instead of raising, so a missing commit aborts here.
        outcome = "yes" if signal is True else "no" if signal is False else "other"
        if not emit_skill_audit_event(
            "skill_feedback",
            req.skill_id,
            rec.tenant_id,
            line_of_moral_responsibility="stream4_skill_feedback.submit_skill_feedback",
            details={"feedback_type": "outcome", "signal": outcome, "tenant_id": rec.tenant_id},
        ):
            raise HTTPException(status_code=500, detail="Audit chain unavailable — feedback not stored")

        history_store.append(
            rec.tenant_id,
            feedback_id=feedback_event.feedback_id,
            timestamp=feedback_event.timestamp,
            skill_id=req.skill_id,
            subject_id=req.subject_id,
            rating=req.rating,
            category=req.category,
        )

        logger.info(
            "Feedback submitted: skill=%s, subject=%s, rating=%d, tenant=%s",
            req.skill_id,
            req.subject_id,
            req.rating,
            rec.tenant_id,
        )

        # Loop closure: the outcome adjusts the skill's learned config (audited by the
        # adapter). The feedback above is already chained and stored, so a failure here
        # is reported, never lost.
        config_applied, config_version, note = False, None, ""
        if req.skill_id not in CONFIG_CONSUMERS:
            note = " (this skill reads no learned config yet)"
        elif isinstance(signal, bool):
            try:
                update = await apply_outcome_to_skill(
                    OutcomeFeedback(
                        skill_id=req.skill_id, tenant_id=rec.tenant_id, signal=signal,
                        feedback_id=feedback_event.feedback_id,
                    )
                )
                config_applied = update is not None
                if update is not None:
                    from core.skills.os_skills.skill_adapter import load_skill_config  # noqa: PLC0415

                    config_version = load_skill_config(req.skill_id, rec.tenant_id)[1]
                else:
                    note = " (threshold already at its bound)"
            except Exception as exc:  # noqa: BLE001 — feedback is stored; config change is best-effort
                logger.error("Loop closure failed for %s: %s", req.skill_id, type(exc).__name__)
                note = f" (config not updated: {type(exc).__name__})"

        return {
            "status": "received",
            "feedback_id": feedback_event.feedback_id,
            "timestamp": feedback_event.timestamp,
            "skill_id": req.skill_id,
            "subject_id": req.subject_id,
            "message": f"Feedback received for {req.skill_id} on {req.subject_id}{note}",
            "config_applied": config_applied,
            "config_version": config_version,
        }

    except HTTPException:
        raise

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

        total, events, avg_rating = history_store.history(rec.tenant_id, skill_id, limit)
        feedback_events = [
            {**e, "reasoning": None}  # free text is never stored
            for e in events
        ]

        return {
            "skill_id": skill_id,
            "total_count": total,
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
    Get feedback submission status (ADR-2033).

    Counts are read from the tenant's stored feedback; nothing unmeasured is reported:
    - Total feedback received
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
          "last_feedback_timestamp": "2026-09-22T12:34:00Z",
          "timestamp": "2026-09-22T12:34:56Z"
        }
    """

    try:
        total, last_ts = history_store.status(rec.tenant_id)
        return {
            "total_received": total,
            "last_feedback_timestamp": last_ts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error("Failed to get feedback status: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to retrieve status")
