"""
Vibe Engineering Dashboard Routes (Phase 2 + Phase 3)

Flask API for checkpoint browser, task execution timeline, session statistics,
and guidance decision management.

Phase 2 Endpoints:
- GET /vibe/checkpoints/<task_id> — List checkpoints for task
- GET /vibe/checkpoint/<task_id>/<checkpoint_id> — Get checkpoint details
- GET /vibe/task-status/<task_id> — Get task execution status
- GET /vibe/metrics — Get system-wide metrics
- POST /vibe/restore/<task_id>/<checkpoint_id> — Restore checkpoint
- GET /vibe/tasks — List all active/recent tasks

Phase 3 Endpoints (Guidance Decisions):
- GET /v1/vibe/decisions — List recent guidance decisions
- GET /v1/vibe/guidance/<id> — Get decision details
- POST /v1/vibe/feedback/<id> — Submit operator feedback
- GET /v1/vibe/stats — Get Vibe subsystem statistics
"""

from flask import Blueprint, request, jsonify
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from core.vibe_engineering.vibe_orchestrator import VibeOrchestrator
from core.vibe_engineering.checkpoint_manager import CheckpointManager

logger = logging.getLogger(__name__)

vibe_bp = Blueprint("vibe_dashboard", __name__, url_prefix="/vibe")


# ============================================================================
# GLOBAL STATE (simplistic; in production use dependency injection)
# ============================================================================

_orchestrator: Optional[VibeOrchestrator] = None


