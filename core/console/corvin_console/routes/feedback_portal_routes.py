from core.security.csrf import require_csrf
"""Console API routes for Phase 9 Feedback Portal (ADR-2028, ADR-2029).

Endpoints:
  POST   /v1/console/feedback/bug-report       - Submit bug report
  POST   /v1/console/feedback/feature-request  - Submit feature request
  POST   /v1/console/feedback/nps-survey       - Submit NPS survey
  GET    /v1/console/feedback/status           - Feedback status & counts
  GET    /v1/console/feedback/list             - List triaged feedback (admin)
  GET    /v1/console/feedback/priorities       - Feedback by priority
"""

import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional

from core.feedback.phase9_feedback_portal import FeedbackPortal
from core.feedback.feedback_models import FeedbackSeverity
from core.paths.tenant import tenant_home

from .. import auth as session_auth
from ..deps import require_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/console/feedback", tags=["feedback"])


# ============================================================================
# Request Models
# ============================================================================

class BugReportRequest(BaseModel):
    """Bug report request (validated)."""
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    severity: str = Field(..., description="critical | high | medium | low")
    component: str = Field(..., description="console | voice | video_producer | etc.")
    user_email: str = Field(..., description="Reporter email")
    reproduction_steps: Optional[str] = Field(None, max_length=2000)
    environment: str = Field("production", description="production | staging | local")
    version: str = Field("", description="CorvinOS version")
    session_id: Optional[str] = None


class FeatureRequestRequest(BaseModel):
    """Feature request (validated)."""
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=10, max_length=5000)
    component: str = Field(...)
    user_email: str = Field(...)
    use_case: Optional[str] = Field(None, max_length=1000)
    priority_hint: Optional[str] = Field(None, description="quick_win | high_value | nice_to_have")


class NPSSurveyRequest(BaseModel):
    """NPS survey request (validated)."""
    nps_score: int = Field(..., ge=0, le=10, description="0-10 score")
    user_email: str = Field(...)
    component: Optional[str] = Field(None)
    comment: Optional[str] = Field(None, max_length=500)


class FeedbackResponse(BaseModel):
    """Generic feedback response."""
    status: str
    feedback_id: str
    message: str


# ============================================================================
# Dependency: Get Portal
# ============================================================================

