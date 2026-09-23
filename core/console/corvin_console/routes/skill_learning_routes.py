"""Skill Learning Routes — Phase 7 feedback API (ADR-0683).

FastAPI routes for:
- POST /v1/skills/{skill_id}/feedback — submit feedback
- GET /v1/skills/{skill_id}/stats — get learning stats
- GET /v1/skills/{skill_id}/learning/history — get learning event history
- POST /v1/skills/{skill_id}/learning/reset — reset learning state (testing)

Integrates with SkillLearningLoop and EventStore (ADR-0314).
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["console-skill-learning"])

# In-memory registry of learning loops (keyed by skill_id)
# In production, use a persistent store or load from disk
_LEARNING_LOOPS: Dict[str, Any] = {}


# ============================================================================
# Data Models (Request/Response)
# ============================================================================

class SubmitFeedbackRequest(BaseModel):
    """Request to submit feedback on a skill execution."""

    execution_id: str = Field(..., description="ID of the execution being rated")
    rating: int = Field(..., ge=1, le=5, description="Quality rating (1-5)")
    comment: str = Field(default="", max_length=200, description="Optional feedback comment")
    useful: bool = Field(default=True, description="Was this feedback useful?")


class SkillStats(BaseModel):
    """Skill learning statistics."""

    skill_id: str
    skill_name: str
    skill_version: str

    # Metrics
    execution_count: int
    success_count: int
    error_count: int
    feedback_count: int

    # Confidence
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    success_rate: float = Field(..., ge=0.0, le=1.0)
    feedback_ratio: float = Field(..., ge=0.0, le=1.0)

    # Performance
    avg_latency_ms: float
    total_tokens: int

    # Convergence
    is_converged: bool
    convergence_reason: Optional[str]

    # Timestamps
    last_execution_at: Optional[str]
    last_feedback_at: Optional[str]
    created_at: str


class LearningEvent(BaseModel):
    """A single learning event."""

    event_id: str
    event_type: str  # 'execution', 'feedback'
    timestamp: str
    data: Dict[str, Any]


class LearningHistory(BaseModel):
    """Learning history response (paginated)."""

    skill_id: str
    events: List[LearningEvent]
    total_count: int
    offset: int
    limit: int


# ============================================================================
# Utility Functions
# ============================================================================

def _get_or_create_learning_loop(skill_id: str, tenant_id: str) -> Any:
    """Get or create a learning loop for a skill.

    Args:
        skill_id: Skill ID
        tenant_id: Tenant ID

    Returns:
        SkillLearningLoop instance
    """
    key = f"{tenant_id}:{skill_id}"
    if key not in _LEARNING_LOOPS:
        from core.skills.skill_learning_loop import SkillLearningLoop

        # Parse skill_id to get name and version
        # Format: "name@version" or "org/name@version"
        parts = skill_id.rsplit('@', 1)
        skill_name = parts[0] if parts else skill_id
        skill_version = parts[1] if len(parts) > 1 else "1.0.0"

        _LEARNING_LOOPS[key] = SkillLearningLoop(
            skill_id=skill_id,
            skill_name=skill_name,
            skill_version=skill_version,
            tenant_id=tenant_id,
        )

    return _LEARNING_LOOPS[key]


# ============================================================================
# API Routes
# ============================================================================

@router.post("/{skill_id}/feedback")
async def submit_skill_feedback(
    skill_id: str,
    request: SubmitFeedbackRequest,
    x_tenant_id: str = Header(..., alias="x-tenant-id"),
) -> Dict[str, Any]:
    """Submit operator feedback on a skill execution.

    Args:
        skill_id: Skill ID
        request: Feedback data (execution_id, rating, comment, useful)
        x_tenant_id: Tenant ID (from header)

    Returns:
        Feedback record with feedback_id

    Raises:
        HTTPException: If execution not found or validation fails
    """
    try:
        learning_loop = _get_or_create_learning_loop(skill_id, x_tenant_id)

        feedback = learning_loop.submit_feedback(
            execution_id=request.execution_id,
            rating=request.rating,
            comment=request.comment,
            useful=request.useful,
        )

        return {
            'feedback_id': feedback.feedback_id,
            'execution_id': feedback.execution_id,
            'skill_id': feedback.skill_id,
            'rating': feedback.rating,
            'given_at': feedback.given_at.isoformat(),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to submit feedback: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{skill_id}/stats")
async def get_skill_stats(
    skill_id: str,
    x_tenant_id: str = Header(..., alias="x-tenant-id"),
) -> SkillStats:
    """Get current skill learning statistics.

    Args:
        skill_id: Skill ID
        x_tenant_id: Tenant ID (from header)

    Returns:
        SkillStats with all metrics
    """
    try:
        learning_loop = _get_or_create_learning_loop(skill_id, x_tenant_id)
        stats = learning_loop.get_stats()

        return SkillStats(
            skill_id=stats.skill_id,
            skill_name=stats.skill_name,
            skill_version=stats.skill_version,
            execution_count=stats.execution_count,
            success_count=stats.success_count,
            error_count=stats.error_count,
            feedback_count=stats.feedback_count,
            confidence_score=stats.confidence_score,
            success_rate=stats.success_rate,
            feedback_ratio=stats.feedback_ratio,
            avg_latency_ms=stats.avg_latency_ms,
            total_tokens=stats.total_tokens,
            is_converged=stats.is_converged,
            convergence_reason=stats.convergence_reason,
            last_execution_at=stats.last_execution_at.isoformat() if stats.last_execution_at else None,
            last_feedback_at=stats.last_feedback_at.isoformat() if stats.last_feedback_at else None,
            created_at=stats.created_at.isoformat(),
        )

    except Exception as e:
        logger.error(f"Failed to get skill stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{skill_id}/learning/history")
async def get_learning_history(
    skill_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    x_tenant_id: str = Header(..., alias="x-tenant-id"),
) -> LearningHistory:
    """Get learning event history (paginated).

    Args:
        skill_id: Skill ID
        skip: Number of events to skip
        limit: Maximum events to return
        x_tenant_id: Tenant ID (from header)

    Returns:
        LearningHistory with paginated events
    """
    try:
        learning_loop = _get_or_create_learning_loop(skill_id, x_tenant_id)

        # Reconstruct history from in-memory state
        events: List[LearningEvent] = []

        # Recent executions
        for exec_id, exec_event in sorted(
            learning_loop._recent_executions.items(),
            key=lambda x: x[1].executed_at,
            reverse=True
        ):
            events.append(LearningEvent(
                event_id=exec_event.execution_id,
                event_type='execution',
                timestamp=exec_event.executed_at.isoformat(),
                data={
                    'success': exec_event.success,
                    'latency_ms': exec_event.latency_ms,
                    'error': exec_event.error,
                    'input_tokens': exec_event.input_tokens,
                    'output_tokens': exec_event.output_tokens,
                },
            ))

        # Recent feedback
        for fb_id, feedback in sorted(
            learning_loop._recent_feedback.items(),
            key=lambda x: x[1].given_at,
            reverse=True
        ):
            events.append(LearningEvent(
                event_id=feedback.feedback_id,
                event_type='feedback',
                timestamp=feedback.given_at.isoformat(),
                data={
                    'execution_id': feedback.execution_id,
                    'rating': feedback.rating,
                    'comment': feedback.comment,
                    'useful': feedback.useful,
                },
            ))

        # Sort by timestamp (newest first)
        events = sorted(events, key=lambda e: e.timestamp, reverse=True)

        # Paginate
        total_count = len(events)
        paginated = events[skip:skip + limit]

        return LearningHistory(
            skill_id=skill_id,
            events=paginated,
            total_count=total_count,
            offset=skip,
            limit=limit,
        )

    except Exception as e:
        logger.error(f"Failed to get learning history: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{skill_id}/learning/reset")
async def reset_learning_state(
    skill_id: str,
    x_tenant_id: str = Header(..., alias="x-tenant-id"),
) -> Dict[str, Any]:
    """Reset learning state (for testing).

    WARNING: This is destructive and should only be used in development.

    Args:
        skill_id: Skill ID
        x_tenant_id: Tenant ID (from header)

    Returns:
        Confirmation message
    """
    try:
        learning_loop = _get_or_create_learning_loop(skill_id, x_tenant_id)
        learning_loop.reset_learning()

        return {
            'status': 'reset',
            'skill_id': skill_id,
            'message': f'Learning state reset for {skill_id}',
        }

    except Exception as e:
        logger.error(f"Failed to reset learning state: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{skill_id}/learning/optimizer/config")
async def get_optimizer_config(
    skill_id: str,
    x_tenant_id: str = Header(..., alias="x-tenant-id"),
) -> Dict[str, Any]:
    """Get current optimizer configuration.

    Args:
        skill_id: Skill ID
        x_tenant_id: Tenant ID (from header)

    Returns:
        Current skill configuration
    """
    try:
        learning_loop = _get_or_create_learning_loop(skill_id, x_tenant_id)

        # Get optimizer (or create if needed)
        if not hasattr(learning_loop, '_optimizer'):
            from core.skills.skill_optimizer import SkillOptimizer
            learning_loop._optimizer = SkillOptimizer(skill_id, learning_loop)

        config = learning_loop._optimizer.get_config()

        return {
            'skill_id': skill_id,
            'version': config.version,
            'parameters': config.parameters,
            'created_at': config.created_at.isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get optimizer config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{skill_id}/learning/optimizer/history")
async def get_optimization_history(
    skill_id: str,
    limit: int = Query(10, ge=1, le=50),
    x_tenant_id: str = Header(..., alias="x-tenant-id"),
) -> Dict[str, Any]:
    """Get A/B test and parameter tuning history.

    Args:
        skill_id: Skill ID
        limit: Maximum records to return
        x_tenant_id: Tenant ID (from header)

    Returns:
        Test history and parameter change history
    """
    try:
        learning_loop = _get_or_create_learning_loop(skill_id, x_tenant_id)

        # Get optimizer
        if not hasattr(learning_loop, '_optimizer'):
            from core.skills.skill_optimizer import SkillOptimizer
            learning_loop._optimizer = SkillOptimizer(skill_id, learning_loop)

        optimizer = learning_loop._optimizer

        return {
            'skill_id': skill_id,
            'ab_tests': [t.to_dict() for t in optimizer.get_test_history(limit)],
            'parameter_changes': optimizer.get_parameter_history(limit),
        }

    except Exception as e:
        logger.error(f"Failed to get optimization history: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
