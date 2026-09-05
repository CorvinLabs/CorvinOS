"""TreeOfThoughts Learning Dashboard API — /v1/console/learning

Endpoints:
  GET /v1/console/learning/nodes    — fetch all TreeNodes with confidences
  POST /v1/console/learning/grade   — operator grades a pattern
  POST /v1/console/learning/note    — operator adds note to pattern
  POST /v1/console/learning/tools/{tool_id}/rating     — rate a tool (Gap 7)
  POST /v1/console/learning/skills/{skill_id}/rating   — rate a skill (Gap 7)
  GET /v1/console/learning/tools/{tool_id}/feedback    — get tool feedback stats (Gap 7)
  GET /v1/console/learning/skills/{skill_id}/feedback  — get skill feedback stats (Gap 7)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import require_session

# Optional: core.learning integration (may not be available in all environments)
try:
    from core.learning import LearningIntegration
    from core.learning.event_store import EventStore
    from core.learning.operator_feedback import OperatorFeedbackHandler
except ImportError:
    LearningIntegration = None  # type: ignore
    EventStore = None  # type: ignore
    OperatorFeedbackHandler = None  # type: ignore


def _tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — honours CORVIN_HOME (never a bare ~/.corvin)."""
    from forge.tenants import tenant_home  # type: ignore[import-not-found]

    return Path(tenant_home(tenant_id))

router = APIRouter()


class GradeRequest(BaseModel):
    """Operator grades a pattern."""
    pattern_id: str
    grade: float  # -1.0 to +1.0
    reason: str = ""


class NoteRequest(BaseModel):
    """Operator adds a note to a pattern."""
    pattern_id: str
    text: str


class TreeNodeJSON(BaseModel):
    """JSON serialization of TreeNode."""
    id: str
    level: str  # "pattern" | "method" | "framework"
    name: str
    confidence: float
    calls_in_production: int
    when: list[str] = []
    anti_when: list[str] = []
    children: list[str] = []
    operator_notes: list = []
    adr_link: str | None = None


# Gap 7: Operator Feedback Loop (ADR-0327)


class ToolRatingRequest(BaseModel):
    """Operator rates a tool execution."""
    rating: int  # 1-5
    feedback_text: Optional[str] = None
    task_id: Optional[str] = None


class SkillRatingRequest(BaseModel):
    """Operator rates a skill execution."""
    rating: int  # 1-5
    feedback_text: Optional[str] = None
    task_id: Optional[str] = None


class FeedbackStatsResponse(BaseModel):
    """Aggregated feedback statistics."""
    entity_id: str
    entity_type: str  # "tool" | "skill"
    entity_name: str
    sample_count: int
    average_rating: float
    median_rating: float
    std_dev: Optional[float]
    min_rating: int
    max_rating: int
    confidence: float
    feedback_sentiment: str
    window_days: int


def get_feedback_handler(session = Depends(require_session)) -> OperatorFeedbackHandler:
    """Get OperatorFeedbackHandler for this tenant.

    The store is ``event_store.EventStore(tenant_home)`` — the SAME store the
    EventEmitter writes to — rooted at ``<corvin_home>/tenants/<tenant_id>/``
    (events land in ``learning/events/YYYY-MM-DD.jsonl``). It used to be handed
    a FILE path (``.../learning/events.db``) as ``tenant_home``.
    """
    event_store = EventStore(_tenant_home(session.tenant_id))
    return OperatorFeedbackHandler(event_store)


def get_learning_integration(session = Depends(require_session)) -> LearningIntegration:
    """Get LearningIntegration for this tenant (``<tenant_home>/learning/``)."""
    store_path = _tenant_home(session.tenant_id) / "learning"
    return LearningIntegration(store_path, tenant_id=session.tenant_id)


