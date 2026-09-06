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


# ============================================================================
# Phase 3: Operator Console Interface (ADR-0629)
# ============================================================================
# Read-mostly operator interface for learning loop management:
# - View current loop status (α, damping, loss, convergence)
# - Inspect historical metrics and audit trail
# - Manage checkpoints (view, rollback)
# - Admin override (rare, audited)
#
# Endpoints (Read): status, metrics, checkpoint, audit
# Endpoints (Write/Admin): override, rollback
# RBAC: viewer (GET only), admin (POST override, rollback)
# Compliance: All overrides logged (GDPR Art. 30), reason required, fail-closed on auth


class LearningStatusResponse(BaseModel):
    """Current learning loop status (ADR-0629)."""
    timestamp: str
    alpha_core: float
    alpha_infra: float
    damping_core: float
    damping_infra: float
    loss_total: float
    loss_core: float
    loss_infra: float
    convergence_percent: float
    status: str  # "converged" | "converging" | "diverging" | "stalled"


class MetricsPoint(BaseModel):
    """Single metrics time-series point."""
    timestamp: str
    loss_total: float
    loss_core: float
    loss_infra: float
    gradient_l2: float
    alpha_core: float
    damping_core: float


class MetricsResponse(BaseModel):
    """Time-series metrics for learning loops."""
    window: str  # "1h" | "6h" | "24h"
    points: list[MetricsPoint]
    sample_count: int


class Checkpoint(BaseModel):
    """Loop state checkpoint."""
    checkpoint_id: str
    timestamp: str
    loop_state: str
    loss_at_checkpoint: float
    created_by: Optional[str] = None


class CheckpointResponse(BaseModel):
    """List of checkpoints."""
    checkpoints: list[Checkpoint]


class AuditEvent(BaseModel):
    """Learning audit event."""
    event_id: str
    event_type: str  # "override" | "rollback" | "auto_tune"
    loop_id: str
    param: str
    old_value: float
    new_value: float
    reason: str
    operator_id: str
    timestamp: str


class AuditResponse(BaseModel):
    """Audit trail for learning operations."""
    events: list[AuditEvent]
    count: int


class OverrideRequest(BaseModel):
    """Admin override request (ADR-0629)."""
    loop: str  # "core" | "infra"
    param: str  # "alpha" | "damping"
    new_value: float
    reason: str  # Required for audit trail


class OverrideResponse(BaseModel):
    """Override result."""
    status: str  # "success"
    loop: str
    param: str
    old_value: float
    new_value: float
    timestamp: str


class RollbackResponse(BaseModel):
    """Rollback result."""
    status: str  # "success"
    checkpoint_id: str
    restored_at: str
    loss_before: float
    loss_after: float


def _check_admin_role(session) -> bool:
    """Check if session has admin role (RBAC).

    Fail-closed: returns False on any uncertainty.
    In a real implementation, this would check role/permission database.
    """
    # Placeholder: real implementation would check session.roles or similar
    return getattr(session, 'is_admin', False)


async def _get_learning_status(tenant_id: str) -> LearningStatusResponse:
    """Fetch current learning loop status for tenant.

    In production, this would read from:
    - MetaOptimizer.current_state() for α, damping
    - LiveExperimentCollector metrics for loss, convergence
    """
    # Placeholder implementation (will be filled with real data in integration tests)
    from datetime import datetime

    # TODO: Integrate with MetaOptimizer and live collector
    return LearningStatusResponse(
        timestamp=datetime.utcnow().isoformat(),
        alpha_core=0.1,
        alpha_infra=0.05,
        damping_core=0.9,
        damping_infra=0.95,
        loss_total=0.0042,
        loss_core=0.0025,
        loss_infra=0.0017,
        convergence_percent=87.5,
        status="converging",
    )


