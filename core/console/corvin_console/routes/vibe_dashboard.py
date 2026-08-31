"""
Vibe Engineering Phase 2: Operator Dashboard Routes

Flask API for checkpoint browser, task execution timeline, and session statistics.

Endpoints:
- GET /vibe/checkpoints/<task_id> — List checkpoints for task
- GET /vibe/checkpoint/<task_id>/<checkpoint_id> — Get checkpoint details
- GET /vibe/task-status/<task_id> — Get task execution status
- GET /vibe/metrics — Get system-wide metrics
- POST /vibe/restore/<task_id>/<checkpoint_id> — Restore checkpoint
- GET /vibe/tasks — List all active/recent tasks
"""

from flask import Blueprint, request, jsonify
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import logging

from core.vibe_engineering.vibe_orchestrator import VibeOrchestrator
from core.vibe_engineering.checkpoint_manager import CheckpointManager
from core.endpoints.k1_decorators import k1_flask

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
@k1_flask()
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
@k1_flask()
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
@k1_flask()
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
@k1_flask()
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
@k1_flask()
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
@k1_flask()
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
@k1_flask()
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
