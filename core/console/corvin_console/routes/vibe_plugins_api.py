"""Phase 4: Vibe Plugin Installation API Routes (Console).

Flask blueprint for POST /v1/vibe/plugins/install, GET /v1/vibe/plugins, etc.
"""

from flask import Blueprint, request, jsonify
import logging

logger = logging.getLogger(__name__)

# Global plugin API handler (injected at app init)
_plugin_api = None

def set_plugin_api(api):
    """Inject plugin API handler."""
    global _plugin_api
    _plugin_api = api

def get_plugin_api():
    """Get plugin API handler."""
    return _plugin_api

# Create blueprint
bp = Blueprint("vibe_plugins", __name__, url_prefix="/v1/vibe/plugins")

@bp.route("/install", methods=["POST"])
async def install_plugin():
    """
    Install plugin from manifest.

    Request:
    {
        "manifest_url": "file:///path/to/plugin.json",
        "or": "manifest_json": "{...}"
    }

    Response:
    {
        "plugin_id": "my_plugin",
        "status": "success",
        "message": "Plugin installed",
        "manifest": {...}
    }
    """
    from ...vibe_engineering.plugin_api import PluginInstallRequest

    try:
        api = get_plugin_api()
        if not api:
            return jsonify({"error": "Plugin API not initialized"}), 500

        req_body = request.get_json()
        install_req = PluginInstallRequest.from_request_body(req_body)
        response = await api.install_plugin(install_req)

        status_code = 200 if response.status == "success" else 400
        return jsonify(response.to_dict()), status_code

    except Exception as e:
        logger.error(f"Install failed: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route("/list", methods=["GET"])
async def list_plugins():
    """
    List installed plugins.

    Response:
    {
        "loaded": [...],
        "failed": {...},
        "count_total": 5
    }
    """
    try:
        api = get_plugin_api()
        if not api:
            return jsonify({"error": "Plugin API not initialized"}), 500

        result = await api.list_plugins()
        return jsonify(result), 200

    except Exception as e:
        logger.error(f"List failed: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route("/<plugin_id>", methods=["GET"])
async def get_plugin(plugin_id: str):
    """Get plugin details."""
    try:
        api = get_plugin_api()
        if not api:
            return jsonify({"error": "Plugin API not initialized"}), 500

        plugin = await api.get_plugin(plugin_id)
        if not plugin:
            return jsonify({"error": f"Plugin {plugin_id} not found"}), 404

        return jsonify(plugin), 200

    except Exception as e:
        logger.error(f"Get plugin failed: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route("/<plugin_id>/disable", methods=["POST"])
async def disable_plugin(plugin_id: str):
    """Disable plugin."""
    try:
        api = get_plugin_api()
        response = await api.disable_plugin(plugin_id)
        status_code = 200 if response.status == "success" else 400
        return jsonify(response.to_dict()), status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/<plugin_id>/uninstall", methods=["POST"])
async def uninstall_plugin(plugin_id: str):
    """Uninstall plugin."""
    try:
        api = get_plugin_api()
        response = await api.uninstall_plugin(plugin_id)
        status_code = 200 if response.status == "success" else 400
        return jsonify(response.to_dict()), status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500
