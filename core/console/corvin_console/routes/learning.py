"""TreeOfThoughts Learning Dashboard API — /v1/console/learning

Endpoints:
  GET  /v1/console/learning/nodes    — fetch all TreeNodes with confidences
  POST /v1/console/learning/grade    — operator grades a pattern (chained, no free text)
  POST /v1/console/learning/note     — operator adds note to pattern (in-memory node)
  POST /v1/console/tools/{tool_id}/rating      — rate a tool (Gap 7)
  POST /v1/console/skills/{skill_id}/rating    — rate a skill (Gap 7)
  GET  /v1/console/tools/{tool_id}/feedback    — tool feedback stats (Gap 7)
  GET  /v1/console/skills/{skill_id}/feedback  — skill feedback stats (Gap 7)
  GET  /v1/console/learning/patterns           — discovered workstyle patterns (ADR-0548)
  POST /v1/console/learning/patterns/{id}/confirm
  GET  /v1/console/learning/status     — REAL learning-loop status from the EventStore
  GET  /v1/console/learning/metrics    — REAL time series (bucketed OUTCOME/FEEDBACK events)
  GET  /v1/console/learning/checkpoint — REAL Skill config versions (the rollback points)
  GET  /v1/console/learning/audit      — REAL ``learning.*`` records from the core hash chain
  POST /v1/console/learning/override   — 501 (no live meta loop to override)
  POST /v1/console/learning/rollback/{checkpoint_id} — 501 (use POST learning/config/rollback)

Until 2026-09-07 ``status``/``metrics``/``checkpoint``/``audit`` answered with
hard-coded constants (``alpha_core=0.1``, ``convergence_percent=87.5``, ``[]``)
and ``override``/``rollback`` reported ``"success"`` while changing nothing,
gated on a ``session.is_admin`` attribute that no ``SessionRecord`` has
(adversarial review F-L2). Everything here is now computed from the
audit-first ``event_store.EventStore`` and the core chain, or answers 503
"not wired" — never a constant.

Tenant: always ``session.tenant_id`` from the authenticated ``SessionRecord``.
Mutations: ``require_csrf``. Free text (``feedback_text``, grade ``reason``)
is accepted but NEVER persisted (only presence + length).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

# Optional: core.learning integration (may not be available in all environments)
try:
    from core.learning import LearningIntegration
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType
    from core.learning.operator_feedback import OperatorFeedbackHandler
except ImportError:  # pragma: no cover - stripped install
    LearningIntegration = None  # type: ignore
    EventStore = None  # type: ignore
    EventType = None  # type: ignore
    OperatorFeedbackHandler = None  # type: ignore


def _tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` — honours CORVIN_HOME (never a bare ~/.corvin)."""
    from core.paths.tenant import tenant_home  # noqa: PLC0415

    return tenant_home(tenant_id)


def _require_learning() -> None:
    """503 on a stripped install — the pattern of method_discovery_api.py, never a mock."""
    if EventStore is None or EventType is None:
        raise HTTPException(status_code=503, detail="learning subsystem not wired (core.learning unavailable)")


router = APIRouter()


class GradeRequest(BaseModel):
    """Operator grades a pattern."""
    pattern_id: str
    grade: float  # -1.0 to +1.0
    reason: str = ""  # accepted, never persisted (presence + length only)


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
    feedback_text: Optional[str] = None  # accepted, never persisted (has_text/text_length only)
    task_id: Optional[str] = None


class SkillRatingRequest(BaseModel):
    """Operator rates a skill execution."""
    rating: int  # 1-5
    feedback_text: Optional[str] = None  # accepted, never persisted (has_text/text_length only)
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


def _event_store(tenant_id: str) -> "EventStore":
    """The tenant-BOUND audit-first store (rejects events of any other tenant)."""
    _require_learning()
    return EventStore(_tenant_home(tenant_id), tenant_id=tenant_id)


def get_feedback_handler(session = Depends(require_session)) -> OperatorFeedbackHandler:
    """Get OperatorFeedbackHandler for this tenant.

    The store is ``event_store.EventStore(tenant_home, tenant_id)`` — the SAME
    store the EventEmitter writes to — rooted at ``<corvin_home>/tenants/<tenant_id>/``
    (events land in ``learning/events/YYYY-MM-DD.jsonl``).
    """
    return OperatorFeedbackHandler(_event_store(session.tenant_id))


