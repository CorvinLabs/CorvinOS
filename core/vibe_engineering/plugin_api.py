"""Phase 4: Vibe Plugin Install Flow API Routes.

Backend routes for plugin installation, discovery, and lifecycle management.
Mounted at /v1/vibe/plugins/* in Console API.
"""

import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class PluginInstallRequest:
    """Request to install a plugin."""

    def __init__(self, manifest_url: Optional[str] = None, manifest_json: Optional[str] = None):
        self.manifest_url = manifest_url
        self.manifest_json = manifest_json

    @classmethod
    def from_request_body(cls, body: Dict[str, Any]) -> "PluginInstallRequest":
        """Parse from HTTP request body."""
        return cls(
            manifest_url=body.get("manifest_url"),
            manifest_json=body.get("manifest_json")
        )

class PluginInstallResponse:
    """Response from plugin installation."""

    def __init__(self, plugin_id: str, status: str, message: str, manifest: Optional[Dict] = None):
        self.plugin_id = plugin_id
        self.status = status  # "success", "error", "in_progress"
        self.message = message
        self.manifest = manifest
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "plugin_id": self.plugin_id,
            "status": self.status,
            "message": self.message,
            "manifest": self.manifest,
            "timestamp": self.timestamp
        }

class PluginAPIv1:
    """API handler for Vibe plugin management (Phase 4)."""

    def __init__(self, plugin_registry, base_plugins_dir: Optional[Path] = None):
        self.registry = plugin_registry
        self.base_dir = base_plugins_dir or Path("~/.corvin/plugins").expanduser()

    async def install_plugin(self, request: PluginInstallRequest) -> PluginInstallResponse:
        """
        Install plugin from manifest.

        Args:
            request: PluginInstallRequest with either manifest_url or manifest_json

        Returns:
            PluginInstallResponse with status
        """
        try:
            # Step 1: Load manifest
            manifest = None
            if request.manifest_url:
                manifest = await self._fetch_manifest(request.manifest_url)
            elif request.manifest_json:
                manifest = json.loads(request.manifest_json)
            else:
                return PluginInstallResponse(
                    plugin_id="unknown",
                    status="error",
                    message="No manifest_url or manifest_json provided"
                )

            if not manifest:
                return PluginInstallResponse(
                    plugin_id="unknown",
                    status="error",
                    message="Failed to load manifest"
                )

            # Step 2: Validate manifest structure
            plugin_id = manifest.get("plugin", {}).get("id")
            if not plugin_id:
                return PluginInstallResponse(
                    plugin_id="unknown",
                    status="error",
                    message="Manifest missing plugin.id"
                )

            # Step 3: Create plugin directory
            plugin_dir = self.base_dir / plugin_id
            plugin_dir.mkdir(parents=True, exist_ok=True)

            # Step 4: Save manifest
            manifest_path = plugin_dir / "plugin.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

            logger.info(f"Saved plugin manifest: {manifest_path}")

            # Step 5: Load plugin via registry
            loaded_plugin = await self.registry.load_plugin(plugin_dir)

            if loaded_plugin:
                logger.info(f"✅ Plugin installed: {plugin_id}")
                return PluginInstallResponse(
                    plugin_id=plugin_id,
                    status="success",
                    message=f"Plugin {plugin_id} installed successfully",
                    manifest=manifest.get("plugin", {})
                )
            else:
                error_reason = self.registry.failed_plugins.get(plugin_id, "Unknown error")
                return PluginInstallResponse(
                    plugin_id=plugin_id,
                    status="error",
                    message=f"Plugin load failed: {error_reason}"
                )

        except Exception as e:
            logger.error(f"Plugin install failed: {e}")
            return PluginInstallResponse(
                plugin_id=request.manifest_url or "unknown",
                status="error",
                message=f"Install error: {str(e)}"
            )

    async def list_plugins(self) -> Dict[str, Any]:
        """List all installed plugins."""
        loaded = self.registry.list_plugins(loaded_only=True)
        failed = self.registry.get_failed_plugins()

        return {
            "loaded": [{"id": pid, "plugin": self.registry.get_plugin(pid).manifest.__dict__} for pid in loaded],
            "failed": failed,
            "count_total": len(loaded) + len(failed)
        }

    async def get_plugin(self, plugin_id: str) -> Optional[Dict]:
        """Get plugin details."""
        plugin = self.registry.get_plugin(plugin_id)
        if not plugin:
            return None

        return {
            "id": plugin.id,
            "version": plugin.version,
            "author": plugin.manifest.author,
            "description": plugin.manifest.description,
            "skills": plugin.manifest.skills or []
        }

    async def enable_plugin(self, plugin_id: str) -> PluginInstallResponse:
        """Enable a disabled plugin."""
        plugin = self.registry.get_plugin(plugin_id)
        if not plugin:
            return PluginInstallResponse(
                plugin_id=plugin_id,
                status="error",
                message=f"Plugin {plugin_id} not found"
            )

        # In MVP: all loaded plugins are enabled
        # Future: add enable/disable flag to manifest
        return PluginInstallResponse(
            plugin_id=plugin_id,
            status="success",
            message=f"Plugin {plugin_id} enabled"
        )

    async def disable_plugin(self, plugin_id: str) -> PluginInstallResponse:
        """Disable plugin (unload from registry)."""
        await self.registry.unload_plugin(plugin_id)
        return PluginInstallResponse(
            plugin_id=plugin_id,
            status="success",
            message=f"Plugin {plugin_id} disabled"
        )

    async def uninstall_plugin(self, plugin_id: str) -> PluginInstallResponse:
        """Uninstall plugin (remove from disk)."""
        try:
            # Unload first
            await self.registry.unload_plugin(plugin_id)

            # Remove directory
            plugin_dir = self.base_dir / plugin_id
            import shutil
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)

            logger.info(f"✅ Plugin uninstalled: {plugin_id}")
            return PluginInstallResponse(
                plugin_id=plugin_id,
                status="success",
                message=f"Plugin {plugin_id} uninstalled"
            )

        except Exception as e:
            logger.error(f"Plugin uninstall failed: {e}")
            return PluginInstallResponse(
                plugin_id=plugin_id,
                status="error",
                message=f"Uninstall error: {str(e)}"
            )

    async def _fetch_manifest(self, url: str) -> Optional[Dict]:
        """Fetch manifest from URL (MVP: local file fallback)."""
        try:
            # MVP: support file:// URLs only (security)
            if url.startswith("file://"):
                path = Path(url.replace("file://", ""))
                with open(path) as f:
                    return json.load(f)
            else:
                logger.warning(f"Only file:// URLs supported in MVP")
                return None
        except Exception as e:
            logger.error(f"Failed to fetch manifest: {e}")
            return None
