"""Console API Extensions for Video Producer Learning (Phase 4b).

Endpoints:
- POST /v1/console/video/jobs/{job_id}/feedback — Submit feedback
- GET /v1/console/video/learning/stats — Get learning statistics
- GET /v1/console/video/learning/models — Get model selection stats
- GET /v1/console/video/learning/confidence — Get confidence metrics
"""

from __future__ import annotations

from flask import Blueprint, request, jsonify
from typing import Any, Optional
import sys
from pathlib import Path

# Add parent dirs to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent.parent))

from core.skills.os_skills.video_producer.src.learning.loop_integration import (
    LearningLoopIntegration,
)

bp = Blueprint("video_learning", __name__, url_prefix="/v1/console/video")


# Global learning integration instance (shared across requests)
_learning_loop: Optional[LearningLoopIntegration] = None


def get_learning_loop() -> LearningLoopIntegration:
    """Get or create global learning loop instance."""
    global _learning_loop
    if _learning_loop is None:
        # Initialize with .corvin directory
        corvin_home = Path.home() / ".corvin"
        learning_dir = corvin_home / "video_producer_learning"
        _learning_loop = LearningLoopIntegration(str(learning_dir))
    return _learning_loop


# ============================================================================
# Feedback Submission
# ============================================================================

@bp.route("/jobs/<job_id>/feedback", methods=["POST"])
def submit_feedback(job_id: str) -> tuple[dict[str, Any], int]:
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
        "feedback_id": "...",
        "message": "Feedback recorded"
    }
    """
    try:
        data = request.get_json() or {}

        # Validate required fields
        scene_id = data.get("scene_id")
        feedback_type = data.get("feedback_type", "quality")
        rating = data.get("rating")
        worker_notes = data.get("worker_notes")

        if not scene_id:
            return {"error": "scene_id required"}, 400

        if not isinstance(rating, int) or not 1 <= rating <= 5:
            return {"error": "rating must be 1-5"}, 400

        # Submit to learning loop
        loop = get_learning_loop()
        success, error = loop.submit_feedback(
            job_id=job_id,
            scene_id=scene_id,
            feedback_type=feedback_type,
            rating=rating,
            worker_notes=worker_notes,
        )

        if not success:
            return {"error": error}, 400

        return {
            "success": True,
            "job_id": job_id,
            "scene_id": scene_id,
            "feedback_type": feedback_type,
            "rating": rating,
            "message": "Feedback recorded and learning updated",
        }, 200

    except Exception as e:
        return {"error": str(e)}, 500


# ============================================================================
# Learning Statistics
# ============================================================================

@bp.route("/learning/stats", methods=["GET"])
def get_learning_stats() -> tuple[dict[str, Any], int]:
    """Get comprehensive learning statistics.

    Response:
    {
        "confidence_metrics": {...},
        "model_stats": {...},
        "feedback_stats": {...},
        "timestamp": "2026-09-13T12:34:56Z"
    }
    """
    try:
        loop = get_learning_loop()
        stats = loop.get_learning_stats()
        return stats, 200
    except Exception as e:
        return {"error": str(e)}, 500


@bp.route("/learning/models", methods=["GET"])
def get_model_stats() -> tuple[dict[str, Any], int]:
    """Get model selection statistics.

    Response:
    {
        "total_decisions": 42,
        "exploration_rate": 0.1,
        "by_duration": {
            "1min": {
                "selected_model": "claude-opus",
                "models": {
                    "gpt-4": {
                        "win_rate": 0.75,
                        "attempts": 10,
                        "wins": 8
                    },
                    ...
                }
            },
            ...
        }
    }
    """
    try:
        loop = get_learning_loop()
        stats = loop.model_selector.get_model_stats()
        return stats, 200
    except Exception as e:
        return {"error": str(e)}, 500


@bp.route("/learning/confidence", methods=["GET"])
def get_confidence_metrics() -> tuple[dict[str, Any], int]:
    """Get worker confidence metrics.

    Response:
    {
        "slide_renderer": {
            "overall_score": 0.85,
            "is_converged": true,
            "metrics": {
                "slide_quality": {
                    "confidence": 0.85,
                    "samples": 12,
                    "variance": 0.08
                },
                ...
            }
        },
        ...
    }
    """
    try:
        loop = get_learning_loop()
        metrics = loop.confidence_scorer.get_all_confidence_metrics()
        return metrics, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ============================================================================
# Model Selection
# ============================================================================

@bp.route("/learning/select-model", methods=["POST"])
def select_model() -> tuple[dict[str, Any], int]:
    """Select model for a new video.

    Request Body:
    {
        "duration_seconds": 60
    }

    Response:
    {
        "model": "claude-opus",
        "duration_seconds": 60,
        "stats": {...}
    }
    """
    try:
        data = request.get_json() or {}
        duration = data.get("duration_seconds", 60)

        loop = get_learning_loop()
        model, stats = loop.select_model_for_video(duration)

        return {
            "model": model,
            "duration_seconds": duration,
            "stats": stats,
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


@bp.route("/learning/report-quality", methods=["POST"])
def report_video_quality() -> tuple[dict[str, Any], int]:
    """Report video quality and update model selection.

    Request Body:
    {
        "job_id": "job1",
        "duration_seconds": 60,
        "quality_score": 0.8,
        "model_used": "claude-opus"
    }

    Response:
    {
        "success": true,
        "model_switched_to": null or "new_model"
    }
    """
    try:
        data = request.get_json() or {}
        job_id = data.get("job_id")
        duration = data.get("duration_seconds")
        quality = data.get("quality_score")
        model = data.get("model_used")

        if not all([job_id, duration, quality is not None, model]):
            return {"error": "Missing required fields"}, 400

        loop = get_learning_loop()
        new_model = loop.report_video_quality(
            job_id=job_id,
            video_duration_seconds=duration,
            quality_score=quality,
            model_used=model,
        )

        return {
            "success": True,
            "job_id": job_id,
            "model_used": model,
            "model_switched_to": new_model,
            "message": f"Quality recorded{f' (switched to {new_model})' if new_model else ''}",
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ============================================================================
# Health Check
# ============================================================================

@bp.route("/learning/health", methods=["GET"])
def learning_health() -> tuple[dict[str, Any], int]:
    """Health check for learning infrastructure."""
    try:
        loop = get_learning_loop()
        return {
            "status": "ok",
            "components": {
                "feedback_collector": "ready",
                "confidence_scorer": "ready",
                "model_selector": "ready",
                "audit_trail": "ready",
            },
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500