def get_learning_integration(session = Depends(require_session)) -> LearningIntegration:
    """Get LearningIntegration for this tenant (``<tenant_home>/learning/``)."""
    _require_learning()
    store_path = _tenant_home(session.tenant_id) / "learning"
    return LearningIntegration(store_path, tenant_id=session.tenant_id)


@router.get("/learning/debug", response_model=dict)
async def debug_learning(session = Depends(require_session)):
    """Debug endpoint — test if learning system is initialized."""
    store_path = _tenant_home(session.tenant_id) / "learning"
    return {
        "tenant_id": session.tenant_id,
        "store_path": str(store_path),
        "store_exists": store_path.exists(),
        "store_is_dir": store_path.is_dir() if store_path.exists() else None,
    }


@router.get("/learning/nodes", response_model=dict)
async def get_learning_nodes(
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_session),
):
    """Fetch all TreeNodes (Pattern/Method/Framework) with current confidences."""
    try:
        store = integration.store
        nodes = store.all_nodes()

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
        logger.exception("learning/nodes failed for tenant %s", session.tenant_id)
        raise HTTPException(status_code=500, detail=f"Failed to load nodes: {type(e).__name__}")


@router.post("/learning/grade")
async def grade_pattern(
    request: GradeRequest,
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_csrf),
):
    """Operator manually grades a pattern.

    The grade is committed to the core hash chain + ADR-0314 store BEFORE the
    confidence moves (``LearningIntegration.grade_pattern``); the free-text
    ``reason`` is not stored.
    """
    grade = max(-1.0, min(1.0, request.grade))
    try:
        event_id = integration.grade_pattern(request.pattern_id, grade, reason=request.reason)
    except RuntimeError as e:  # chain did not commit → nothing was graded
        raise HTTPException(status_code=503, detail=f"audit chain unavailable: {e}")

    node = integration.store.get_node(request.pattern_id)
    return {
        "pattern_id": request.pattern_id,
        "new_confidence": node.confidence if node else None,
        "event_id": event_id,
        "status": "success",
    }


@router.post("/learning/note")
async def add_operator_note(
    request: NoteRequest,
    integration: LearningIntegration = Depends(get_learning_integration),
    session = Depends(require_csrf),
):
    """Operator adds a note to a pattern (in-memory node; attributed by session fingerprint)."""
    node = integration.store.get_node(request.pattern_id)
    if not node:
        raise HTTPException(status_code=404, detail="Pattern not found")

    node.add_operator_note(session.sid_fingerprint, request.text)

    return {
        "pattern_id": request.pattern_id,
        "notes_count": len(node.operator_notes),
        "status": "success",
    }


# ============================================================================
# Gap 7: Operator Feedback Loop API Endpoints
# ============================================================================


def _record_rating(kind: str, entity_id: str, request, handler, session) -> dict:
    if not 1 <= request.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be 1-5")
    common = dict(
        rating=request.rating,
        tenant_id=session.tenant_id,
        feedback_text=request.feedback_text,  # presence + length only are persisted
        task_id=request.task_id,
        session_id=None,  # the session id is a secret; never into a learning record
        instance_id="console",
    )
    try:
        if kind == "tool":
            handler.record_tool_rating(tool_id=entity_id, tool_name=entity_id, **common)
            stats = handler.get_tool_feedback_stats(tool_id=entity_id, tenant_id=session.tenant_id, use_cache=False)
        else:
            handler.record_skill_rating(skill_id=entity_id, skill_name=entity_id, **common)
            stats = handler.get_skill_feedback_stats(skill_id=entity_id, tenant_id=session.tenant_id, use_cache=False)
    except RuntimeError as e:  # chain did not commit → the rating was NOT recorded
        raise HTTPException(status_code=503, detail=f"audit chain unavailable: {e}")
    return {
        f"{kind}_id": entity_id,
        "rating_recorded": request.rating,
        "feedback_stats": {
            "sample_count": stats.sample_count,
            "average_rating": round(stats.average_rating, 2),
            "confidence": round(stats.confidence, 2),
            "sentiment": stats.feedback_sentiment,
        },
        "status": "success",
    }


