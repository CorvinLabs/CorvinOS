"""
Learning-loop Console API (ADR-0549 Stages 1–4, CONCEPT-0029 Phase 2).

Endpoints for the Learning Dashboard:
  GET  /v1/console/learning/config-versions       — REAL Skill config history (SkillAdapter)
  POST /v1/console/learning/feedback              — user feedback → hypotheses → optimizer
  POST /v1/console/learning/config/rollback       — REAL rollback (404 on unknown version)
  GET  /v1/console/learning/preferences           — preferences learned from recorded outcomes
  POST /v1/console/learning/preferences/confirm   — records a PREFERENCE learning event
  GET  /v1/console/learning/health                — learning system availability

``GET /v1/console/learning/patterns`` is served by ``routes/learning.py``
(ADR-0548 method discovery, chain-verified) — it is NOT duplicated here.

History (adversarial review 2026-09-06, F3 — CRITICAL): until this rewrite
every endpoint here answered with hard-coded MOCK data: a fabricated config
history, fabricated preferences, ``POST /feedback`` discarded the feedback and
said "received", ``POST /config/rollback`` said "success" and rolled nothing
back. The dashboard was a facade — the audit chain said nothing happened while
the UI told the operator it had. Everything below is now backed by the real
subsystems and every mutation is audited; an empty history is returned as
EMPTY, never dressed up.

Loop closure (what makes this a learning LOOP, not a log):
  feedback (here) → ``FeedbackInterpreter`` hypotheses → ``SkillAdapter``
  optimizer epoch, fed with the REAL recent task outcomes recorded by
  ``core.learning.outcome_sink`` → accepted hypothesis persisted as a new
  config version → ``DelegationRouterSkill`` reads that config on its next
  (shadow) execution from ``delegation_policy.resolve_worker_engine``.

Compliance:
  * tenant ONLY from the authenticated ``SessionRecord`` (never body/env);
  * mutations require CSRF (``require_csrf``); reads require a session;
  * audit: console chain ``action_performed`` for every mutation + a
    hash-chained learning event (``EventStore`` is audit-first, ADR-0314);
  * content-free: the free-text ``reason`` is never written to any chain —
    only the closed-enum ``outcome_quality`` and ``would_repeat``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

# Import backend components (stripped install → endpoints answer 503, never mock)
try:
    from core.skills.os_skills.feedback_loop import FeedbackInterpreter, UserFeedback
    from core.skills.os_skills.skill_adapter import SkillAdapter
    from core.skills.os_skills.workstyle_model import PreferenceInferencer
    from core.learning.outcome_sink import learning_emitter, recent_outcomes

    _LEARNING_AVAILABLE = True
except ImportError:  # pragma: no cover - stripped install without core.skills
    FeedbackInterpreter = None  # type: ignore[assignment]
    UserFeedback = None  # type: ignore[assignment]
    SkillAdapter = None  # type: ignore[assignment]
    PreferenceInferencer = None  # type: ignore[assignment]
    learning_emitter = None  # type: ignore[assignment]
    recent_outcomes = None  # type: ignore[assignment]
    _LEARNING_AVAILABLE = False

# Backwards-compat names for callers/tests that probe availability.
MethodDiscovery = SkillAdapter if _LEARNING_AVAILABLE else None


router = APIRouter(prefix="/learning", tags=["learning"])

#: The only Skill Phase 2 tunes (ADR-0549) — a closed choice, not a free id.
TUNABLE_SKILLS: tuple[str, ...] = ("os.delegation_router",)


# ── Request/Response Models ──────────────────────────────────────────────

class ConfigVersionDTO(BaseModel):
    """Skill Config Version DTO"""
    version_id: str
    timestamp: str
    change_reason: str
    improvement_pct: float
    user_can_undo: bool
    config: dict[str, float] = Field(default_factory=dict)


class UserFeedbackRequest(BaseModel):
    """User submits feedback on a task"""
    task_id: str = Field(min_length=1, max_length=128)
    outcome_quality: str  # excellent, good, okay, poor, bad
    would_repeat: Optional[bool] = None
    reason: Optional[str] = Field(default=None, max_length=2000)


class HypothesisDTO(BaseModel):
    hypothesis_id: str
    skill_id: str
    param: str
    delta: float
    reason: str
    confidence: float
    accepted: bool
    optimizer_reason: str


class FeedbackResponse(BaseModel):
    status: str
    task_id: str
    feedback_type: str
    learning_event_queued: bool
    recent_outcomes: dict[str, int]
    hypotheses: list[HypothesisDTO]
    current_config: dict[str, float]
    current_version: Optional[str]


class PreferencesDTO(BaseModel):
    """User workstyle preferences"""
    task_type: str
    confidence_score: float
    preferred_skills: dict[str, float]
    observation_count: int


_QUALITIES = ("excellent", "good", "okay", "poor", "bad")


# ── helpers ─────────────────────────────────────────────────────────────

def _require_learning() -> None:
    if not _LEARNING_AVAILABLE:
        raise HTTPException(status_code=503, detail="Learning system not available")


def _skill_id(skill_id: str) -> str:
    if skill_id not in TUNABLE_SKILLS:
        raise HTTPException(status_code=400, detail=f"unknown tunable skill: {skill_id}")
    return skill_id


def _adapter(rec: session_auth.SessionRecord, skill_id: str) -> "SkillAdapter":
    """The tenant's SkillAdapter, announcing config changes to the audit chain."""

    def _announce(change: dict[str, Any]) -> None:
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action=f"skill_config_updated:{change.get('change')}",
            target_kind="skill_config",
            target_id=f"{skill_id}@{change.get('version_id') or 'baseline'}",
        )
        _emit_learning(
            "config_updated",
            rec.tenant_id,
            skill_id,
            {
                "change": change.get("change"),
                "version_id": change.get("version_id"),
                "param": change.get("param"),
                "delta": change.get("delta"),
                "to_version": change.get("to_version"),
            },
            config_delta={"config": change.get("config")},
        )

    return SkillAdapter(skill_id, rec.tenant_id, on_config_change=_announce)


