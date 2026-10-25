"""Console API for Video Producer learning (Phase 4b), tenant-scoped.

Endpoints (under /v1/console/video):
- GET  /jobs/{job_id}/learning-metrics — feedback recorded for one job
- POST /jobs/{job_id}/feedback         — record job-level feedback
- GET  /learning/stats                 — counts over this tenant's feedback
- GET  /learning/health                — whether the feedback store is reachable
- GET  /learning/models                — recorded ratings per production variant (the narration tier
                                         each job's own report.json names), measured, with a sample floor
- GET  /learning/confidence            — distribution of the confidence raters attached to feedback
- POST /learning/select-model          — ADVICE which variant rated best; never applied, and withheld
                                         below the sample floor or without a clear margin
- POST /learning/report-quality        — one overall 1–5 rating for a whole job (audit-first, like a scene's)

These four answered 501 until 2026-10-26. They are descriptive: nothing here chooses a variant for a
job (CONCEPT-0117: a second router is not a connection). The consumer is the Learning tab of the
Video Producer page. Every read and write uses the authenticated session's tenant.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/video", tags=["video-learning"])

_SessionRec = Depends(require_session_csrf_on_mutation)
_SKILL_ID = "os.video_producer"
#: Rated scenes a variant needs before its mean is shown and before it can be advised.
_MIN_VARIANT_SAMPLES = 5
#: Mean-rating lead (on the 1–5 scale) the best variant needs over the runner-up to be advised.
_ADVICE_MARGIN = 0.5
_VARIANT_DIMENSION = "tts_tier"
_JOB_SCOPE_SCENE = "job"


class FeedbackSubmissionRequest(BaseModel):
    scene_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    rating: int = Field(..., ge=1, le=5)
    worker_notes: Optional[str] = Field(None, max_length=500)


def _feedback_events(tenant_id: str) -> List[Any]:
    """This tenant's recorded video-producer feedback events (raises if the
    store cannot be opened — callers decide how to report that)."""
    from core.learning.event_store import EventStore  # noqa: PLC0415
    from core.learning.learning_events import EventType  # noqa: PLC0415
    from core.paths.tenant import tenant_home  # noqa: PLC0415

    store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
    return store.query_events(tenant_id, event_type=EventType.FEEDBACK, skill_id=_SKILL_ID, limit=5000)


def _job_learning_metrics(job_id: str, tenant_id: str) -> Dict[str, Any]:
    events: list = []
    try:
        for ev in _feedback_events(tenant_id):
            sig = ev.signal or {}
            if str(sig.get("task_id")) == job_id:
                events.append({
                    "timestamp": ev.timestamp,
                    "scene_id": sig.get("scene_id"),
                    "outcome": sig.get("outcome_feedback"),
                    "quality_rating": sig.get("quality_rating"),
                    "confidence": sig.get("confidence"),
                    "source": sig.get("source"),
                })
    except Exception as exc:  # noqa: BLE001 — no store, no events; never invent
        logger.debug("learning metrics unavailable (%s)", type(exc).__name__)
    approved = sum(1 for e in events if e["outcome"] == "yes")
    rejected = sum(1 for e in events if e["outcome"] == "no")
    confs = [float(e["confidence"]) for e in events if isinstance(e.get("confidence"), (int, float))]
    return {
        "job_id": job_id,
        "total_feedback_events": len(events),
        "approved": approved,
        "rejected": rejected,
        "average_confidence": round(sum(confs) / len(confs), 3) if confs else None,
        "events": sorted(events, key=lambda e: str(e["timestamp"]))[-50:],
        "source": "learning.event_store",
    }


@router.get("/jobs/{job_id}/learning-metrics")
def get_job_learning_metrics(job_id: str, rec=_SessionRec) -> Dict[str, Any]:
    """Feedback the operator recorded for THIS job, from this tenant's store."""
    from .video_producer_api import _check_job_id  # noqa: PLC0415

    _check_job_id(job_id)
    return _job_learning_metrics(job_id, rec.tenant_id)