@router.post("/tools/{tool_id}/rating", response_model=dict)
async def rate_tool(
    tool_id: str,
    request: ToolRatingRequest,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_csrf),
):
    """Record an operator rating for a tool (Gap 7). ``feedback_text`` is never persisted."""
    return _record_rating("tool", tool_id, request, handler, session)


@router.post("/skills/{skill_id}/rating", response_model=dict)
async def rate_skill(
    skill_id: str,
    request: SkillRatingRequest,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_csrf),
):
    """Record an operator rating for a skill (Gap 7). ``feedback_text`` is never persisted."""
    return _record_rating("skill", skill_id, request, handler, session)


def _stats_response(stats) -> FeedbackStatsResponse:
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


@router.get("/tools/{tool_id}/feedback", response_model=FeedbackStatsResponse)
async def get_tool_feedback(
    tool_id: str,
    window_days: int = 7,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_session),
):
    """Retrieve aggregated feedback statistics for a tool (Gap 7)."""
    return _stats_response(
        handler.get_tool_feedback_stats(tool_id=tool_id, tenant_id=session.tenant_id, window_days=window_days)
    )


@router.get("/skills/{skill_id}/feedback", response_model=FeedbackStatsResponse)
async def get_skill_feedback(
    skill_id: str,
    window_days: int = 7,
    handler: OperatorFeedbackHandler = Depends(get_feedback_handler),
    session = Depends(require_session),
):
    """Retrieve aggregated feedback statistics for a skill (Gap 7)."""
    return _stats_response(
        handler.get_skill_feedback_stats(skill_id=skill_id, tenant_id=session.tenant_id, window_days=window_days)
    )


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
async def confirm_method_pattern(pattern_id: str, session = Depends(require_csrf)):
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
# Phase 3: Operator Console Interface (ADR-0629) — REAL data only
# ============================================================================

#: Number of most-recent OUTCOME events that define the "recent" success rate
#: (the same window ``outcome_sink.recent_outcomes`` and the live collector use).
RECENT_OUTCOME_WINDOW = 50
_WINDOWS = {"1h": timedelta(hours=1), "6h": timedelta(hours=6), "24h": timedelta(days=1)}


class RecentOutcomes(BaseModel):
    window: int
    total: int
    successes: int
    success_rate: Optional[float]  # None until at least one outcome exists


class LearningStatusResponse(BaseModel):
    """Current learning-loop status, computed from the tenant's EventStore (ADR-0613)."""
    timestamp: str
    tenant_id: str
    source: str  # "event_store"
    event_counts: dict[str, int]  # per EventType, all time
    recent_outcomes: RecentOutcomes
    outcome_loss: Optional[float]  # 1 - success_rate of the recent outcomes; None without outcomes
    last_outcome_at: Optional[str]
    last_feedback_at: Optional[str]
    last_config_update_at: Optional[str]
    status: str  # "no_data" | "collecting" | "learning"


class MetricsPoint(BaseModel):
    """One bucket of the real event time series."""
    timestamp: str  # bucket start (UTC ISO)
    outcomes: int
    successes: int
    success_rate: Optional[float]
    feedback: int
    skill_executions: int
    config_updates: int


class MetricsResponse(BaseModel):
    """Time series of learning events over a window."""
    window: str  # "1h" | "6h" | "24h"
    start: str
    end: str
    bucket_seconds: int
    points: list[MetricsPoint]
    sample_count: int  # events in the window


class Checkpoint(BaseModel):
    """A persisted Skill config version — the real rollback point (ADR-0613)."""
    checkpoint_id: str  # version_id ("v1", "v2", …)
    skill_id: str
    timestamp: str
    change_reason: str
    improvement_pct: float
    config: dict[str, float]


class CheckpointResponse(BaseModel):
    checkpoints: list[Checkpoint]


class AuditEvent(BaseModel):
    """One ``learning.*`` record from the core hash chain (content-free by construction)."""
    audit_ref: Optional[str]
    event_type: str
    timestamp: str
    skill_id: Optional[str]
    lom: Optional[str]
    hash: Optional[str]
    prev_hash: Optional[str]


class AuditResponse(BaseModel):
    events: list[AuditEvent]
    count: int
    chain_path: str


class OverrideRequest(BaseModel):
    loop: str
    param: str
    new_value: float
    reason: str


def _event_time(ts: str) -> datetime:
    return datetime.fromisoformat(ts.rstrip("Z"))