def _emit_learning(
    event_type: str,
    tenant_id: str,
    skill_id: str,
    signal: dict[str, Any],
    *,
    config_delta: Optional[dict[str, Any]] = None,
) -> bool:
    """Queue one learning event on the booted (audit-first) emitter."""
    em = learning_emitter() if learning_emitter else None
    if em is None:
        logger.warning("learning emitter not booted — %s event for %s not recorded", event_type, skill_id)
        return False
    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        ev = LearningEvent.create(
            event_type=EventType(event_type),
            skill_id=skill_id,
            tenant_id=tenant_id,
            signal={**signal, "source": "console.learning"},
            lom="core/console/corvin_console/routes/method_discovery_api.py:_emit_learning",
        )
        if config_delta:
            from dataclasses import replace  # noqa: PLC0415

            ev = replace(ev, skill_config_delta=config_delta)
        return bool(em.emit(ev))
    except Exception as exc:  # noqa: BLE001 — a learning write must not 500 the request
        logger.error("learning event not queued (%s): %s", event_type, exc)
        return False


def _version_dto(v: Any) -> ConfigVersionDTO:
    return ConfigVersionDTO(
        version_id=v.version_id,
        timestamp=v.timestamp.isoformat(),
        change_reason=v.change_reason,
        improvement_pct=round(float(v.improvement_pct), 3),
        user_can_undo=bool(v.user_can_undo),
        config=v.config.to_dict(),
    )


# ── Routes ──────────────────────────────────────────────────────────────

