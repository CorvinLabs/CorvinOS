"""Console API for Video Producer learning (Phase 4b), tenant-scoped.

Endpoints (under /v1/console/video):
- GET  /jobs/{job_id}/learning-metrics — feedback recorded for one job
- POST /jobs/{job_id}/feedback         — record job-level feedback
- GET  /learning/stats                 — counts over this tenant's feedback
- GET  /learning/health                — whether the feedback store is reachable
- /learning/models, /learning/confidence, /learning/select-model,
  /learning/report-quality             — not built: 501, never sample numbers

Every read and write uses the authenticated session's tenant.
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
_NOT_BUILT = "not available on this build"


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
    from .video_producer_api import _check_job_id, _store  # noqa: PLC0415

    _check_job_id(job_id)
    if not _store(rec).get_job(job_id):
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
        raise HTTPException(status_code=503, detail="Feedback could not be recorded")
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


def _not_built() -> None:
    raise HTTPException(status_code=501, detail=f"Video model selection / confidence scoring is {_NOT_BUILT}")


@router.get("/learning/models")
def get_model_stats() -> Dict[str, Any]:
    _not_built()


@router.get("/learning/confidence")
def get_confidence_metrics() -> Dict[str, Any]:
    _not_built()


@router.post("/learning/select-model")
def select_model() -> Dict[str, Any]:
    _not_built()


@router.post("/learning/report-quality")
def report_video_quality() -> Dict[str, Any]:
    _not_built()