async def get_feedback_portal(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> FeedbackPortal:
    """Get feedback portal for the session's tenant (never an env var)."""
    tenant_id = rec.tenant_id
    feedback_home = tenant_home(tenant_id) / "feedback"
    return FeedbackPortal(feedback_home, tenant_id)


# ============================================================================
# Routes
# ============================================================================

@require_csrf
@router.post("/bug-report", response_model=FeedbackResponse)
async def submit_bug_report(
    req: BugReportRequest,
    portal: FeedbackPortal = Depends(get_feedback_portal),
) -> dict:
    """Submit a bug report (audit-logged, auto-triaged).

    Responses:
      - 200: Bug report submitted successfully
      - 400: Invalid request (missing fields, invalid severity)
      - 500: System error (audit chain failure)

    Examples:
      POST /v1/console/feedback/bug-report
      {
        "title": "Voice stops responding after 5 minutes",
        "description": "When using voice input, the connection drops after ~5 minutes...",
        "severity": "high",
        "component": "voice",
        "user_email": "user@example.com",
        "reproduction_steps": "1. Start voice session. 2. Speak for 5 minutes. 3. Voice stops.",
        "environment": "production",
        "version": "v2.0.0"
      }
    """
    try:
        # Validate severity enum
        try:
            severity = FeedbackSeverity(req.severity.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid severity: {req.severity}")

        # Submit bug report (async, non-blocking)
        feedback_id = await portal.submit_bug_report(
            title=req.title,
            description=req.description,
            severity=severity,
            component=req.component.lower(),
            user_email=req.user_email,
            reproduction_steps=req.reproduction_steps,
            environment=req.environment.lower(),
            version=req.version,
        )

        return FeedbackResponse(
            status="success",
            feedback_id=feedback_id,
            message="Bug report submitted. Our team will review it shortly.",
        ).dict()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"bug_report_error: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit bug report")


@require_csrf
@router.post("/feature-request", response_model=FeedbackResponse)
async def submit_feature_request(
    req: FeatureRequestRequest,
    portal: FeedbackPortal = Depends(get_feedback_portal),
) -> dict:
    """Submit a feature request (auto-triaged).

    Examples:
      POST /v1/console/feedback/feature-request
      {
        "title": "Dark mode for console UI",
        "description": "Add a dark mode option to reduce eye strain...",
        "component": "console",
        "user_email": "user@example.com",
        "use_case": "Long sessions at night",
        "priority_hint": "high_value"
      }
    """
    try:
        feedback_id = await portal.submit_feature_request(
            title=req.title,
            description=req.description,
            component=req.component.lower(),
            user_email=req.user_email,
            use_case=req.use_case,
            priority_hint=req.priority_hint,
        )

        return FeedbackResponse(
            status="success",
            feedback_id=feedback_id,
            message="Feature request received. Thanks for the suggestion!",
        ).dict()

    except Exception as e:
        logger.exception(f"feature_request_error: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit feature request")


@require_csrf
@router.post("/nps-survey", response_model=FeedbackResponse)
async def submit_nps_survey(
    req: NPSSurveyRequest,
    portal: FeedbackPortal = Depends(get_feedback_portal),
) -> dict:
    """Submit NPS survey response (auto-triaged).

    Examples:
      POST /v1/console/feedback/nps-survey
      {
        "nps_score": 9,
        "user_email": "user@example.com",
        "component": "console",
        "comment": "Love the new UI!"
      }
    """
    try:
        feedback_id = await portal.submit_nps_survey(
            nps_score=req.nps_score,
            user_email=req.user_email,
            component=req.component,
            comment=req.comment,
        )

        return FeedbackResponse(
            status="success",
            feedback_id=feedback_id,
            message="Thanks for your feedback!",
        ).dict()

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"nps_survey_error: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit survey")


@router.get("/status")
async def get_feedback_status(portal: FeedbackPortal = Depends(get_feedback_portal)) -> dict:
    """Get feedback status & priority counts (public).

    Returns:
      {
        "total_feedback": 42,
        "p0_critical": 1,
        "p1_high": 3,
        "p2_medium": 8,
        "p3_low": 30
      }
    """
    try:
        counts = portal.get_feedback_by_priority()
        total = sum(counts.values())

        return {
            "status": "ok",
            "total_feedback": total,
            "p0_critical": counts.get("p0", 0),
            "p1_high": counts.get("p1", 0),
            "p2_medium": counts.get("p2", 0),
            "p3_low": counts.get("p3", 0),
        }
    except Exception as e:
        logger.exception(f"status_error: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/priorities")
async def get_feedback_by_priority(portal: FeedbackPortal = Depends(get_feedback_portal)) -> dict:
    """Get feedback breakdown by priority (public).

    Returns breakdown suitable for dashboard visualization.
    """
    try:
        counts = portal.get_feedback_by_priority()
        return {
            "status": "ok",
            "breakdown": counts,
        }
    except Exception as e:
        logger.exception(f"priorities_error: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch priorities")


@router.get("/list")
async def list_triaged_feedback(
    priority: Optional[str] = None,
    limit: int = 50,
    portal: FeedbackPortal = Depends(get_feedback_portal),
) -> dict:
    """List triaged feedback (admin only).

    Query parameters:
      - priority: Filter by priority (p0 | p1 | p2 | p3)
      - limit: Max results (1-100)

    Note: Full feedback list only visible to admins (auth gate TBD).
    """
    try:
        # TODO: Add admin auth gate

        feedback_list = portal.get_triaged_feedback(limit=min(limit, 100))

        return {
            "status": "ok",
            "count": len(feedback_list),
            "feedback": feedback_list,
        }
    except Exception as e:
        logger.exception(f"list_error: {e}")
        raise HTTPException(status_code=500, detail="Failed to list feedback")


__all__ = ["router"]