@router.get("/config-versions", response_model=list[ConfigVersionDTO])
async def list_config_versions(
    skill_id: str = Query("os.delegation_router"),
    rec: session_auth.SessionRecord = Depends(require_session),
) -> list[ConfigVersionDTO]:
    """Real Skill config version history for the caller's tenant (for rollback).

    Empty until the optimizer has accepted a hypothesis — that is the honest
    answer, not a placeholder history.
    """
    _require_learning()
    adapter = SkillAdapter(_skill_id(skill_id), rec.tenant_id)
    return [_version_dto(v) for v in adapter.get_version_history()]


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    request: UserFeedbackRequest,
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> FeedbackResponse:
    """User feedback on a task → hypotheses → one optimizer epoch (ADR-0549).

    1. ``UserFeedback`` (validated, tenant from the session)
    2. ``FeedbackInterpreter`` → deterministic ``ConfigHypothesis`` list
    3. each hypothesis → ``SkillAdapter.run_optimizer_epoch`` fed with the
       REAL recent task outcomes (``outcome_sink.recent_outcomes``)
    4. audit: console chain ``learning.feedback_received`` + hash-chained
       ``feedback`` learning event; an accepted hypothesis additionally
       announces ``skill_config_updated`` (via the adapter).
    """
    _require_learning()
    if request.outcome_quality not in _QUALITIES:
        raise HTTPException(status_code=400, detail=f"outcome_quality must be one of {_QUALITIES}")

    feedback = UserFeedback(
        task_id=request.task_id,
        tenant_id=rec.tenant_id,
        timestamp=datetime.now(timezone.utc),
        outcome_quality=request.outcome_quality,  # type: ignore[arg-type]
        would_repeat=request.would_repeat,
        reason=request.reason,
    )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=f"learning.feedback_received:{request.outcome_quality}",
        target_kind="task",
        target_id=request.task_id,
    )
    queued = _emit_learning(
        "feedback",
        rec.tenant_id,
        TUNABLE_SKILLS[0],
        {
            "task_id": request.task_id,
            "outcome_quality": request.outcome_quality,
            "would_repeat": request.would_repeat,
            "has_reason": bool(request.reason),  # the text itself never enters a chain
        },
    )

    hypotheses = FeedbackInterpreter().interpret(feedback)
    successes, total = recent_outcomes(rec.tenant_id, limit=10)

    adapters: dict[str, SkillAdapter] = {}
    out: list[HypothesisDTO] = []
    for hyp in hypotheses:
        adapter = adapters.get(hyp.skill_id)
        if adapter is None:
            adapter = adapters[hyp.skill_id] = _adapter(rec, _skill_id(hyp.skill_id))
        accepted, why = adapter.run_optimizer_epoch(hyp, successes, total)
        out.append(
            HypothesisDTO(
                hypothesis_id=hyp.hypothesis_id,
                skill_id=hyp.skill_id,
                param=hyp.param,
                delta=hyp.delta,
                reason=hyp.reason,
                confidence=hyp.confidence,
                accepted=accepted,
                optimizer_reason=why,
            )
        )

    primary = adapters.get(TUNABLE_SKILLS[0]) or SkillAdapter(TUNABLE_SKILLS[0], rec.tenant_id)
    history = primary.get_version_history()
    return FeedbackResponse(
        status="recorded",
        task_id=request.task_id,
        feedback_type=request.outcome_quality,
        learning_event_queued=queued,
        recent_outcomes={"successes": successes, "total": total},
        hypotheses=out,
        current_config=primary.get_current_config().to_dict(),
        current_version=history[-1].version_id if history else None,
    )


@router.post("/config/rollback")
async def rollback_config(
    skill_id: str = Query("os.delegation_router"),
    to_version: str = Query(..., min_length=1, max_length=32),
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> dict[str, Any]:
    """Rollback a Skill config to a prior version (real; 404 when unknown)."""
    _require_learning()
    adapter = _adapter(rec, _skill_id(skill_id))
    try:
        config = adapter.rollback(to_version)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"version {to_version!r} not found for {skill_id}")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="learning.config_rollback",
        target_kind="skill_config",
        target_id=f"{skill_id}@{to_version}",
    )
    return {
        "status": "success",
        "skill_id": skill_id,
        "reverted_to": to_version,
        "config": config.to_dict(),
    }