def _events_since(store, tenant_id: str, event_type, since: datetime) -> list:
    events = store.query_events(
        tenant_id, event_type=event_type, since=since.strftime("%Y-%m-%d"), limit=100000
    )
    return [e for e in events if _event_time(e.timestamp) >= since]


def learning_status(tenant_id: str) -> LearningStatusResponse:
    """Status computed from the tenant's real learning events (no constants).

    Shared by ``GET learning/status``, ``GET learning/metrics/current`` and the
    metrics WebSocket so all three can never disagree.
    """
    store = _event_store(tenant_id)
    counts = {et.value: store.count_events(tenant_id, et) for et in EventType}
    week_ago = datetime.utcnow() - timedelta(days=7)
    outcomes = _events_since(store, tenant_id, EventType.OUTCOME, week_ago)
    feedback = _events_since(store, tenant_id, EventType.FEEDBACK, week_ago)
    config_updates = _events_since(store, tenant_id, EventType.CONFIG_UPDATED, week_ago)

    recent = outcomes[-RECENT_OUTCOME_WINDOW:]
    total = len(recent)
    successes = sum(1 for e in recent if (e.signal or {}).get("success") is True)
    success_rate = (successes / total) if total else None

    if sum(counts.values()) == 0:
        status = "no_data"
    elif counts.get(EventType.CONFIG_UPDATED.value, 0) > 0:
        status = "learning"
    else:
        status = "collecting"

    def _last(events: list) -> Optional[str]:
        return max((e.timestamp for e in events), default=None)

    return LearningStatusResponse(
        timestamp=datetime.utcnow().isoformat() + "Z",
        tenant_id=tenant_id,
        source="event_store",
        event_counts=counts,
        recent_outcomes=RecentOutcomes(
            window=RECENT_OUTCOME_WINDOW, total=total, successes=successes, success_rate=success_rate
        ),
        outcome_loss=(1.0 - success_rate) if success_rate is not None else None,
        last_outcome_at=_last(outcomes),
        last_feedback_at=_last(feedback),
        last_config_update_at=_last(config_updates),
        status=status,
    )


def learning_series(tenant_id: str, window: str, buckets: int = 12) -> MetricsResponse:
    """Real event time series over ``window`` in ``buckets`` equal buckets."""
    if window not in _WINDOWS:
        raise HTTPException(status_code=400, detail="Invalid window; use 1h, 6h, or 24h")
    store = _event_store(tenant_id)
    end = datetime.utcnow()
    start = end - _WINDOWS[window]
    bucket = _WINDOWS[window] / buckets

    per_type = {
        et: _events_since(store, tenant_id, et, start)
        for et in (EventType.OUTCOME, EventType.FEEDBACK, EventType.SKILL_EXECUTED, EventType.CONFIG_UPDATED)
    }

    def _index(ts: str) -> int:
        return min(buckets - 1, int((_event_time(ts) - start) / bucket))

    rows = [
        {"outcomes": 0, "successes": 0, "feedback": 0, "skill_executions": 0, "config_updates": 0}
        for _ in range(buckets)
    ]
    for e in per_type[EventType.OUTCOME]:
        row = rows[_index(e.timestamp)]
        row["outcomes"] += 1
        if (e.signal or {}).get("success") is True:
            row["successes"] += 1
    for et, key in (
        (EventType.FEEDBACK, "feedback"),
        (EventType.SKILL_EXECUTED, "skill_executions"),
        (EventType.CONFIG_UPDATED, "config_updates"),
    ):
        for e in per_type[et]:
            rows[_index(e.timestamp)][key] += 1

    points = [
        MetricsPoint(
            timestamp=(start + i * bucket).isoformat() + "Z",
            success_rate=(r["successes"] / r["outcomes"]) if r["outcomes"] else None,
            **r,
        )
        for i, r in enumerate(rows)
    ]
    return MetricsResponse(
        window=window,
        start=start.isoformat() + "Z",
        end=end.isoformat() + "Z",
        bucket_seconds=int(bucket.total_seconds()),
        points=points,
        sample_count=sum(len(v) for v in per_type.values()),
    )


@router.get("/learning/status", response_model=LearningStatusResponse)
async def get_learning_status(session = Depends(require_session)):
    """Current learning-loop status for the caller's tenant — computed, never constant."""
    return learning_status(session.tenant_id)