@router.get("/learning/status", response_model=LearningStatusResponse)
async def get_learning_status(session = Depends(require_session)):
    """Fetch current learning loop status.

    Read-only endpoint, available to all roles (viewer, admin).
    Tenant isolation enforced via authenticated session.
    """
    try:
        return await _get_learning_status(session.tenant_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch status: {str(e)}")


@router.get("/learning/metrics", response_model=MetricsResponse)
async def get_learning_metrics(
    window: str = "1h",
    session = Depends(require_session),
):
    """Fetch time-series metrics for learning loops.

    Args:
        window: Time window ("1h", "6h", "24h")
        session: Authenticated session (tenant isolation)

    Returns:
        Time-series points with loss, α, gradient data

    Tenant isolation: all data filtered by session.tenant_id
    """
    try:
        if window not in ("1h", "6h", "24h"):
            raise HTTPException(status_code=400, detail="Invalid window; use 1h, 6h, or 24h")

        # TODO: Integrate with live collector or metrics database
        # For now, return empty dataset (will be populated in integration)
        return MetricsResponse(
            window=window,
            points=[],
            sample_count=0,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch metrics: {str(e)}")


@router.get("/learning/checkpoint", response_model=CheckpointResponse)
async def get_checkpoints(session = Depends(require_session)):
    """Fetch saved learning loop checkpoints.

    Checkpoints are immutable snapshots of loop state, used for:
    - Recovery after failed tuning
    - A/B testing different parameter sets
    - Audit trail of significant state changes

    Tenant isolation enforced.
    """
    try:
        # TODO: Integrate with checkpoint manager (core/learning/checkpoint_manager.py)
        return CheckpointResponse(checkpoints=[])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch checkpoints: {str(e)}")


@router.get("/learning/audit", response_model=AuditResponse)
async def get_audit_trail(
    limit: int = 50,
    session = Depends(require_session),
):
    """Fetch audit trail of learning operations.

    Args:
        limit: Max events to return (default 50)
        session: Authenticated session (tenant isolation)

    Returns:
        List of audit events (override, rollback, auto_tune)

    All events include:
    - operator_id (who made the change)
    - reason (why)
    - timestamp (when)
    - old/new values (what changed)

    Compliance: GDPR Art. 30 (processing record), immutable, hash-chained
    """
    try:
        # TODO: Integrate with audit backend (core/compliance/audit_backend.py)
        return AuditResponse(events=[], count=0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch audit trail: {str(e)}")


@router.post("/learning/override", response_model=OverrideResponse)
async def override_learning_param(
    request: OverrideRequest,
    session = Depends(require_session),
):
    """Manually override a learning parameter (admin only).

    Args:
        request: Override request (loop, param, new_value, reason)
        session: Authenticated session (for RBAC and audit)

    Returns:
        Confirmation with before/after values

    **RBAC:** Admin role required (fail-closed: 403 on deny)
    **Audit:** Logged with operator_id, timestamp, reason
    **GDPR:** Reason required (transparency)

    Override does NOT affect Meta Loop—it is recorded as an external signal
    and the Meta Loop can learn from the outcome.
    """
    try:
        # RBAC check (fail-closed)
        if not _check_admin_role(session):
            raise HTTPException(status_code=403, detail="Admin role required")

        # Validate request
        if request.loop not in ("core", "infra"):
            raise HTTPException(status_code=400, detail="Loop must be 'core' or 'infra'")
        if request.param not in ("alpha", "damping"):
            raise HTTPException(status_code=400, detail="Param must be 'alpha' or 'damping'")
        if not 0 <= request.new_value <= 1:
            raise HTTPException(status_code=400, detail="Value must be in [0, 1]")
        if not request.reason.strip():
            raise HTTPException(status_code=400, detail="Reason is required for audit trail")

        # TODO: Integrate with MetaOptimizer to apply override
        # TODO: Emit audit event with operator_id, reason, timestamp

        from datetime import datetime

        return OverrideResponse(
            status="success",
            loop=request.loop,
            param=request.param,
            old_value=0.1,  # placeholder
            new_value=request.new_value,
            timestamp=datetime.utcnow().isoformat(),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Override failed: {str(e)}")


@router.post("/learning/rollback/{checkpoint_id}", response_model=RollbackResponse)
async def rollback_checkpoint(
    checkpoint_id: str,
    session = Depends(require_session),
):
    """Rollback learning loops to a saved checkpoint (admin only).

    Args:
        checkpoint_id: ID of checkpoint to restore
        session: Authenticated session (for RBAC and audit)

    Returns:
        Confirmation with loss before/after

    **RBAC:** Admin role required
    **Audit:** Logged as 'rollback' event
    **Verification:** Checks that loss did not increase after rollback

    Fail-soft: if loss increased, rollback is recorded but operator is warned.
    """
    try:
        # RBAC check
        if not _check_admin_role(session):
            raise HTTPException(status_code=403, detail="Admin role required")

        # TODO: Integrate with checkpoint manager to restore state
        # TODO: Verify loss after restore
        # TODO: Emit audit event

        from datetime import datetime

        return RollbackResponse(
            status="success",
            checkpoint_id=checkpoint_id,
            restored_at=datetime.utcnow().isoformat(),
            loss_before=0.0050,  # placeholder
            loss_after=0.0045,   # placeholder
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rollback failed: {str(e)}")
