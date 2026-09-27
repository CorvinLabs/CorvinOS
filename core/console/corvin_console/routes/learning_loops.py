"""Learning Loops Console Routes — ADR-0906 Feature 1 Wiring + k=2 Real Wiring

Exposes `/v1/console/capabilities/manifest` with learning_loops array.
Integrates with plugin registry to discover loops at plugin load time (k=2).
Populates health scores from audit chain (k=2 fixture-based, k=3 real audit).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). This is
a FLASK blueprint; the console is FastAPI and never mounts it, and nothing
calls ``integrate_learning_loops_into_capabilities_manifest``. The mounted
learning-loops surface is ``routes/learning_analytics.py``
(``/v1/console/learning-loops/*``), which the SPA calls. Left unmounted on
purpose rather than ported: porting would add a second loops API beside the
one the product uses.

Defused 2026-09-27: when the audit chain could not measure a loop, this module
substituted FABRICATED health (42 events, score 0.85, status "active"); it also
passed the status as a plain string, so ``to_manifest_dict`` crashed on any
measured loop. An unmeasured loop is now reported ``status: "not_measured"``
with null health, never as healthy.
"""

from flask import Blueprint, jsonify, current_app
from typing import List, Dict, Any, Optional
import json
import logging
from pathlib import Path

from core.learning.learning_loop_manifest import LearningLoop, LoopStatus, ManifestParser
from core.learning.learning_loop_audit_integration import AuditQueryHelper

learning_loops_bp = Blueprint("learning_loops", __name__, url_prefix="/v1/console/learning")

# k=2: Global registry (seeded at boot)
_registered_loops: List[LearningLoop] = []
_loops_registry_initialized = False
#: loop_ids whose health the audit chain could not compute.
_unmeasured: set = set()

logger = logging.getLogger(__name__)


def loop_manifest(loop: LearningLoop) -> Dict[str, Any]:
    """``to_manifest_dict`` with an honest status for unmeasured loops."""
    out = loop.to_manifest_dict()
    if loop.loop_id in _unmeasured:
        out.update(status="not_measured", health_score=None, last_event_ts=None)
    return out


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
            "learning_loops": [loop_manifest(loop) for loop in loops],
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
            "loop": loop_manifest(loop),
        }), 200

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


def _get_registered_loops() -> List[LearningLoop]:
    """
    Fetch all registered learning loops.

    k=1: Load from test fixture
    k=2: Scan plugins/ directories + enrich with health data (fixture-based)
    k=3+: Query audit chain for real event history + health computation
    """
    global _registered_loops, _loops_registry_initialized

    if _loops_registry_initialized:
        return _registered_loops

    # k=2: Initialize registry (bootstrap on first call)
    _registered_loops = _bootstrap_learning_loops_from_plugins()
    _loops_registry_initialized = True

    return _registered_loops


def _bootstrap_learning_loops_from_plugins() -> List[LearningLoop]:
    """
    k=2 Real Wiring: Scan all plugins/ directories for plugin.json manifests.
    Parse learning_loops and enrich with health data.

    Scans:
    - core/plugins/buildin/*/plugin.json
    - core/skills/*/plugin.json
    - tests/fixtures/*plugin.json (for testing)
    """
    loops = []

    # Search patterns for plugin.json files
    search_patterns = [
        "core/plugins/buildin/*/plugin.json",
        "core/skills/*/plugin.json",
        "tests/fixtures/*plugin.json",
    ]

    for pattern in search_patterns:
        for manifest_path in Path(".").glob(pattern):
            try:
                with open(manifest_path, "r") as f:
                    manifest = json.load(f)

                # Skip if no learning_loops declared
                if "learning_loops" not in manifest:
                    continue

                # Parse and enrich loops
                parsed_loops = ManifestParser.parse_plugin_manifest(manifest)
                enriched_loops = [_enrich_loop_with_health_data(loop) for loop in parsed_loops]
                loops.extend(enriched_loops)

            except (json.JSONDecodeError, ValueError, Exception) as e:
                # Graceful degradation per ADR-0906 § 2
                logger.warning("failed to parse learning_loops from %s: %s", manifest_path, e)
                continue

    return loops


def _enrich_loop_with_health_data(loop: LearningLoop) -> LearningLoop:
    """
    Enrich a LearningLoop with health metrics.

    k=2: Synthetic data (for schema validation)
    k=3: Real audit chain (implemented here)
    k=4+: Cached + optimized (future enhancement)
    """
    # k=3: Query audit chain for real health data
    health_data = AuditQueryHelper.compute_loop_health_from_audit(
        loop_id=loop.loop_id,
        event_source=loop.event_source
    )

    # Audit chain could not measure this loop → report it as NOT MEASURED.
    # (A synthetic "active / 0.85 / 42 events" used to be substituted here.)
    try:
        status = LoopStatus(health_data.get("status"))
    except ValueError:
        _unmeasured.add(loop.loop_id)
        status = loop.status  # placeholder; loop_manifest() reports "not_measured"
        health_data = {"last_event_ts": None, "event_count_7d": 0, "health_score": None}
    else:
        _unmeasured.discard(loop.loop_id)

    # Return enriched copy (dataclass is frozen, so we rebuild)
    return LearningLoop(
        loop_id=loop.loop_id,
        plugin_id=loop.plugin_id,
        description=loop.description,
        event_source=loop.event_source,
        feedback_types=loop.feedback_types,
        aggregation=loop.aggregation,
        health_threshold=loop.health_threshold,
        dormancy_alert_hours=loop.dormancy_alert_hours,
        owner_skill=loop.owner_skill,
        metadata=loop.metadata,
        last_event_ts=health_data["last_event_ts"],
        event_count_7d=health_data["event_count_7d"],
        health_score=health_data["health_score"],
        status=status,
    )


def integrate_learning_loops_into_capabilities_manifest(manifest_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enhance the capabilities manifest with learning_loops array.

    Called by `/v1/console/capabilities/manifest` (ADR-0904).
    Adds learning_loops as a top-level array per ADR-0906 § 3.
    """
    try:
        loops = _get_registered_loops()
        manifest_dict["learning_loops"] = [loop_manifest(loop) for loop in loops]
    except Exception as e:
        logger.warning("failed to add learning_loops to manifest: %s", e)
        manifest_dict["learning_loops"] = []

    return manifest_dict