def get_orchestrator() -> VibeOrchestrator:
    """Get or create VibeOrchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        checkpoint_dir = Path.home() / ".corvin" / "vibe" / "checkpoints"
        _orchestrator = VibeOrchestrator(checkpoint_dir=checkpoint_dir)
    return _orchestrator


# ============================================================================
# CHECKPOINT MANAGEMENT
# ============================================================================

@vibe_bp.route("/checkpoints/<task_id>", methods=["GET"])
def list_checkpoints(task_id: str) -> Dict[str, Any]:
    """
    List all checkpoints for a task.

    Response:
    {
        "task_id": "task_001",
        "checkpoints": [
            {
                "checkpoint_id": "abc123",
                "iteration": 50,
                "timestamp": "2026-08-26T10:30:00",
                "trigger": "context_limit_85",
                "compression_pct": 91
            },
            ...
        ],
        "total": 3
    }
    """
    try:
        orchestrator = get_orchestrator()
        checkpoints = orchestrator.list_task_checkpoints(task_id)

        checkpoint_list = []
        for meta in checkpoints:
            checkpoint_detail = orchestrator.checkpoint_manager.load(meta.file_path)
            checkpoint_list.append({
                "checkpoint_id": meta.checkpoint_id,
                "iteration": meta.iteration_num,
                "timestamp": meta.timestamp.isoformat(),
                "trigger": checkpoint_detail.trigger,
                "compression_pct": checkpoint_detail.context_essentials.get("reduction_pct", 91),
                "tokens_saved": (
                    checkpoint_detail.context_essentials.get("original_tokens", 0) -
                    checkpoint_detail.context_essentials.get("reduced_tokens", 0)
                ),
            })

        return jsonify({
            "task_id": task_id,
            "checkpoints": checkpoint_list,
            "total": len(checkpoint_list)
        })

    except Exception as e:
        logger.error(f"Failed to list checkpoints for {task_id}: {e}")
        return jsonify({"error": str(e)}), 500


@vibe_bp.route("/checkpoint/<task_id>/<checkpoint_id>", methods=["GET"])
def get_checkpoint_details(task_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """
    Get full checkpoint details for inspection.

    Response:
    {
        "checkpoint_id": "abc123",
        "task_id": "task_001",
        "iteration": 50,
        "phase": "execution",
        "trigger": "context_limit_85",
        "timestamp": "2026-08-26T10:30:00",
        "compression": {
            "original_tokens": 4000,
            "reduced_tokens": 100,
            "reduction_pct": 97
        },
        "state": {
            "phase": "execution",
            "iteration": 50,
            "context_tokens": 3400,
            "tokens_burned": 50000
        },
        "decisions": [...],
        "errors": [...],
        "learnings": [...]
    }
    """
    try:
        orchestrator = get_orchestrator()
        checkpoint = orchestrator.get_checkpoint_details(checkpoint_id, task_id)

        if not checkpoint:
            return jsonify({"error": "Checkpoint not found"}), 404

        return jsonify({
            "checkpoint_id": checkpoint.checkpoint_id,
            "task_id": checkpoint.task_id,
            "iteration": checkpoint.iteration_num,
            "phase": checkpoint.phase,
            "trigger": checkpoint.trigger,
            "timestamp": checkpoint.timestamp_iso,
            "compression": {
                "original_tokens": checkpoint.context_essentials.get("original_tokens", 0),
                "reduced_tokens": checkpoint.context_essentials.get("reduced_tokens", 0),
                "reduction_pct": checkpoint.context_essentials.get("reduction_pct", 91),
            },
            "state": {
                "phase": checkpoint.phase,
                "iteration": checkpoint.iteration_num,
                "context_tokens": checkpoint.task_state.get("context_tokens", 0),
                "tokens_burned": checkpoint.task_state.get("tokens_burned", 0),
                "progress": checkpoint.task_state.get("progress", {}),
            },
            "decisions": checkpoint.context_essentials.get("decisions", [])[:10],
            "errors": checkpoint.context_essentials.get("errors", []),
            "learnings": checkpoint.context_essentials.get("learnings", [])[:5],
            "strategies_tried": checkpoint.learning_state.get("strategies_tried", []),
            "recommendations": checkpoint.learning_state.get("recommendations", []),
        })

    except Exception as e:
        logger.error(f"Failed to get checkpoint details: {e}")
        return jsonify({"error": str(e)}), 500


@vibe_bp.route("/restore/<task_id>/<checkpoint_id>", methods=["POST"])
def restore_checkpoint(task_id: str, checkpoint_id: str) -> Dict[str, Any]:
    """
    Restore (resume) from a checkpoint.

    Response:
    {
        "success": true,
        "task_id": "task_001",
        "checkpoint_id": "abc123",
        "resume_iteration": 50,
        "message": "Task restored and ready to resume"
    }
    """
    try:
        orchestrator = get_orchestrator()
        execution_state = orchestrator.resume_from_checkpoint(task_id, checkpoint_id)

        if not execution_state:
            return jsonify({
                "success": False,
                "error": "Failed to restore checkpoint"
            }), 500

        return jsonify({
            "success": True,
            "task_id": execution_state.task_id,
            "checkpoint_id": execution_state.last_checkpoint_id,
            "resume_iteration": execution_state.iteration_num,
            "phase": execution_state.phase,
            "message": f"Task restored and ready to resume at iteration {execution_state.iteration_num + 1}"
        })

    except Exception as e:
        logger.error(f"Failed to restore checkpoint: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# TASK STATUS & MONITORING
# ============================================================================

@vibe_bp.route("/task-status/<task_id>", methods=["GET"])
def get_task_status(task_id: str) -> Dict[str, Any]:
    """
    Get current execution status for a task.

    Response:
    {
        "task_id": "task_001",
        "status": "running",
        "phase": "execution",
        "iteration": 42,
        "context_tokens": 2500,
        "tokens_burned": 45000,
        "tokens_budget": 100000,
        "checkpoints": 3,
        "latest_checkpoint": {
            "checkpoint_id": "abc123",
            "timestamp": "2026-08-26T10:30:00"
        },
        "recovery_success_rate": 1.0
    }
    """
    try:
        orchestrator = get_orchestrator()

        # Get active task (if any)
        active_task = orchestrator.active_task
        if active_task and active_task.task_id == task_id:
            status_str = orchestrator.state.value
        else:
            status_str = "idle"

        # Get checkpoints
        checkpoints = orchestrator.list_task_checkpoints(task_id)
        latest_checkpoint = None
        if checkpoints:
            latest = checkpoints[0]
            latest_checkpoint = {
                "checkpoint_id": latest.checkpoint_id,
                "timestamp": latest.timestamp.isoformat()
            }

        recovery_rate = 0.0
        if orchestrator.metrics.recovery_success_count + orchestrator.metrics.recovery_failure_count > 0:
            total = orchestrator.metrics.recovery_success_count + orchestrator.metrics.recovery_failure_count
            recovery_rate = orchestrator.metrics.recovery_success_count / total

        return jsonify({
            "task_id": task_id,
            "status": status_str,
            "phase": active_task.current_phase if active_task else "unknown",
            "iteration": active_task.iteration_count if active_task else None,
            "context_tokens": active_task.context_tokens if active_task else None,
            "tokens_burned": active_task.tokens_burned_today if active_task else None,
            "tokens_budget": active_task.daily_token_budget if active_task else 100000,
            "checkpoints": len(checkpoints),
            "latest_checkpoint": latest_checkpoint,
            "recovery_success_rate": recovery_rate
        })

    except Exception as e:
        logger.error(f"Failed to get task status: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# SYSTEM METRICS
# ============================================================================

@vibe_bp.route("/metrics", methods=["GET"])
def get_metrics() -> Dict[str, Any]:
    """
    Get system-wide Vibe Engineering metrics.

    Response:
    {
        "checkpoints_created": 12,
        "total_iterations": 256,
        "total_splits": 12,
        "avg_compression_pct": 91,
        "tokens_saved": 123456,
        "recovery_success_rate": 0.95,
        "uptime_seconds": 3600,
        "splits_by_trigger": {
            "context_limit_85": 5,
            "iteration_cap_50": 4,
            "token_burn": 2,
            "stall_detected": 1
        }
    }
    """
    try:
        orchestrator = get_orchestrator()
        metrics = orchestrator.get_metrics()

        uptime = (datetime.now() - metrics.start_time).total_seconds()

        recovery_rate = 0.0
        if metrics.recovery_success_count + metrics.recovery_failure_count > 0:
            total = metrics.recovery_success_count + metrics.recovery_failure_count
            recovery_rate = metrics.recovery_success_count / total

        return jsonify({
            "checkpoints_created": metrics.checkpoints_created,
            "total_iterations": metrics.total_iterations,
            "total_splits": metrics.total_splits,
            "avg_compression_pct": round(metrics.avg_context_reduction_pct, 1),
            "tokens_saved": metrics.total_tokens_saved,
            "recovery_success_rate": round(recovery_rate, 2),
            "uptime_seconds": int(uptime),
            "splits_by_trigger": metrics.total_splits_by_trigger,
            "last_checkpoint_time": metrics.last_checkpoint_time.isoformat() if metrics.last_checkpoint_time else None
        })

    except Exception as e:
        logger.error(f"Failed to get metrics: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# TASK LISTING
# ============================================================================

@vibe_bp.route("/tasks", methods=["GET"])
def list_tasks() -> Dict[str, Any]:
    """
    List all active/recent tasks.

    Response:
    {
        "active": [
            {
                "task_id": "task_001",
                "session_id": "session_001",
                "goal": "Build summarizer",
                "phase": "execution",
                "iteration": 42,
                "context_tokens": 2500,
                "checkpoints": 3
            }
        ],
        "total_tasks": 5
    }
    """
    try:
        orchestrator = get_orchestrator()

        active_tasks = []
        if orchestrator.active_task:
            task = orchestrator.active_task
            checkpoints = orchestrator.list_task_checkpoints(task.task_id)
            active_tasks.append({
                "task_id": task.task_id,
                "session_id": task.session_id,
                "goal": task.goal[:100],  # Truncate for display
                "phase": task.current_phase,
                "iteration": task.iteration_count,
                "context_tokens": task.context_tokens,
                "checkpoints": len(checkpoints),
                "created_at": task.created_at.isoformat()
            })

        return jsonify({
            "active": active_tasks,
            "total_tasks": len(active_tasks)
        })

    except Exception as e:
        logger.error(f"Failed to list tasks: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================================================
# HEALTH CHECK
# ============================================================================

@vibe_bp.route("/health", methods=["GET"])
def health_check() -> Dict[str, Any]:
    """
    Health check endpoint.

    Response:
    {
        "status": "healthy",
        "orchestrator_state": "running",
        "checkpoint_dir": "/home/user/.corvin/vibe/checkpoints",
        "version": "0.2-rc1"
    }
    """
    try:
        orchestrator = get_orchestrator()
        return jsonify({
            "status": "healthy",
            "orchestrator_state": orchestrator.state.value,
            "checkpoint_dir": str(orchestrator.checkpoint_manager.checkpoint_dir),
            "version": "0.2-rc1"
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 503


# ============================================================================
# PHASE 3: GUIDANCE DECISION MANAGEMENT
# ============================================================================

# In-memory decision history (would be persistent in production)
_DECISION_HISTORY: list = []
_DECISION_MAX_RETENTION = 1000


@vibe_bp.route("/decisions", methods=["GET"])
def list_decisions() -> Dict[str, Any]:
    """
    List recent guidance decisions (paginated, filterable).

    Query parameters:
      - limit (int): Results per page (default: 20, max: 100)
      - offset (int): Pagination offset (default: 0)
      - category (str): Filter by category (e.g., 'parallelize')
      - min_confidence (float): Filter by confidence >= value

    Response:
    {
        "decisions": [
            {
                "id": "task-123-0",
                "task_id": "task-123",
                "category": "parallelize",
                "confidence": 0.85,
                "rationale": "Found parallel branches",
                "timestamp": "2026-08-29T12:30:00"
            }
        ],
        "total_count": 42,
        "has_more": true
    }
    """
    limit = min(request.args.get("limit", 20, type=int), 100)
    offset = request.args.get("offset", 0, type=int)
    category_filter = request.args.get("category")
    min_confidence = request.args.get("min_confidence", 0.0, type=float)

    # Filter
    filtered = _DECISION_HISTORY
    if category_filter:
        filtered = [d for d in filtered if d.get("category") == category_filter]
    if min_confidence > 0:
        filtered = [d for d in filtered if d.get("confidence", 0) >= min_confidence]

    # Sort by timestamp (newest first)
    filtered = sorted(filtered, key=lambda d: d.get("timestamp", ""), reverse=True)

    total = len(filtered)
    paginated = filtered[offset:offset + limit]

    return jsonify({
        "decisions": paginated,
        "total_count": total,
        "has_more": offset + limit < total,
        "offset": offset,
        "limit": limit
    })


@vibe_bp.route("/guidance/<decision_id>", methods=["GET"])
def get_guidance_details(decision_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a guidance decision.

    Response:
    {
        "decision": { ... full decision object ... },
        "related_decisions": [ ... decisions for same task ... ],
        "feedback_count": 2
    }
    """
    decision = None
    for d in _DECISION_HISTORY:
        if d.get("id") == decision_id:
            decision = d
            break

    if not decision:
        return jsonify({"error": "Decision not found"}), 404

    # Find related decisions
    related = [
        d for d in _DECISION_HISTORY
        if d.get("task_id") == decision.get("task_id")
        and d.get("id") != decision_id
    ][:5]

    return jsonify({
        "decision": decision,
        "related_decisions": related,
        "feedback_count": len(decision.get("feedback_history", []))
    })


