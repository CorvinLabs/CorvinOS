"""Flask API routes for Skill-Creator (console integration).

Endpoints:
  POST /api/quality/skill-creator/generate — Generate new skill
  GET /api/quality/skill-creator/status/<run_id> — Poll generation status
  GET /api/quality/skill-creator/skills — List generated skills
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any

from flask import Blueprint, request, jsonify
from werkzeug.exceptions import BadRequest

# Import Skill-Creator orchestrator
try:
    from operator.skill_forge.skill_creator import (
        SkillCreatorOrchestrator,
        SkillCreatorError,
    )
except ImportError:
    SkillCreatorOrchestrator = None
    SkillCreatorError = Exception

logger = logging.getLogger(__name__)

skill_creator_bp = Blueprint("skill_creator", __name__, url_prefix="/api/quality/skill-creator")

# In-memory store for generation runs (in production: use DB)
_generation_runs: Dict[str, Dict[str, Any]] = {}
_skill_stats = {
    "total_generated": 0,
    "avg_quality": 0.0,
    "total_iterations": 0,
    "last_generated_at": None,
}


@skill_creator_bp.route("/generate", methods=["POST"])
def generate_skill():
    """POST /api/quality/skill-creator/generate

    Request body:
    {
      "user_request": "erzeuge einen Skill der JSON validiert",
      "async": true  // Optional: run async, return run_id; default false
    }

    Response (sync):
    {
      "status": "success",
      "skill": {...},
      "quality": 0.9,
      "iterations": 2
    }

    Response (async):
    {
      "status": "accepted",
      "run_id": "run-abc123",
      "message": "Generation in progress..."
    }
    """
    if SkillCreatorOrchestrator is None:
        return jsonify({"error": "Skill-Creator not available"}), 500

    try:
        data = request.get_json() or {}
        user_request = data.get("user_request", "").strip()
        async_mode = data.get("async", False)

        if not user_request:
            raise BadRequest("Missing 'user_request' field")

        if len(user_request) < 10:
            raise BadRequest("Request must be at least 10 characters")

        # Create orchestrator
        orchestrator = SkillCreatorOrchestrator()

        if async_mode:
            # Async mode: spawn background task, return run_id
            run_id = _spawn_generation_task(orchestrator, user_request)
            return jsonify({
                "status": "accepted",
                "run_id": run_id,
                "message": f"Skill generation started. Poll /status/{run_id} for progress."
            }), 202

        else:
            # Sync mode: block until complete (not recommended for long tasks)
            # In production, this would timeout
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                artifact = loop.run_until_complete(
                    orchestrator.create_skill(user_request)
                )
                return jsonify({
                    "status": "success",
                    "skill": {
                        "name": artifact.spec.name,
                        "purpose": artifact.spec.purpose,
                        "scope": artifact.spec.scope.value,
                        "dependencies": artifact.spec.dependencies,
                        "quality": artifact.quality_score,
                        "iterations": artifact.ldd_iterations,
                    },
                    "message": f"Skill '{artifact.spec.name}' generated successfully."
                }), 200
            finally:
                loop.close()

    except BadRequest:
        raise
    except SkillCreatorError as e:
        logger.error(f"Skill generation failed: {e}")
        return jsonify({"error": str(e), "status": "failed"}), 400
    except Exception as e:
        logger.error(f"Unexpected error in skill generation: {e}")
        return jsonify({"error": str(e), "status": "error"}), 500


@skill_creator_bp.route("/status/<run_id>", methods=["GET"])
def check_status(run_id: str):
    """GET /api/quality/skill-creator/status/<run_id>

    Poll the status of an async skill generation run.

    Response:
    {
      "run_id": "run-abc123",
      "status": "running" | "success" | "failed",
      "phase": "planning" | "validation" | "ldd_iteration" | "review" | "promotion",
      "progress": 40,
      "message": "...",
      "skill": {...}  // Only if status == "success"
    }
    """
    if run_id not in _generation_runs:
        return jsonify({"error": f"Run not found: {run_id}"}), 404

    run = _generation_runs[run_id]

    response = {
        "run_id": run_id,
        "status": run["status"],
        "phase": run.get("phase", "pending"),
        "progress": run.get("progress", 0),
        "message": run.get("message", ""),
    }

    if run["status"] == "success":
        response["skill"] = run.get("skill")

    return jsonify(response), 200


@skill_creator_bp.route("/skills", methods=["GET"])
def list_generated_skills():
    """GET /api/quality/skill-creator/skills

    List all generated skills (from disk).

    Response:
    {
      "skills": [
        {"name": "assistant.validate_json", "quality": 0.9, "created_at": "..."},
        ...
      ]
    }
    """
    from pathlib import Path

    skills_dir = Path.home() / ".claude" / "skills"
    skills = []

    if skills_dir.exists():
        for skill_file in skills_dir.glob("assistant_*.md"):
            # Parse YAML frontmatter
            content = skill_file.read_text()
            lines = content.split("\n")

            skill_name = skill_file.stem.replace("_", ".")
            skills.append({
                "name": skill_name,
                "file": str(skill_file),
                "created_at": datetime.fromtimestamp(skill_file.stat().st_ctime).isoformat(),
            })

    return jsonify({"skills": skills}), 200


# ============================================================================
# BACKGROUND TASK HELPERS
# ============================================================================

@skill_creator_bp.route("/stats", methods=["GET"])
def get_stats():
    """GET /api/quality/skill-creator/stats

    Returns aggregated statistics about skill generation.

    Response:
    {
      "total_generated": 5,
      "avg_quality": 0.87,
      "total_iterations": 12,
      "last_generated_at": "2026-08-20T14:32:00"
    }
    """
    return jsonify(_skill_stats), 200


def _spawn_generation_task(orchestrator, user_request: str) -> str:
    """Spawn async generation task; return run_id."""
    import threading
    from uuid import uuid4

    run_id = f"run-{uuid4().hex[:12]}"

    _generation_runs[run_id] = {
        "status": "running",
        "phase": "planning",
        "progress": 10,
        "message": "Initializing...",
        "created_at": datetime.utcnow().isoformat(),
    }

    def run_task():
        """Background worker for skill generation."""
        try:
            import asyncio
            loop = asyncio.new_event_loop()

            # Update progress as phases complete
            _generation_runs[run_id]["phase"] = "planning"
            _generation_runs[run_id]["progress"] = 20

            artifact = loop.run_until_complete(
                orchestrator.create_skill(user_request)
            )

            _generation_runs[run_id].update({
                "status": "success",
                "phase": "promotion",
                "progress": 100,
                "message": f"Skill '{artifact.spec.name}' generated successfully.",
                "skill": {
                    "name": artifact.spec.name,
                    "purpose": artifact.spec.purpose,
                    "scope": artifact.spec.scope.value,
                    "quality": artifact.quality_score,
                    "iterations": artifact.ldd_iterations,
                },
            })

            loop.close()

        except Exception as e:
            logger.error(f"Task failed: {e}")
            _generation_runs[run_id].update({
                "status": "failed",
                "message": str(e),
            })

    # Spawn thread
    thread = threading.Thread(target=run_task, daemon=True)
    thread.start()

    return run_id
