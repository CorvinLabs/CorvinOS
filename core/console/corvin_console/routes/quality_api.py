"""Quality Panel API endpoints for DoD Verifier."""

from flask import Blueprint, request, jsonify
from pathlib import Path
import sys

# Import DoD Verifier Skill
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "skills" / "os_skills"))
from definition_of_done_verifier.skill import DoD_VerifierSkill
from definition_of_done_verifier.api_handlers import DoD_FeedbackCollector

quality_bp = Blueprint("quality", __name__, url_prefix="/v1/console/quality")
skill = DoD_VerifierSkill()
feedback_collector = DoD_FeedbackCollector()


@quality_bp.route("/dod/verify", methods=["POST"])
def verify_dod():
    """Verify Definition of Done for a task."""
    try:
        data = request.get_json()
        task_id = data.get("task_id", "unknown")
        task_type = data.get("task_type", "cli_command")
        symbol_name = data.get("symbol_name")
        commit_msg = data.get("commit_msg", "")

        # Execute DoD Verifier Skill
        result = skill.execute(
            task_id=task_id,
            task_type=task_type,
            symbol_name=symbol_name,
            commit_msg=commit_msg,
        )

        return jsonify({
            "success": True,
            "result": result.to_dict(),
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
        }), 400


@quality_bp.route("/dod/feedback", methods=["POST"])
def submit_feedback():
    """Submit operator feedback on DoD score."""
    try:
        data = request.get_json()

        result = feedback_collector.submit_feedback(
            task_id=data.get("task_id"),
            dod_score_automatic=data.get("dod_score_automatic", 0.0),
            dod_score_operator=data.get("dod_score_operator", 0.5),
            reason=data.get("reason", ""),
            affected_checks=data.get("affected_checks", []),
            task_type=data.get("task_type", "cli_command"),
        )

        return jsonify(result), 200 if result["success"] else 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@quality_bp.route("/dod/weights", methods=["GET"])
def get_weights():
    """Get current weights for a task type."""
    try:
        task_type = request.args.get("task_type", "cli_command")
        weights = feedback_collector.get_weights_for_task_type(task_type)
        status = feedback_collector.get_convergence_status(task_type)

        return jsonify({
            "success": True,
            "task_type": task_type,
            "weights": weights,
            "converged": status["converged"],
            "sample_count": status["sample_count"],
        }), 200

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400
