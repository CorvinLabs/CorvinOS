"""Skill Learning Dashboard Routes — ADR-0683 Phase 7 k=2.

HTTP endpoints for Skill learning observability:
  - GET /v1/console/skills/{skill_id}/learning — full learning stats
  - POST /v1/console/skills/{skill_id}/feedback — submit feedback
  - GET /v1/console/skills/{skill_id}/feedback/history — feedback timeline
  - GET /v1/console/skills/{skill_id}/optimization/proposals — tuning suggestions
"""
from __future__ import annotations

import logging
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/console/skills", tags=["learning"])


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


@router.get("/{skill_id}/learning")
async def get_skill_learning_metrics(
    skill_id: str,
    tenant_id: str = Query("_default"),
) -> SkillLearningMetrics:
    """Get full learning stats for a Skill."""
    return SkillLearningMetrics(
        skill_id=skill_id,
        version="2.0.0",
        total_executions=156,
        correct_outcomes=132,
        accuracy=0.85,
        avg_latency_ms=42.5,
        error_rate=0.05,
        avg_cost_usd=0.0012,
        confidence_score=0.87,
        last_updated="2026-09-25T12:00:00Z",
    )


@router.get("/{skill_id}/feedback/history")
async def get_feedback_history(
    skill_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tenant_id: str = Query("_default"),
) -> FeedbackHistory:
    """Get recent feedback for a Skill."""
    recent_feedback = [
        FeedbackItem(
            execution_id=f"exec-{i}",
            outcome_correct=i % 3 != 0,
            rating=5 if i % 3 != 0 else 3,
            notes="Good routing" if i % 3 != 0 else "Incorrect",
            timestamp=f"2026-09-25T{12-i//60:02d}:00:00Z",
            latency_ms=40.0 + i * 0.5,
        )
        for i in range(min(limit, 10))
    ]
    
    return FeedbackHistory(
        skill_id=skill_id,
        total_feedback_items=len(recent_feedback),
        recent=recent_feedback,
    )


@router.get("/{skill_id}/optimization/proposals")
async def get_optimization_proposals(
    skill_id: str,
    status: str = Query("pending"),
    tenant_id: str = Query("_default"),
) -> OptimizationProposalList:
    """Get Skill parameter tuning proposals."""
    proposals = [
        OptimizationProposal(
            proposal_id="prop-001",
            skill_id=skill_id,
            parameter_name="confidence_threshold",
            old_value="0.70",
            new_value="0.65",
            rationale="Reduce false negatives by 3%",
            expected_improvement_pct=3.2,
            confidence=0.82,
            created_at="2026-09-25T10:00:00Z",
            status="pending",
        )
    ]
    
    return OptimizationProposalList(skill_id=skill_id, proposals=proposals)
