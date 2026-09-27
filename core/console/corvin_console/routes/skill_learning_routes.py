"""Skill Learning Dashboard Routes — ADR-0683 Phase 7 k=2.

HTTP endpoints for Skill learning observability:
  - GET /v1/console/skills/{skill_id}/learning — full learning stats
  - GET /v1/console/skills/{skill_id}/feedback/history — feedback timeline
  - GET /v1/console/skills/{skill_id}/optimization/proposals — tuning suggestions

No per-skill learning store is wired behind these routes yet. They used to
answer every skill id with the same hard-coded numbers (156 executions, 85 %
accuracy, ten invented feedback items, one invented tuning proposal) — sample
data presented as measurements on a production surface. They now say "not
available on this build" (404 for the metrics, empty lists for the rest)
until a real source exists. Every route needs a console session; the tenant is
the session's, never a query parameter.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/skills", tags=["learning"],
    dependencies=[Depends(require_session_csrf_on_mutation)],
)
Session = Annotated[session_auth.SessionRecord, Depends(require_session_csrf_on_mutation)]

_UNAVAILABLE = "skill learning metrics are not available on this build"


class SkillLearningMetrics(BaseModel):
    skill_id: str
    version: str
    total_executions: int
    correct_outcomes: int
    accuracy: float = Field(..., ge=0.0, le=1.0)
    avg_latency_ms: float
    error_rate: float = Field(..., ge=0.0, le=1.0)
    avg_cost_usd: float
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    last_updated: str


class FeedbackItem(BaseModel):
    execution_id: str
    outcome_correct: bool
    rating: int
    notes: str
    timestamp: str
    latency_ms: float


class FeedbackHistory(BaseModel):
    skill_id: str
    total_feedback_items: int
    recent: list[FeedbackItem]
    available: bool = False
    reason: str = _UNAVAILABLE


class OptimizationProposal(BaseModel):
    proposal_id: str
    skill_id: str
    parameter_name: str
    old_value: str
    new_value: str
    rationale: str
    expected_improvement_pct: float
    confidence: float = Field(..., ge=0.0, le=1.0)
    created_at: str
    status: str = "pending"


class OptimizationProposalList(BaseModel):
    skill_id: str
    proposals: list[OptimizationProposal]
    available: bool = False
    reason: str = _UNAVAILABLE


@router.get("/{skill_id}/learning")
async def get_skill_learning_metrics(skill_id: str, rec: Session) -> SkillLearningMetrics:
    """Full learning stats for a Skill — no source is wired yet."""
    raise HTTPException(status_code=404, detail=_UNAVAILABLE)


@router.get("/{skill_id}/feedback/history")
async def get_feedback_history(
    skill_id: str,
    rec: Session,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FeedbackHistory:
    """Recent feedback for a Skill — empty until a source is wired."""
    return FeedbackHistory(skill_id=skill_id, total_feedback_items=0, recent=[])


@router.get("/{skill_id}/optimization/proposals")
async def get_optimization_proposals(
    skill_id: str,
    rec: Session,
    status: str = Query("pending"),
) -> OptimizationProposalList:
    """Parameter tuning proposals for a Skill — empty until a source is wired."""
    return OptimizationProposalList(skill_id=skill_id, proposals=[])