@router.get("/learning/metrics", response_model=MetricsResponse)
async def get_learning_metrics(
    window: str = Query("1h"),
    session = Depends(require_session),
):
    """Bucketed time series of the tenant's real learning events."""
    return learning_series(session.tenant_id, window)


@router.get("/learning/checkpoint", response_model=CheckpointResponse)
async def get_checkpoints(session = Depends(require_session)):
    """The real rollback points: persisted Skill config versions (ADR-0613).

    Roll back with ``POST /v1/console/learning/config/rollback?to_version=``.
    """
    try:
        from core.skills.os_skills.skill_adapter import SkillAdapter  # noqa: PLC0415
        from .method_discovery_api import TUNABLE_SKILLS  # noqa: PLC0415
    except ImportError:
        raise HTTPException(status_code=503, detail="skill adapter not wired (core.skills unavailable)")

    checkpoints: list[Checkpoint] = []
    for skill_id in TUNABLE_SKILLS:
        adapter = SkillAdapter(skill_id, session.tenant_id)
        for v in adapter.get_version_history():
            ts = v.timestamp.isoformat() if isinstance(v.timestamp, datetime) else str(v.timestamp)
            checkpoints.append(
                Checkpoint(
                    checkpoint_id=v.version_id,
                    skill_id=v.skill_id,
                    timestamp=ts,
                    change_reason=v.change_reason,
                    improvement_pct=float(v.improvement_pct),
                    config=v.config.to_dict(),
                )
            )
    return CheckpointResponse(checkpoints=checkpoints)


def _chain_path() -> Path:
    from core.learning.event_persistence import _resolve_core_audit  # noqa: PLC0415

    return Path(_resolve_core_audit().audit_path())


@router.get("/learning/audit", response_model=AuditResponse)
async def get_audit_trail(
    limit: int = Query(50, ge=1, le=1000),
    session = Depends(require_session),
):
    """The tenant's ``learning.*`` records from the core hash chain (newest last).

    Every learning event is committed to this chain BEFORE it reaches disk
    (``EventStore.write_event``, audit-first); the records are content-free
    (ids, type, skill, LoM) so nothing here can leak a payload.
    """
    _require_learning()
    try:
        path = _chain_path()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"core audit writer not wired: {e}")

    events: list[AuditEvent] = []
    if path.exists():
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"learning.' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                details = rec.get("details") or {}
                if not str(rec.get("event_type", "")).startswith("learning."):
                    continue
                if details.get("tenant_id") != session.tenant_id:
                    continue
                ts = rec.get("ts")
                events.append(
                    AuditEvent(
                        audit_ref=details.get("audit_ref"),
                        event_type=rec["event_type"],
                        timestamp=datetime.utcfromtimestamp(float(ts)).isoformat() + "Z"
                        if isinstance(ts, (int, float)) else str(ts),
                        skill_id=details.get("skill_id"),
                        lom=details.get("lom"),
                        hash=rec.get("hash"),
                        prev_hash=rec.get("prev_hash"),
                    )
                )
    events = events[-limit:]
    return AuditResponse(events=events, count=len(events), chain_path=str(path))


@router.post("/learning/override")
async def override_learning_param(request: OverrideRequest, session = Depends(require_csrf)):
    """Not implemented: there is no live meta loop whose α/damping could be overridden.

    The only learned, live-consumed configuration is the Skill config
    (``confidence_threshold`` of ``os.delegation_router``); it changes through
    the audited optimizer (``POST learning/feedback``) or a real rollback
    (``POST learning/config/rollback``). Answering ``"success"`` here without
    changing anything — as this route did until 2026-09-07 — is exactly the
    silent no-op the audit-first rule forbids.
    """
    raise HTTPException(
        status_code=501,
        detail="learning parameter override is not wired; use POST /v1/console/learning/config/rollback "
               "for a real, audited config change",
    )


@router.post("/learning/rollback/{checkpoint_id}")
async def rollback_checkpoint(checkpoint_id: str, session = Depends(require_csrf)):
    """Not implemented here — the real rollback is ``POST learning/config/rollback?to_version=``."""
    raise HTTPException(
        status_code=501,
        detail=f"use POST /v1/console/learning/config/rollback?to_version={checkpoint_id} "
               "(real, audited, 404 on unknown version)",
    )