@vibe_bp.route("/feedback/<decision_id>", methods=["POST"])
def submit_feedback(decision_id: str) -> Dict[str, Any]:
    """
    Submit operator feedback on a decision.

    Request body:
    {
        "rating": "good" | "bad" | "neutral",
        "notes": "Optional feedback",
        "corrective_action": "What should have happened"
    }

    Response:
    {
        "decision_id": "task-123-0",
        "feedback_recorded": true,
        "rating": "good"
    }
    """
    decision = None
    decision_idx = None
    for idx, d in enumerate(_DECISION_HISTORY):
        if d.get("id") == decision_id:
            decision = d
            decision_idx = idx
            break

    if not decision:
        return jsonify({"error": "Decision not found"}), 404

    data = request.get_json() or {}
    rating = data.get("rating")

    if rating not in ("good", "bad", "neutral"):
        return jsonify({"error": "Rating must be 'good', 'bad', or 'neutral'"}), 400

    feedback_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "rating": rating,
        "notes": data.get("notes", ""),
        "corrective_action": data.get("corrective_action", "")
    }

    if "feedback_history" not in decision:
        decision["feedback_history"] = []
    decision["feedback_history"].append(feedback_entry)

    _DECISION_HISTORY[decision_idx] = decision

    return jsonify({
        "decision_id": decision_id,
        "feedback_recorded": True,
        "rating": rating
    })


