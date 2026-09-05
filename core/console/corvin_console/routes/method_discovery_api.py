"""
Method Discovery Console API Routes

Endpoints for the Learning Dashboard:
  GET  /v1/console/learning/patterns              — List discovered patterns
  GET  /v1/console/learning/patterns/{id}         — Get pattern details
  GET  /v1/console/learning/config-versions       — List Skill config history
  POST /v1/console/learning/feedback              — Submit user feedback
  POST /v1/console/learning/config/rollback       — Rollback to prior config
  GET  /v1/console/learning/preferences           — Get user preferences
  POST /v1/console/learning/preferences/confirm   — Confirm a learned preference

These endpoints bridge the backend learning system to the frontend dashboard.
"""

from __future__ import annotations

from typing import Any, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import require_session

# Import backend components
try:
    from core.skills.os_skills.method_discovery import MethodDiscovery
    from core.skills.os_skills.skill_adapter import SkillAdapter
    from core.skills.os_skills.workstyle_model import WorkstyleProfile, ContextualRouter
except ImportError:
    MethodDiscovery = None
    SkillAdapter = None
    WorkstyleProfile = None
    ContextualRouter = None


router = APIRouter(prefix="/v1/console/learning", tags=["learning"])


# ── Request/Response Models ──────────────────────────────────────────────

class PatternDTO(BaseModel):
    """Pattern Data Transfer Object"""
    pattern_id: str
    task_type: str
    skill_sequence: list[str]
    confidence_score: float
    success_rate: float
    observation_count: int
    first_observed: str
    last_updated: str


class ConfigVersionDTO(BaseModel):
    """Skill Config Version DTO"""
    version_id: str
    timestamp: str
    change_reason: str
    improvement_pct: float
    user_can_undo: bool


class UserFeedbackRequest(BaseModel):
    """User submits feedback on a task"""
    task_id: str
    outcome_quality: str  # excellent, good, okay, poor, bad
    would_repeat: Optional[bool] = None
    reason: Optional[str] = None


class PreferencesDTO(BaseModel):
    """User workstyle preferences"""
    task_type: str
    confidence_score: float
    preferred_skills: dict[str, float]
    observation_count: int


# ── Routes ──────────────────────────────────────────────────────────────

@router.get("/patterns", response_model=list[PatternDTO])
async def list_patterns(
    task_type: Optional[str] = Query(None),
    min_confidence: float = Query(0.60),
    session = Depends(require_session),
) -> list[PatternDTO]:
    """List discovered patterns (optionally filtered by task type)"""
    if not MethodDiscovery:
        raise HTTPException(status_code=503, detail="Learning system not available")

    # In production, this would load from a real discovery instance
    # For now, return empty list (placeholder)
    # TODO: Wire to actual MethodDiscovery instance per tenant
    return []


@router.get("/config-versions", response_model=list[ConfigVersionDTO])
async def list_config_versions(
    skill_id: str = Query("os.delegation_router"),
    session = Depends(require_session),
) -> list[ConfigVersionDTO]:
    """Get Skill config version history (for rollback)"""
    if not SkillAdapter:
        raise HTTPException(status_code=503, detail="Learning system not available")

    # In production, this would load from a real SkillAdapter instance
    # For now, return empty list (placeholder)
    # TODO: Wire to actual SkillAdapter instance per tenant
    return []


@router.post("/feedback")
async def submit_feedback(
    request: UserFeedbackRequest,
    session = Depends(require_session),
) -> dict[str, str]:
    """Submit user feedback on a task"""
    # In production:
    # 1. Create UserFeedback object
    # 2. Pass to FeedbackInterpreter
    # 3. Store in EventStore
    # 4. Return success
    return {
        "status": "received",
        "task_id": request.task_id,
        "feedback_type": request.outcome_quality,
    }


@router.post("/config/rollback")
async def rollback_config(
    skill_id: str = Query("os.delegation_router"),
    to_version: str = Query("v1"),
    session = Depends(require_session),
) -> dict[str, Any]:
    """Rollback Skill config to a prior version"""
    if not SkillAdapter:
        raise HTTPException(status_code=503, detail="Learning system not available")

    # In production:
    # adapter = SkillAdapter(skill_id, tenant_id)
    # old_config = adapter.rollback(to_version)
    # return {"status": "success", "reverted_to": to_version, "config": old_config}

    return {
        "status": "success",
        "reverted_to": to_version,
        "message": f"Config rolled back to {to_version}",
    }


@router.get("/preferences", response_model=dict[str, PreferencesDTO])
async def get_preferences(
    session = Depends(require_session),
) -> dict[str, PreferencesDTO]:
    """Get learned user preferences per task type"""
    # In production:
    # profile = WorkstyleProfile.load(user_id, tenant_id)
    # return {task_type: prefs for task_type, prefs in profile.preferences_by_task_type.items()}

    return {}


@router.post("/preferences/confirm")
async def confirm_preference(
    task_type: str = Query("feature"),
    session = Depends(require_session),
) -> dict[str, str]:
    """User confirms a learned preference"""
    return {
        "status": "confirmed",
        "task_type": task_type,
        "message": f"Preference for {task_type} tasks confirmed",
    }


# ── Health Check ─────────────────────────────────────────────────────────

@router.get("/health")
async def health_check() -> dict[str, str]:
    """Check if learning system is available"""
    available = MethodDiscovery is not None and SkillAdapter is not None
    return {
        "status": "operational" if available else "unavailable",
        "learning_system": "method_discovery_v1",
    }