def _outcome_observations(tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    """Recorded task outcomes grouped by task_type, shaped for the inferencer."""
    em = learning_emitter() if learning_emitter else None
    store = getattr(em, "store", None)
    if store is None:
        return {}
    from core.learning.learning_events import EventType  # noqa: PLC0415

    grouped: dict[str, list[dict[str, Any]]] = {}
    for ev in store.query_events(tenant_id, event_type=EventType.OUTCOME, limit=5000):
        sig = ev.signal or {}
        task_type = str(sig.get("task_type") or "general")
        engine = sig.get("engine")
        grouped.setdefault(task_type, []).append(
            {
                "outcome": "success" if sig.get("success") is True else "failure",
                "skill_sequence": [str(engine)] if engine else [],
                "timestamp": ev.timestamp,
            }
        )
    return grouped


@router.get("/preferences", response_model=dict[str, PreferencesDTO])
async def get_preferences(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> dict[str, PreferencesDTO]:
    """Preferences inferred from the tenant's RECORDED task outcomes.

    Empty until outcomes exist — never a fabricated profile.
    """
    _require_learning()
    result: dict[str, PreferencesDTO] = {}
    for task_type, observations in _outcome_observations(rec.tenant_id).items():
        recent = observations[-10:]
        try:
            prefs = PreferenceInferencer.infer_preferences(task_type, recent)
            confidence, preferred, count = (
                float(prefs.confidence_score), dict(prefs.preferred_skills), int(prefs.observation_count)
            )
        except ValueError:
            # Routing-level task types ("chat", "delegate", "big_data") are not
            # in ADR-0550's closed TASK_TYPES vocabulary; aggregate them with the
            # inferencer's own arithmetic instead of dropping them silently.
            successful = [o for o in recent if o.get("outcome") == "success"]
            counts: dict[str, int] = {}
            for o in successful:
                for skill in o.get("skill_sequence") or []:
                    counts[skill] = counts.get(skill, 0) + 1
            preferred = {k: v / len(successful) for k, v in counts.items()} if successful else {}
            count = len(successful)
            confidence = (
                PreferenceInferencer._compute_confidence(
                    observation_count=count,
                    consistency=PreferenceInferencer._compute_consistency(recent),
                    recency=PreferenceInferencer._recency_boost(recent),
                )
                if successful else 0.0
            )
        result[task_type] = PreferencesDTO(
            task_type=task_type,
            confidence_score=round(confidence, 3),
            preferred_skills={k: round(float(v), 3) for k, v in preferred.items()},
            observation_count=count,
        )
    return result


@router.post("/preferences/confirm")
async def confirm_preference(
    task_type: str = Query(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"),
    rec: session_auth.SessionRecord = Depends(require_csrf),
) -> dict[str, Any]:
    """User confirms a learned preference → PREFERENCE learning event (audited)."""
    _require_learning()
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="learning.preference_confirmed",
        target_kind="task_type",
        target_id=task_type,
    )
    queued = _emit_learning(
        "preference",
        rec.tenant_id,
        TUNABLE_SKILLS[0],
        {"task_type": task_type, "confirmed": True},
    )
    return {
        "status": "confirmed",
        "task_type": task_type,
        "learning_event_queued": queued,
    }


# ── Health Check ─────────────────────────────────────────────────────────

@router.get("/health")
async def health_check(
    rec: session_auth.SessionRecord = Depends(require_session),
) -> dict[str, Any]:
    """Learning-loop availability: subsystem importable AND an emitter booted."""
    em = learning_emitter() if (_LEARNING_AVAILABLE and learning_emitter) else None
    return {
        "status": "operational" if (_LEARNING_AVAILABLE and em is not None) else "unavailable",
        "learning_system": "adr_0549_feedback_loop",
        "subsystem_importable": _LEARNING_AVAILABLE,
        "emitter_booted": em is not None,
        "tunable_skills": list(TUNABLE_SKILLS),
    }