@vibe_bp.route("/stats", methods=["GET"])
def get_vibe_stats() -> Dict[str, Any]:
    """
    Get Vibe subsystem statistics.

    Response:
    {
        "total_decisions": 42,
        "categories": {
            "parallelize": 15,
            "error_recovery": 12,
            "optimize_cost": 10,
            "refactor": 5
        },
        "avg_confidence": 0.78,
        "feedback_rate": 0.5
    }
    """
    categories: Dict[str, int] = {}
    for d in _DECISION_HISTORY:
        cat = d.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    confidences = [d.get("confidence", 0.5) for d in _DECISION_HISTORY]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    feedback_count = sum(
        len(d.get("feedback_history", []))
        for d in _DECISION_HISTORY
    )
    feedback_rate = (
        feedback_count / len(_DECISION_HISTORY)
        if _DECISION_HISTORY
        else 0.0
    )

    return jsonify({
        "total_decisions": len(_DECISION_HISTORY),
        "categories": categories,
        "avg_confidence": round(avg_confidence, 2),
        "feedback_count": feedback_count,
        "feedback_rate": round(feedback_rate, 2),
        "retention_max": _DECISION_MAX_RETENTION
    })


def record_guidance_decision(event: Dict[str, Any]) -> None:
    """Record a guidance event to history (called by publisher).

    Args:
        event: GuidanceEvent dict from VibeEventPublisher
    """
    global _DECISION_HISTORY

    record = {
        "id": f"{event.get('task_id')}-{len(_DECISION_HISTORY)}",
        "timestamp": event.get("timestamp"),
        "task_id": event.get("task_id"),
        "tenant_id": event.get("tenant_id"),
        "category": event.get("category"),
        "confidence": event.get("confidence"),
        "rationale": event.get("rationale"),
        "recommended_action": event.get("recommended_action"),
        "fallback_used": event.get("fallback_used"),
        "severity": event.get("severity"),
        "supporting_metrics": event.get("supporting_metrics", {}),
        "feedback_history": []
    }

    if len(_DECISION_HISTORY) >= _DECISION_MAX_RETENTION:
        _DECISION_HISTORY = _DECISION_HISTORY[-(
            _DECISION_MAX_RETENTION // 2
        ):]

    _DECISION_HISTORY.append(record)
    logger.debug(f"Recorded guidance decision for task {event.get('task_id')}")