@router.post("/jobs/{job_id}/feedback")
async def submit_feedback(job_id: str, feedback: FeedbackSubmissionRequest, rec=_SessionRec) -> Dict[str, Any]:
    """Record a 1–5 rating for one scene of a job (same audit-first path as the
    scene-feedback route). 503 when nothing was recorded."""
    from .feedback_emitter_helper import emit_feedback_event  # noqa: PLC0415
    from .video_producer_api import _check_job_id, _feedback_refusal_detail, _store  # noqa: PLC0415

    _check_job_id(job_id)
    job = _store(rec).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    audit_ref = await emit_feedback_event(
        skill_id=_SKILL_ID,
        task_id=job_id,
        tenant_id=rec.tenant_id,
        quality_rating=feedback.rating,
        reason=feedback.worker_notes,
        source="user",
        lom="corvin_console.routes.video_learning_api:submit_feedback",
        scene_id=feedback.scene_id,
    )
    if not audit_ref:
        raise HTTPException(status_code=503, detail=_feedback_refusal_detail(job))
    return {"job_id": job_id, "scene_id": feedback.scene_id, "rating": feedback.rating,
            "status": "recorded", "audit_ref": audit_ref}


@router.get("/learning/stats")
def get_learning_stats(rec=_SessionRec) -> Dict[str, Any]:
    """Counts over this tenant's recorded feedback — measured, not sampled."""
    try:
        events = _feedback_events(rec.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("learning stats unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Feedback store unavailable") from None
    sigs = [ev.signal or {} for ev in events]
    confs = [float(s["confidence"]) for s in sigs if isinstance(s.get("confidence"), (int, float))]
    return {
        "feedback_stats": {
            "total_events": len(sigs),
            "positive": sum(1 for s in sigs if s.get("outcome_feedback") == "yes"),
            "negative": sum(1 for s in sigs if s.get("outcome_feedback") == "no"),
            "rating_only": sum(1 for s in sigs if s.get("outcome_feedback") is None),
        },
        "confidence_metrics": {
            "samples": len(confs),
            "average": round(sum(confs) / len(confs), 3) if confs else None,
            "min": min(confs) if confs else None,
            "max": max(confs) if confs else None,
        },
        "source": "learning.event_store",
    }


@router.get("/learning/health")
def learning_health(rec=_SessionRec) -> Dict[str, Any]:
    """Whether this tenant's feedback store can actually be opened."""
    try:
        _feedback_events(rec.tenant_id)
        store = "ready"
    except Exception as exc:  # noqa: BLE001
        logger.debug("feedback store unavailable (%s)", type(exc).__name__)
        store = "unavailable"
    return {"status": "ok" if store == "ready" else "degraded", "components": {"feedback_store": store}}


def _variant_of(rec, job_id: str, cache: Dict[str, str]) -> str:
    """The production variant of a job: the narration tier(s) its own report.json records
    (closed identifiers, schema 1). ``unknown`` when the job has no readable report."""
    if job_id not in cache:
        from .video_producer_api import _report_facts, _store  # noqa: PLC0415

        try:
            facts = _report_facts(_store(rec), job_id)
        except HTTPException:
            facts = {}
        cache[job_id] = str(facts.get("tts_tiers") or "unknown")
    return cache[job_id]


def _variant_stats(rec) -> Dict[str, Any]:
    """Ratings grouped by production variant — counts and means over recorded feedback only."""
    try:
        events = _feedback_events(rec.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("variant stats unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Feedback store unavailable") from None
    cache: Dict[str, str] = {}
    groups: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        sig = ev.signal or {}
        job_id = str(sig.get("task_id") or "")
        if not job_id:
            continue
        g = groups.setdefault(_variant_of(rec, job_id, cache), {"jobs": set(), "ratings": [], "positive": 0, "negative": 0})
        g["jobs"].add(job_id)
        rating = sig.get("quality_rating")
        if isinstance(rating, int) and not isinstance(rating, bool):
            g["ratings"].append(rating)
        g["positive"] += 1 if sig.get("outcome_feedback") == "yes" else 0
        g["negative"] += 1 if sig.get("outcome_feedback") == "no" else 0
    variants = []
    for name in sorted(groups):
        g = groups[name]
        enough = len(g["ratings"]) >= _MIN_VARIANT_SAMPLES
        variants.append({
            "variant": name,
            "jobs": len(g["jobs"]),
            "rated_scenes": len(g["ratings"]),
            "positive": g["positive"],
            "negative": g["negative"],
            "enough_samples": enough,
            # a mean over a handful of ratings is one data point wearing a number
            "mean_rating": round(sum(g["ratings"]) / len(g["ratings"]), 2) if enough else None,
        })
    return {"dimension": _VARIANT_DIMENSION, "min_samples": _MIN_VARIANT_SAMPLES, "variants": variants}


@router.get("/learning/models")
def get_model_stats(rec=_SessionRec) -> Dict[str, Any]:
    """Recorded ratings per production variant. Descriptive: nothing reads it to pick a variant."""
    return {**_variant_stats(rec), "source": "learning.event_store",
            "note": "descriptive statistic; no production reads it to choose a variant"}


@router.get("/learning/confidence")
def get_confidence_metrics(rec=_SessionRec) -> Dict[str, Any]:
    """Distribution of the confidence raters attached to their feedback (self-reported, not a
    model's calibration)."""
    try:
        events = _feedback_events(rec.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("confidence metrics unavailable (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Feedback store unavailable") from None
    sigs = [ev.signal or {} for ev in events]
    confs = [float(s["confidence"]) for s in sigs if isinstance(s.get("confidence"), (int, float))
             and not isinstance(s.get("confidence"), bool)]
    edges = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    buckets = {f"{lo:.1f}-{hi:.1f}": sum(1 for c in confs if lo <= c < hi or (hi == 1.0 and c == 1.0)) for lo, hi in edges}
    by_source: Dict[str, int] = {}
    for s in sigs:
        by_source[str(s.get("source") or "unknown")] = by_source.get(str(s.get("source") or "unknown"), 0) + 1
    return {
        "samples": len(confs),
        "feedback_events": len(sigs),
        "average": round(sum(confs) / len(confs), 3) if confs else None,
        "min": min(confs) if confs else None,
        "max": max(confs) if confs else None,
        "buckets": buckets,
        "by_source": by_source,
        "source": "learning.event_store",
        "note": "confidence is what the rater stated, not a model's calibration",
    }


class SelectModelRequest(BaseModel):
    dimension: str = Field(_VARIANT_DIMENSION, pattern=r"^tts_tier$")


@router.post("/learning/select-model")
def select_model(body: Optional[SelectModelRequest] = None, rec=_SessionRec) -> Dict[str, Any]:
    """Advice: the production variant that rated best. NEVER applied — nothing reads this to choose
    a variant for a job. Withheld (``advice: null``) below the sample floor or without a clear lead."""
    stats = _variant_stats(rec)
    ranked = sorted((v for v in stats["variants"] if v["enough_samples"]), key=lambda v: v["mean_rating"], reverse=True)
    advice: Optional[Dict[str, Any]] = None
    if len(ranked) < 2:
        reason = "insufficient_data"
    elif ranked[0]["mean_rating"] - ranked[1]["mean_rating"] < _ADVICE_MARGIN:
        reason = "no_clear_difference"
    else:
        reason = "ok"
        advice = {"variant": ranked[0]["variant"], "mean_rating": ranked[0]["mean_rating"],
                  "over": ranked[1]["variant"], "over_mean_rating": ranked[1]["mean_rating"]}
    return {"dimension": stats["dimension"], "advice": advice, "reason": reason, "applied": False,
            "min_samples": _MIN_VARIANT_SAMPLES, "margin": _ADVICE_MARGIN, "source": "learning.event_store"}


class ReportQualityRequest(BaseModel):
    job_id: str = Field(..., min_length=1, max_length=64)
    rating: int = Field(..., ge=1, le=5)
    notes: Optional[str] = Field(None, max_length=500)


@router.post("/learning/report-quality")
async def report_video_quality(body: ReportQualityRequest, rec=_SessionRec) -> Dict[str, Any]:
    """One overall 1–5 rating for a whole job, recorded exactly like a scene's (audit-first, only for
    a production that really ran in the last 24 h). 503 when nothing was recorded."""
    from .feedback_emitter_helper import emit_feedback_event  # noqa: PLC0415
    from .video_producer_api import _check_job_id, _feedback_refusal_detail, _store  # noqa: PLC0415

    _check_job_id(body.job_id)
    job = _store(rec).get_job(body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    audit_ref = await emit_feedback_event(
        skill_id=_SKILL_ID, task_id=body.job_id, tenant_id=rec.tenant_id, quality_rating=body.rating,
        reason=body.notes, source="user", scene_id=_JOB_SCOPE_SCENE,
        lom="corvin_console.routes.video_learning_api:report_video_quality",
    )
    if not audit_ref:
        raise HTTPException(status_code=503, detail=_feedback_refusal_detail(job))
    return {"job_id": body.job_id, "scope": "job", "rating": body.rating, "status": "recorded", "audit_ref": audit_ref}