@router.get("/learning/debug", response_model=dict)
async def debug_learning(session = Depends(require_session)):
    """Debug endpoint — test if learning system is initialized."""
    try:
        store_path = _tenant_home(session.tenant_id) / "learning"
        return {
            "tenant_id": session.tenant_id,
            "store_path": str(store_path),
            "store_exists": store_path.exists(),
            "store_is_dir": store_path.is_dir() if store_path.exists() else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Debug error: {str(e)}")


@router.get("/learning/nodes", response_model=dict)
async def get_learning_nodes(
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Fetch all TreeNodes (Pattern/Method/Framework) with current confidences."""
    try:
        store = integration.store
        nodes = store.all_nodes()

        # Serialize to JSON
        serialized = []
        for node in nodes:
            serialized.append(TreeNodeJSON(
                id=node.id,
                level=node.level,
                name=node.name,
                confidence=node.confidence,
                calls_in_production=node.calls_in_production,
                when=node.when,
                anti_when=node.anti_when,
                children=node.children,
                operator_notes=node.operator_notes,
                adr_link=node.adr_link,
            ).model_dump())

        # Weg A: the in-memory node store is empty in production (nodes are not
        # persisted). Rather than return an empty tree, project the SELF-EARNED
        # confidence from the CEL stage-grade store (auto-filled by the outcome loop
        # G4 + operator overrides G3). This makes the tree real and earned, not mock.
        source = "nodes"
        if not serialized:
            try:
                from core.learning.earned_tree import build_earned_tree  # noqa: PLC0415
                serialized = build_earned_tree(session.tenant_id)
                source = "earned"
            except Exception:  # noqa: BLE001 — fall back to the (empty) node list
                pass
        return {"nodes": serialized, "source": source}
    except Exception as e:
        import traceback
        error_detail = f"{str(e)}\n{traceback.format_exc()}"
        raise HTTPException(status_code=500, detail=error_detail)


@router.post("/learning/grade")
async def grade_pattern(
    request: GradeRequest,
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Operator manually grades a pattern."""
    try:
        # Clamp grade to [-1.0, +1.0]
        grade = max(-1.0, min(1.0, request.grade))
        
        integration.grade_pattern(
            request.pattern_id,
            grade,
            reason=f"Operator: {request.reason}"
        )
        
        # Return updated node
        node = integration.store.get_node(request.pattern_id)
        return {
            "pattern_id": request.pattern_id,
            "new_confidence": node.confidence if node else None,
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/learning/note")
async def add_operator_note(
    request: NoteRequest,
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Operator adds a note to a pattern."""
    try:
        node = integration.store.get_node(request.pattern_id)
        if not node:
            raise HTTPException(status_code=404, detail="Pattern not found")

        node.add_operator_note(session.user_id, request.text)

        return {
            "pattern_id": request.pattern_id,
            "notes_count": len(node.operator_notes),
            "status": "success"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Gap 7: Operator Feedback Loop API Endpoints
# ============================================================================


@router.post("/tools/{tool_id}/rating", response_model=dict)
async def rate_tool(
    tool_id: str,
    request: ToolRatingRequest,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_session),
):
    """Record an operator rating for a tool (Gap 7).

    Args:
        tool_id: Tool identifier
        request: Rating (1-5) and optional feedback text
        session: Current user session (for tenant isolation)

    Returns:
        Confirmation and aggregated feedback stats
    """
    try:
        # Validate rating
        if not 1 <= request.rating <= 5:
            raise HTTPException(status_code=400, detail="Rating must be 1-5")

        # Record rating (synchronous: EventEmitter.emit()/EventStore.write_event() are sync)
        handler.record_tool_rating(
            tool_id=tool_id,
            tool_name=tool_id,  # Will be overridden by event payload if available
            rating=request.rating,
            tenant_id=session.tenant_id,
            feedback_text=request.feedback_text,
            task_id=request.task_id,
            session_id=session.session_id if hasattr(session, 'session_id') else None,
            instance_id=session.instance_id if hasattr(session, 'instance_id') else "console",
        )

        # Get updated feedback stats
        stats = handler.get_tool_feedback_stats(
            tool_id=tool_id,
            tenant_id=session.tenant_id,
            use_cache=False,  # Force fresh calculation
        )

        return {
            "tool_id": tool_id,
            "rating_recorded": request.rating,
            "feedback_stats": {
                "sample_count": stats.sample_count,
                "average_rating": round(stats.average_rating, 2),
                "confidence": round(stats.confidence, 2),
                "sentiment": stats.feedback_sentiment,
            },
            "status": "success",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record tool rating: {str(e)}")


@router.post("/skills/{skill_id}/rating", response_model=dict)
async def rate_skill(
    skill_id: str,
    request: SkillRatingRequest,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_session),
):
    """Record an operator rating for a skill (Gap 7).

    Args:
        skill_id: Skill identifier
        request: Rating (1-5) and optional feedback text
        session: Current user session (for tenant isolation)

    Returns:
        Confirmation and aggregated feedback stats
    """
    try:
        # Validate rating
        if not 1 <= request.rating <= 5:
            raise HTTPException(status_code=400, detail="Rating must be 1-5")

        # Record rating (synchronous: EventEmitter.emit()/EventStore.write_event() are sync)
        handler.record_skill_rating(
            skill_id=skill_id,
            skill_name=skill_id,  # Will be overridden by event payload if available
            rating=request.rating,
            tenant_id=session.tenant_id,
            feedback_text=request.feedback_text,
            task_id=request.task_id,
            session_id=session.session_id if hasattr(session, 'session_id') else None,
            instance_id=session.instance_id if hasattr(session, 'instance_id') else "console",
        )

        # Get updated feedback stats
        stats = handler.get_skill_feedback_stats(
            skill_id=skill_id,
            tenant_id=session.tenant_id,
            use_cache=False,  # Force fresh calculation
        )

        return {
            "skill_id": skill_id,
            "rating_recorded": request.rating,
            "feedback_stats": {
                "sample_count": stats.sample_count,
                "average_rating": round(stats.average_rating, 2),
                "confidence": round(stats.confidence, 2),
                "sentiment": stats.feedback_sentiment,
            },
            "status": "success",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record skill rating: {str(e)}")


@router.get("/tools/{tool_id}/feedback", response_model=FeedbackStatsResponse)
async def get_tool_feedback(
    tool_id: str,
    window_days: int = 7,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_session),
):
    """Retrieve aggregated feedback statistics for a tool (Gap 7).

    Args:
        tool_id: Tool identifier
        window_days: Time window for aggregation (default 7 days)
        session: Current user session (for tenant isolation)

    Returns:
        Aggregated feedback statistics
    """
    try:
        stats = handler.get_tool_feedback_stats(
            tool_id=tool_id,
            tenant_id=session.tenant_id,
            window_days=window_days,
        )

        return FeedbackStatsResponse(
            entity_id=stats.entity_id,
            entity_type=stats.entity_type,
            entity_name=stats.entity_name,
            sample_count=stats.sample_count,
            average_rating=round(stats.average_rating, 2),
            median_rating=float(stats.median_rating),
            std_dev=round(stats.std_dev, 2) if stats.std_dev else None,
            min_rating=stats.min_rating,
            max_rating=stats.max_rating,
            confidence=round(stats.confidence, 2),
            feedback_sentiment=stats.feedback_sentiment,
            window_days=stats.window_days,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve tool feedback: {str(e)}")


@router.get("/skills/{skill_id}/feedback", response_model=FeedbackStatsResponse)
async def get_skill_feedback(
    skill_id: str,
    window_days: int = 7,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_session),
):
    """Retrieve aggregated feedback statistics for a skill (Gap 7).

    Args:
        skill_id: Skill identifier
        window_days: Time window for aggregation (default 7 days)
        session: Current user session (for tenant isolation)

    Returns:
        Aggregated feedback statistics
    """
    try:
        stats = handler.get_skill_feedback_stats(
            skill_id=skill_id,
            tenant_id=session.tenant_id,
            window_days=window_days,
        )

        return FeedbackStatsResponse(
            entity_id=stats.entity_id,
            entity_type=stats.entity_type,
            entity_name=stats.entity_name,
            sample_count=stats.sample_count,
            average_rating=round(stats.average_rating, 2),
            median_rating=float(stats.median_rating),
            std_dev=round(stats.std_dev, 2) if stats.std_dev else None,
            min_rating=stats.min_rating,
            max_rating=stats.max_rating,
            confidence=round(stats.confidence, 2),
            feedback_sentiment=stats.feedback_sentiment,
            window_days=stats.window_days,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve skill feedback: {str(e)}")


# ── Method Discovery (ADR-0548, Phase 1) ────────────────────────────────────


class MethodPatternJSON(BaseModel):
    """One discovered workstyle pattern, as the dashboard consumes it."""

    pattern_id: str
    pattern_name: str
    task_type: str
    skill_sequence: list[str]
    success_rate: float
    observation_count: int
    confidence_score: float
    first_observed: str
    last_observed: str
    observation_ids: list[str]
    user_confirmed: bool
    discovered: bool  # confidence >= threshold, or user-confirmed
    confidence_derivation: dict
    confidence_explanation: str


class MethodPatternsResponse(BaseModel):
    """Response of ``GET /v1/console/learning/patterns``."""

    tenant_id: str
    threshold: float
    observation_count: int
    chain_verified: bool
    chain_error: Optional[str] = None
    patterns: list[MethodPatternJSON]


async def _method_patterns_response(tenant_id: str) -> dict:
    """Build the patterns response for one tenant.

    Split out from the route so the E2E test can drive the REAL handler rather
    than a reimplementation of it (a test that rebuilds the response itself
    proves only that the test can do arithmetic).

    Patterns are always re-derived from the audit trail, never read from the
    ``patterns.json`` snapshot: the dashboard must not be able to show a
    pattern the audit chain does not support.
    """
    from core.skills.os_skills.confidence_scorer import ConfidenceScorer
    from core.skills.os_skills.method_discovery import MethodDiscovery

    discovery = MethodDiscovery(tenant_id)
    scored = await discovery.current_patterns()
    verification = await discovery.sink.verify_chain()

    patterns = []
    for pattern, breakdown in scored:
        payload = pattern.to_payload()
        patterns.append(
            {
                **payload,
                "discovered": ConfidenceScorer.is_discoverable(
                    pattern.confidence_score, user_confirmed=pattern.user_confirmed
                ),
                "confidence_derivation": breakdown.to_payload(),
                "confidence_explanation": breakdown.explain(),
            }
        )

    return {
        "tenant_id": tenant_id,
        "threshold": discovery.threshold,
        "observation_count": verification.count,
        "chain_verified": verification.ok,
        "chain_error": verification.error,
        "patterns": patterns,
    }


@router.get("/learning/patterns", response_model=MethodPatternsResponse)
async def get_method_patterns(session = Depends(require_session)):
    """Discovered workstyle patterns for the caller's tenant (ADR-0548).

    Tenant comes from the authenticated ``SessionRecord``, never from an env
    var — cross-tenant pattern leakage is the CRITICAL risk on this feature's
    own risk matrix.

    ``chain_verified`` is reported rather than enforced: a broken chain must be
    VISIBLE to the operator, and 500-ing here would hide the one signal that
    says the trail was tampered with. The individual patterns are still derived
    only from records that were on the chain.
    """
    try:
        return MethodPatternsResponse(**await _method_patterns_response(session.tenant_id))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve method patterns: {e}")


@router.post("/learning/patterns/{pattern_id}/confirm", response_model=dict)
async def confirm_method_pattern(pattern_id: str, session = Depends(require_session)):
    """Record an explicit user confirmation of a pattern (CONCEPT-0029 C4).

    Confirmation is only ever taken from an active user action like this one —
    never inferred from behaviour, which is Attack 2 in the concept.
    """
    from core.skills.os_skills.method_discovery import MethodDiscovery

    try:
        discovery = MethodDiscovery(session.tenant_id)
        discovery.confirm_pattern(pattern_id)
        newly = await discovery.discover()
        return {
            "pattern_id": pattern_id,
            "confirmed": True,
            "newly_discovered": [p.pattern_id for p, _ in newly],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to confirm pattern: {e}")
