"""Console API for Phase 5 metrics + visualization

Exposes tier performance, learning statistics, job status.
"""

from flask import Blueprint, jsonify, request as flask_request
from datetime import datetime
from typing import Optional

bp = Blueprint("video_producer_metrics", __name__, url_prefix="/api/v1/video-producer")

# Global instances (in real: dependency injection)
# These would be injected from the main app initialization
dispatcher = None
optimizer = None


@bp.before_request
def initialize_globals():
    """Initialize global instances on first request"""
    global dispatcher, optimizer

    if dispatcher is None or optimizer is None:
        # Lazy import to avoid circular dependencies
        from core.skills.video_producer_skill_2_0.phase5.tier_dispatcher import TierDispatcher
        from core.skills.video_producer_skill_2_0.phase5.quick_renderer import QuickRendererWorker
        from core.skills.video_producer_skill_2_0.phase5.manim_animator import ManimAnimatorWorker
        from core.skills.video_producer_skill_2_0.phase5.premium_renderer import PremiumAsyncQueue
        from core.skills.video_producer_skill_2_0.phase5.learning_integration import LearningOptimizer

        if dispatcher is None:
            tier1 = QuickRendererWorker()
            tier2 = ManimAnimatorWorker()
            tier3 = PremiumAsyncQueue()
            dispatcher = TierDispatcher(tier1, tier2, tier3)

        if optimizer is None:
            optimizer = LearningOptimizer()


@bp.route("/metrics/tier-performance", methods=["GET"])
def get_tier_performance():
    """Get tier success/fail metrics

    Returns:
        {
            "tier_1_quick": {"success_count": N, "fail_count": M},
            "tier_2_rich": {"success_count": N, "fail_count": M},
            "tier_3_premium": {"success_count": N, "fail_count": M},
            "timestamp": "2026-09-14T..."
        }
    """
    if dispatcher is None:
        return jsonify({"error": "Dispatcher not initialized"}), 500

    return jsonify(dispatcher.get_metrics())


@bp.route("/metrics/learning-stats", methods=["GET"])
def get_learning_stats():
    """Get learning loop statistics

    Returns:
        {
            "animation_id": {
                "avg_quality": 8.5,
                "avg_engagement": 9.0,
                "avg_render_time_ms": 30000,
                "preferred_tier": "TIER_2_RICH",
                "num_samples": 3
            },
            ...
        }
    """
    if optimizer is None:
        return jsonify({"error": "Optimizer not initialized"}), 500

    return jsonify(optimizer.get_statistics())


@bp.route("/metrics/learning-stats/<animation_id>", methods=["GET"])
def get_learning_stats_single(animation_id):
    """Get learning stats for a specific animation

    Args:
        animation_id: The animation to get stats for

    Returns:
        {
            "animation_id": "learning-loop",
            "avg_quality": 8.5,
            "avg_engagement": 9.0,
            "avg_render_time_ms": 30000,
            "preferred_tier": "TIER_2_RICH",
            "num_samples": 3
        }
    """
    if optimizer is None:
        return jsonify({"error": "Optimizer not initialized"}), 500

    stats = optimizer.get_statistics()

    if animation_id not in stats:
        return jsonify({"error": f"No stats for animation: {animation_id}"}), 404

    return jsonify({
        "animation_id": animation_id,
        **stats[animation_id]
    })


@bp.route("/job/<job_id>/status", methods=["GET"])
def get_job_status(job_id):
    """Get async job status

    Args:
        job_id: The job ID to check

    Returns:
        {
            "job_id": "abc123def456",
            "status": "queued|rendering|complete|failed",
            "created_at": "2026-09-14T...",
            "started_at": "2026-09-14T...",
            "completed_at": "2026-09-14T...",
            "output_path": "/path/to/output.mp4",
            "error": null
        }
    """
    if dispatcher is None:
        return jsonify({"error": "Dispatcher not initialized"}), 500

    status = dispatcher.tier3.get_job_status(job_id)

    if "error" in status:
        return jsonify(status), 404

    return jsonify(status)


@bp.route("/queue/stats", methods=["GET"])
def get_queue_stats():
    """Get async queue statistics

    Returns:
        {
            "queued": 3,
            "rendering": 1,
            "complete": 5,
            "failed": 0,
            "total": 9
        }
    """
    if dispatcher is None:
        return jsonify({"error": "Dispatcher not initialized"}), 500

    return jsonify(dispatcher.tier3.get_queue_stats())


@bp.route("/feedback", methods=["POST"])
def record_feedback():
    """Record user feedback on a rendered video

    Request JSON:
        {
            "animation_id": "learning-loop",
            "render_time_ms": 45000,
            "quality_score": 8.5,
            "engagement_score": 9.0,
            "tier_used": "TIER_2_RICH",
            "user_id": "user_123"
        }

    Returns:
        {
            "success": true,
            "message": "Feedback recorded"
        }
    """
    if optimizer is None:
        return jsonify({"error": "Optimizer not initialized"}), 500

    data = flask_request.get_json()

    if not data:
        return jsonify({"error": "No JSON data provided"}), 400

    try:
        from core.skills.video_producer_skill_2_0.phase5.learning_integration import RenderFeedback

        feedback = RenderFeedback(
            animation_id=data.get("animation_id", ""),
            render_time_ms=int(data.get("render_time_ms", 0)),
            quality_score=float(data.get("quality_score", 0)),
            engagement_score=float(data.get("engagement_score", 0)),
            tier_used=data.get("tier_used", ""),
            user_id=data.get("user_id", "")
        )

        optimizer.record_feedback(feedback)

        return jsonify({
            "success": True,
            "message": "Feedback recorded",
            "animation_id": feedback.animation_id
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400


@bp.route("/dashboard-data", methods=["GET"])
def get_dashboard_data():
    """Combined data for dashboard visualization

    Returns:
        {
            "tier_performance": {...},
            "learning_stats": {...},
            "queue_stats": {...},
            "timestamp": "2026-09-14T..."
        }
    """
    if dispatcher is None or optimizer is None:
        return jsonify({"error": "Services not initialized"}), 500

    return jsonify({
        "tier_performance": dispatcher.get_metrics(),
        "learning_stats": optimizer.get_statistics(),
        "queue_stats": dispatcher.tier3.get_queue_stats(),
        "timestamp": datetime.now().isoformat()
    })


@bp.route("/health", methods=["GET"])
def health_check():
    """Health check for video producer services

    Returns:
        {
            "status": "healthy",
            "dispatcher": "ready|error",
            "optimizer": "ready|error"
        }
    """
    status = {
        "status": "healthy",
        "dispatcher": "ready" if dispatcher is not None else "error",
        "optimizer": "ready" if optimizer is not None else "error"
    }

    return jsonify(status)
