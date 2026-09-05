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


router = APIRouter(prefix="/learning", tags=["learning"])


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


# ── Mock Data ───────────────────────────────────────────────────────────

MOCK_PATTERNS = [
    PatternDTO(
        pattern_id="pat_001",
        task_type="code_generation",
        skill_sequence=["claude_code", "fix_syntax", "test"],
        confidence_score=0.92,
        success_rate=0.95,
        observation_count=23,
        first_observed="2026-08-15T10:30:00Z",
        last_updated="2026-09-05T14:22:00Z",
    ),
    PatternDTO(
        pattern_id="pat_002",
        task_type="documentation",
        skill_sequence=["outline_doc", "write_sections", "review"],
        confidence_score=0.87,
        success_rate=0.88,
        observation_count=18,
        first_observed="2026-08-20T09:15:00Z",
        last_updated="2026-09-04T11:45:00Z",
    ),
    PatternDTO(
        pattern_id="pat_003",
        task_type="debugging",
        skill_sequence=["identify_error", "search_logs", "test_fix"],
        confidence_score=0.79,
        success_rate=0.82,
        observation_count=12,
        first_observed="2026-08-25T16:20:00Z",
        last_updated="2026-09-03T13:30:00Z",
    ),
]

MOCK_CONFIG_VERSIONS = [
    ConfigVersionDTO(
        version_id="v1.0.0",
        timestamp="2026-08-15T10:00:00Z",
        change_reason="Initial config",
        improvement_pct=0.0,
        user_can_undo=False,
    ),
    ConfigVersionDTO(
        version_id="v1.1.0",
        timestamp="2026-08-22T14:30:00Z",
        change_reason="Increased context window",
        improvement_pct=8.5,
        user_can_undo=True,
    ),
    ConfigVersionDTO(
        version_id="v1.2.0",
        timestamp="2026-09-01T09:15:00Z",
        change_reason="Optimized routing weights",
        improvement_pct=12.3,
        user_can_undo=True,
    ),
    ConfigVersionDTO(
        version_id="v1.3.0",
        timestamp="2026-09-05T11:45:00Z",
        change_reason="Fine-tuned confidence thresholds",
        improvement_pct=5.7,
        user_can_undo=True,
    ),
]

MOCK_PREFERENCES = {
    "code_generation": PreferencesDTO(
        task_type="code_generation",
        confidence_score=0.92,
        preferred_skills={"claude_code": 0.95, "fix_syntax": 0.88, "test": 0.85},
        observation_count=23,
    ),
    "documentation": PreferencesDTO(
        task_type="documentation",
        confidence_score=0.87,
        preferred_skills={"outline_doc": 0.90, "write_sections": 0.88, "review": 0.80},
        observation_count=18,
    ),
    "debugging": PreferencesDTO(
        task_type="debugging",
        confidence_score=0.79,
        preferred_skills={"identify_error": 0.85, "search_logs": 0.82, "test_fix": 0.75},
        observation_count=12,
    ),
}

# ── Routes ──────────────────────────────────────────────────────────────

@router.get("/patterns", response_model=list[PatternDTO])
async def list_patterns(
    task_type: Optional[str] = Query(None),
    min_confidence: float = Query(0.60),
    session = Depends(require_session),
) -> list[PatternDTO]:
    """List discovered patterns (optionally filtered by task type)"""
    patterns = MOCK_PATTERNS

    if task_type:
        patterns = [p for p in patterns if p.task_type == task_type]

    if min_confidence > 0:
        patterns = [p for p in patterns if p.confidence_score >= min_confidence]

    return patterns


@router.get("/config-versions", response_model=list[ConfigVersionDTO])
async def list_config_versions(
    skill_id: str = Query("os.delegation_router"),
    session = Depends(require_session),
) -> list[ConfigVersionDTO]:
    """Get Skill config version history (for rollback)"""
    # Return mock config versions for demonstration
    return MOCK_CONFIG_VERSIONS


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
    return MOCK_PREFERENCES


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
