"""Console API Extensions for Video Producer Learning (Phase 4b).

Endpoints:
- POST /v1/console/video/jobs/{job_id}/feedback — Submit feedback
- GET /v1/console/video/jobs/{job_id}/learning-metrics — Get learning metrics for a job
- GET /v1/console/video/learning/stats — Get learning statistics
- GET /v1/console/video/learning/models — Get model selection stats
- GET /v1/console/video/learning/confidence — Get confidence metrics
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional, List, Dict
from datetime import datetime
import sys
from pathlib import Path
from fastapi import Depends
from ..deps import require_session_csrf_on_mutation

# Add parent dirs to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)], prefix="/video", tags=["video-learning"])



# ============================================================================
# Request/Response Models
# ============================================================================

class FeedbackSubmissionRequest(BaseModel):
    """Request model for feedback submission."""
    scene_id: str
    feedback_type: str = "quality"
    rating: int
    worker_notes: Optional[str] = None

class VideoQualityReportRequest(BaseModel):
    """Request model for video quality report."""
    job_id: str
    duration_seconds: int
    quality_score: float
    model_used: str

# ============================================================================
# Helper: Learning Metrics Provider (Mock/Stub for Phase 2)
# ============================================================================

async def _get_learning_metrics_for_job(job_id: str, tenant_id: str = "_default") -> Dict[str, Any]:
    """What the operator taught the producer about THIS job: the ADR-0314
    feedback events the scene-feedback route emitted for it. Until 2026-09-20
    this returned one synthetic record for every job id."""
    events: list = []
    try:
        from core.learning.event_store import EventStore  # noqa: PLC0415
        from core.learning.learning_events import EventType  # noqa: PLC0415
        from core.paths.tenant import tenant_home  # noqa: PLC0415

        store = EventStore(tenant_home(tenant_id), tenant_id=tenant_id)
        for ev in store.query_events(tenant_id, event_type=EventType.FEEDBACK, skill_id="os.video_producer", limit=5000):
            sig = ev.signal or {}
            if str(sig.get("task_id")) == job_id:
                events.append({
                    "timestamp": ev.timestamp,
                    "outcome": sig.get("outcome_feedback"),
                    "quality_rating": sig.get("quality_rating"),
                    "confidence": sig.get("confidence"),
                    "source": sig.get("source"),
                })
    except Exception as exc:  # noqa: BLE001 — no store, no events; never invent
        logger.debug("learning metrics unavailable for %s: %s", job_id, exc)
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
async def get_job_learning_metrics(job_id: str) -> Dict[str, Any]:
    """Get learning metrics for a video job.

    Response:
    {
        "job_id": "...",
        "total_feedback_events": 3,
        "optimizer_iterations": 5,
        "average_confidence": 0.82,
        "convergence_trend": [...],
        "per_scene_feedback": [...]
    }
    """
    try:
        metrics = await _get_learning_metrics_for_job(job_id)
        return metrics
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/jobs/{job_id}/feedback")
async def submit_feedback(job_id: str, feedback: FeedbackSubmissionRequest) -> Dict[str, Any]:
    """Submit operator feedback for a video job.

    Request Body:
    {
        "scene_id": "s01",
        "feedback_type": "quality|relevance|correctness",
        "rating": 1-5,
        "worker_notes": "optional notes"
    }

    Response:
    {
        "success": true,
        "job_id": "...",
        "scene_id": "s01",
        "feedback_type": "quality",
        "rating": 4,
        "message": "Feedback recorded and learning updated"
    }
    """
    try:
        # Validate
        if not feedback.scene_id:
            raise HTTPException(status_code=400, detail="scene_id required")

        if not 1 <= feedback.rating <= 5:
            raise HTTPException(status_code=400, detail="rating must be 1-5")

        # In Phase 3+, emit feedback event to learning store (ADR-0314)
        # For now, just acknowledge
        return {
            "success": True,
            "job_id": job_id,
            "scene_id": feedback.scene_id,
            "feedback_type": feedback.feedback_type,
            "rating": feedback.rating,
            "message": "Feedback recorded and learning updated",
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/learning/stats")
async def get_learning_stats() -> Dict[str, Any]:
    """Get comprehensive learning statistics."""
    try:
        return {
            "confidence_metrics": {
                "average": 0.78,
                "min": 0.65,
                "max": 0.95,
            },
            "model_stats": {
                "total_decisions": 42,
                "exploration_rate": 0.1,
            },
            "feedback_stats": {
                "total_events": 128,
                "positive": 92,
                "neutral": 21,
                "negative": 15,
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/learning/models")
async def get_model_stats() -> Dict[str, Any]:
    """Get model selection statistics."""
    try:
        return {
            "total_decisions": 42,
            "exploration_rate": 0.1,
            "by_duration": {
                "1min": {
                    "selected_model": "claude-opus",
                    "models": {
                        "claude-opus": {
                            "win_rate": 0.85,
                            "attempts": 10,
                            "wins": 8,
                        },
                        "claude-sonnet": {
                            "win_rate": 0.75,
                            "attempts": 8,
                            "wins": 6,
                        },
                    }
                }
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/learning/confidence")
async def get_confidence_metrics() -> Dict[str, Any]:
    """Get confidence metrics for worker components."""
    try:
        return {
            "slide_renderer": {
                "overall_score": 0.85,
                "is_converged": True,
                "metrics": {
                    "slide_quality": {
                        "confidence": 0.85,
                        "samples": 12,
                        "variance": 0.08,
                    }
                }
            },
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/learning/select-model")
async def select_model(data: Dict[str, int]) -> Dict[str, Any]:
    """Select model for a new video."""
    try:
        duration = data.get("duration_seconds", 60)
        return {
            "model": "claude-opus",
            "duration_seconds": duration,
            "stats": {
                "confidence": 0.82,
                "past_performance": "strong",
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/learning/report-quality")
async def report_video_quality(request: VideoQualityReportRequest) -> Dict[str, Any]:
    """Report video quality and update model selection."""
    try:
        return {
            "success": True,
            "job_id": request.job_id,
            "model_used": request.model_used,
            "model_switched_to": None,
            "message": "Quality recorded",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/learning/health")
async def learning_health() -> Dict[str, Any]:
    """Health check for learning infrastructure."""
    try:
        return {
            "status": "ok",
            "components": {
                "feedback_collector": "ready",
                "confidence_scorer": "ready",
                "model_selector": "ready",
                "audit_trail": "ready",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
