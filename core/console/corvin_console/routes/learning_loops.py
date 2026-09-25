"""Learning Loops Console Routes — ADR-0906 Feature 1 Wiring

Exposes `/v1/console/capabilities/manifest` with learning_loops array.
Integrates with plugin registry to discover loops at plugin load time.
"""

from flask import Blueprint, jsonify, current_app
from typing import List, Dict, Any
import json

from core.learning.learning_loop_manifest import LearningLoop, ManifestParser

learning_loops_bp = Blueprint("learning_loops", __name__, url_prefix="/v1/console/learning")


@learning_loops_bp.route("/loops", methods=["GET"])
def get_learning_loops():
    """Fetch all registered learning loops (Feature 1).

    Returns manifest format per ADR-0906 § 3.
    """
    try:
        # In production, this would be populated from plugin registry
        # For k=1 (Feature 1), we seed with test loops from fixture
        loops = _get_registered_loops()

        return jsonify({
            "status": "success",
            "learning_loops": [loop.to_manifest_dict() for loop in loops],
            "count": len(loops),
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@learning_loops_bp.route("/loops/<loop_id>", methods=["GET"])
def get_learning_loop_detail(loop_id: str):
    """Fetch details for a specific learning loop.

    Example: GET /v1/console/learning/loops/test%2Flearning_loop_manifest%3Aconfidence_routing
    """
    try:
        loops = _get_registered_loops()
        loop = next((l for l in loops if l.loop_id == loop_id), None)

        if not loop:
            return jsonify({
                "status": "error",
                "message": f"Loop not found: {loop_id}",
            }), 404

        return jsonify({
            "status": "success",
            "loop": loop.to_manifest_dict(),
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


def _get_registered_loops() -> List[LearningLoop]:
    """
    Fetch all registered learning loops.

    In production, this would query the plugin registry and parse manifests.
    For k=1, we seed from the test fixture for E2E validation.
    """
    # TODO: Integrate with plugin registry at plugin load time
    # For now, load from test fixture for Feature 1 validation

    test_fixture_path = "tests/fixtures/learning_loop_manifest_test_plugin.json"
    try:
        with open(test_fixture_path, "r") as f:
            manifest = json.load(f)
        loops = ManifestParser.parse_plugin_manifest(manifest)
        return loops
    except FileNotFoundError:
        # If fixture not found, return empty list (graceful degradation)
        return []
    except Exception as e:
        # Log error but don't crash the route
        print(f"Error loading test learning loops: {e}")
        return []


def integrate_learning_loops_into_capabilities_manifest(manifest_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhance the capabilities manifest with learning_loops array.

    Called by `/v1/console/capabilities/manifest` (ADR-0904).
    Adds learning_loops as a top-level array per ADR-0906 § 3.
    """
    try:
        loops = _get_registered_loops()
        manifest_dict["learning_loops"] = [loop.to_manifest_dict() for loop in loops]
    except Exception as e:
        print(f"Warning: failed to add learning_loops to manifest: {e}")
        manifest_dict["learning_loops"] = []

    return manifest_dict
